"""
ファイルロック。

用途は3種類。
- daemon lock : 各デーモンの二重起動防止
- issue lock  : 同一Issueを複数プロセスが同時に処理することの防止
- gui lock    : Cline（GUI操作）は同時に1つしか触れないため全体で排他

いずれも O_CREAT|O_EXCL による原子的作成を利用する。
プロセスが異常終了した場合に備え、PIDの生存確認による
stale lock の自動回収を行う。

複数PCで運用する場合の注意:
PIDは各PC（各ホスト）のプロセス空間でしか意味を持たない。
ロックファイルにホスト名を記録し、自分のホスト名と異なるロックは
「生死判定ができないもの」として扱い、絶対に自動回収しない。
これを怠ると、別PCが処理中のロックを「(このPC上では)そのPIDは
存在しない＝死んでいる」と誤判定して奪ってしまう事故につながる。
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import socket
import time
from datetime import datetime

from .config import CONFIG

STALE_CHECK_ENABLED = True


def _current_host() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return ""


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False

    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == STILL_ACTIVE
            return False
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def lock_info(path: pathlib.Path) -> dict:
    """
    ロックファイルの所有者情報を読む。

    新形式は1行のJSON（pid/host/at/note）。
    旧形式（ホスト名を持たない "PID\\nタイムスタンプ\\nnote" の3行）も
    読み込めるようにし、既存のロックファイルと混在しても壊れないようにする。
    読めない・壊れている場合は空dictを返す。
    """
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return {}

    if not text:
        return {}

    lines = text.splitlines()

    try:
        data = json.loads(lines[0])
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, IndexError):
        pass

    # 旧形式へのフォールバック（ホスト情報は無い＝ローカルとみなす）。
    try:
        pid = int(lines[0])
    except (ValueError, IndexError):
        pid = -1

    return {
        "pid": pid,
        "host": "",
        "at": lines[1] if len(lines) > 1 else "",
        "note": lines[2] if len(lines) > 2 else "",
    }


def _read_owner_pid(path: pathlib.Path) -> int:
    """後方互換用。新規コードは lock_info() を使うこと。"""
    try:
        return int(lock_info(path).get("pid", -1) or -1)
    except (TypeError, ValueError):
        return -1


def _reclaim_if_stale(path: pathlib.Path) -> bool:
    """
    所有プロセスが死んでいればロックを削除してTrueを返す。

    所有ホストが自分と異なる場合は、生死を判定する手段が無いため
    （他ホストのPIDは自分のプロセス空間には存在しない）、
    STALE_CHECK自体を無効化し、絶対に自動回収しない。
    その場合ロックは「使用中」として扱われ、取得側は待つか諦めるかになる。
    強制的に外す必要があるときは `agent_cli.py unlock --force` を使う。
    """
    if not STALE_CHECK_ENABLED:
        return False

    owner = lock_info(path)
    owner_host = str(owner.get("host", "")).strip()

    if owner_host and owner_host != _current_host():
        return False

    try:
        pid = int(owner.get("pid", -1) or -1)
    except (TypeError, ValueError):
        pid = -1

    if pid > 0 and _process_alive(pid):
        return False

    try:
        path.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def try_acquire(path: pathlib.Path, note: str = "") -> bool:
    """ロックを取得できたらTrue。取得できなければFalse。"""
    path.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(2):
        try:
            descriptor = os.open(
                str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY
            )
        except FileExistsError:
            if attempt == 0 and _reclaim_if_stale(path):
                continue
            return False

        payload = {
            "pid": os.getpid(),
            "host": _current_host(),
            "at": datetime.now().isoformat(),
            "note": note,
        }
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return True

    return False


def acquire_or_wait(
    path: pathlib.Path,
    timeout_seconds: int,
    poll_seconds: float = 2.0,
    note: str = "",
    on_wait=None,
) -> bool:
    """タイムアウトまでロック取得を試み続ける。"""
    started_at = time.time()
    notified = False

    while True:
        if try_acquire(path, note=note):
            return True

        if not notified and on_wait is not None:
            on_wait(lock_info(path))
            notified = True

        if time.time() - started_at > timeout_seconds:
            return False

        time.sleep(poll_seconds)


def release(path: pathlib.Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


# ============================================================
# 用途別のショートカット
# ============================================================


def daemon_lock_path(name: str) -> pathlib.Path:
    return CONFIG.locks_dir / f"{name}.lock"


def issue_lock_path(issue_number: int) -> pathlib.Path:
    return CONFIG.locks_dir / f"issue-{issue_number}.lock"


def gui_lock_path() -> pathlib.Path:
    return CONFIG.locks_dir / "cline-gui.lock"


@contextlib.contextmanager
def gui_lock(issue_number: int, timeout_seconds: int | None = None, logger=None):
    """
    Cline GUI操作の排他。

    実装フェーズそのものは並走させるが、
    「VS Codeへプロンプトを貼り付ける瞬間」だけは1件ずつに直列化する。
    """
    path = gui_lock_path()
    timeout = timeout_seconds or CONFIG.gui_lock_timeout

    def on_wait(owner: dict) -> None:
        if logger is not None:
            host = owner.get("host") or "不明"
            pid = owner.get("pid", "不明")
            logger.info(
                f"GUIロック待機中（使用中: {host} / PID={pid}）。順番が来るまで待ちます。"
            )

    acquired = acquire_or_wait(
        path,
        timeout_seconds=timeout,
        note=f"issue-{issue_number}",
        on_wait=on_wait,
    )

    if not acquired:
        raise TimeoutError("GUIロックの取得がタイムアウトしました。")

    try:
        yield
    finally:
        release(path)
