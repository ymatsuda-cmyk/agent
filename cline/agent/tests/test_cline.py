from __future__ import annotations

import pytest

from agent_core import cline
from agent_core.cline import _window_title_pattern


@pytest.mark.parametrize(
    "issue_number,title,expected",
    [
        (1, "issue-1 - Visual Studio Code", True),
        (1, "issue-12 - Visual Studio Code", False),
        (1, "issue-123 - Visual Studio Code", False),
        (12, "issue-12 - Visual Studio Code", True),
        (12, "issue-123 - Visual Studio Code", False),
        (12, "issue-2 - Visual Studio Code", False),
        (123, "issue-123 - Visual Studio Code", True),
        (12, "● index.html - issue-12 - Visual Studio Code", True),
        (1, "issue-10 - Visual Studio Code", False),
    ],
)
def test_window_title_pattern_boundary_matching(issue_number, title, expected):
    pattern = _window_title_pattern(issue_number)
    assert bool(pattern.search(title)) is expected


# ============================================================
# maximize_window: 実際のWindows APIはこの環境では呼べないため、
# フォールバックの組み立て（pywinauto優先→win32→両方失敗で警告）
# だけをモックで検証する。
# ============================================================


def test_maximize_window_uses_pywinauto_when_it_succeeds(monkeypatch):
    monkeypatch.setattr(cline, "_maximize_with_pywinauto", lambda pattern, logger=None: True)
    monkeypatch.setattr(
        cline, "_maximize_with_win32",
        lambda pattern, logger=None: (_ for _ in ()).throw(AssertionError("win32は呼ばれないはず")),
    )

    assert cline.maximize_window(68) is True


def test_maximize_window_falls_back_to_win32(monkeypatch):
    monkeypatch.setattr(cline, "_maximize_with_pywinauto", lambda pattern, logger=None: False)
    monkeypatch.setattr(cline, "_maximize_with_win32", lambda pattern, logger=None: True)

    assert cline.maximize_window(68) is True


def test_maximize_window_returns_false_and_warns_when_both_fail(monkeypatch):
    warnings = []

    class FakeLogger:
        def warn(self, message):
            warnings.append(message)

    monkeypatch.setattr(cline, "_maximize_with_pywinauto", lambda pattern, logger=None: False)
    monkeypatch.setattr(cline, "_maximize_with_win32", lambda pattern, logger=None: False)

    result = cline.maximize_window(68, logger=FakeLogger())

    assert result is False
    assert any("68" in message for message in warnings)


# ============================================================
# launch_vscode: issue_number 引数の後方互換性
# ============================================================


def test_launch_vscode_returns_false_without_code_command(monkeypatch, tmp_path):
    monkeypatch.setattr(cline.shutil, "which", lambda name: None)
    # codeが無ければ即falseで返り、maximize_windowは呼ばれない
    monkeypatch.setattr(
        cline, "maximize_window",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("呼ばれないはず")),
    )

    assert cline.launch_vscode(tmp_path, issue_number=68) is False


def test_launch_vscode_skips_maximize_when_issue_number_omitted(monkeypatch, tmp_path):
    monkeypatch.setattr(cline.shutil, "which", lambda name: "/usr/bin/code")
    monkeypatch.setattr(cline.subprocess, "Popen", lambda *a, **k: None)
    monkeypatch.setattr(cline.time, "sleep", lambda s: None)

    called = []
    monkeypatch.setattr(cline, "maximize_window", lambda *a, **k: called.append(a))

    result = cline.launch_vscode(tmp_path)  # issue_number省略（既存呼び出しとの後方互換）

    assert result is True
    assert called == []  # issue_number未指定なら最大化を試みない


def test_launch_vscode_calls_maximize_with_issue_number(monkeypatch, tmp_path):
    monkeypatch.setattr(cline.shutil, "which", lambda name: "/usr/bin/code")
    monkeypatch.setattr(cline.subprocess, "Popen", lambda *a, **k: None)
    monkeypatch.setattr(cline.time, "sleep", lambda s: None)

    called = []
    monkeypatch.setattr(cline, "maximize_window", lambda *a, **k: called.append(a))

    result = cline.launch_vscode(tmp_path, issue_number=68)

    assert result is True
    assert called == [(68,)]


# ============================================================
# launch_vscode: 信頼確認ダイアログの回避（--disable-workspace-trust）
#
# worktreeは毎回新規フォルダなので、VS Codeが「このフォルダを信頼しますか」
# ダイアログを出し、Clineの入力欄を塞いで自動投入が失敗する事例が
# 実環境の検証で実際に発生した。
# ============================================================


def test_launch_vscode_passes_disable_workspace_trust_flag(monkeypatch, tmp_path):
    monkeypatch.setattr(cline.shutil, "which", lambda name: "/usr/bin/code")
    monkeypatch.setattr(cline.time, "sleep", lambda s: None)

    captured_commands = []
    monkeypatch.setattr(
        cline.subprocess, "Popen",
        lambda command, **kwargs: captured_commands.append(command),
    )

    cline.launch_vscode(tmp_path)

    assert captured_commands
    assert "--disable-workspace-trust" in captured_commands[0]


# ============================================================
# copy_to_clipboard: 他プロセスがクリップボードを握っている場合のリトライ
# ============================================================


def test_copy_to_clipboard_retries_then_succeeds(monkeypatch):
    import sys
    import types

    attempts = {"count": 0}

    def flaky_copy(text):
        attempts["count"] += 1
        if attempts["count"] < 2:
            raise RuntimeError("OpenClipboard failed (別プロセスが使用中)")

    fake_pyperclip = types.ModuleType("pyperclip")
    fake_pyperclip.copy = flaky_copy
    monkeypatch.setitem(sys.modules, "pyperclip", fake_pyperclip)
    monkeypatch.setattr(cline.time, "sleep", lambda s: None)

    result = cline.copy_to_clipboard("プロンプト本文")

    assert result is True
    assert attempts["count"] == 2  # 1回失敗し、2回目で成功


def test_copy_to_clipboard_gives_up_after_max_retries(monkeypatch):
    import sys
    import types

    def always_fails(text):
        raise RuntimeError("OpenClipboard failed")

    fake_pyperclip = types.ModuleType("pyperclip")
    fake_pyperclip.copy = always_fails
    monkeypatch.setitem(sys.modules, "pyperclip", fake_pyperclip)
    monkeypatch.setattr(cline.time, "sleep", lambda s: None)

    result = cline.copy_to_clipboard("プロンプト本文")

    assert result is False


def test_inject_prompt_aborts_when_clipboard_copy_fails(monkeypatch):
    monkeypatch.setattr(cline, "copy_to_clipboard", lambda text, logger=None: False)
    monkeypatch.setattr(
        cline, "focus_window",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("クリップボード失敗時はfocus_windowを呼ばないはず")),
    )

    result = cline.inject_prompt(68, "テストプロンプト")

    assert result is False


# ============================================================
# Clineタブ切り替え（他の拡張機能とタブを共有している場合の対策）
# ============================================================


def test_inject_prompt_clicks_cline_tab_before_input_when_configured(monkeypatch):
    from agent_core.config import CONFIG

    monkeypatch.setattr(CONFIG, "cline_tab_x", 1240)
    monkeypatch.setattr(CONFIG, "cline_tab_y", 50)
    monkeypatch.setattr(CONFIG, "cline_input_x", 1500)
    monkeypatch.setattr(CONFIG, "cline_input_y", 900)

    monkeypatch.setattr(cline, "copy_to_clipboard", lambda text, logger=None: True)
    monkeypatch.setattr(cline, "focus_window", lambda *a, **k: True)
    monkeypatch.setattr(cline.time, "sleep", lambda s: None)
    monkeypatch.setattr(cline, "_set_cursor_pos", lambda x, y: None)

    clicked_points = []

    import sys
    import types

    fake_pyautogui = types.ModuleType("pyautogui")
    fake_pyautogui.FAILSAFE = False
    fake_pyautogui.click = lambda x, y: clicked_points.append((x, y))
    fake_pyautogui.hotkey = lambda *a: None
    fake_pyautogui.press = lambda *a: None
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)

    cline.inject_prompt(68, "テストプロンプト")

    # タブ座標 → 入力欄座標の順でクリックされていること
    assert clicked_points[0] == (1240, 50)
    assert clicked_points[1] == (1500, 900)


def test_inject_prompt_skips_tab_click_when_not_configured(monkeypatch):
    from agent_core.config import CONFIG

    monkeypatch.setattr(CONFIG, "cline_tab_x", 0)
    monkeypatch.setattr(CONFIG, "cline_tab_y", 0)
    monkeypatch.setattr(CONFIG, "cline_input_x", 1500)
    monkeypatch.setattr(CONFIG, "cline_input_y", 900)

    monkeypatch.setattr(cline, "copy_to_clipboard", lambda text, logger=None: True)
    monkeypatch.setattr(cline, "focus_window", lambda *a, **k: True)
    monkeypatch.setattr(cline.time, "sleep", lambda s: None)
    monkeypatch.setattr(cline, "_set_cursor_pos", lambda x, y: None)

    clicked_points = []

    import sys
    import types

    fake_pyautogui = types.ModuleType("pyautogui")
    fake_pyautogui.FAILSAFE = False
    fake_pyautogui.click = lambda x, y: clicked_points.append((x, y))
    fake_pyautogui.hotkey = lambda *a: None
    fake_pyautogui.press = lambda *a: None
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)

    cline.inject_prompt(68, "テストプロンプト")

    # タブ未設定なら、入力欄への1回だけクリックされること
    assert clicked_points == [(1500, 900)]
