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


# ============================================================
# 添付画像（Teams投稿の画像をIssueへ載せる）
# ============================================================

def test_build_issue_body_places_images_section_before_requester(agent_env):
    body = ia.build_issue_body(
        {"sender": "松田", "id": "m1"}, "依頼内容です",
        "## 添付画像\n\n![a.png](https://example/a.png)\n",
    )
    assert body.index("## 依頼内容") < body.index("## 添付画像") < body.index("## 依頼元")
    assert "![a.png](https://example/a.png)" in body


def test_build_issue_body_unchanged_without_images(agent_env):
    body = ia.build_issue_body({"sender": "松田", "id": "m1"}, "依頼内容です")
    assert "## 添付画像" not in body
    assert "依頼内容です\n\n## 依頼元" in body


def test_prepare_attachments_pushes_images_to_tools_beta(beta_git_repo):
    import subprocess

    from agent_core import attachments

    folder = attachments.attachments_root() / "msg-77"
    folder.mkdir(parents=True)
    (folder / "画面.png").write_bytes(b"\x89PNG-test")
    source = {"attachments": [{"name": "画面.png"}]}

    images, markdown = ia.prepare_attachments(source, "msg-77")

    assert [image["name"] for image in images] == ["画面.png"]
    assert images[0]["url"].endswith("/issue-assets/msg-77/%E7%94%BB%E9%9D%A2.png")
    assert f"![画面.png]({images[0]['url']})" in markdown

    # リモート(origin)の tools-beta に実際に届いていること
    origin = beta_git_repo.beta_repo.parent / f"{beta_git_repo.beta_repo.name}-origin.git"
    listing = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-tree", "-r", "--name-only", "main"],
        cwd=origin, capture_output=True, text=True, encoding="utf-8",
    ).stdout
    assert "issue-assets/msg-77/画面.png" in listing


def test_prepare_attachments_reports_upload_failure_without_raising(agent_env, monkeypatch):
    from agent_core import attachments

    folder = attachments.attachments_root() / "msg-78"
    folder.mkdir(parents=True)
    (folder / "a.png").write_bytes(b"img")

    import deploy_preview

    def broken():
        raise RuntimeError("push失敗")

    monkeypatch.setattr(deploy_preview, "ensure_beta_repo", broken)

    images, markdown = ia.prepare_attachments({"attachments": [{"name": "a.png"}]}, "msg-78")

    assert images == []
    assert "a.png（アップロード失敗）" in markdown


def test_prepare_attachments_noop_without_images(agent_env):
    assert ia.prepare_attachments({}, "msg-79") == ([], "")
