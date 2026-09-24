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


def test_per_folder_override_env_vars_do_not_bypass_agent_root(monkeypatch, tmp_path):
    """
    実際に起きた事故の回帰テスト。

    AGENT_STATE_DIR のような、フォルダ別の個別上書き環境変数が
    (テストのフィクスチャが関知しない場所で)実在すると、AGENT_ROOTを
    どれだけ差し替えても state_dir だけはその上書き値を指し続けてしまい、
    本番相当のフォルダをテストが直接汚染する。conftest.py の agent_env
    フィクスチャは、これらの上書きキーを毎回明示的に削除することで防いでいる。
    ここでは agent_env を経由せず、素の Config で同じ危険な状況を再現し、
    「AGENT_STATE_DIR を明示的に消せば安全になる」ことを確認する。
    """
    fake_production = tmp_path / "fake-production-onedrive" / "state"
    fake_production.mkdir(parents=True)
    (fake_production / "issue-74.json").write_text("{}", encoding="utf-8")

    safe_root = tmp_path / "agent-root-safe"

    monkeypatch.setenv("GITHUB_TOKEN", "dummy")
    monkeypatch.setenv("GITHUB_OWNER", "x")
    monkeypatch.setenv("GITHUB_REPO", "tools")
    monkeypatch.setenv("TARGET_REPO_PATH", str(tmp_path / "tools"))
    monkeypatch.setenv("AGENT_ROOT", str(safe_root))
    # 危険な状況を意図的に再現: AGENT_STATE_DIRが本番相当を指したまま
    monkeypatch.setenv("AGENT_STATE_DIR", str(fake_production))

    CONFIG.__init__()
    assert str(CONFIG.state_dir) == str(fake_production), (
        "この再現手順自体が成立していない（先に危険な状態を作れていない）"
    )

    # ここが実際の修正: 上書きキーを明示的に消せば、AGENT_ROOT配下に戻ること
    monkeypatch.delenv("AGENT_STATE_DIR", raising=False)
    CONFIG.__init__()

    assert CONFIG.state_dir == safe_root / "state"
    assert not list(fake_production.glob("issue-*.json")) or (
        fake_production / "issue-74.json"
    ).read_text(encoding="utf-8") == "{}", "本番相当フォルダの中身が変化してはいけない"


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


# ============================================================
# Cline タブ座標（Claude Code等とタブを共有している場合の対策）
# ============================================================

def test_cline_tab_coordinates_use_documented_defaults(agent_env):
    from agent_core.config import CONFIG

    assert CONFIG.cline_tab_x == 1240
    assert CONFIG.cline_tab_y == 50


def test_cline_tab_coordinates_can_be_overridden(monkeypatch, agent_env):
    from agent_core.config import CONFIG

    monkeypatch.setenv("AGENT_CLINE_TAB_X", "999")
    monkeypatch.setenv("AGENT_CLINE_TAB_Y", "111")
    CONFIG.__init__()

    assert CONFIG.cline_tab_x == 999
    assert CONFIG.cline_tab_y == 111
