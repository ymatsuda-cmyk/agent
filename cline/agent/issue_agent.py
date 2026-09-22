"""
[工程1] Teams投稿 → GitHub Issue作成

request フォルダ（Power Automateが作成）を監視し、
1件のTeams投稿につき1件のIssueを作成する。

重複防止:
- Teams Message ID 単位で processed マーカーを O_EXCL で原子的に予約する
- OneDriveの再同期で同じファイルが再検知されてもIssueは1件だけになる
- 処理済みJSONは request/done へ退避する
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import signal
import sys
import threading
import time

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from agent_core import statefile, teams
from agent_core.config import CONFIG
from agent_core.ghcli import create_issue
from agent_core.jsonio import move_to_done, now_iso, read_json_when_ready, write_json
from agent_core.locks import daemon_lock_path, release, try_acquire
from agent_core.logs import get_logger, print_banner

LOGGER = get_logger("issue_agent")

MESSAGE_ID_KEYS = ("messageId", "id", "parentMessageId")
TITLE_MAX_LENGTH = 60

_processing: set[str] = set()
_processing_lock = threading.Lock()
_shutdown = threading.Event()


# ============================================================
# Teams投稿本文の整形
# ============================================================

def extract_plain_text(message: object) -> str:
    """TeamsのHTML本文からプレーンテキストを取り出す。"""
    if message is None:
        return ""

    text = str(message)
    if "<" in text and ">" in text:
        try:
            from bs4 import BeautifulSoup

            text = BeautifulSoup(text, "html.parser").get_text("\n")
        except ImportError:
            text = re.sub(r"<br\s*/?>", "\n", text)
            text = re.sub(r"</p\s*>", "\n", text)
            text = re.sub(r"<[^>]+>", "", text)

    text = html.unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def build_issue_title(text: str) -> str:
    first_line = next((line for line in text.splitlines() if line.strip()), "")
    title = first_line.strip() or "Teamsからの依頼"
    if len(title) > TITLE_MAX_LENGTH:
        title = title[: TITLE_MAX_LENGTH - 1] + "…"
    return title


def build_issue_body(source: dict, text: str) -> str:
    sender = str(source.get("sender", "") or "不明")
    posted_at = str(source.get("datetime", "") or "")
    message_id = extract_message_id(source)

    return f"""## 依頼内容

{text or '(本文なし)'}

## 依頼元

| 項目 | 内容 |
| --- | --- |
| 依頼者 | {sender} |
| 投稿日時 | {posted_at} |
| Teams Message ID | `{message_id}` |

## 受入条件

- [ ] 依頼内容の通りに実装されている
- [ ] 検証用サイト（{CONFIG.github_beta_repo}）で動作確認できる
- [ ] Teamsで承認を得ている

---

本Issueは Teams 投稿から自動生成されました。
"""


# ============================================================
# 重複防止
# ============================================================

def extract_message_id(source: dict) -> str:
    for key in MESSAGE_ID_KEYS:
        value = source.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def marker_path(message_id: str):
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", message_id)[:120] or "unknown"
    return CONFIG.processed_dir / f"{safe}.json"


def reserve(message_id: str) -> bool:
    """message_id を原子的に予約する。既に予約済みならFalse。"""
    CONFIG.processed_dir.mkdir(parents=True, exist_ok=True)
    return try_acquire(marker_path(message_id), note="issue_agent-reserve")


def confirm_reservation(message_id: str, issue_number: int, issue_url: str) -> None:
    write_json(
        marker_path(message_id),
        {
            "messageId": message_id,
            "issueNumber": issue_number,
            "issueUrl": issue_url,
            "processedAt": now_iso(),
        },
    )


def cancel_reservation(message_id: str) -> None:
    release(marker_path(message_id))


def existing_issue_for(message_id: str) -> dict | None:
    data = read_json_when_ready(marker_path(message_id))
    if data and data.get("issueNumber"):
        return data
    return None


# ============================================================
# 1ファイルの処理
# ============================================================

def process_file(path) -> None:
    if path.parent.name == "done":
        return
    if path.suffix.lower() != ".json":
        return

    key = str(path.resolve()).lower()
    with _processing_lock:
        if key in _processing:
            return
        _processing.add(key)

    try:
        _process_file_inner(path)
    finally:
        with _processing_lock:
            _processing.discard(key)


def _process_file_inner(path) -> None:
    LOGGER.info("-" * 60)
    LOGGER.info(f"検知: {path.name}")

    source = read_json_when_ready(path)
    if source is None:
        if not path.exists():
            # 既にdoneへ退避済みのファイルに対する遅延イベント（OneDrive再同期など）。
            LOGGER.info(f"処理済みのため無視します: {path.name}")
        else:
            LOGGER.warn(f"JSONを読み取れないためスキップします: {path.name}")
        return

    text = extract_plain_text(source.get("message"))
    message_id = extract_message_id(source)

    if not message_id:
        LOGGER.warn("messageIdが無いためスキップします。")
        move_to_done(path)
        return

    if not text:
        LOGGER.warn("本文が空のためIssueを作成しません。")
        move_to_done(path)
        return

    # 既処理判定
    if not reserve(message_id):
        existing = existing_issue_for(message_id)
        if existing:
            LOGGER.info(
                f"処理済みのため作成しません: Issue #{existing['issueNumber']}"
            )
        else:
            LOGGER.info("他プロセスが処理中のためスキップします。")
        move_to_done(path)
        return

    title = build_issue_title(text)
    body = build_issue_body(source, text)

    # Issue作成・Teams通知はネットワークI/Oで時間がかかるため、その前に
    # requestフォルダから退避しておく。OneDriveの遅延イベントが処理完了後に
    # 届いても、ファイルが既に無いことで誤って「JSON不正」と扱われないようにする。
    done_path = move_to_done(path)
    if done_path is None:
        cancel_reservation(message_id)
        LOGGER.error(f"処理済みへの退避に失敗しました。ファイルは残します: {path.name}")
        return

    LOGGER.info(f"Issue作成: {title}")
    issue = create_issue(title, body)

    if issue is None:
        cancel_reservation(message_id)
        try:
            shutil.move(str(done_path), str(path))
            LOGGER.error("Issueの作成に失敗しました。ファイルをrequestへ戻しました。")
        except OSError:
            LOGGER.error(
                f"Issueの作成に失敗し、ファイルの復元にも失敗しました: {done_path.name}"
            )
        return

    issue_number = int(issue["number"])
    issue_url = str(issue["html_url"])
    confirm_reservation(message_id, issue_number, issue_url)

    LOGGER.info(f"Issue #{issue_number} を作成しました: {issue_url}")

    statefile.create(
        issue_number,
        {
            "status": statefile.Status.CREATED,
            "issueTitle": title,
            "issueUrl": issue_url,
            "messageId": message_id,
            "requester": str(source.get("sender", "")),
            "requestFile": path.name,
            "requestedAt": str(source.get("datetime", "")),
        },
    )

    teams.notify(
        issue_number,
        "issue_created",
        (
            f"📝 Issue #{issue_number} を作成しました。\n\n"
            f"タイトル: {title}\n"
            f"URL: {issue_url}\n\n"
            f"順番が来次第、自動で実装を開始します。"
        ),
        {"issueTitle": title, "issueUrl": issue_url},
        logger=LOGGER,
    )

    LOGGER.info(f"処理済みへ移動しました: {path.name}")


# ============================================================
# 監視
# ============================================================

class RequestHandler(FileSystemEventHandler):
    def on_created(self, event) -> None:
        if event.is_directory:
            return
        import pathlib

        process_file(pathlib.Path(event.src_path))

    # OneDriveは一時ファイル経由で置き換えることがあるため、
    # 変更イベントも拾う（重複はmessageId予約で弾かれる）。
    def on_modified(self, event) -> None:
        if event.is_directory:
            return
        import pathlib

        process_file(pathlib.Path(event.src_path))


def process_existing() -> None:
    files = sorted(CONFIG.request_dir.glob("*.json"))
    if not files:
        LOGGER.info("未処理のrequestはありません。")
        return

    LOGGER.info(f"未処理のrequestを {len(files)} 件処理します。")
    for path in files:
        process_file(path)


def handle_shutdown(signum, frame) -> None:
    del signum, frame
    _shutdown.set()
    LOGGER.info("終了要求を受け付けました。")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="requestフォルダを監視してGitHub Issueを作成します。"
    )
    parser.add_argument("--once", action="store_true", help="既存分を処理して終了する")
    args = parser.parse_args()

    CONFIG.validate()
    CONFIG.ensure_directories()

    lock = daemon_lock_path("issue_agent")
    if not try_acquire(lock, note="issue_agent"):
        LOGGER.fail("issue_agent.py は既に起動しています。")

    print_banner(
        "工程1: Teams投稿 → Issue作成エージェント",
        {
            "監視フォルダ": CONFIG.request_dir,
            "処理済み": CONFIG.request_dir / "done",
            "リポジトリ": CONFIG.repository,
            "state": CONFIG.state_dir,
        },
    )

    try:
        process_existing()

        if args.once:
            return

        signal.signal(signal.SIGINT, handle_shutdown)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, handle_shutdown)

        observer = Observer()
        observer.schedule(RequestHandler(), str(CONFIG.request_dir), recursive=False)
        observer.start()
        LOGGER.info("監視を開始しました。終了するには Ctrl+C を押してください。")

        try:
            while not _shutdown.is_set():
                time.sleep(1)
        finally:
            observer.stop()
            observer.join()
    finally:
        release(lock)
        LOGGER.info("issue_agent を終了しました。")


if __name__ == "__main__":
    sys.exit(main())
