"""
Teams投稿に含まれる画像の受け渡し。

役割分担:
- Power Automate①: Teamsから画像を取り出し、
  request/attachments/<フォルダ名>/ へ保存したうえで、
  依頼JSONに "attachments": [{"name": "画像.png"}, ...] を載せる。
  画像を先に保存し、依頼JSONは最後に書き出すこと（JSONの作成が
  issue_agent の起動合図になるため）。
- issue_agent.py: 画像を tools-beta の issue-assets/ へpushし、
  Issue本文に埋め込む（GitHubにはIssueへ画像を添付する公式APIが無いため、
  リポジトリ内のファイルとして置き、そのURLを本文から参照する）。
- implement_agent.py: 画像を worktree の .agent-attachments/ へコピーし、
  Clineへのプロンプトで場所を伝える（コミット対象外）。

Python側はTeamsの認証を一切持たない（Teamsから取り出すのはPower Automateの役目）。
"""

from __future__ import annotations

import pathlib
import re
import shutil
import time
from urllib.parse import quote

from .config import CONFIG

#: 受け付ける画像の拡張子
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})

#: 1ファイルあたりの上限（GitHubへpushするため、極端に大きいものは除外する）
MAX_IMAGE_BYTES = 10 * 1024 * 1024

#: 1件の依頼あたりの上限枚数
MAX_IMAGES = 10

#: OneDrive同期の遅れを見込んで、画像が揃うまで待つ最大秒数
WAIT_TIMEOUT_SECONDS = 60
WAIT_INTERVAL_SECONDS = 2

#: worktree内の置き場所（.git/info/exclude でコミット対象外にする）
WORKTREE_DIR_NAME = ".agent-attachments"

#: tools-beta 内の置き場所
ASSET_ROOT = "issue-assets"


def attachments_root() -> pathlib.Path:
    return CONFIG.request_dir / "attachments"


def safe_name(name: str) -> str:
    """
    ファイル名として安全な文字列にする。

    Power Automateから渡る名前をそのままパスに使うと、"../" などで
    想定外の場所を読み書きされる恐れがあるため、ディレクトリ部分を捨て、
    区切り文字や制御文字を置き換える。拡張子は小文字に揃える。
    """
    base = pathlib.PurePath(str(name).replace("\\", "/")).name
    base = re.sub(r"[\x00-\x1f/\\:*?\"<>|]", "_", base).strip(" .")
    if not base:
        return ""
    stem, dot, suffix = base.rpartition(".")
    if not dot:
        return base[:120]
    return f"{stem[:100]}.{suffix.lower()}"


def folder_name_for(source: dict, message_id: str) -> str:
    """依頼JSONの attachmentsFolder を優先し、無ければ Message ID を使う。"""
    raw = str(source.get("attachmentsFolder") or message_id or "").strip()
    return re.sub(r"[^A-Za-z0-9._-]", "_", raw)[:120]


def requested_names(source: dict) -> list[str]:
    """依頼JSONの attachments から、画像のファイル名だけを取り出す。"""
    items = source.get("attachments")
    if not isinstance(items, list):
        return []

    names: list[str] = []
    for item in items:
        raw = item.get("name") if isinstance(item, dict) else item
        name = safe_name(str(raw or ""))
        if not name or pathlib.Path(name).suffix.lower() not in IMAGE_SUFFIXES:
            continue
        if name not in names:
            names.append(name)
    return names[:MAX_IMAGES]


def _stable_size(path: pathlib.Path) -> int | None:
    """ファイルが存在し、サイズが2回続けて同じならそのサイズを返す。"""
    try:
        first = path.stat().st_size
        time.sleep(0.3)
        second = path.stat().st_size
    except OSError:
        return None
    if first == second and first > 0:
        return first
    return None


def collect_request_images(
    source: dict,
    message_id: str,
    wait_timeout: float = WAIT_TIMEOUT_SECONDS,
    logger=None,
) -> tuple[list[pathlib.Path], list[str]]:
    """
    依頼JSONに載っている画像を、request/attachments/<フォルダ名>/ から集める。

    戻り値は (揃った画像のパス, 取得できなかった画像の説明)。
    OneDrive同期の都合で、JSONが先に届いて画像が後から届くことがあるため、
    wait_timeout 秒まで揃うのを待つ。揃わなかった画像があってもIssue作成は
    止めない（本文に「取得できなかった」と書き残す）。
    """
    names = requested_names(source)
    if not names:
        return [], []

    folder = attachments_root() / folder_name_for(source, message_id)
    pending = list(names)
    ready: dict[str, pathlib.Path] = {}
    problems: list[str] = []
    deadline = time.time() + max(0.0, wait_timeout)

    while pending:
        for name in list(pending):
            path = folder / name
            size = _stable_size(path) if path.exists() else None
            if size is None:
                continue
            pending.remove(name)
            if size > MAX_IMAGE_BYTES:
                problems.append(f"{name}（{size // (1024 * 1024)}MBのため除外）")
            else:
                ready[name] = path

        if not pending or time.time() >= deadline:
            break
        time.sleep(WAIT_INTERVAL_SECONDS)

    for name in pending:
        problems.append(f"{name}（ファイルが見つかりません）")
        if logger is not None:
            logger.warn(f"添付画像が見つかりません: {folder / name}")

    ordered = [ready[name] for name in names if name in ready]
    return ordered, problems


def asset_relative_dir(folder: str) -> str:
    return f"{ASSET_ROOT}/{folder}"


def raw_url(relative_path: str) -> str:
    """tools-beta 上のファイルを直接表示するURL（pushした直後から表示できる）。"""
    encoded = "/".join(quote(part) for part in relative_path.split("/"))
    return (
        f"https://raw.githubusercontent.com/{CONFIG.github_owner}/"
        f"{CONFIG.github_beta_repo}/{CONFIG.base_branch}/{encoded}"
    )


def build_markdown(images: list[dict], problems: list[str]) -> str:
    """Issue本文に追記する「添付画像」セクション。"""
    if not images and not problems:
        return ""

    lines = ["## 添付画像", ""]
    for image in images:
        lines.append(f"![{image['name']}]({image['url']})")
        lines.append("")
    if problems:
        lines.append("取得できなかった画像:")
        lines.extend(f"- {item}" for item in problems)
        lines.append("")
    return "\n".join(lines)


def copy_into_worktree(
    images: list[dict], worktree: pathlib.Path, logger=None
) -> list[str]:
    """
    state に記録された画像を worktree/.agent-attachments/ へコピーする。

    元ファイル（OneDrive上）が無くなっていれば、tools-beta のURLから取得する。
    戻り値は worktree からの相対パスの一覧（プロンプトに載せる）。
    """
    if not images:
        return []

    destination_dir = worktree / WORKTREE_DIR_NAME
    destination_dir.mkdir(parents=True, exist_ok=True)
    placed: list[str] = []

    for image in images:
        name = safe_name(str(image.get("name", "")))
        if not name:
            continue
        destination = destination_dir / name
        source = pathlib.Path(str(image.get("localPath") or ""))

        try:
            if source.is_file():
                shutil.copy2(source, destination)
            elif image.get("url"):
                import requests

                response = requests.get(str(image["url"]), timeout=30)
                response.raise_for_status()
                destination.write_bytes(response.content)
            else:
                continue
        except Exception as error:  # 画像が無くても実装自体は進める
            if logger is not None:
                logger.warn(f"添付画像を配置できませんでした: {name} / {error}")
            continue

        placed.append(f"{WORKTREE_DIR_NAME}/{name}")

    return placed
