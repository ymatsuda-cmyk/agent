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
