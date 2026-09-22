"""
共有フィクスチャ。

CONFIG は agent_core.config で作られるモジュールレベルのシングルトンで、
各モジュールは `from .config import CONFIG` で同じオブジェクトの参照を
受け取っている。そのため、テストごとに環境変数を設定したうえで
CONFIG.__init__() を呼び直せば、同じオブジェクトの属性がその場で
書き換わり、全モジュールに一貫して反映される（インポートし直す必要はない）。

これにより、実際のOneDriveやGitHub、gh CLIに触れずに、
tmp_path 配下だけで完結したテストが書ける。
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

AGENT_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from agent_core.config import CONFIG  # noqa: E402


def run_git(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )


@pytest.fixture()
def agent_env(tmp_path, monkeypatch):
    """AGENT_ROOTや各リポジトリパスをtmp_path配下に隔離したCONFIGを用意する。"""
    agent_root = tmp_path / "agent-root"
    repo_dir = tmp_path / "repo"

    monkeypatch.setenv("GITHUB_TOKEN", "dummy-token-for-tests")
    monkeypatch.setenv("GITHUB_OWNER", "test-owner")
    monkeypatch.setenv("GITHUB_REPO", "tools")
    monkeypatch.setenv("GITHUB_BETA_REPO", "tools-beta")
    monkeypatch.setenv("AGENT_ROOT", str(agent_root))
    monkeypatch.setenv("TARGET_REPO_PATH", str(repo_dir / "tools"))
    monkeypatch.setenv("BETA_REPO_PATH", str(repo_dir / "tools-beta"))
    monkeypatch.setenv("AGENT_WORKTREE_ROOT", str(repo_dir / "worktrees"))
    monkeypatch.setenv("BASE_BRANCH", "main")

    CONFIG.__init__()  # 同じインスタンスの属性を、新しい環境変数で再構築する
    CONFIG.ensure_directories()

    return CONFIG


def _init_local_repo(repo_dir: pathlib.Path, seed_files: dict[str, str]) -> pathlib.Path:
    """bareのorigin + そこからcloneした作業リポジトリを作り、初回コミットをpushする。"""
    origin = repo_dir.parent / f"{repo_dir.name}-origin.git"
    repo_dir.parent.mkdir(parents=True, exist_ok=True)

    run_git(["init", "--quiet", "--bare", str(origin)], cwd=repo_dir.parent)
    run_git(["clone", "--quiet", str(origin), str(repo_dir)], cwd=repo_dir.parent)
    run_git(["config", "user.email", "test@example.com"], cwd=repo_dir)
    run_git(["config", "user.name", "Test User"], cwd=repo_dir)

    for relative, content in seed_files.items():
        path = repo_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    run_git(["add", "-A"], cwd=repo_dir)
    run_git(["commit", "--quiet", "-m", "init"], cwd=repo_dir)
    run_git(["branch", "-M", "main"], cwd=repo_dir)
    run_git(["push", "--quiet", "-u", "origin", "main"], cwd=repo_dir)
    return origin


@pytest.fixture()
def git_repo(agent_env):
    """origin(bare) + 本体クローンを用意する。worktree操作のテストに使う。"""
    _init_local_repo(agent_env.target_repo, {"index.html": "<h1>hi</h1>"})
    return agent_env


@pytest.fixture()
def beta_git_repo(agent_env):
    """origin(bare) + tools-betaクローンを用意する。deploy_previewのテストに使う。"""
    _init_local_repo(agent_env.beta_repo, {"README.md": "beta"})
    return agent_env


@pytest.fixture()
def full_repo_env(git_repo, beta_git_repo):
    """
    tools（worktree作成元）とtools-betaの両方を用意する。

    deploy_preview は worktree（tools側）の内容を tools-beta へ
    コピーするため、両方のリポジトリが揃っていないと成立しない。
    """
    return beta_git_repo
