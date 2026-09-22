"""
マージ前の簡易静的チェック。

GitHub Actions等のCIを別途用意していなくても、最低限の壊れは
ここで検出してからマージする。CIを設定できる場合は
`repo-templates/.github/workflows/static-check.yml` を
本体・検証用リポジトリへコピーすると、PR側でも同じ観点のチェックが動く。

検査する範囲はあえて小さくしている。
- HTMLタグの対応（閉じ忘れ・対応しない閉じタグ）
- href / src に書かれたローカルパスのリンク切れ

構文解析やビルドを必要としない、静的サイトなら常に成立する最低限の検査。
"""

from __future__ import annotations

import pathlib
from html.parser import HTMLParser
from urllib.parse import urlparse

VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}
LINK_ATTRS = {"href", "src"}


class _HtmlAuditor(HTMLParser):
    """タグの対応関係と、href/src に書かれたローカルパスを同時に集める。"""

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.unbalanced: list[str] = []
        self.references: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        attrs_dict = dict(attrs)
        for name in LINK_ATTRS:
            value = attrs_dict.get(name)
            if value:
                self.references.append(value)
        if tag not in VOID_TAGS:
            self.stack.append(tag)

    def handle_endtag(self, tag) -> None:
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        elif tag in self.stack:
            # 閉じタグの直前に、閉じられていない別のタグが挟まっている
            while self.stack and self.stack[-1] != tag:
                self.unbalanced.append(f"開始タグ <{self.stack.pop()}> が閉じられていません")
            if self.stack:
                self.stack.pop()
        else:
            self.unbalanced.append(f"対応する開始タグの無い </{tag}>")


def check_html_balance(path: pathlib.Path) -> list[str]:
    """HTMLタグの対応が崩れていないかを検査する。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []  # 文字コードの問題は check_encoding 側の担当

    auditor = _HtmlAuditor()
    try:
        auditor.feed(text)
    except Exception:
        return ["HTMLの解析に失敗しました"]

    issues = list(auditor.unbalanced)
    if auditor.stack:
        tags = ", ".join(f"<{t}>" for t in auditor.stack[:5])
        issues.append(f"閉じられていないタグが残っています: {tags}")
    return issues


def check_broken_local_links(path: pathlib.Path, repo_root: pathlib.Path) -> list[str]:
    """href / src に書かれたローカルパスの参照先が実在するかを検査する。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    auditor = _HtmlAuditor()
    try:
        auditor.feed(text)
    except Exception:
        return []

    issues: list[str] = []
    for raw in auditor.references:
        target = _resolve_local_reference(raw, path, repo_root)
        if target is not None and not target.exists():
            issues.append(f"リンク切れ: {raw}")

    return issues


def _resolve_local_reference(
    raw: str, source: pathlib.Path, repo_root: pathlib.Path
) -> pathlib.Path | None:
    """外部URL・アンカーのみの参照はNoneを返してスキップ対象にする。"""
    value = raw.strip()
    if not value or value.startswith("#"):
        return None

    parsed = urlparse(value)
    if parsed.scheme or value.startswith("//"):
        return None  # http(s):// mailto: tel: data: など

    clean = parsed.path
    if not clean:
        return None

    if clean.startswith("/"):
        return repo_root / clean.lstrip("/")
    return (source.parent / clean).resolve()


def check_files(worktree: pathlib.Path, relative_paths: list[str]) -> dict[str, list[str]]:
    """
    HTMLファイルについて、タグバランスとローカルリンクを検査する。

    戻り値は {相対パス: [問題の説明, ...]}。問題の無いファイルは含まれない。
    """
    results: dict[str, list[str]] = {}

    for relative in relative_paths:
        path = worktree / relative
        if not path.exists() or path.suffix.lower() not in (".html", ".htm"):
            continue

        issues = check_html_balance(path) + check_broken_local_links(path, worktree)
        if issues:
            results[relative] = issues

    return results
