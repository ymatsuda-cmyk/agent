"""
標準出力とファイルの両方へ出力するシンプルなロガー。

並走時にどのIssueのログか判別できるよう、プレフィックスを付ける。
"""

from __future__ import annotations

import codecs
import pathlib
import sys
import threading
from datetime import datetime

from .config import CONFIG

_write_lock = threading.Lock()


class Logger:
    def __init__(self, name: str, log_file: pathlib.Path | None = None) -> None:
        self.name = name
        self.log_file = log_file

        if self.log_file is not None:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)

    def _emit(self, level: str, message: str) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}][{self.name}]{level} {message}"

        with _write_lock:
            print(line, flush=True)
            if self.log_file is not None:
                try:
                    # Windows PowerShellの Get-Content は、BOMが無いUTF-8ファイルを
                    # システムのロケール（日本語Windowsなら Shift-JIS）で読んでしまい、
                    # 日本語ログが文字化けする（.ps1 と同じ系統の問題）。
                    # ファイル作成時にだけ先頭へBOMを書き、それ以降は素のUTF-8で
                    # 追記する（毎回 utf-8-sig で開くと、追記のたびにBOMが
                    # ファイルの途中に挿入されて壊れるため、最初の1回だけにする）。
                    is_new = not self.log_file.exists()
                    if is_new:
                        with self.log_file.open("wb") as raw:
                            raw.write(codecs.BOM_UTF8)

                    with self.log_file.open("a", encoding="utf-8") as stream:
                        stream.write(line + "\n")
                except OSError:
                    pass

    def info(self, message: str) -> None:
        self._emit("", message)

    def warn(self, message: str) -> None:
        self._emit("[WARN]", message)

    def error(self, message: str) -> None:
        self._emit("[ERROR]", message)

    def rule(self, title: str = "") -> None:
        self.info("=" * 60)
        if title:
            self.info(title)
            self.info("=" * 60)

    def fail(self, message: str, code: int = 1) -> None:
        self.error(message)
        raise SystemExit(code)


def get_logger(name: str, issue_number: int | None = None) -> Logger:
    """エージェント名（と任意のIssue番号）からロガーを生成する。"""
    CONFIG.logs_dir.mkdir(parents=True, exist_ok=True)

    if issue_number is None:
        log_file = CONFIG.logs_dir / f"{name}.log"
        display = name
    else:
        log_file = CONFIG.logs_dir / f"issue-{issue_number}.log"
        display = f"{name}#{issue_number}"

    return Logger(display, log_file)


def print_banner(title: str, rows: dict[str, object]) -> None:
    """起動時サマリーを表示する。"""
    print("=" * 60)
    print(title)
    print("=" * 60)
    width = max((len(key) for key in rows), default=0)
    for key, value in rows.items():
        print(f"{key.ljust(width)} : {value}")
    print("=" * 60)
    sys.stdout.flush()
