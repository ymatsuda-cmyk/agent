"""
AGENT_ROOT / TARGET_REPO_PATH が未設定のまま実行され、
pathlib.Path("") が暗黙的にカレントディレクトリを指すことで
「気づかずカレントディレクトリにフォルダを作ってしまう」事故が
実際に起きた。この回帰テストは、その安全網が機能し続けることを確認する。
"""

from __future__ import annotations

from agent_core.config import CONFIG


def test_agent_root_was_set_flag_true_when_provided(agent_env):
    """agent_envフィクスチャは常にAGENT_ROOTを明示的に設定する。"""
    assert CONFIG._agent_root_was_set is True
    assert CONFIG._target_repo_was_set is True


def test_ensure_directories_raises_when_agent_root_unset(monkeypatch, tmp_path):
    """
    AGENT_ROOTが未設定のまま ensure_directories() を呼ぶと、
    カレントディレクトリへフォルダを作らず、明確なエラーで止まること。
    """
    monkeypatch.delenv("AGENT_ROOT", raising=False)
    monkeypatch.setenv("TARGET_REPO_PATH", str(tmp_path / "tools"))
    monkeypatch.setenv("GITHUB_TOKEN", "dummy")
    monkeypatch.setenv("GITHUB_OWNER", "test-owner")
    monkeypatch.setenv("GITHUB_REPO", "tools")

    CONFIG.__init__()

    assert CONFIG._agent_root_was_set is False

    try:
        raised = False
        try:
            CONFIG.ensure_directories()
        except RuntimeError as error:
            raised = True
            assert "AGENT_ROOT" in str(error)
        assert raised, "AGENT_ROOT未設定でも ensure_directories() が例外を出さなかった"
    finally:
        # 次のテストに影響しないよう、正常な状態へ戻しておく
        monkeypatch.setenv("AGENT_ROOT", str(tmp_path / "agent-root"))
        CONFIG.__init__()


def test_ensure_directories_does_not_touch_cwd_when_agent_root_unset(
    monkeypatch, tmp_path
):
    """
    ensure_directories() が例外を出す前に、カレントディレクトリへ
    フォルダを作ってしまっていないこと（例外を出すだけで実害がないこと）を確認する。
    """
    import os

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENT_ROOT", raising=False)
    monkeypatch.setenv("TARGET_REPO_PATH", str(tmp_path / "tools"))
    monkeypatch.setenv("GITHUB_TOKEN", "dummy")
    monkeypatch.setenv("GITHUB_OWNER", "test-owner")
    monkeypatch.setenv("GITHUB_REPO", "tools")

    CONFIG.__init__()

    before = set(os.listdir(tmp_path))
    try:
        CONFIG.ensure_directories()
    except RuntimeError:
        pass

    after = set(os.listdir(tmp_path))
    # "request" "question" 等のフォルダが tmp_path 直下に作られていないこと
    assert after == before, f"カレントディレクトリに何か作られた: {after - before}"

    monkeypatch.setenv("AGENT_ROOT", str(tmp_path / "agent-root"))
    CONFIG.__init__()
