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
import shutil
import subprocess
import sys
import uuid

import pytest

AGENT_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from agent_core.config import CONFIG, STATE_FOLDERS  # noqa: E402

# STATE_FOLDERSの各フォルダは、CONFIG内で個別に環境変数上書きできる
# （例: AGENT_STATE_DIR）。この上書きは AGENT_ROOT を無視して優先されるため、
# もし実行環境（Windowsのユーザー環境変数など）にこれらが実際に設定されて
# いると、AGENT_ROOT をどれだけテスト用に差し替えても、該当フォルダだけは
# 本番の場所を指し続けてしまう。実際にこれが原因で、pytest実行時に
# 本番のOneDrive上の state/ を直接汚染する事故が起きたため、
# テストでは常にこれらを明示的に未設定へ倒す。
_PER_FOLDER_OVERRIDE_KEYS = tuple(STATE_FOLDERS.values())


def run_git(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )


@pytest.fixture()
def agent_env(tmp_path, monkeypatch):
    """
    AGENT_ROOTや各リポジトリパスをtmp_path配下に隔離したCONFIGを用意する。

    Linux環境では tmp_path のみで完全に隔離できているが、
    Windows + Python 3.14 の組み合わせで、テスト間にstateが漏れる現象が
    報告されている（原因未特定）。念のため以下の対策を入れる。

    1. uuid4 を挟んだサブフォルダにし、tmp_path の再利用/衝突の可能性を排除する
    2. ensure_directories() の前に明示的に rmtree する（何か残っていても必ず空にする）
    3. セットアップ直後に「本当に空か」を assert する。もしテスト間の漏れが
       実在するなら、ここで即座に、分かりやすいエラーとして失敗する。
       原因不明のまま後続テストが変な失敗をするより、ここで確実に検知したい。
    """
    unique = uuid.uuid4().hex[:8]
    agent_root = tmp_path / f"agent-root-{unique}"
    repo_dir = tmp_path / f"repo-{unique}"

    # AGENT_ROOTの再設定より前に、フォルダ別の個別上書きを必ず消す。
    # これを怠ると、実行環境に例えば AGENT_STATE_DIR が実在した場合、
    # AGENT_ROOTをどれだけ差し替えても state/ だけは本番の場所を
    # 指し続けてしまう（実際にこの経路で本番データが汚染された）。
    for override_key in _PER_FOLDER_OVERRIDE_KEYS:
        monkeypatch.delenv(override_key, raising=False)

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

    assert CONFIG.agent_root == agent_root, (
        f"CONFIG.agent_rootの再構築に失敗しました: "
        f"期待={agent_root} 実際={CONFIG.agent_root}"
    )

    shutil.rmtree(agent_root, ignore_errors=True)  # 念のための明示的な事前クリーン
    CONFIG.ensure_directories()

    existing_issues = list(CONFIG.state_dir.glob("issue-*.json"))
    assert not existing_issues, (
        "テスト隔離が壊れています。新規作成したはずのAGENT_ROOT "
        f"({agent_root}) の state/ に、既にIssueファイルが存在します: "
        f"{existing_issues}\n"
        "これが出る場合、CONFIGのシングルトン再構築か、pytestのtmp_path隔離が"
        "この環境で機能していません。このエラーメッセージをそのまま報告してください。"
    )

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
