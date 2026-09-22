from __future__ import annotations

import issue_agent as ia


def test_extract_plain_text_strips_html_and_blank_lines():
    html = "<div><p>顧客一覧ページを作ってください</p><p>&nbsp;</p><p>期限は今週中です</p></div>"
    text = ia.extract_plain_text(html)
    assert text == "顧客一覧ページを作ってください\n期限は今週中です"


def test_extract_plain_text_handles_none_and_plain_string():
    assert ia.extract_plain_text(None) == ""
    assert ia.extract_plain_text("plain text") == "plain text"


def test_build_issue_title_uses_first_nonblank_line():
    text = "\n\n顧客一覧ページを作ってください\n期限は今週中です"
    assert ia.build_issue_title(text) == "顧客一覧ページを作ってください"


def test_build_issue_title_truncates_long_titles():
    text = "あ" * 100
    title = ia.build_issue_title(text)
    assert len(title) == ia.TITLE_MAX_LENGTH
    assert title.endswith("…")


def test_build_issue_title_falls_back_when_empty():
    assert ia.build_issue_title("") == "Teamsからの依頼"


def test_extract_message_id_prefers_first_matching_key():
    assert ia.extract_message_id({"messageId": "m1", "id": "i1"}) == "m1"
    assert ia.extract_message_id({"id": "i1"}) == "i1"
    assert ia.extract_message_id({}) == ""


def test_build_issue_body_includes_sender_and_message_id():
    source = {"sender": "松田", "datetime": "2026-09-22T00:00:00Z", "id": "msg-1"}
    body = ia.build_issue_body(source, "依頼内容です")
    assert "依頼内容です" in body
    assert "松田" in body
    assert "msg-1" in body


def test_reserve_prevents_duplicate_processing(agent_env):
    message_id = "msg-abc"
    assert ia.reserve(message_id) is True
    assert ia.reserve(message_id) is False  # 二重予約はできない


def test_confirm_and_existing_issue_for_round_trip(agent_env):
    message_id = "msg-xyz"
    ia.reserve(message_id)
    ia.confirm_reservation(message_id, 55, "https://github.com/x/y/issues/55")

    existing = ia.existing_issue_for(message_id)
    assert existing is not None
    assert existing["issueNumber"] == 55


def test_cancel_reservation_allows_retry(agent_env):
    message_id = "msg-retry"
    ia.reserve(message_id)
    ia.cancel_reservation(message_id)

    assert ia.reserve(message_id) is True


def test_marker_path_sanitizes_unsafe_characters(agent_env):
    """
    パス区切り文字が残らないことが安全性の要点（"/"が無ければ
    ディレクトリトラバーサルは起きない）。"..” という文字列自体が
    ファイル名に残ること自体は問題ない。
    """
    path = ia.marker_path("weird/../id:with*chars")
    assert "/" not in path.name
    assert path.parent == agent_env.processed_dir
