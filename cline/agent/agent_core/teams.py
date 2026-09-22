"""
Teamsへの通知。

Pythonは直接Teamsへ投稿せず、reply フォルダへJSONを書くだけにする。
投稿はPower Automate（04_reply→Teams投稿）が担当し、
投稿後にファイルを reply/done へ移動する。

この分離により、
- Python側にTeams認証を持たせない
- 通知失敗時もJSONが残るので再送できる
という利点がある。
"""

from __future__ import annotations

from datetime import datetime

from . import statefile
from .config import CONFIG
from .jsonio import now_iso, write_json


def notify(
    issue_number: int,
    message_type: str,
    message: str,
    extra: dict | None = None,
    logger=None,
) -> bool:
    """reply/issue-<N>-<type>-<時刻>.json を出力する。"""
    state = statefile.load(issue_number)
    message_id = str(state.get("messageId", "")).strip()

    payload: dict = {
        "type": message_type,
        "status": message_type,
        "issueNumber": issue_number,
        "issueTitle": str(state.get("issueTitle", "")),
        "issueUrl": str(state.get("issueUrl", "")),
        "branch": str(state.get("branch", "")),
        "messageId": message_id,
        "message": message,
        "createdAt": now_iso(),
    }

    if extra:
        payload.update(extra)

    stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    file_name = f"issue-{issue_number}-{message_type}-{stamp}.json"
    path = CONFIG.reply_dir / file_name

    CONFIG.reply_dir.mkdir(parents=True, exist_ok=True)
    succeeded = write_json(path, payload)

    if logger is not None:
        if succeeded:
            logger.info(f"Teams通知JSON出力: {path.name}")
        else:
            logger.warn(f"Teams通知JSON出力失敗: {path.name}")

    return succeeded


# ============================================================
# よく使う定型通知
# ============================================================

def notify_started(issue_number: int, title: str, branch: str, logger=None) -> None:
    notify(
        issue_number,
        "implementation_started",
        (
            f"🚀 Issue #{issue_number} の実装を開始しました。\n\n"
            f"タイトル: {title}\n"
            f"ブランチ: {branch}"
        ),
        {"branch": branch},
        logger=logger,
    )


def notify_failed(issue_number: int, reason: str, logger=None) -> None:
    notify(
        issue_number,
        "implementation_failed",
        f"⚠️ Issue #{issue_number} の処理が中断しました。\n\n理由: {reason}",
        logger=logger,
    )


def notify_completed(
    issue_number: int,
    title: str,
    pr_url: str,
    changed_files: list[str],
    logger=None,
) -> None:
    file_list = "\n".join(f"- {path}" for path in changed_files) or "- (変更なし)"
    notify(
        issue_number,
        "completed",
        (
            f"🎉 Issue #{issue_number} が完了しました。\n\n"
            f"タイトル: {title}\n\n"
            f"変更ファイル:\n{file_list}\n\n"
            f"PR: {pr_url or '(URL取得失敗)'}\n\n"
            f"mainへマージし、Issueをクローズしました。"
        ),
        {"pullRequestUrl": pr_url},
        logger=logger,
    )


def notify_rejected(issue_number: int, logger=None) -> None:
    notify(
        issue_number,
        "rejected",
        f"❌ Issue #{issue_number} の実装が却下されました。変更は破棄しました。",
        logger=logger,
    )


def notify_rework(issue_number: int, comment: str, logger=None) -> None:
    notify(
        issue_number,
        "rework",
        (
            f"🔄 Issue #{issue_number} の再実装を開始します。\n\n"
            f"修正指示:\n{comment or '(指示なし)'}"
        ),
        logger=logger,
    )
