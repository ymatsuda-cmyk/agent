from __future__ import annotations

from agent_core import statefile, teams
from agent_core.config import CONFIG


# ============================================================
# decision_sections: 承認カードの内容を(見出し, 本文)へ整形する
# ============================================================

def test_decision_sections_includes_only_nonempty_fields():
    detail = {
        "summaryText": "疎通確認ページを実装しました。",
        "implementationText": "",  # 空は除外される
        "verificationText": "ブラウザで表示を確認しました。",
        "notPerformedText": "",
    }

    sections = teams.decision_sections(detail)

    assert sections == [
        (None, "疎通確認ページを実装しました。"),
        ("確認した内容", "ブラウザで表示を確認しました。"),
    ]


def test_decision_sections_appends_rework_comment_when_given():
    sections = teams.decision_sections({}, rework_comment="ボタンの色を青にしてください")

    assert sections == [("修正指示", "ボタンの色を青にしてください")]


def test_decision_sections_omits_rework_heading_when_no_comment():
    sections = teams.decision_sections({"summaryText": "内容"}, rework_comment="")
    headings = [heading for heading, _ in sections]
    assert "修正指示" not in headings


def test_decision_sections_preserves_field_order():
    detail = {
        "summaryText": "A",
        "implementationText": "B",
        "verificationText": "C",
        "notPerformedText": "D",
    }
    sections = teams.decision_sections(detail, rework_comment="E")

    headings = [heading for heading, _ in sections]
    assert headings == [None, "実装内容", "確認した内容", "未実施の確認", "修正指示"]


# ============================================================
# notify_decision: reply/ へ正しい内容で出力する
# ============================================================

def test_notify_decision_writes_reply_with_action_label(agent_env):
    statefile.create(68, {"issueTitle": "疎通確認", "messageId": "msg-1"})

    teams.notify_decision(
        68, "approve", {"summaryText": "実装完了報告"}, logger=None
    )

    files = list(CONFIG.reply_dir.glob("issue-68-approval_approve-*.json"))
    assert len(files) == 1

    import json

    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert "承認" in payload["message"]
    assert "実装完了報告" in payload["message"]


def test_notify_decision_includes_rework_comment_in_message(agent_env):
    statefile.create(68, {"issueTitle": "疎通確認"})

    teams.notify_decision(
        68, "rework", {"summaryText": "内容"}, rework_comment="色を変えて", logger=None
    )

    files = list(CONFIG.reply_dir.glob("issue-68-approval_rework-*.json"))
    assert len(files) == 1

    import json

    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert "再実装依頼" in payload["message"]
    assert "色を変えて" in payload["message"]


def test_notify_decision_falls_back_to_raw_action_for_unknown_action(agent_env):
    statefile.create(68, {"issueTitle": "疎通確認"})

    teams.notify_decision(68, "unknown_action", {}, logger=None)

    files = list(CONFIG.reply_dir.glob("issue-68-approval_unknown_action-*.json"))
    assert len(files) == 1
