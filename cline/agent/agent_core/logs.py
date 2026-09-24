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
    """
    ログファイルのパスを固定で覚えず、書き込みのたびに現在の
    CONFIG.logs_dir を見に行く。

    以前は log_file をコンストラクタ時点で確定させていたため、
    モジュール読み込み時（= main()がCONFIG.validate()を呼ぶ前）に
    作られる LOGGER = get_logger(...) が、AGENT_ROOT未設定のまま
    「たまたまその瞬間のカレントディレクトリ」を指すパスを永久に
    覚えてしまう問題があった。テストではフィクスチャが後から
    CONFIG を正しい一時ディレクトリへ再構築するが、既に出来上がった
    Loggerはそれに追従できず、リポジトリ本体の agent/logs/ へ
    書き込み続けるという実害が実際に発生していた。
    """

    def __init__(self, name: str, log_filename: str | None = None) -> None:
        self.name = name
        self.log_filename = log_filename

    def _resolve_log_file(self) -> pathlib.Path | None:
        if self.log_filename is None:
            return None
        # AGENT_ROOTが未設定のままだと CONFIG.logs_dir はカレント
        # ディレクトリ相当になる。その場合はファイルへは書かず、
        # コンソール出力だけにする（ログ関数自体が例外で落ちるのは避ける）。
        if not CONFIG._agent_root_was_set:
            return None
        return CONFIG.logs_dir / self.log_filename

    def _emit(self, level: str, message: str) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}][{self.name}]{level} {message}"

        with _write_lock:
            print(line, flush=True)
            log_file = self._resolve_log_file()
            if log_file is not None:
                try:
                    log_file.parent.mkdir(parents=True, exist_ok=True)

                    # Windows PowerShellの Get-Content は、BOMが無いUTF-8ファイルを
                    # システムのロケール（日本語Windowsなら Shift-JIS）で読んでしまい、
                    # 日本語ログが文字化けする（.ps1 と同じ系統の問題）。
                    # ファイル作成時にだけ先頭へBOMを書き、それ以降は素のUTF-8で
                    # 追記する（毎回 utf-8-sig で開くと、追記のたびにBOMが
                    # ファイルの途中に挿入されて壊れるため、最初の1回だけにする）。
                    is_new = not log_file.exists()
                    if is_new:
                        with log_file.open("wb") as raw:
                            raw.write(codecs.BOM_UTF8)

                    with log_file.open("a", encoding="utf-8") as stream:
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
    """
    エージェント名（と任意のIssue番号）からロガーを生成する。

    ディレクトリはここで確定させない（Loggerが書き込み時に
    現在のCONFIGを見て解決する）。そのため、このモジュールが
    main() より前に呼ばれても、AGENT_ROOT未設定によるカレント
    ディレクトリ汚染は起きない。
    """
    if issue_number is None:
        log_filename = f"{name}.log"
        display = name
    else:
        log_filename = f"issue-{issue_number}.log"
        display = f"{name}#{issue_number}"

    return Logger(display, log_filename)


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
