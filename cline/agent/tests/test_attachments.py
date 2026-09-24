from __future__ import annotations

import pytest

from agent_core import attachments


# ============================================================
# safe_name: Power Automateから渡る名前を安全なファイル名にする
# ============================================================

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("画面.png", "画面.png"),
        ("Screenshot.PNG", "Screenshot.png"),
        ("../../etc/passwd.png", "passwd.png"),
        ("..\\..\\secret.jpg", "secret.jpg"),
        ('a:b*c?"d<e>f|.png', "a_b_c__d_e_f_.png"),
        ("", ""),
        ("...", ""),
    ],
)
def test_safe_name(raw, expected):
    assert attachments.safe_name(raw) == expected


def test_requested_names_keeps_only_images_and_dedupes():
    source = {
        "attachments": [
            {"name": "a.png"},
            {"name": "report.pdf"},
            "b.JPG",
            {"name": "a.png"},
            {"name": "../c.gif"},
        ]
    }
    assert attachments.requested_names(source) == ["a.png", "b.jpg", "c.gif"]


def test_requested_names_limits_count():
    source = {"attachments": [{"name": f"{i}.png"} for i in range(30)]}
    assert len(attachments.requested_names(source)) == attachments.MAX_IMAGES


def test_requested_names_handles_missing_or_invalid_field():
    assert attachments.requested_names({}) == []
    assert attachments.requested_names({"attachments": "a.png"}) == []


def test_folder_name_prefers_attachments_folder_and_sanitizes():
    assert attachments.folder_name_for({"attachmentsFolder": "abc/../x"}, "m1") == "abc_.._x"
    assert attachments.folder_name_for({}, "1790080540703") == "1790080540703"


# ============================================================
# collect_request_images
# ============================================================

def _write(folder, name, size=100):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"\x89PNG" + b"0" * size)
    return path


def test_collect_request_images_returns_present_files_in_order(agent_env):
    folder = attachments.attachments_root() / "msg-1"
    _write(folder, "b.png")
    _write(folder, "a.png")
    source = {"attachments": [{"name": "b.png"}, {"name": "a.png"}]}

    paths, problems = attachments.collect_request_images(source, "msg-1", wait_timeout=0)

    assert [p.name for p in paths] == ["b.png", "a.png"]
    assert problems == []


def test_collect_request_images_reports_missing_without_blocking(agent_env):
    folder = attachments.attachments_root() / "msg-2"
    _write(folder, "a.png")
    source = {"attachments": [{"name": "a.png"}, {"name": "missing.png"}]}

    paths, problems = attachments.collect_request_images(source, "msg-2", wait_timeout=0)

    assert [p.name for p in paths] == ["a.png"]
    assert problems == ["missing.png（ファイルが見つかりません）"]


def test_collect_request_images_excludes_oversized(agent_env, monkeypatch):
    monkeypatch.setattr(attachments, "MAX_IMAGE_BYTES", 50)
    folder = attachments.attachments_root() / "msg-3"
    _write(folder, "big.png", size=200)
    source = {"attachments": [{"name": "big.png"}]}

    paths, problems = attachments.collect_request_images(source, "msg-3", wait_timeout=0)

    assert paths == []
    assert "big.png" in problems[0] and "除外" in problems[0]


def test_collect_request_images_waits_for_late_arrival(agent_env, monkeypatch):
    """OneDrive同期の都合で、JSONより後から画像が届くケース。"""
    folder = attachments.attachments_root() / "msg-4"
    folder.mkdir(parents=True)
    calls = {"n": 0}

    def fake_sleep(seconds):
        calls["n"] += 1
        if calls["n"] == 3:  # 数回待った後に画像が届く
            _write(folder, "late.png")

    monkeypatch.setattr(attachments.time, "sleep", fake_sleep)
    source = {"attachments": [{"name": "late.png"}]}

    paths, problems = attachments.collect_request_images(source, "msg-4", wait_timeout=60)

    assert [p.name for p in paths] == ["late.png"]
    assert problems == []


def test_collect_request_images_no_attachments_is_noop(agent_env):
    assert attachments.collect_request_images({}, "msg-5", wait_timeout=0) == ([], [])


# ============================================================
# URL と Markdown
# ============================================================

def test_raw_url_encodes_japanese_file_names(agent_env):
    url = attachments.raw_url("issue-assets/msg-1/画面 1.png")
    assert url == (
        "https://raw.githubusercontent.com/test-owner/tools-beta/main/"
        "issue-assets/msg-1/%E7%94%BB%E9%9D%A2%201.png"
    )


def test_build_markdown_lists_images_and_problems():
    markdown = attachments.build_markdown(
        [{"name": "a.png", "url": "https://example/a.png"}],
        ["b.png（ファイルが見つかりません）"],
    )
    assert "## 添付画像" in markdown
    assert "![a.png](https://example/a.png)" in markdown
    assert "- b.png（ファイルが見つかりません）" in markdown


def test_build_markdown_empty_when_nothing():
    assert attachments.build_markdown([], []) == ""


# ============================================================
# copy_into_worktree
# ============================================================

def test_copy_into_worktree_copies_local_files(tmp_path):
    source = tmp_path / "src" / "a.png"
    source.parent.mkdir()
    source.write_bytes(b"img")
    worktree = tmp_path / "wt"
    worktree.mkdir()

    placed = attachments.copy_into_worktree(
        [{"name": "a.png", "localPath": str(source), "url": ""}], worktree
    )

    assert placed == [".agent-attachments/a.png"]
    assert (worktree / ".agent-attachments" / "a.png").read_bytes() == b"img"


def test_copy_into_worktree_downloads_when_local_file_missing(tmp_path, monkeypatch):
    class FakeResponse:
        content = b"downloaded"

        def raise_for_status(self):
            pass

    import requests

    monkeypatch.setattr(requests, "get", lambda url, timeout: FakeResponse())
    worktree = tmp_path / "wt"
    worktree.mkdir()

    placed = attachments.copy_into_worktree(
        [{"name": "a.png", "localPath": str(tmp_path / "gone.png"), "url": "https://x/a.png"}],
        worktree,
    )

    assert placed == [".agent-attachments/a.png"]
    assert (worktree / ".agent-attachments" / "a.png").read_bytes() == b"downloaded"


def test_copy_into_worktree_sanitizes_names_from_state(tmp_path):
    source = tmp_path / "a.png"
    source.write_bytes(b"img")
    worktree = tmp_path / "wt"
    worktree.mkdir()

    placed = attachments.copy_into_worktree(
        [{"name": "../../escape.png", "localPath": str(source)}], worktree
    )

    assert placed == [".agent-attachments/escape.png"]
    assert not (tmp_path / "escape.png").exists()


def test_copy_into_worktree_noop_without_images(tmp_path):
    assert attachments.copy_into_worktree([], tmp_path) == []
    assert not (tmp_path / ".agent-attachments").exists()
