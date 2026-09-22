"""
[工程6] マージ・クローズ

承認されたIssueについて、
  1. ドラフトPRをレビュー可能状態にする
  2. main へ squash merge する（PR本文の Closes #N でIssueは自動クローズ）
  3. tools-beta のプレビューを削除する
  4. worktree とトピックブランチを片付ける
  5. Teams へ完了通知を出す

却下された場合は、PRをクローズし、変更を破棄してIssueを閉じる。

文字化け対策として、マージ前にコミット済みファイルのエンコーディングを検査する。
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

from agent_core import checks, ghcli, gitops, statefile, teams
from agent_core.config import CONFIG
from agent_core.jsonio import now_iso
from agent_core.locks import issue_lock_path, release, try_acquire
from agent_core.logs import get_logger

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent

REWORK_LABEL = "needs-rework"

TEXT_SUFFIXES = {
    ".html", ".htm", ".css", ".js", ".json", ".md", ".txt", ".svg", ".xml", ".csv",
}
MOJIBAKE_MARKERS = ("�", "縺", "繧", "繝", "ｱ", "郢")
MOJIBAKE_THRESHOLD = 3


class Finisher:
    def __init__(self, issue_number: int, decision: str) -> None:
        self.issue_number = issue_number
        self.decision = decision
        self.log = get_logger("finish", issue_number)
        self.state = statefile.load(issue_number)
        self.branch = str(self.state.get("branch", "")).strip()
        self.title = str(self.state.get("issueTitle", "")) or f"Issue #{issue_number}"
        self.worktree = CONFIG.worktree_path(issue_number)

    # --------------------------------------------------------

    def check_encoding(self) -> list[str]:
        """テキストファイルの文字化けを検出する。"""
        problems: list[str] = []
        changed = self.state.get("changedFiles") or []

        for relative in changed:
            path = self.worktree / str(relative)
            if not path.exists() or path.suffix.lower() not in TEXT_SUFFIXES:
                continue

            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                problems.append(f"{relative} (UTF-8として読めません)")
                continue

            hits = sum(text.count(marker) for marker in MOJIBAKE_MARKERS)
            if hits >= MOJIBAKE_THRESHOLD:
                problems.append(f"{relative} (文字化けの可能性: {hits}箇所)")

        return problems

    def check_static(self) -> dict[str, list[str]]:
        """
        HTMLタグの対応とローカルリンク切れを検査する。

        GitHub Actions等のCIが無い環境でも、
        マージ前に最低限の壊れを検出するための安全網。
        検査対象は今回変更されたHTMLファイルのみ（未変更ファイルまで
        検査すると、この改修と無関係な既存の問題で毎回止まってしまうため）。
        """
        changed = [str(item) for item in (self.state.get("changedFiles") or [])]
        if not self.worktree.exists():
            return {}
        return checks.check_files(self.worktree, changed)

    # --------------------------------------------------------

    def approve(self) -> int:
        if not self.branch:
            self.log.error("ブランチ情報がありません。")
            statefile.update(
                self.issue_number,
                {"status": statefile.Status.FAILED, "error": "branch未設定"},
            )
            return 1

        if self.state.get("status") == statefile.Status.COMPLETED:
            self.log.info("既に完了済みです。処理をスキップします。")
            return 0

        self.log.rule(f"Issue #{self.issue_number} マージ処理")
        self.log.info(f"ブランチ : {self.branch}")

        problems = self.check_encoding()
        if problems:
            detail = "\n".join(f"- {item}" for item in problems)
            self.log.error(f"文字化けを検出したためマージを中止します。\n{detail}")
            statefile.update(
                self.issue_number,
                {"status": statefile.Status.FAILED, "error": "文字化け検出"},
            )
            teams.notify(
                self.issue_number,
                "encoding_error",
                (
                    f"⚠️ Issue #{self.issue_number} のマージを中止しました。\n\n"
                    f"文字化けの可能性があります:\n{detail}\n\n"
                    f"修正後、再度承認してください。"
                ),
                logger=self.log,
            )
            return 1

        static_problems = self.check_static()
        if static_problems:
            detail = "\n".join(
                f"- {relative}\n"
                + "\n".join(f"    - {issue}" for issue in issues)
                for relative, issues in static_problems.items()
            )
            self.log.error(f"静的チェックで問題を検出したためマージを中止します。\n{detail}")
            statefile.update(
                self.issue_number,
                {"status": statefile.Status.FAILED, "error": "静的チェック不合格"},
            )
            teams.notify(
                self.issue_number,
                "static_check_failed",
                (
                    f"⚠️ Issue #{self.issue_number} のマージを中止しました。\n\n"
                    f"HTMLの検査で問題が見つかりました:\n{detail}\n\n"
                    f"修正後、再度承認してください。"
                ),
                logger=self.log,
            )
            return 1

        pr = ghcli.find_open_pr(self.branch)
        if pr is None:
            self.log.error("対象のPRが見つかりません。")
            statefile.update(
                self.issue_number,
                {"status": statefile.Status.FAILED, "error": "PRが見つかりません"},
            )
            teams.notify_failed(self.issue_number, "PRが見つかりません。", self.log)
            return 1

        pr_number = int(pr["number"])
        pr_url = str(pr["url"])
        self.log.info(f"PR #{pr_number}: {pr_url}")

        teams.notify(
            self.issue_number,
            "approved",
            (
                f"✅ Issue #{self.issue_number} が承認されました。\n\n"
                f"PR #{pr_number} を {CONFIG.base_branch} へマージします。"
            ),
            {"pullRequestUrl": pr_url},
            logger=self.log,
        )

        # ドラフトを解除する。
        gitops.run(
            ["gh", "pr", "ready", str(pr_number), "--repo", CONFIG.repository],
            cwd=CONFIG.target_repo,
            check=False,
        )

        merge_state = ghcli.wait_until_mergeable(pr_number)
        self.log.info(f"マージ状態: {merge_state}")

        if merge_state in ("DIRTY", "BLOCKED", "BEHIND"):
            self.log.warn(f"そのままマージできません（{merge_state}）。更新を試みます。")
            gitops.run(
                ["gh", "pr", "update-branch", str(pr_number), "--repo", CONFIG.repository],
                cwd=CONFIG.target_repo,
                check=False,
            )
            merge_state = ghcli.wait_until_mergeable(pr_number, timeout_seconds=120)

        merged, detail = ghcli.merge_pull_request(
            pr_number, method="squash", delete_branch=True, cwd=CONFIG.target_repo
        )

        if not merged:
            self.log.error(f"マージに失敗しました: {detail}")
            statefile.update(
                self.issue_number,
                {"status": statefile.Status.FAILED, "error": f"merge失敗: {detail}"},
            )
            teams.notify_failed(
                self.issue_number,
                f"PR #{pr_number} のマージに失敗しました。\n{detail}",
                self.log,
            )
            return 1

        self.log.info(f"PR #{pr_number} を {CONFIG.base_branch} へマージしました。")

        # Closes #N で閉じていない場合に備える。
        ghcli.close_issue(
            self.issue_number,
            f"PR #{pr_number} のマージにより対応完了しました。",
        )

        self.cleanup(merged=True)

        statefile.update(
            self.issue_number,
            {
                "status": statefile.Status.COMPLETED,
                "pullRequestNumber": pr_number,
                "pullRequestUrl": pr_url,
                "mergedAt": now_iso(),
            },
        )

        teams.notify_completed(
            self.issue_number,
            self.title,
            pr_url,
            list(self.state.get("changedFiles") or []),
            self.log,
        )

        statefile.archive_flow_files(self.issue_number)
        statefile.archive(self.issue_number)

        self.log.rule("完了")
        return 0

    # --------------------------------------------------------

    def reject(self) -> int:
        self.log.rule(f"Issue #{self.issue_number} 却下処理")

        if self.branch:
            pr = ghcli.find_open_pr(self.branch)
            if pr:
                gitops.run(
                    [
                        "gh", "pr", "close", str(pr["number"]),
                        "--repo", CONFIG.repository,
                        "--comment", "Teamsで却下されたためクローズします。",
                        "--delete-branch",
                    ],
                    cwd=CONFIG.target_repo,
                    check=False,
                )
                self.log.info(f"PR #{pr['number']} をクローズしました。")

        if self.worktree.exists():
            gitops.discard_changes(self.worktree)

        # 却下は「実装が気に入らない」であって、依頼そのものの取り下げとは
        # 限らない。Issueを閉じてしまうと依頼内容そのものが消えたように
        # 見えてしまうため、openのまま "needs-rework" ラベルを付けて
        # 再着手できる状態にしておく。
        ghcli.ensure_label(
            REWORK_LABEL,
            description="Teamsで却下され、再実装が必要なIssue",
        )
        ghcli.add_issue_label(self.issue_number, REWORK_LABEL)
        ghcli.comment_issue(
            self.issue_number,
            (
                "Teamsで却下されたため、今回の実装（ブランチ・PR）は破棄しました。\n"
                f"Issueは `{REWORK_LABEL}` ラベルを付けたままopenで残します。\n"
                "内容を見直したうえで、`python agent_cli.py retry "
                f"{self.issue_number}` を実行するか、Teamsから改めて"
                "依頼し直してください。"
            ),
        )

        self.cleanup(merged=False)

        statefile.update(
            self.issue_number,
            {"status": statefile.Status.REJECTED, "rejectedAt": now_iso()},
        )
        teams.notify_rejected(self.issue_number, self.log)

        statefile.archive_flow_files(self.issue_number)
        statefile.archive(self.issue_number)

        self.log.rule("却下処理完了")
        return 0

    # --------------------------------------------------------

    def cleanup(self, merged: bool) -> None:
        """プレビューとworktreeを片付ける。"""
        script = SCRIPT_DIR / "deploy_preview.py"
        if script.exists():
            self.log.info("tools-betaのプレビューを削除します。")
            subprocess.run(
                [
                    sys.executable, str(script),
                    "--issue", str(self.issue_number), "--cleanup",
                ],
                cwd=str(SCRIPT_DIR),
                capture_output=True,
                text=True,
                timeout=600,
            )

        try:
            gitops.remove_worktree(self.issue_number, logger=self.log)
        except Exception as error:
            self.log.warn(f"worktreeの削除に失敗しました: {error}")

        if merged:
            # マージ時に --delete-branch でリモートは消えるのでローカルを掃除する。
            gitops.git("branch", "-D", self.branch, check=False)
        gitops.git("fetch", "origin", "--prune", check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="承認結果に応じてマージ/却下を行います。")
    parser.add_argument("issue", type=int)
    parser.add_argument("--approve", action="store_true")
    parser.add_argument("--reject", action="store_true")
    args = parser.parse_args()

    if args.approve == args.reject:
        parser.error("--approve か --reject のどちらかを指定してください。")

    CONFIG.validate()
    CONFIG.ensure_directories()
    gitops.require_command("git")
    gitops.require_command("gh")

    lock_path = issue_lock_path(args.issue)
    if not try_acquire(lock_path, note="finish_agent"):
        print(f"Issue #{args.issue} は他プロセスが処理中です。")
        return 0

    try:
        finisher = Finisher(args.issue, "approve" if args.approve else "reject")
        return finisher.approve() if args.approve else finisher.reject()
    finally:
        release(lock_path)


if __name__ == "__main__":
    sys.exit(main())
