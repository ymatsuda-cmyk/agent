"""
VS Code（Cline）へプロンプトを投入する。

並走時の要点:
- Issueごとに別ウィンドウ（別worktree）でVS Codeを開く
- 貼り付け前に「そのIssueのウィンドウ」を必ず前面へ出す
- 前面化と貼り付けの間は GUI ロックで排他する（locks.gui_lock）

ウィンドウの特定はタイトルに含まれるフォルダ名（issue-<N>）で行う。
pywinauto が使える場合はそれを、無い場合は Win32 API を直接使う。
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import time

from .config import CONFIG

VSCODE_BOOT_WAIT_SECONDS = 25
WINDOW_FOCUS_WAIT_SECONDS = 2
CLINE_FOCUS_WAIT_SECONDS = 2
PASTE_WAIT_SECONDS = 3
CLIPBOARD_COPY_RETRIES = 3
CLIPBOARD_COPY_RETRY_INTERVAL_SECONDS = 0.5


# ============================================================
# VS Code 起動
# ============================================================

def launch_vscode(
    worktree: pathlib.Path, issue_number: int | None = None, logger=None
) -> bool:
    """
    worktreeを新しいVS Codeウィンドウで開く。

    `code` CLIには「最大化して開く」オプションが無いため、
    起動直後は前回のウィンドウサイズを引き継いだ小さい状態で
    開くことがある。issue_number を渡すと、起動待ちの後に
    そのウィンドウを見つけて最大化する（自動投入・手動投入どちらでも
    見やすくするため）。見つからなくても致命的ではないので警告のみ。
    """
    executable = shutil.which("code") or shutil.which("code.cmd")

    if not executable:
        if logger:
            logger.warn("'code' コマンドが見つかりません。VS Code起動をスキップします。")
        return False

    subprocess.Popen(
        # worktreeは毎回新規フォルダなので、信頼確認ダイアログが
        # Clineの入力欄を塞いで自動投入が失敗するのを防ぐ。
        [executable, "--new-window", "--disable-workspace-trust", str(worktree)],
        shell=True,
    )

    if logger:
        logger.info(f"VS Codeを起動しました: {worktree}")
        logger.info(f"起動完了まで {VSCODE_BOOT_WAIT_SECONDS} 秒待機します。")

    time.sleep(VSCODE_BOOT_WAIT_SECONDS)

    if issue_number is not None:
        maximize_window(issue_number, logger=logger)

    return True


def maximize_window(issue_number: int, logger=None) -> bool:
    """タイトルに issue-<N> を含むVS Codeウィンドウを最大化する。"""
    pattern = _window_title_pattern(issue_number)

    if _maximize_with_pywinauto(pattern, logger):
        return True
    if _maximize_with_win32(pattern, logger):
        return True

    if logger:
        logger.warn(
            f"Issue #{issue_number} のVS Codeウィンドウを最大化できませんでした"
            "（見つからなかったか、非対応環境です）。"
        )
    return False


# ============================================================
# ウィンドウ前面化
# ============================================================

def focus_window(issue_number: int, logger=None) -> bool:
    """タイトルに issue-<N> を含むVS Codeウィンドウを前面化する。"""
    pattern = _window_title_pattern(issue_number)

    if _focus_with_pywinauto(pattern, logger):
        return True
    if _focus_with_win32(pattern, logger):
        return True

    if logger:
        logger.warn(
            f"Issue #{issue_number} のVS Codeウィンドウを特定できませんでした。"
        )
    return False


def _window_title_pattern(issue_number: int) -> re.Pattern[str]:
    """
    ウィンドウタイトル照合用の正規表現を作る。

    単純な部分一致（"issue-1" in title）だと、
    Issue #1 のワーカーが Issue #12 や #123 のウィンドウを
    誤って掴んでしまう（数字列が前方一致してしまうため）。
    数字の前後に他の数字が続かないことを要求し、
    "issue-1" が "issue-12" にマッチしないようにする。
    """
    return re.compile(rf"(?<!\d)issue-{issue_number}(?!\d)")


def _focus_with_pywinauto(pattern: re.Pattern[str], logger=None) -> bool:
    try:
        from pywinauto import Desktop
    except ImportError:
        return False

    try:
        for window in Desktop(backend="uia").windows():
            title = window.window_text() or ""
            if pattern.search(title) and "Visual Studio Code" in title:
                window.set_focus()
                time.sleep(WINDOW_FOCUS_WAIT_SECONDS)
                if logger:
                    logger.info(f"ウィンドウを前面化しました: {title}")
                return True
    except Exception as error:  # pywinautoは多様な例外を投げる
        if logger:
            logger.warn(f"pywinautoでの前面化に失敗しました: {error}")

    return False


def _focus_with_win32(pattern: re.Pattern[str], logger=None) -> bool:
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return False

    user32 = ctypes.windll.user32
    found: list[int] = []

    EnumWindowsProc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value
        if pattern.search(title) and "Visual Studio Code" in title:
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)

    if not found:
        return False

    hwnd = found[0]
    # SW_RESTORE(9)は最大化状態を解除してしまうため、最大化を維持するために
    # SW_MAXIMIZE(3)を使う（最小化からの復帰も兼ねる）。
    SW_MAXIMIZE = 3
    user32.ShowWindow(hwnd, SW_MAXIMIZE)
    user32.SetForegroundWindow(hwnd)
    time.sleep(WINDOW_FOCUS_WAIT_SECONDS)

    if logger:
        logger.info("ウィンドウを前面化しました（Win32 API）。")
    return True


def _maximize_with_pywinauto(pattern: re.Pattern[str], logger=None) -> bool:
    try:
        from pywinauto import Desktop
    except ImportError:
        return False

    try:
        for window in Desktop(backend="uia").windows():
            title = window.window_text() or ""
            if pattern.search(title) and "Visual Studio Code" in title:
                window.maximize()
                time.sleep(WINDOW_FOCUS_WAIT_SECONDS)
                if logger:
                    logger.info(f"ウィンドウを最大化しました: {title}")
                return True
    except Exception as error:  # pywinautoは多様な例外を投げる
        if logger:
            logger.warn(f"pywinautoでの最大化に失敗しました: {error}")

    return False


def _maximize_with_win32(pattern: re.Pattern[str], logger=None) -> bool:
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return False

    user32 = ctypes.windll.user32
    found: list[int] = []

    EnumWindowsProc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value
        if pattern.search(title) and "Visual Studio Code" in title:
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)

    if not found:
        return False

    hwnd = found[0]
    SW_MAXIMIZE = 3
    user32.ShowWindow(hwnd, SW_MAXIMIZE)
    time.sleep(WINDOW_FOCUS_WAIT_SECONDS)

    if logger:
        logger.info("ウィンドウを最大化しました（Win32 API）。")
    return True


# ============================================================
# プロンプト投入
# ============================================================

def _set_cursor_pos(x: int, y: int) -> None:
    """
    pyautoguiを経由せずWin32 APIで直接カーソル位置を設定する。

    pyautoguiのフェイルセーフは「呼び出し時点のマウス位置」が
    画面隅にあると誤発動するため、pyautogui呼び出しの前に
    ここで目標座標へ動かしておくことで回避する。
    """
    try:
        import ctypes

        ctypes.windll.user32.SetCursorPos(int(x), int(y))
    except Exception:
        pass


def copy_to_clipboard(text: str, logger=None) -> bool:
    """他プロセスが一時的にOpenClipboardを握っていることがあるため、数回リトライする。"""
    import pyperclip

    last_error: Exception | None = None
    for attempt in range(1, CLIPBOARD_COPY_RETRIES + 1):
        try:
            pyperclip.copy(text)
            if logger:
                logger.info("プロンプトをクリップボードへコピーしました。")
            return True
        except Exception as error:  # noqa: BLE001 - pyperclipは多様な例外を投げる
            last_error = error
            if attempt < CLIPBOARD_COPY_RETRIES:
                time.sleep(CLIPBOARD_COPY_RETRY_INTERVAL_SECONDS)

    if logger:
        logger.warn(f"クリップボードへのコピーに失敗しました: {last_error}")
    return False


def inject_prompt(issue_number: int, prompt: str, logger=None) -> bool:
    """
    Clineの入力欄へプロンプトを貼り付けて送信する。

    呼び出し側で locks.gui_lock を取得していることを前提とする。
    """
    if not copy_to_clipboard(prompt, logger):
        return False

    if not focus_window(issue_number, logger):
        return False

    try:
        import pyautogui
    except ImportError:
        if logger:
            logger.warn("pyautoguiが未インストールのため自動投入できません。")
        return False

    try:
        pyautogui.FAILSAFE = True

        # チャット/Claude Codeなどとタブを共有している場合、起動直後は
        # 別のタブがアクティブなことがある。Clineタブの座標が設定されて
        # いれば、入力欄をクリックする前にそのタブへ切り替える。
        if CONFIG.cline_tab_x and CONFIG.cline_tab_y:
            _set_cursor_pos(CONFIG.cline_tab_x, CONFIG.cline_tab_y)
            pyautogui.click(CONFIG.cline_tab_x, CONFIG.cline_tab_y)
            time.sleep(CLINE_FOCUS_WAIT_SECONDS)

        # マウスが画面隣に放置されているとpyautoguiのフェイルセーフが
        # 誤発動するため、Win32 APIで先にカーソルを目標坐標へ移す。
        _set_cursor_pos(CONFIG.cline_input_x, CONFIG.cline_input_y)
        pyautogui.click(CONFIG.cline_input_x, CONFIG.cline_input_y)
        time.sleep(CLINE_FOCUS_WAIT_SECONDS)

        pyautogui.hotkey("ctrl", "a")
        pyautogui.press("delete")
        time.sleep(0.3)

        pyautogui.hotkey("ctrl", "v")
        time.sleep(PASTE_WAIT_SECONDS)

        pyautogui.press("enter")

        if logger:
            logger.info("Clineへプロンプトを投入しました。")
        return True
    except Exception as error:
        if logger:
            logger.warn(f"GUI投入に失敗しました: {error}")
        return False


def show_manual_instruction(issue_number: int, worktree: pathlib.Path, logger=None) -> None:
    """自動投入できない場合の手動手順を表示する。"""
    lines = [
        "-" * 60,
        f"【手動投入】Issue #{issue_number}",
        f"1. VS Codeで次のフォルダを開く: {worktree}",
        "2. Clineのチャット入力欄をクリックする",
        "3. Ctrl+V で貼り付け、Enterで送信する",
        "   （プロンプトはクリップボードにコピー済みです）",
        "-" * 60,
    ]
    for line in lines:
        if logger:
            logger.info(line)
        else:
            print(line)


def test_click(logger=None) -> bool:
    """Cline入力欄の座標が正しいか確認する。"""
    try:
        import pyautogui
    except ImportError:
        if logger:
            logger.error("pyautoguiが未インストールです。")
        return False

    if CONFIG.cline_tab_x and CONFIG.cline_tab_y:
        if logger:
            logger.info(
                f"5秒後に Clineタブ X={CONFIG.cline_tab_x}, Y={CONFIG.cline_tab_y} "
                "をクリックします。"
            )
        time.sleep(5)
        pyautogui.click(CONFIG.cline_tab_x, CONFIG.cline_tab_y)
        time.sleep(1)
        if logger:
            logger.info("Clineタブがアクティブになっていれば座標は正しいです。")

    if logger:
        logger.info(
            f"5秒後に X={CONFIG.cline_input_x}, Y={CONFIG.cline_input_y} をクリックします。"
        )
    time.sleep(5)
    pyautogui.click(CONFIG.cline_input_x, CONFIG.cline_input_y)
    time.sleep(1)
    pyautogui.typewrite("cline input test")
    if logger:
        logger.info("入力欄にテキストが入っていれば座標は正しいです。")
    return True
