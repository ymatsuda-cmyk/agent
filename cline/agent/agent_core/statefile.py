"""
Issue単位の状態管理。

state/issue-<N>.json が唯一の正本（single source of truth）。
状態別フォルダのJSONは「工程間の受け渡し伝票」であり、
処理が終わったら各フォルダの done/ へ退避する。
"""

from __future__ import annotations

import pathlib

from .config import CONFIG
from .jsonio import move_to_done, now_iso, read_json_when_ready, write_json


# ============================================================
# 状態定義
# ============================================================

class Status:
    CREATED = "created"                      # Issue作成済み・着手待ち
    QUEUED = "queued"                        # オーケストレーターが着手予約
    IMPLEMENTING = "implementing"            # Clineが実装中
    WAITING_DECISION = "waiting_decision"    # Clineの質問をTeamsへ投げ回答待ち
    PREVIEW_DEPLOYING = "preview_deploying"  # tools-betaへ検証公開中
    WAITING_APPROVAL = "waiting_approval"    # Teams承認待ち
    APPROVED = "approved"                    # 承認済み・マージ処理中
    REWORK = "rework"                        # 差し戻し・再実装待ち
    COMPLETED = "completed"                  # マージ＆Issueクローズ完了
    REJECTED = "rejected"                    # 却下（変更破棄）
    FAILED = "failed"                        # 異常終了

#: 再着手してはいけない状態
TERMINAL_STATUSES = frozenset({Status.COMPLETED, Status.REJECTED})

#: オーケストレーターが拾う状態
DISPATCHABLE_STATUSES = frozenset({Status.CREATED, Status.REWORK})

#: 状態の日本語表示
STATUS_LABELS: dict[str, str] = {
    Status.CREATED: "着手待ち",
    Status.QUEUED: "着手予約",
    Status.IMPLEMENTING: "実装中",
    Status.WAITING_DECISION: "回答待ち",
    Status.PREVIEW_DEPLOYING: "検証環境公開中",
    Status.WAITING_APPROVAL: "承認待ち",
    Status.APPROVED: "マージ処理中",
    Status.REWORK: "再実装待ち",
    Status.COMPLETED: "完了",
    Status.REJECTED: "却下",
    Status.FAILED: "異常終了",
}


def label(status: str) -> str:
    return STATUS_LABELS.get(status, status or "不明")


# ============================================================
# 読み書き
# ============================================================

def load(issue_number: int) -> dict:
    path = CONFIG.state_path(issue_number)
    return read_json_when_ready(path) or {}


def save(issue_number: int, state: dict) -> None:
    state["issueNumber"] = issue_number
    state["updatedAt"] = now_iso()
    write_json(CONFIG.state_path(issue_number), state)


def update(issue_number: int, updates: dict) -> dict:
    """既存stateへ差分を反映する。履歴も追記する。"""
    state = load(issue_number)

    new_status = updates.get("status")
    old_status = state.get("status")

    state.update(updates)

    if new_status and new_status != old_status:
        history = state.get("history")
        if not isinstance(history, list):
            history = []
        history.append({"status": new_status, "at": now_iso()})
        state["history"] = history[-50:]

    save(issue_number, state)
    return state


def create(issue_number: int, payload: dict) -> dict:
    """新規state。既存があれば上書きせず差分反映のみ。"""
    state = load(issue_number)
    if state:
        return update(issue_number, payload)

    base = {
        "issueNumber": issue_number,
        "status": Status.CREATED,
        "createdAt": now_iso(),
    }
    base.update(payload)
    base["history"] = [{"status": base.get("status", Status.CREATED), "at": now_iso()}]
    save(issue_number, base)
    return base


def status_of(issue_number: int) -> str:
    return str(load(issue_number).get("status", "")).strip().lower()


def message_id_of(issue_number: int) -> str:
    return str(load(issue_number).get("messageId", "")).strip()


def branch_of(issue_number: int) -> str:
    return str(load(issue_number).get("branch", "")).strip()


# ============================================================
# 一覧
# ============================================================

def iter_states() -> list[tuple[int, dict]]:
    """state配下のIssue状態を番号順に返す。"""
    results: list[tuple[int, dict]] = []

    for path in sorted(CONFIG.state_dir.glob("issue-*.json")):
        data = read_json_when_ready(path)
        if not data:
            continue

        issue_number = _issue_number_from(path, data)
        if issue_number is None:
            continue

        results.append((issue_number, data))

    results.sort(key=lambda item: item[0])
    return results


def _issue_number_from(path: pathlib.Path, data: dict) -> int | None:
    value = data.get("issueNumber")
    try:
        if value is not None:
            return int(value)
    except (TypeError, ValueError):
        pass

    stem = path.stem  # issue-123
    if stem.startswith("issue-"):
        try:
            return int(stem[len("issue-"):])
        except ValueError:
            return None
    return None


def active_issue_numbers() -> set[int]:
    """処理中（実装〜マージ）のIssue番号。並走数の判定に使う。"""
    running = {
        Status.QUEUED,
        Status.IMPLEMENTING,
        Status.WAITING_DECISION,
        Status.PREVIEW_DEPLOYING,
        Status.APPROVED,
    }
    return {
        number
        for number, data in iter_states()
        if str(data.get("status", "")).strip().lower() in running
    }


# ============================================================
# 完了後の退避
# ============================================================

def archive(issue_number: int) -> None:
    """完了/却下したIssueのstateをstate/done へ退避する。"""
    move_to_done(CONFIG.state_path(issue_number), CONFIG.state_dir / "done")


def restore_from_archive(issue_number: int) -> bool:
    """
    state/done/ に退避済みのstateを state/ へ戻す。

    却下(rejected)はIssueをcloseせず、needs-reworkラベルを付けて
    openのまま残す設計にしたため、退避された古いstateを復元できないと
    「ラベルは付いているがretryできない」という中途半端な状態になる。
    複数世代分退避されている場合は、最も新しいものを使う。
    """
    done_dir = CONFIG.state_dir / "done"
    candidates = sorted(
        done_dir.glob(f"issue-{issue_number}*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return False

    latest = candidates[0]
    destination = CONFIG.state_path(issue_number)
    if destination.exists():
        return True  # 既に state/ 側にあるので何もしなくてよい

    latest.rename(destination)
    return True


def archive_flow_files(issue_number: int) -> None:
    """工程フォルダに残っているIssue伝票をすべて done/ へ退避する。"""
    file_name = f"issue-{issue_number}.json"

    for folder_name in ("question", "decision", "waiting", "approval"):
        directory = getattr(CONFIG, f"{folder_name}_dir")
        move_to_done(directory / file_name, directory / "done")
