from __future__ import annotations

import pathlib

import deploy_preview as dp
from agent_core import gitops


def _make_worktree(agent_env, issue_number: int) -> pathlib.Path:
    path, _ = gitops.prepare_worktree(issue_number, "テスト", "main")
    return path


def test_copy_worktree_tree_copies_everything_including_unchanged_assets(full_repo_env):
    worktree = _make_worktree(full_repo_env, 13)
    (worktree / "shared").mkdir()
    (worktree / "shared" / "style.css").write_text("body{color:red}", encoding="utf-8")
    (worktree / "shop").mkdir()
    (worktree / "shop" / "index.html").write_text(
        '<link rel="stylesheet" href="/shared/style.css">', encoding="utf-8"
    )

    beta_repo = dp.ensure_beta_repo()
    copied = dp.copy_worktree_tree(worktree, beta_repo, 13)

    # 「変更ファイルではない」共有CSSも含め、worktree全体が運ばれること（弱点3の修正）
    assert "shared/style.css" in copied
    assert "shop/index.html" in copied

    destination = beta_repo / dp.PREVIEW_ROOT / "issue-13"
    assert (destination / "shared" / "style.css").exists()
    assert (destination / "shop" / "index.html").exists()


def test_copy_worktree_tree_excludes_git_and_control_files(full_repo_env):
    worktree = _make_worktree(full_repo_env, 14)
    (worktree / ".agent-summary.json").write_text("{}", encoding="utf-8")

    beta_repo = dp.ensure_beta_repo()
    copied = dp.copy_worktree_tree(worktree, beta_repo, 14)

    assert not any(".git" in item.split("/") for item in copied)
    assert ".agent-summary.json" not in copied


def test_copy_worktree_tree_clears_previous_deployment(full_repo_env):
    worktree = _make_worktree(full_repo_env, 15)
    beta_repo = dp.ensure_beta_repo()

    (worktree / "old.html").write_text("old", encoding="utf-8")
    dp.copy_worktree_tree(worktree, beta_repo, 15)

    (worktree / "old.html").unlink()
    (worktree / "new.html").write_text("new", encoding="utf-8")
    dp.copy_worktree_tree(worktree, beta_repo, 15)

    destination = beta_repo / dp.PREVIEW_ROOT / "issue-15"
    assert not (destination / "old.html").exists()
    assert (destination / "new.html").exists()


def test_write_index_skips_when_index_already_exists(full_repo_env):
    worktree = _make_worktree(full_repo_env, 16)
    beta_repo = dp.ensure_beta_repo()
    dp.copy_worktree_tree(worktree, beta_repo, 16)  # worktree自体のindex.htmlが運ばれる

    destination = beta_repo / dp.PREVIEW_ROOT / "issue-16"
    original = (destination / "index.html").read_text(encoding="utf-8")

    dp.write_index(beta_repo, 16, ["shop/new.html"], "テスト")

    # 既存のindex.htmlを一覧ページで上書きしていないこと
    assert (destination / "index.html").read_text(encoding="utf-8") == original


def test_write_index_creates_fallback_when_no_index(full_repo_env):
    worktree = _make_worktree(full_repo_env, 17)
    (worktree / "index.html").unlink()  # worktree由来のindex.htmlを消す

    beta_repo = dp.ensure_beta_repo()
    dp.copy_worktree_tree(worktree, beta_repo, 17)
    dp.write_index(beta_repo, 17, ["shop/new.html"], "テスト")

    destination = beta_repo / dp.PREVIEW_ROOT / "issue-17"
    content = (destination / "index.html").read_text(encoding="utf-8")
    assert "shop/new.html" in content


def test_build_preview_url_prefers_changed_file_index():
    url = dp.build_preview_url(18, ["shop/index.html", "shop/app.js"])
    assert url.endswith("/issue-18/shop/")


def test_build_preview_url_falls_back_to_issue_root_when_no_index_changed():
    url = dp.build_preview_url(18, ["shop/app.js"])
    assert url.endswith("/issue-18/")


def test_build_preview_url_skips_index_missing_from_preview(tmp_path):
    # ファイル移動のIssue: 移動元(beta/shop)は削除済みでプレビューに存在しない
    preview_root = tmp_path / "issue-18"
    (preview_root / "shop").mkdir(parents=True)
    (preview_root / "shop" / "index.html").write_text("<p>shop</p>", encoding="utf-8")

    url = dp.build_preview_url(
        18,
        ["beta/shop/index.html", "shop/index.html"],
        preview_root,
    )

    assert url.endswith("/issue-18/shop/")


def test_deploy_url_skips_moved_away_index_end_to_end(full_repo_env):
    """
    ファイル移動のIssue（例: beta/clipstock を clipstock へ移動）で、
    changed_files に移動元(削除された)パスも含まれる場合、
    deploy() が実際に配置されたファイルだけを候補にしてURLを組み立てること。
    deploy_preview.py側の編集ミスで、この preview_root の受け渡しが
    壊れていた実例があったための回帰テスト。
    """
    worktree = _make_worktree(full_repo_env, 19)
    (worktree / "shop").mkdir()
    (worktree / "shop" / "index.html").write_text("<p>new location</p>", encoding="utf-8")
    # "old/index.html" は worktree上に存在しない(移動元・削除済み)想定

    result = dp.deploy(19, worktree, ["old/index.html", "shop/index.html"])

    assert result["previewUrl"].endswith("/issue-19/shop/")


def test_deploy_full_flow_pushes_to_beta_repo(full_repo_env):
    worktree = _make_worktree(full_repo_env, 19)
    (worktree / "shop").mkdir()
    (worktree / "shop" / "index.html").write_text("<p>shop</p>", encoding="utf-8")

    result = dp.deploy(19, worktree, ["shop/index.html"])

    assert result["status"] == "deployed"
    assert result["previewUrl"].endswith("/issue-19/shop/")

    # リモートにpushされていること
    beta_repo = dp.ensure_beta_repo()
    log = gitops.git("log", "--oneline", "-1", cwd=beta_repo).stdout
    assert "Issue #19" in log


def test_cleanup_removes_preview_directory(full_repo_env):
    worktree = _make_worktree(full_repo_env, 20)
    (worktree / "index.html").write_text("<p>x</p>", encoding="utf-8")
    dp.deploy(20, worktree, ["index.html"])

    result = dp.cleanup(20)
    assert result["status"] == "cleaned"

    beta_repo = dp.ensure_beta_repo()
    assert not (beta_repo / dp.PREVIEW_ROOT / "issue-20").exists()


def test_cleanup_is_noop_when_nothing_deployed(full_repo_env):
    result = dp.cleanup(999)
    assert result["status"] == "nothing_to_clean"
