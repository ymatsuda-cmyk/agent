"""
[工程4] 検証用サブリポジトリ（tools-beta）への公開

承認前の動作確認を行うため、変更ファイルを tools-beta の
preview/issue-<N>/ 配下へ配置し、GitHub Pages で公開する。

    https://<owner>.github.io/tools-beta/preview/issue-<N>/...

Issueごとにディレクトリが分かれるため、複数Issueのプレビューが共存できる。
tools-beta へのpushは競合するので、リポジトリ単位のロックで直列化する。

標準出力の最終行にJSONを出す（呼び出し側が解析する）。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys

from agent_core import gitops
from agent_core.config import CONFIG
from agent_core.locks import acquire_or_wait, release
from agent_core.logs import get_logger

LOGGER = get_logger("deploy_preview")

BETA_LOCK_TIMEOUT_SECONDS = 900
PREVIEW_ROOT = "preview"
SKIP_NAMES = {".agent-question.json", ".agent-summary.json", ".agent-rework.txt"}
SKIP_DIR_NAMES = {".git", "__pycache__", "node_modules", ".DS_Store"}


def beta_lock_path() -> pathlib.Path:
    return CONFIG.locks_dir / "tools-beta.lock"


def ensure_beta_repo() -> pathlib.Path:
    """tools-beta のローカルクローンを用意する。"""
    path = CONFIG.beta_repo

    if (path / ".git").exists():
        gitops.git("fetch", "origin", "--prune", cwd=path)
        gitops.git("checkout", CONFIG.base_branch, cwd=path, check=False)
        gitops.git("reset", "--hard", f"origin/{CONFIG.base_branch}", cwd=path, check=False)
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    LOGGER.info(f"tools-beta をクローンします: {CONFIG.beta_repository}")
    gitops.run(
        [
            "gh", "repo", "clone", CONFIG.beta_repository, str(path),
            "--", "--depth", "50",
        ],
        cwd=path.parent,
    )
    return path


def copy_worktree_tree(
    worktree: pathlib.Path,
    beta_repo: pathlib.Path,
    issue_number: int,
) -> list[str]:
    """
    worktree全体（.gitと制御ファイルを除く）を preview/issue-<N>/ へコピーする。

    以前は「変更ファイルだけ」をコピーしていたため、変更ファイルが参照する
    既存の共有CSS/JS/画像がプレビューに存在せず、レイアウトが崩れた状態で
    承認判断をさせてしまう問題があった。
    worktreeは既に「baseブランチ + 今回の変更」を含む完全なチェックアウトなので、
    ディレクトリ全体をそのまま配置すれば、mainへマージした後と同じ構成になる。
    """
    destination_root = beta_repo / PREVIEW_ROOT / f"issue-{issue_number}"

    if destination_root.exists():
        shutil.rmtree(destination_root, ignore_errors=True)
    destination_root.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []

    for source in sorted(worktree.rglob("*")):
        if source.is_dir():
            continue

        relative = source.relative_to(worktree)
        if any(part in SKIP_DIR_NAMES for part in relative.parts):
            continue
        if relative.name in SKIP_NAMES:
            continue

        destination = destination_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(relative.as_posix())

    return copied


def write_index(
    beta_repo: pathlib.Path,
    issue_number: int,
    changed_files: list[str],
    issue_title: str,
) -> None:
    """
    プレビュー直下にファイル一覧ページを置く（index.htmlが無い場合の入口）。

    worktree全体を配置するようになったため、変更していないページの
    index.htmlがそのまま存在する場合はそちらを優先し、このフォールバックは
    上書きしない。ここに載せるのは「今回変更したファイル」だけで、
    変更していない大量のファイルを一覧してもレビューの助けにならないため。
    """
    destination_root = beta_repo / PREVIEW_ROOT / f"issue-{issue_number}"
    index_path = destination_root / "index.html"

    if index_path.exists():
        return

    links = "\n".join(
        f'      <li><a href="{path}">{path}</a></li>' for path in sorted(changed_files)
    ) or "      <li>(変更ファイルなし)</li>"

    index_path.write_text(
        f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Issue #{issue_number} プレビュー</title>
  <style>
    body {{ font-family: "Segoe UI", "Meiryo", sans-serif; margin: 2rem; line-height: 1.8; }}
    h1 {{ font-size: 1.25rem; }}
    .note {{ color: #666; font-size: 0.9rem; }}
  </style>
</head>
<body>
  <h1>Issue #{issue_number} 検証用プレビュー</h1>
  <p>{issue_title}</p>
  <p class="note">承認前の確認用です。マージ後に自動で削除されます。</p>
  <p>今回変更したファイル:</p>
  <ul>
{links}
  </ul>
</body>
</html>
""",
        encoding="utf-8",
    )


def commit_and_push(beta_repo: pathlib.Path, message: str) -> bool:
    gitops.git("add", "-A", cwd=beta_repo)
    staged = gitops.git("diff", "--cached", "--name-only", cwd=beta_repo).stdout.strip()

    if not staged:
        LOGGER.info("tools-betaに変更はありません。")
        return False

    gitops.git("commit", "-m", message, cwd=beta_repo)

    # 他ワーカーのpushと競合した場合に備えてリベースしてから再試行する。
    result = gitops.git("push", "origin", CONFIG.base_branch, cwd=beta_repo, check=False)
    if result.returncode != 0:
        LOGGER.warn("pushが競合しました。リベースして再試行します。")
        gitops.git("pull", "--rebase", "origin", CONFIG.base_branch, cwd=beta_repo)
        gitops.git("push", "origin", CONFIG.base_branch, cwd=beta_repo)

    return True


def build_preview_url(
    issue_number: int,
    changed_files: list[str],
    preview_root: pathlib.Path | None = None,
) -> str:
    """
    プレビューURLを推測する。

    今回変更したファイルの中に index.html があれば、そのディレクトリを返す。
    無ければ、Issue直下（プレビュールート）を返す。
    プレビュールートには、worktree全体コピーによりサイト本来の
    index.htmlが通常は既に存在している。

    preview_root を渡した場合は、実際に配置済みのファイルだけを候補にする。
    ファイル移動のIssueでは削除側（移動元）のパスも changed_files に含まれ、
    そのディレクトリはプレビューに存在しないため404になる。
    """
    base = f"{CONFIG.pages_base_url}/{PREVIEW_ROOT}/issue-{issue_number}"

    for path in sorted(changed_files):
        normalized = path.replace("\\", "/")
        if not normalized.lower().endswith("index.html"):
            continue
        if preview_root is not None and not (preview_root / normalized).exists():
            continue
        directory = "/".join(normalized.split("/")[:-1])
        return f"{base}/{directory}/" if directory else f"{base}/"

    return f"{base}/"


def deploy(issue_number: int, worktree: pathlib.Path, changed_files: list[str]) -> dict:
    beta_repo = ensure_beta_repo()
    preview_root = beta_repo / PREVIEW_ROOT / f"issue-{issue_number}"

    copied = copy_worktree_tree(worktree, beta_repo, issue_number)
    if not copied:
        LOGGER.warn("公開対象のファイルがありません（worktreeが空です）。")
        return {
            "status": "skipped",
            "issueNumber": issue_number,
            "previewUrl": build_preview_url(issue_number, []),
            "files": [],
        }

    write_index(beta_repo, issue_number, changed_files, f"{len(changed_files)} ファイル変更")
    pushed = commit_and_push(
        beta_repo,
        f"preview: Issue #{issue_number} ({len(changed_files)} changed / {len(copied)} total files)",
    )

    LOGGER.info(
        f"公開ファイル数: {len(copied)}件（うち今回の変更: {len(changed_files)}件）"
    )

    return {
        "status": "deployed" if pushed else "unchanged",
        "issueNumber": issue_number,
        "previewUrl": build_preview_url(issue_number, changed_files, preview_root),
        "files": changed_files,
    }


def cleanup(issue_number: int) -> dict:
    """マージ後に preview/issue-<N>/ を削除する。"""
    beta_repo = ensure_beta_repo()
    target = beta_repo / PREVIEW_ROOT / f"issue-{issue_number}"

    if not target.exists():
        return {"status": "nothing_to_clean", "issueNumber": issue_number}

    shutil.rmtree(target, ignore_errors=True)
    commit_and_push(beta_repo, f"preview: remove Issue #{issue_number}")
    LOGGER.info(f"プレビューを削除しました: preview/issue-{issue_number}")

    return {"status": "cleaned", "issueNumber": issue_number}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="tools-betaへ承認前プレビューを公開します。"
    )
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--worktree", default="")
    parser.add_argument("--changed-file", action="append", default=[])
    parser.add_argument("--cleanup", action="store_true", help="プレビューを削除する")
    args = parser.parse_args()

    CONFIG.validate()
    CONFIG.ensure_directories()
    gitops.require_command("git")
    gitops.require_command("gh")

    lock = beta_lock_path()
    acquired = acquire_or_wait(
        lock,
        timeout_seconds=BETA_LOCK_TIMEOUT_SECONDS,
        note=f"issue-{args.issue}",
        on_wait=lambda owner: LOGGER.info(
            f"tools-betaロック待機中（使用中: {owner.get('host') or '不明'} / "
            f"PID={owner.get('pid', '不明')}）"
        ),
    )

    if not acquired:
        print(json.dumps({"status": "lock_timeout", "previewUrl": ""}, ensure_ascii=False))
        return 1

    try:
        if args.cleanup:
            result = cleanup(args.issue)
        else:
            worktree = (
                pathlib.Path(args.worktree)
                if args.worktree
                else CONFIG.worktree_path(args.issue)
            )
            result = deploy(args.issue, worktree, args.changed_file)

        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as error:  # 呼び出し側がJSONを期待するため必ず出力する
        LOGGER.error(str(error))
        print(json.dumps({"status": "error", "message": str(error), "previewUrl": ""},
                         ensure_ascii=False))
        return 1
    finally:
        release(lock)


if __name__ == "__main__":
    sys.exit(main())
