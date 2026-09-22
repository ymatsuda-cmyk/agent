from __future__ import annotations

from agent_core import gitops


def test_slugify_keeps_japanese_characters():
    assert gitops.slugify("顧客一覧ページを追加") == "顧客一覧ページを追加"


def test_slugify_handles_mixed_ascii_and_symbols():
    slug = gitops.slugify("【緊急】ログイン画面の bug 修正!!")
    assert slug == "緊急-ログイン画面の-bug-修正"


def test_slugify_falls_back_to_task_when_empty():
    assert gitops.slugify("!!!") == "task"


def test_slugify_respects_max_length():
    slug = gitops.slugify("a" * 100, max_length=10)
    assert len(slug) <= 10


def test_branch_name_format():
    name = gitops.branch_name(12, "Add customer API")
    assert name == "feature/issue-12-add-customer-api"


def test_prepare_worktree_creates_new_branch_from_main(git_repo):
    path, branch = gitops.prepare_worktree(1, "顧客一覧を追加", "main")

    assert path.exists()
    assert branch == "feature/issue-1-顧客一覧を追加"
    assert (path / "index.html").exists()  # mainの内容を引き継いでいる

    current = gitops.git("branch", "--show-current", cwd=path).stdout.strip()
    assert current == branch


def test_prepare_worktree_reuses_existing_on_second_call(git_repo):
    path1, branch1 = gitops.prepare_worktree(2, "再利用テスト", "main")
    (path1 / "new-file.txt").write_text("hello", encoding="utf-8")

    path2, branch2 = gitops.prepare_worktree(2, "再利用テスト", "main")

    assert path1 == path2
    assert branch1 == branch2
    # 前回作ったファイルが消えていない（作り直されていない）こと
    assert (path2 / "new-file.txt").exists()


def test_two_issues_get_independent_worktrees(git_repo):
    path_a, branch_a = gitops.prepare_worktree(10, "A", "main")
    path_b, branch_b = gitops.prepare_worktree(11, "B", "main")

    assert path_a != path_b
    assert branch_a != branch_b

    result = gitops.git("worktree", "list", "--porcelain")
    assert str(path_a) in result.stdout
    assert str(path_b) in result.stdout


def test_changed_files_lists_new_nested_files(git_repo):
    path, _ = gitops.prepare_worktree(20, "変更検出テスト", "main")

    (path / "shop").mkdir()
    (path / "shop" / "index.html").write_text("<p>shop</p>", encoding="utf-8")
    (path / "shop" / "parts").mkdir()
    (path / "shop" / "parts" / "app.js").write_text("// js", encoding="utf-8")

    changed = gitops.changed_files(path)

    # ディレクトリ名だけでなく、配下のファイルまで個別に列挙されること(-uall)
    assert "shop/index.html" in changed
    assert "shop/parts/app.js" in changed
    assert not any(item == "shop/" for item in changed)


def test_commit_all_returns_false_when_nothing_to_commit(git_repo):
    path, _ = gitops.prepare_worktree(21, "コミット無しテスト", "main")
    assert gitops.commit_all(path, "empty commit") is False


def test_commit_all_and_has_commits_ahead(git_repo):
    path, branch = gitops.prepare_worktree(22, "コミットテスト", "main")
    (path / "new.html").write_text("<p>new</p>", encoding="utf-8")

    assert gitops.commit_all(path, "add new.html") is True
    assert gitops.has_commits_ahead(path, "main", branch) is True


def test_committed_files_lists_diff_against_base(git_repo):
    path, branch = gitops.prepare_worktree(23, "差分テスト", "main")
    (path / "new.html").write_text("<p>new</p>", encoding="utf-8")
    gitops.commit_all(path, "add new.html")

    files = gitops.committed_files(path, "main", branch)
    assert files == ["new.html"]


def test_push_branch_makes_branch_visible_on_remote(git_repo, agent_env):
    path, branch = gitops.prepare_worktree(24, "pushテスト", "main")
    (path / "new.html").write_text("<p>new</p>", encoding="utf-8")
    gitops.commit_all(path, "add new.html")

    gitops.push_branch(path, branch)

    result = gitops.git("ls-remote", "--heads", "origin", branch, cwd=agent_env.target_repo)
    assert branch in result.stdout


def test_discard_changes_reverts_uncommitted_edits(git_repo):
    path, _ = gitops.prepare_worktree(25, "破棄テスト", "main")
    (path / "index.html").write_text("<p>changed</p>", encoding="utf-8")
    (path / "untracked.html").write_text("<p>new</p>", encoding="utf-8")

    gitops.discard_changes(path)

    assert (path / "index.html").read_text(encoding="utf-8") == "<h1>hi</h1>"
    assert not (path / "untracked.html").exists()


def test_remove_worktree_cleans_up(git_repo):
    path, _ = gitops.prepare_worktree(26, "削除テスト", "main")
    gitops.remove_worktree(26)

    assert not path.exists()
    result = gitops.git("worktree", "list", "--porcelain")
    assert str(path) not in result.stdout
