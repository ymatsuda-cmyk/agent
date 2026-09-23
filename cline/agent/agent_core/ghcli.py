"""
GitHub操作のラッパー。

Issue作成はREST API（requests）、それ以外は gh CLI を使う。
gh CLI を使う理由は、PR作成・マージ・クローズの挙動が
GitHubの標準運用（GitHub Flow）とそのまま一致するため。
"""

from __future__ import annotations

import json
import pathlib
import time

import requests

from .config import CONFIG
from .gitops import run

HTTP_TIMEOUT_SECONDS = 30
HTTP_RETRIES = 3
HTTP_RETRY_INTERVAL_SECONDS = 3


# ============================================================
# REST
# ============================================================

def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {CONFIG.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json; charset=utf-8",
    }


def create_issue(title: str, body: str, labels: list[str] | None = None) -> dict | None:
    """Issueを作成し、APIレスポンスを返す。"""
    url = f"https://api.github.com/repos/{CONFIG.repository}/issues"
    payload: dict = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels

    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            response = requests.post(
                url,
                headers=_headers(),
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                timeout=HTTP_TIMEOUT_SECONDS,
            )
        except requests.RequestException:
            if attempt == HTTP_RETRIES:
                return None
            time.sleep(HTTP_RETRY_INTERVAL_SECONDS)
            continue

        if response.status_code == 201:
            return response.json()

        # レート制限・一時障害はリトライする。
        if response.status_code in (403, 429, 500, 502, 503) and attempt < HTTP_RETRIES:
            time.sleep(HTTP_RETRY_INTERVAL_SECONDS * attempt)
            continue

        return None

    return None


# ============================================================
# gh CLI
# ============================================================

def _gh(*args: str, cwd: pathlib.Path | None = None, check: bool = True):
    return run(["gh", *args], cwd=cwd or CONFIG.target_repo, check=check)


def auth_status() -> bool:
    """gh の認証状態。gh 未インストールや未認証の場合は False。"""
    import shutil

    if not shutil.which("gh"):
        return False

    try:
        return _gh("auth", "status", check=False).returncode == 0
    except OSError:
        return False


def view_issue(issue_number: int) -> dict:
    result = _gh(
        "issue", "view", str(issue_number),
        "--repo", CONFIG.repository,
        "--json", "number,title,body,url,state,labels,assignees",
    )
    return json.loads(result.stdout)


def comment_issue(issue_number: int, body: str) -> None:
    _gh(
        "issue", "comment", str(issue_number),
        "--repo", CONFIG.repository, "--body", body,
        check=False,
    )


def ensure_label(name: str, color: str = "D93F0B", description: str = "") -> None:
    """
    ラベルが無ければ作成する。既にあれば何もしない（gh label create の失敗は無視する）。
    """
    _gh(
        "label", "create", name,
        "--repo", CONFIG.repository,
        "--color", color,
        "--description", description,
        check=False,
    )


def add_issue_label(issue_number: int, label: str) -> None:
    _gh(
        "issue", "edit", str(issue_number),
        "--repo", CONFIG.repository,
        "--add-label", label,
        check=False,
    )


def remove_issue_label(issue_number: int, label: str) -> None:
    _gh(
        "issue", "edit", str(issue_number),
        "--repo", CONFIG.repository,
        "--remove-label", label,
        check=False,
    )


def close_issue(issue_number: int, comment: str = "") -> None:
    args = ["issue", "close", str(issue_number), "--repo", CONFIG.repository]
    if comment:
        args += ["--comment", comment]
    _gh(*args, check=False)


def find_open_pr(branch: str) -> dict | None:
    result = _gh(
        "pr", "list", "--repo", CONFIG.repository,
        "--head", branch, "--state", "open",
        "--json", "number,title,url,headRefName,mergeable",
        "--limit", "1",
    )
    try:
        items = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return None
    return items[0] if items else None


def create_pull_request(
    issue_number: int,
    issue_title: str,
    branch: str,
    base_branch: str,
    body: str,
    draft: bool = False,
    cwd: pathlib.Path | None = None,
) -> str:
    args = [
        "pr", "create", "--repo", CONFIG.repository,
        "--base", base_branch, "--head", branch,
        "--title", f"#{issue_number} {issue_title}",
        "--body", body,
    ]
    if draft:
        args.append("--draft")

    result = _gh(*args, cwd=cwd)
    urls = [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip().startswith("http")
    ]
    return urls[-1] if urls else ""


def update_pull_request_body(pr_number: int, body: str) -> None:
    """既存PRの本文を最新の実装内容で上書きする（reworkで再利用される際に使う）。"""
    _gh(
        "pr", "edit", str(pr_number),
        "--repo", CONFIG.repository, "--body", body,
        check=False,
    )


def merge_pull_request(
    pr_number: int,
    method: str = "squash",
    delete_branch: bool = True,
    cwd: pathlib.Path | None = None,
) -> tuple[bool, str]:
    """
    PRをマージする。

    デファクトスタンダードに合わせ squash merge を既定とする。
    PR本文の `Closes #N` によってIssueは自動クローズされる。
    """
    args = ["pr", "merge", str(pr_number), "--repo", CONFIG.repository, f"--{method}"]
    if delete_branch:
        args.append("--delete-branch")

    result = _gh(*args, cwd=cwd, check=False)
    if result.returncode == 0:
        return True, result.stdout.strip()
    return False, (result.stderr or result.stdout).strip()


def pr_state(pr_number: int) -> dict:
    result = _gh(
        "pr", "view", str(pr_number), "--repo", CONFIG.repository,
        "--json", "number,state,merged,mergeStateStatus,url",
        check=False,
    )
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def wait_until_mergeable(pr_number: int, timeout_seconds: int = 300) -> str:
    """
    マージ可能になるまで待つ。

    GitHubはPR作成直後 mergeStateStatus が UNKNOWN になるため、
    確定するまで短時間ポーリングする。
    """
    started_at = time.time()
    last = "UNKNOWN"

    while time.time() - started_at < timeout_seconds:
        info = pr_state(pr_number)
        last = str(info.get("mergeStateStatus", "UNKNOWN"))
        if last not in ("UNKNOWN", ""):
            return last
        time.sleep(5)

    return last
