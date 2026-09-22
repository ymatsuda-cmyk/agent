from __future__ import annotations

import base64

import pytest

import approval_agent as aa
from agent_core import statefile
from agent_core.config import CONFIG
from agent_core.jsonio import write_json


@pytest.mark.parametrize(
    "value,expected",
    [
        ("approve", "approve"),
        ("Approved", "approve"),
        ("承認", "approve"),
        ("REJECT", "reject"),
        ("却下", "reject"),
        ("rework", "rework"),
        ("再実装", "rework"),
        ("差し戻し", "rework"),
        ("???", "???"),
        (None, ""),
    ],
)
def test_normalize_action(value, expected):
    assert aa.normalize_action(value) == expected


def test_decode_comment_prefers_base64_field():
    encoded = base64.b64encode("ボタンの色を青に".encode()).decode()
    assert aa.decode_comment({"reworkCommentBase64": encoded}) == "ボタンの色を青に"


def test_decode_comment_falls_back_to_plain_field():
    assert aa.decode_comment({"reworkComment": "そのまま"}) == "そのまま"


def test_decode_comment_ignores_invalid_base64():
    assert aa.decode_comment({"reworkCommentBase64": "not valid base64!!"}) == ""


def test_handle_approve_updates_state_and_spawns_finish_agent(agent_env, monkeypatch):
    statefile.create(1, {"issueTitle": "t", "status": statefile.Status.WAITING_APPROVAL})

    spawned = []
    monkeypatch.setattr(
        aa.subprocess, "Popen", lambda command, **kwargs: spawned.append(command)
    )

    aa.handle_approve(1)

    assert statefile.status_of(1) == statefile.Status.APPROVED
    assert spawned and spawned[0][-1] == "--approve"
    assert spawned[0][-2] == "1"


def test_handle_reject_spawns_finish_agent_with_reject_flag(agent_env, monkeypatch):
    spawned = []
    monkeypatch.setattr(
        aa.subprocess, "Popen", lambda command, **kwargs: spawned.append(command)
    )

    aa.handle_reject(2)

    assert spawned and spawned[0][-1] == "--reject"


def test_handle_rework_moves_waiting_file_and_updates_state(agent_env):
    statefile.create(3, {"issueTitle": "t", "status": statefile.Status.WAITING_APPROVAL})
    write_json(CONFIG.waiting_dir / "issue-3.json", {"issueNumber": 3})

    aa.handle_rework(3, "ボタンの色を青に")

    state = statefile.load(3)
    assert state["status"] == statefile.Status.REWORK
    assert state["reworkComment"] == "ボタンの色を青に"
    assert state["reworkCount"] == 1

    assert not (CONFIG.waiting_dir / "issue-3.json").exists()
    assert (CONFIG.waiting_dir / "done" / "issue-3.json").exists()


def test_handle_rework_increments_count_on_repeat(agent_env):
    statefile.create(4, {"status": statefile.Status.WAITING_APPROVAL})
    aa.handle_rework(4, "1回目")
    aa.handle_rework(4, "2回目")

    assert statefile.load(4)["reworkCount"] == 2


def test_handle_rework_writes_rework_file_into_worktree(agent_env):
    worktree = CONFIG.worktree_path(5)
    worktree.mkdir(parents=True)
    statefile.create(5, {"status": statefile.Status.WAITING_APPROVAL})

    aa.handle_rework(5, "修正指示テキスト")

    rework_file = worktree / ".agent-rework.txt"
    assert rework_file.read_text(encoding="utf-8") == "修正指示テキスト"
