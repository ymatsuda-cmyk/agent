"""
Git操作と git worktree の管理。

並走の要。
Issueごとに独立した作業ディレクトリ（worktree）を作ることで、
同じリポジトリを使いながらブランチ切り替えの競合を起こさずに
複数Issueを同時に進められる。

    C:\\repo\\tools                 ... 本体クローン（mainを保持）
    C:\\repo\\worktrees\\issue-12   ... Issue #12 の作業ツリー
    C:\\repo\\worktrees\\issue-13   ... Issue #13 の作業ツリー
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

from .config import CONFIG


class GitError(RuntimeError):
    pass


def run(
    command: list[str],
    cwd: pathlib.Path | None = None,
    check: bool = True,
    timeout: int = 600,
) -> subprocess.CompletedProcess[str]:
    """外部コマンドを実行する。標準出力/標準エラーは文字列で取得する。"""
    result = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )

    if check and result.returncode != 0:
        raise GitError(
            f"コマンド失敗: {' '.join(command)}\n"
            f"exit={result.returncode}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )

    return result


def git(
    *args: str,
    cwd: pathlib.Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], cwd=cwd or CONFIG.target_repo, check=check)


def require_command(name: str) -> None:
    if not shutil.which(name):
        raise GitError(f"'{name}' がPATHにありません。")


# ============================================================
# ブランチ名
# ============================================================

#: ブランチ名に残す文字（英数字・ひらがな・カタカナ・漢字・長音）
_SLUG_KEEP = (
    "a-z0-9"
    "\u3041-\u3096"  # ひらがな
    "\u30a1-\u30fa"  # カタカナ
    "\u30fc"          # 長音
    "\u4e00-\u9fff"  # 漢字
)


def slugify(text: str, max_length: int = 40) -> str:
    """
    ブランチ名の一部に使える文字列へ変換する。

    日本語タイトルが全て落ちて "task" になると、
    どのIssueのブランチか読めなくなるため日本語は残す。
    Gitはブランチ名にUTF-8を許容する。
    """
    value = re.sub(f"[^{_SLUG_KEEP}]+", "-", text.lower().strip()).strip("-")
    value = re.sub(r"-{2,}", "-", value)
    return value[:max_length].strip("-") or "task"


def branch_name(issue_number: int, issue_title: str) -> str:
    """GitHub Flow準拠のトピックブランチ名。"""
    return f"feature/issue-{issue_number}-{slugify(issue_title)}"


# ============================================================
# worktree
# ============================================================

def _worktree_registered(path: pathlib.Path) -> bool:
    result = git("worktree", "list", "--porcelain", check=False)
    target = str(path.resolve()).replace("\\", "/").lower()
    for line in result.stdout.splitlines():
        if not line.startswith("worktree "):
            continue
        registered = line[len("worktree "):].strip().replace("\\", "/").lower()
        if registered == target:
            return True
    return False


def _local_branch_exists(branch: str) -> bool:
    return git(
        "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False
    ).returncode == 0


def _remote_branch_exists(branch: str) -> bool:
    result = git("ls-remote", "--heads", "origin", branch, check=False)
    return bool(result.stdout.strip())


def prepare_worktree(
    issue_number: int,
    issue_title: str,
    base_branch: str | None = None,
    logger=None,
) -> tuple[pathlib.Path, str]:
    """
    Issue専用のworktreeとトピックブランチを用意する。

    再実装（rework）で再度呼ばれた場合は既存worktreeをそのまま使う。
    戻り値は (worktreeパス, ブランチ名)。
    """
    base = base_branch or CONFIG.base_branch
    branch = branch_name(issue_number, issue_title)
    path = CONFIG.worktree_path(issue_number)

    def say(message: str) -> None:
        if logger is not None:
            logger.info(message)

    CONFIG.worktree_root.mkdir(parents=True, exist_ok=True)

    # 既存worktreeがあれば再利用する。
    if path.exists() and (path / ".git").exists() and _worktree_registered(path):
        current = git("branch", "--show-current", cwd=path, check=False).stdout.strip()
        if current != branch and _local_branch_exists(branch):
            git("checkout", branch, cwd=path)
        say(f"既存worktreeを再利用します: {path}")
        return path, branch

    # 登録が壊れている場合は掃除する。
    if path.exists():
        say(f"不整合なworktreeを削除します: {path}")
        git("worktree", "remove", "--force", str(path), check=False)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    git("worktree", "prune", check=False)

    # 最新のbaseを取得する。
    git("fetch", "origin", "--prune")

    if _local_branch_exists(branch):
        say(f"既存ブランチでworktreeを作成します: {branch}")
        git("worktree", "add", str(path), branch)
    elif _remote_branch_exists(branch):
        say(f"リモートブランチを追跡してworktreeを作成します: {branch}")
        git("worktree", "add", "--track", "-b", branch, str(path), f"origin/{branch}")
    else:
        say(f"{base} から新規ブランチを作成します: {branch}")
        git("worktree", "add", "-b", branch, str(path), f"origin/{base}")

    return path, branch


def remove_worktree(issue_number: int, logger=None) -> None:
    """完了したIssueのworktreeを削除する。"""
    path = CONFIG.worktree_path(issue_number)

    git("worktree", "remove", "--force", str(path), check=False)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    git("worktree", "prune", check=False)

    if logger is not None:
        logger.info(f"worktreeを削除しました: {path}")


# ============================================================
# 変更検出
# ============================================================

def changed_files(worktree: pathlib.Path) -> list[str]:
    """
    未コミットの変更ファイル一覧（削除も含む）。

    -uall を付けないと、新規ディレクトリが "customer/" のように
    ディレクトリ単位でしか出ず、プレビュー公開時にファイルを拾えない。
    """
    result = git("status", "--porcelain", "-uall", cwd=worktree)
    files: list[str] = []

    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip().strip('"')
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path:
            files.append(path)

    return files


def committed_files(worktree: pathlib.Path, base_branch: str, branch: str) -> list[str]:
    """baseブランチからの差分ファイル一覧。"""
    result = git(
        "diff", "--name-only", f"origin/{base_branch}...{branch}",
        cwd=worktree, check=False,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def has_commits_ahead(worktree: pathlib.Path, base_branch: str, branch: str) -> bool:
    result = git(
        "rev-list", "--count", f"origin/{base_branch}..{branch}",
        cwd=worktree, check=False,
    )
    try:
        return int(result.stdout.strip() or "0") > 0
    except ValueError:
        return False


def discard_changes(worktree: pathlib.Path) -> None:
    """未コミットの変更を破棄する（却下時）。"""
    git("checkout", "--", ".", cwd=worktree, check=False)
    git("clean", "-fd", cwd=worktree, check=False)


def commit_all(worktree: pathlib.Path, message: str) -> bool:
    """未コミット変更をすべてコミットする。対象が無ければFalse。"""
    git("add", "-A", cwd=worktree)
    staged = git("diff", "--cached", "--name-only", cwd=worktree).stdout.strip()
    if not staged:
        return False
    git("commit", "-m", message, cwd=worktree)
    return True


def push_branch(worktree: pathlib.Path, branch: str) -> None:
    git("push", "-u", "origin", branch, cwd=worktree)
