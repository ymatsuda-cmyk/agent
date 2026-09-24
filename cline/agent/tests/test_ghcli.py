from __future__ import annotations

from agent_core import ghcli


def test_update_pull_request_body_calls_gh_pr_edit(monkeypatch, agent_env):
    """
    reworkで既存PRを再利用する際、本文が初回実装のまま古くならないように
    最新の実装内容へ更新する。gh pr edit --body を正しく呼ぶことを確認する。
    """
    captured = {}

    def fake_run(command, cwd=None, check=True, timeout=600):
        captured["command"] = command
        captured["cwd"] = cwd

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr(ghcli, "run", fake_run)

    ghcli.update_pull_request_body(69, "新しい本文")

    assert captured["command"][:3] == ["gh", "pr", "edit"]
    assert "69" in captured["command"]
    assert "--body" in captured["command"]
    body_index = captured["command"].index("--body")
    assert captured["command"][body_index + 1] == "新しい本文"


def test_update_pull_request_body_does_not_raise_on_failure(monkeypatch, agent_env):
    """gh コマンドが失敗しても(check=False相当)、呼び出し元へ例外を伝播させない。"""

    def failing_run(command, cwd=None, check=True, timeout=600):
        class Result:
            returncode = 1
            stdout = ""
            stderr = "some error"

        return Result()

    monkeypatch.setattr(ghcli, "run", failing_run)

    # 例外を出さずに戻ってくること
    ghcli.update_pull_request_body(69, "本文")
