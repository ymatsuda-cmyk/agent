"""
[工程5] Teams承認結果の受け取り

approval フォルダ（Power Automateが作成）を監視し、
action に応じて後続処理へ振り分ける。

    approve → finish_agent.py（mainへマージしてIssueクローズ）
    reject  → 変更破棄・PRクローズ・Issueクローズ
    rework  → 修正指示を保存し、stateを rework にする
              （オーケストレーターが再実装ワーカーを起動する）

処理済みJSONは approval/done へ退避する。
"""

from __future__ import annotations

import argparse
import base64
import binascii
import pathlib
import signal
import subprocess
import sys
import threading
import time

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from agent_core import ghcli, statefile, teams
from agent_core.config import CONFIG
from agent_core.jsonio import move_to_done, now_iso, read_json_when_ready
from agent_core.locks import daemon_lock_path, release, try_acquire
from agent_core.logs import get_logger, print_banner

LOGGER = get_logger("approval_agent")
SCRIPT_DIR = pathlib.Path(__file__).resolve().parent

APPROVE_VALUES = {"approve", "approved", "ok", "yes", "承認"}
REJECT_VALUES = {"reject", "rejected", "ng", "no", "却下"}
REWORK_VALUES = {"rework", "redo", "再実装", "差し戻し"}

_processing: set[str] = set()
_processing_lock = threading.Lock()
_shutdown = threading.Event()


def decode_comment(data: dict) -> str:
    encoded = str(data.get("reworkCommentBase64", "")).strip()
    if encoded:
        try:
            return base64.b64decode(encoded, validate=True).decode("utf-8").strip()
        except (binascii.Error, UnicodeDecodeError, ValueError):
            LOGGER.warn("reworkCommentBase64をデコードできませんでした。")

    return str(data.get("reworkComment", "")).strip()


def normalize_action(value: object) -> str:
    action = str(value or "").strip().lower()
    if action in APPROVE_VALUES:
        return "approve"
    if action in REJECT_VALUES:
        return "reject"
    if action in REWORK_VALUES:
        return "rework"
    return action


def read_waiting_detail(issue_number: int) -> dict:
    """承認カードに表示した内容（waiting/issue-<N>.json）を読み込む。"""
    path = CONFIG.waiting_dir / f"issue-{issue_number}.json"
    return read_json_when_ready(path) or {}


def build_result_comment(action: str, detail: dict, rework_comment: str = "") -> str:
    """承認画面の内容と回答をIssueコメント用に整形する。"""
    lines = [f"## Teams承認結果: {teams.ACTION_LABELS.get(action, action)}"]

    for heading, text in teams.decision_sections(detail, rework_comment):
        if heading:
            lines.append(f"\n### {heading}")
        lines.append(text)

    return "\n".join(lines)


# ============================================================
# アクション別処理
# ============================================================

def handle_approve(issue_number: int) -> None:
    LOGGER.info(f"Issue #{issue_number}: 承認されました。マージ処理を開始します。")

    statefile.update(
        issue_number,
        {"status": statefile.Status.APPROVED, "approvedAt": now_iso()},
    )

    subprocess.Popen(
        [sys.executable, str(SCRIPT_DIR / "finish_agent.py"), str(issue_number), "--approve"],
        cwd=str(SCRIPT_DIR),
    )


def handle_reject(issue_number: int) -> None:
    LOGGER.info(f"Issue #{issue_number}: 却下されました。")

    subprocess.Popen(
        [sys.executable, str(SCRIPT_DIR / "finish_agent.py"), str(issue_number), "--reject"],
        cwd=str(SCRIPT_DIR),
    )


def handle_rework(issue_number: int, comment: str) -> None:
    LOGGER.info(f"Issue #{issue_number}: 再実装が指示されました。")
    LOGGER.info(f"修正指示: {comment or '(指示なし)'}")

    worktree = CONFIG.worktree_path(issue_number)
    if worktree.exists():
        try:
            (worktree / ".agent-rework.txt").write_text(
                comment or "(指示なし)", encoding="utf-8"
            )
        except OSError as error:
            LOGGER.warn(f"修正指示ファイルを書けません: {error}")

    # 承認待ちJSONを退避してから rework へ戻す。
    move_to_done(
        CONFIG.waiting_dir / f"issue-{issue_number}.json",
        CONFIG.waiting_dir / "done",
    )

    statefile.update(
        issue_number,
        {
            "status": statefile.Status.REWORK,
            "reworkComment": comment,
            "reworkRequestedAt": now_iso(),
            "reworkCount": int(statefile.load(issue_number).get("reworkCount", 0)) + 1,
        },
    )

    teams.notify_rework(issue_number, comment, LOGGER)


# ============================================================
# ファイル処理
# ============================================================

def process_file(path: pathlib.Path) -> None:
    if path.parent.name == "done" or path.suffix.lower() != ".json":
        return

    key = str(path.resolve()).lower()
    with _processing_lock:
        if key in _processing:
            return
        _processing.add(key)

    try:
        _process_inner(path)
    finally:
        with _processing_lock:
            _processing.discard(key)


def _process_inner(path: pathlib.Path) -> None:
    data = read_json_when_ready(path)
    if data is None:
        LOGGER.warn(f"JSONを読み取れません: {path.name}")
        return

    try:
        issue_number = int(data["issueNumber"])
    except (KeyError, TypeError, ValueError):
        LOGGER.warn(f"issueNumberが不正です: {path.name}")
        move_to_done(path)
        return

    action = normalize_action(data.get("action"))
    LOGGER.info("-" * 60)
    LOGGER.info(f"承認結果を検知: Issue #{issue_number} / action={action}")

    current = statefile.status_of(issue_number)
    if current in statefile.TERMINAL_STATUSES:
        LOGGER.info(f"既に {statefile.label(current)} のため処理しません。")
        move_to_done(path)
        return

    # status_of()はstateが存在しない場合も空文字を返すため、
    # 既にdoneへアーカイブ済み（=完了済み）のIssueへの二重承認を
    # 見逃さないよう、state本体が無い場合はdoneの記録も確認する。
    if not current and list((CONFIG.state_dir / "done").glob(f"issue-{issue_number}*.json")):
        LOGGER.info(f"Issue #{issue_number} は既に完了しdoneへ退避済みのため処理しません。")
        move_to_done(path)
        return

    if action in ("approve", "reject", "rework"):
        rework_comment = decode_comment(data) if action == "rework" else ""
        detail = read_waiting_detail(issue_number)
        ghcli.comment_issue(
            issue_number, build_result_comment(action, detail, rework_comment)
        )
        teams.notify_decision(issue_number, action, detail, rework_comment, logger=LOGGER)

    if action == "approve":
        handle_approve(issue_number)
    elif action == "reject":
        handle_reject(issue_number)
    elif action == "rework":
        handle_rework(issue_number, decode_comment(data))
    else:
        LOGGER.warn(f"未知のactionです: {action}")

    move_to_done(path)
    LOGGER.info(f"処理済みへ移動しました: {path.name}")


# ============================================================
# 監視
# ============================================================

class ApprovalHandler(FileSystemEventHandler):
    def on_created(self, event) -> None:
        if not event.is_directory:
            process_file(pathlib.Path(event.src_path))

    def on_modified(self, event) -> None:
        if not event.is_directory:
            process_file(pathlib.Path(event.src_path))


def handle_shutdown(signum, frame) -> None:
    del signum, frame
    _shutdown.set()
    LOGGER.info("終了要求を受け付けました。")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="approvalフォルダを監視して承認結果を処理します。"
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    CONFIG.validate()
    CONFIG.ensure_directories()

    lock = daemon_lock_path("approval_agent")
    if not try_acquire(lock, note="approval_agent"):
        LOGGER.fail("approval_agent.py は既に起動しています。")

    print_banner(
        "工程5: Teams承認結果エージェント",
        {
            "監視フォルダ": CONFIG.approval_dir,
            "処理済み": CONFIG.approval_dir / "done",
            "リポジトリ": CONFIG.repository,
        },
    )

    try:
        for path in sorted(CONFIG.approval_dir.glob("*.json")):
            process_file(path)

        if args.once:
            return 0

        signal.signal(signal.SIGINT, handle_shutdown)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, handle_shutdown)

        observer = Observer()
        observer.schedule(ApprovalHandler(), str(CONFIG.approval_dir), recursive=False)
        observer.start()
        LOGGER.info("監視を開始しました。終了するには Ctrl+C を押してください。")

        try:
            while not _shutdown.is_set():
                time.sleep(1)
        finally:
            observer.stop()
            observer.join()

        return 0
    finally:
        release(lock)
        LOGGER.info("approval_agent を終了しました。")


if __name__ == "__main__":
    sys.exit(main())
