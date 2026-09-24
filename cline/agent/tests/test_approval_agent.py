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


# ============================================================
# read_waiting_detail / build_result_comment
# （承認カードの内容をGitHub Issueコメントとしても残す機能）
# ============================================================

def test_read_waiting_detail_reads_waiting_json(agent_env):
    from agent_core.jsonio import write_json

    write_json(
        CONFIG.waiting_dir / "issue-68.json",
        {"issueNumber": 68, "summaryText": "疎通確認の実装完了"},
    )

    detail = aa.read_waiting_detail(68)
    assert detail["summaryText"] == "疎通確認の実装完了"


def test_read_waiting_detail_returns_empty_dict_when_missing(agent_env):
    assert aa.read_waiting_detail(999) == {}


def test_build_result_comment_includes_action_label_and_sections():
    comment = aa.build_result_comment(
        "approve", {"summaryText": "実装完了報告"}, rework_comment=""
    )
    assert "承認" in comment
    assert "実装完了報告" in comment


def test_build_result_comment_includes_rework_heading():
    comment = aa.build_result_comment(
        "rework", {"summaryText": "内容"}, rework_comment="色を変えて"
    )
    assert "修正指示" in comment
    assert "色を変えて" in comment


# ============================================================
# _process_inner: 承認結果をGitHub Issueコメントとしても
# 投稿すること（回帰テスト）
# ============================================================

def test_process_inner_posts_github_comment_on_approve(agent_env, monkeypatch):
    from agent_core.jsonio import write_json

    statefile.create(68, {"status": statefile.Status.WAITING_APPROVAL})
    write_json(
        CONFIG.waiting_dir / "issue-68.json",
        {"issueNumber": 68, "summaryText": "実装完了報告テキスト"},
    )
    path = CONFIG.approval_dir / "issue-68.json"
    write_json(path, {"issueNumber": 68, "action": "approve"})

    comments = []
    monkeypatch.setattr(aa.ghcli, "comment_issue", lambda issue, body: comments.append((issue, body)))
    monkeypatch.setattr(aa, "handle_approve", lambda issue: None)
    notify_calls = []
    monkeypatch.setattr(
        aa.teams, "notify_decision",
        lambda *a, **k: notify_calls.append((a, k)),
    )

    aa._process_inner(path)

    assert len(comments) == 1
    assert comments[0][0] == 68
    assert "実装完了報告テキスト" in comments[0][1]
    assert len(notify_calls) == 1


def test_process_inner_skips_double_processing_of_archived_issue(agent_env, monkeypatch):
    """
    stateが既にdone/へアーカイブ済み(=完了済み)のIssueに対して、
    重複した承認結果ファイルが届いても再処理しないこと。
    """
    from agent_core.jsonio import write_json

    statefile.create(70, {"status": statefile.Status.COMPLETED})
    statefile.archive(70)
    assert statefile.status_of(70) == ""  # state本体はもう無い

    path = CONFIG.approval_dir / "issue-70.json"
    write_json(path, {"issueNumber": 70, "action": "approve"})

    called = []
    monkeypatch.setattr(aa, "handle_approve", lambda issue: called.append(issue))

    aa._process_inner(path)

    assert called == []
    assert not path.exists()  # done へ退避されている


def test_process_inner_does_not_post_comment_for_unknown_action(agent_env, monkeypatch):
    from agent_core.jsonio import write_json

    statefile.create(71, {"status": statefile.Status.WAITING_APPROVAL})
    path = CONFIG.approval_dir / "issue-71.json"
    write_json(path, {"issueNumber": 71, "action": "invalid_action"})

    comments = []
    monkeypatch.setattr(aa.ghcli, "comment_issue", lambda issue, body: comments.append((issue, body)))

    aa._process_inner(path)

    assert comments == []
