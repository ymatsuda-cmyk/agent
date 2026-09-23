"""
OneDrive上のJSONを安全に読み書きするためのユーティリティ。

OneDrive同期中のファイルは「作成直後は0バイト」「書き込み途中」という
状態を取り得るため、サイズが安定するまで待ってから読む。
書き込みは一時ファイル + os.replace による原子的置換で行う。
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import time
from datetime import datetime

FILE_READY_RETRIES = 15
FILE_READY_INTERVAL_SECONDS = 0.5
DECODE_ENCODINGS = ("utf-8-sig", "utf-8", "cp932", "shift_jis")


def read_json(path: pathlib.Path) -> dict | None:
    """文字コードを判定しつつJSONを読む。失敗時はNone。"""
    if not path.exists():
        return None

    try:
        raw = path.read_bytes()
    except OSError:
        return None

    if not raw:
        return None

    for encoding in DECODE_ENCODINGS:
        try:
            value = json.loads(raw.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            return value

    return None


def read_json_when_ready(path: pathlib.Path) -> dict | None:
    """ファイルサイズが安定してからJSONを読む。"""
    previous_size = -1

    for _ in range(FILE_READY_RETRIES):
        if not path.exists():
            return None

        try:
            current_size = path.stat().st_size
        except OSError:
            time.sleep(FILE_READY_INTERVAL_SECONDS)
            continue

        if current_size > 0 and current_size == previous_size:
            data = read_json(path)
            if data is not None:
                return data

        previous_size = current_size
        time.sleep(FILE_READY_INTERVAL_SECONDS)

    return None


def write_json(path: pathlib.Path, payload: dict) -> bool:
    """原子的にJSONを書き込む（UTF-8 BOMなし）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")

    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, path)
        return True
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def move_to_done(path: pathlib.Path, done_dir: pathlib.Path | None = None) -> pathlib.Path | None:
    """
    処理済みJSONを同階層のdoneフォルダへ退避する。

    同名ファイルが既にある場合はタイムスタンプを付与して衝突を避ける。
    """
    if not path.exists():
        return None

    destination_dir = done_dir or (path.parent / "done")
    destination_dir.mkdir(parents=True, exist_ok=True)

    destination = destination_dir / path.name
    if destination.exists():
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        destination = destination_dir / f"{path.stem}_{stamp}{path.suffix}"

    try:
        shutil.move(str(path), str(destination))
        return destination
    except OSError:
        return None


def now_iso() -> str:
    """
    タイムゾーン付きのISO 8601文字列を返す（例: 2026-09-23T13:23:13+09:00）。

    state/issue-<N>.json の createdAt/updatedAt/mergedAt 等、
    システム全体のタイムスタンプはすべてこの関数を経由する。
    以前は datetime.now() のみ（タイムゾーン情報なし）だったため、
    GitHub APIが返す mergedAt（UTC、末尾Z付き）と並べたときに
    どちらのタイムゾーンなのか一見して分からず、混同しやすかった。
    .astimezone() でローカルのタイムゾーンオフセットを明示することで、
    どのタイムゾーンの時刻かを文字列だけで判別できるようにする。
    """
    return datetime.now().astimezone().isoformat(timespec="seconds")
