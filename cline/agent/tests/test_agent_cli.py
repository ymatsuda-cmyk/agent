from __future__ import annotations

import argparse

import agent_cli
from agent_core import statefile


def _rework_args(issue: int, comment: str) -> argparse.Namespace:
    return argparse.Namespace(issue=issue, comment=comment)


# ============================================================
# rework: Teamsの承認カード無しに、同じworktree/ブランチで
# Clineへ修正指示を再送する（failed状態からの回帰テスト）
# ============================================================

def test_command_rework_transitions_failed_issue_back_to_rework(agent_env):
    statefile.create(
        77,
        {
            "issueTitle": "issue",
            "status": statefile.Status.FAILED,
            "error": "静的チェック不合格",
            "branch": "feature/issue-77-issue",
        },
    )

    result = agent_cli.command_rework(
        _rework_args(77, "clipstock/index.htmlのタグ対応を修正してください。")
    )

    assert result == 0
    state = statefile.load(77)
    assert state["status"] == statefile.Status.REWORK
    assert state["reworkComment"] == "clipstock/index.htmlのタグ対応を修正してください。"
    assert state["reworkCount"] == 1


def test_command_rework_writes_instruction_into_existing_worktree(agent_env):
    worktree = agent_env.worktree_path(77)
    worktree.mkdir(parents=True)
    statefile.create(77, {"status": statefile.Status.FAILED})

    agent_cli.command_rework(_rework_args(77, "修正指示テキスト"))

    rework_file = worktree / ".agent-rework.txt"
    assert rework_file.read_text(encoding="utf-8") == "修正指示テキスト"


def test_command_rework_makes_issue_dispatchable_again(agent_env):
    """orchestratorが拾える状態(DISPATCHABLE_STATUSES)へ戻ること。"""
    statefile.create(77, {"status": statefile.Status.FAILED})

    agent_cli.command_rework(_rework_args(77, "指示"))

    assert statefile.status_of(77) in statefile.DISPATCHABLE_STATUSES


def test_command_rework_increments_count_on_repeated_calls(agent_env):
    statefile.create(77, {"status": statefile.Status.FAILED})

    agent_cli.command_rework(_rework_args(77, "1回目の指示"))
    agent_cli.command_rework(_rework_args(77, "2回目の指示"))

    state = statefile.load(77)
    assert state["reworkCount"] == 2
    assert state["reworkComment"] == "2回目の指示"


def test_command_rework_works_even_without_prior_waiting_file(agent_env):
    """
    承認カードを経由していない(waiting/issue-N.jsonが元々存在しない)場合でも
    例外を出さずに完了すること。
    """
    statefile.create(77, {"status": statefile.Status.FAILED})
    assert not (agent_env.waiting_dir / "issue-77.json").exists()

    result = agent_cli.command_rework(_rework_args(77, "指示"))

    assert result == 0
