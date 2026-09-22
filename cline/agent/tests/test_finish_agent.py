from __future__ import annotations

import finish_agent as fa
from agent_core import gitops, statefile


def _finisher(issue_number: int, decision: str = "reject") -> fa.Finisher:
    return fa.Finisher(issue_number, decision)


# ============================================================
# check_encoding
# ============================================================


def test_check_encoding_detects_mojibake_markers(agent_env):
    statefile.create(30, {"changedFiles": ["broken.html"]})
    finisher = _finisher(30)
    finisher.worktree.mkdir(parents=True, exist_ok=True)
    (finisher.worktree / "broken.html").write_text(
        "縺縺縺縺", encoding="utf-8"
    )

    problems = finisher.check_encoding()
    assert problems and "broken.html" in problems[0]


def test_check_encoding_ignores_clean_files(agent_env):
    statefile.create(31, {"changedFiles": ["ok.html"]})
    finisher = _finisher(31)
    finisher.worktree.mkdir(parents=True, exist_ok=True)
    (finisher.worktree / "ok.html").write_text("<p>正常なテキストです</p>", encoding="utf-8")

    assert finisher.check_encoding() == []


# ============================================================
# check_static（弱点8の回帰テスト）
# ============================================================


def test_check_static_detects_broken_html_and_link(agent_env):
    statefile.create(32, {"changedFiles": ["broken.html"]})
    finisher = _finisher(32)
    finisher.worktree.mkdir(parents=True, exist_ok=True)
    (finisher.worktree / "broken.html").write_text(
        '<link rel="stylesheet" href="missing.css"><div><p>test</div>', encoding="utf-8"
    )

    problems = finisher.check_static()
    assert "broken.html" in problems
    assert any("リンク切れ" in issue for issue in problems["broken.html"])


def test_check_static_passes_for_valid_html(agent_env):
    statefile.create(33, {"changedFiles": ["ok.html"]})
    finisher = _finisher(33)
    finisher.worktree.mkdir(parents=True, exist_ok=True)
    (finisher.worktree / "ok.html").write_text("<div><p>ok</p></div>", encoding="utf-8")

    assert finisher.check_static() == {}


def test_check_static_returns_empty_when_worktree_missing(agent_env):
    statefile.create(34, {"changedFiles": ["ok.html"]})
    finisher = _finisher(34)
    # worktreeを作らない（Issueがまだ実装フェーズに到達していない状況を模す）
    assert finisher.check_static() == {}


# ============================================================
# reject（弱点9の回帰テスト: Issueをクローズせずopenのまま残す）
# ============================================================


def test_reject_keeps_issue_open_and_adds_needs_rework_label(git_repo, monkeypatch):
    path, branch = gitops.prepare_worktree(35, "却下テスト", "main")
    statefile.create(
        35,
        {
            "status": statefile.Status.WAITING_APPROVAL,
            "branch": branch,
            "issueTitle": "却下テスト",
        },
    )

    calls: dict[str, list] = {"label": [], "comment": [], "close_issue": []}

    monkeypatch.setattr(fa.ghcli, "find_open_pr", lambda branch: None)
    monkeypatch.setattr(
        fa.ghcli, "ensure_label", lambda name, **kw: calls["label"].append(name)
    )
    monkeypatch.setattr(
        fa.ghcli, "add_issue_label", lambda issue, label: calls["label"].append((issue, label))
    )
    monkeypatch.setattr(
        fa.ghcli, "comment_issue", lambda issue, body: calls["comment"].append((issue, body))
    )
    monkeypatch.setattr(
        fa.ghcli, "close_issue", lambda *a, **k: calls["close_issue"].append((a, k))
    )
    # tools-betaクリーンアップとfetchは今回のテストの対象外なので無害化する
    monkeypatch.setattr(fa.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(fa.gitops, "git", lambda *a, **k: None)

    finisher = _finisher(35, "reject")
    finisher.branch = branch
    result = finisher.reject()

    assert result == 0
    # Issueをクローズする close_issue は一度も呼ばれていないこと
    assert calls["close_issue"] == []
    # needs-rework ラベルが付与されていること（ensure_label呼び出しとadd_issue_label呼び出しの両方）
    assert fa.REWORK_LABEL in calls["label"]
    assert (35, fa.REWORK_LABEL) in calls["label"]
    # コメントで再着手の方法(retry)を案内していること
    assert calls["comment"] and "retry" in calls["comment"][0][1]

    # reject()の最後にstateはstate/done/へ退避される
    from agent_core.config import CONFIG
    from agent_core.jsonio import read_json

    archived = read_json(CONFIG.state_dir / "done" / "issue-35.json")
    assert archived["status"] == statefile.Status.REJECTED


def test_reject_moves_state_to_archive(git_repo, monkeypatch):
    path, branch = gitops.prepare_worktree(36, "却下アーカイブテスト", "main")
    statefile.create(36, {"status": statefile.Status.WAITING_APPROVAL, "branch": branch})

    monkeypatch.setattr(fa.ghcli, "find_open_pr", lambda branch: None)
    monkeypatch.setattr(fa.ghcli, "ensure_label", lambda *a, **k: None)
    monkeypatch.setattr(fa.ghcli, "add_issue_label", lambda *a, **k: None)
    monkeypatch.setattr(fa.ghcli, "comment_issue", lambda *a, **k: None)
    monkeypatch.setattr(fa.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(fa.gitops, "git", lambda *a, **k: None)

    finisher = _finisher(36, "reject")
    finisher.branch = branch
    finisher.reject()

    from agent_core.config import CONFIG

    assert not CONFIG.state_path(36).exists()
    assert (CONFIG.state_dir / "done" / "issue-36.json").exists()
