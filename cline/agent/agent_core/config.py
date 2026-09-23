"""
エージェント全体の設定を一元管理する。

設計方針:
- パスは AGENT_ROOT から自動導出する（個別の環境変数で上書き可能）
- 状態別フォルダは必ず done/ を持つ
- GitHubは「本体リポジトリ(tools)」と「検証用サブリポジトリ(tools-beta)」の2本立て
- Issue単位の作業は git worktree で分離し、並走を可能にする
"""

from __future__ import annotations

import os
import pathlib
import sys


# ============================================================
# 環境変数の定義
# ============================================================

#: 必須環境変数
REQUIRED_ENV_VARS: tuple[str, ...] = (
    "GITHUB_TOKEN",
    "GITHUB_OWNER",
    "GITHUB_REPO",
    "AGENT_ROOT",
    "TARGET_REPO_PATH",
)

#: 任意環境変数と既定値
OPTIONAL_ENV_DEFAULTS: dict[str, str] = {
    "GITHUB_BETA_REPO": "tools-beta",
    "BASE_BRANCH": "main",
    "AGENT_MAX_PARALLEL": "3",
    "AGENT_GUI_MODE": "auto",          # auto | manual
    "AGENT_CLINE_INPUT_X": "1500",
    "AGENT_CLINE_INPUT_Y": "900",
    "AGENT_IMPLEMENTATION_TIMEOUT": "1800",
    "AGENT_DECISION_TIMEOUT": "7200",
    "AGENT_GUI_LOCK_TIMEOUT": "1800",
}

ENV_SETUP_EXAMPLES: dict[str, str] = {
    "GITHUB_TOKEN": 'setx GITHUB_TOKEN "github_pat_xxxxxxxxxxxx"',
    "GITHUB_OWNER": 'setx GITHUB_OWNER "ymatsuda-cmyk"',
    "GITHUB_REPO": 'setx GITHUB_REPO "tools"',
    "GITHUB_BETA_REPO": 'setx GITHUB_BETA_REPO "tools-beta"',
    "AGENT_ROOT": (
        'setx AGENT_ROOT "C:\\Users\\matsuda\\OneDrive - '
        '株式会社日本ビジネスアシスト\\work\\agent"'
    ),
    "TARGET_REPO_PATH": 'setx TARGET_REPO_PATH "C:\\repo\\tools"',
    "BETA_REPO_PATH": 'setx BETA_REPO_PATH "C:\\repo\\tools-beta"',
    "AGENT_WORKTREE_ROOT": 'setx AGENT_WORKTREE_ROOT "C:\\repo\\worktrees"',
}


# ============================================================
# 状態別フォルダ
# ============================================================

#: 状態別フォルダ名。値は環境変数による個別上書きキー。
STATE_FOLDERS: dict[str, str] = {
    "request": "AGENT_REQUEST_DIR",
    "question": "AGENT_QUESTION_DIR",
    "decision": "AGENT_DECISION_DIR",
    "waiting": "AGENT_WAITING_DIR",
    "approval": "AGENT_APPROVAL_DIR",
    "reply": "AGENT_REPLY_DIR",
    "state": "AGENT_STATE_DIR",
}


def _env(name: str) -> str:
    value = os.getenv(name, OPTIONAL_ENV_DEFAULTS.get(name, ""))
    return value.strip() if value else ""


def _env_int(name: str) -> int:
    raw = _env(name)
    try:
        return int(raw)
    except ValueError:
        return int(OPTIONAL_ENV_DEFAULTS.get(name, "0"))


class Config:
    """実行時設定のスナップショット。"""

    def __init__(self) -> None:
        self.github_token = _env("GITHUB_TOKEN")
        self.github_owner = _env("GITHUB_OWNER")
        self.github_repo = _env("GITHUB_REPO")
        self.github_beta_repo = _env("GITHUB_BETA_REPO")
        self.base_branch = _env("BASE_BRANCH") or "main"

        # pathlib.Path("") は例外にならず、暗黙的にカレントディレクトリ
        # ("." ) として解釈される。AGENT_ROOT / TARGET_REPO_PATH が
        # 未設定のままこれを使うと、実行時のカレントディレクトリへ
        # 気づかずフォルダを作ったり、無関係なgitリポジトリを操作したり
        # する事故につながる。未設定だったかどうかを明示的に記録しておき、
        # ensure_directories() 側で検知できるようにする。
        raw_agent_root = _env("AGENT_ROOT")
        raw_target_repo = _env("TARGET_REPO_PATH")
        self._agent_root_was_set = bool(raw_agent_root)
        self._target_repo_was_set = bool(raw_target_repo)

        self.agent_root = pathlib.Path(raw_agent_root)
        self.target_repo = pathlib.Path(raw_target_repo)

        beta_path = _env("BETA_REPO_PATH")
        self.beta_repo = (
            pathlib.Path(beta_path)
            if beta_path
            else self.target_repo.parent / self.github_beta_repo
        )

        worktree_root = _env("AGENT_WORKTREE_ROOT")
        self.worktree_root = (
            pathlib.Path(worktree_root)
            if worktree_root
            else self.target_repo.parent / "worktrees"
        )

        self.max_parallel = max(1, _env_int("AGENT_MAX_PARALLEL"))
        self.gui_mode = _env("AGENT_GUI_MODE") or "auto"
        self.cline_input_x = _env_int("AGENT_CLINE_INPUT_X")
        self.cline_input_y = _env_int("AGENT_CLINE_INPUT_Y")
        self.implementation_timeout = _env_int("AGENT_IMPLEMENTATION_TIMEOUT")
        self.decision_timeout = _env_int("AGENT_DECISION_TIMEOUT")
        self.gui_lock_timeout = _env_int("AGENT_GUI_LOCK_TIMEOUT")

        # 状態別フォルダ
        for folder_name, override_key in STATE_FOLDERS.items():
            override = _env(override_key)
            path = (
                pathlib.Path(override)
                if override
                else self.agent_root / folder_name
            )
            setattr(self, f"{folder_name}_dir", path)

        self.logs_dir = self.agent_root / "logs"
        self.locks_dir = self.state_dir / "locks"
        self.processed_dir = self.state_dir / "processed"
        self.archive_dir = self.state_dir / "archive"

    # --------------------------------------------------------

    @property
    def repository(self) -> str:
        return f"{self.github_owner}/{self.github_repo}"

    @property
    def beta_repository(self) -> str:
        return f"{self.github_owner}/{self.github_beta_repo}"

    @property
    def pages_base_url(self) -> str:
        return f"https://{self.github_owner}.github.io/{self.github_beta_repo}"

    def state_dirs(self) -> list[pathlib.Path]:
        return [getattr(self, f"{name}_dir") for name in STATE_FOLDERS]

    def done_dir(self, folder_name: str) -> pathlib.Path:
        return getattr(self, f"{folder_name}_dir") / "done"

    def worktree_path(self, issue_number: int) -> pathlib.Path:
        return self.worktree_root / f"issue-{issue_number}"

    def state_path(self, issue_number: int) -> pathlib.Path:
        return self.state_dir / f"issue-{issue_number}.json"

    # --------------------------------------------------------

    def ensure_directories(self) -> None:
        """
        状態別フォルダとdone、管理フォルダを作成する。

        AGENT_ROOTが未設定のまま呼ばれると、pathlib.Path("")が
        暗黙的にカレントディレクトリを指すため、実行時にたまたま
        いた場所へフォルダを作ってしまう（原因が分かりにくい事故になる）。
        これを未然に防ぐため、未設定なら明確なエラーで止める。
        通常は各スクリプトのmain()がこれより先にvalidate()を呼ぶため
        ここに到達しないが、呼び忘れがあっても事故らないための二重の安全網。
        """
        if not self._agent_root_was_set:
            raise RuntimeError(
                "AGENT_ROOT が設定されていません。このまま続けると、"
                f"カレントディレクトリ（{pathlib.Path('.').resolve()}）の"
                "直下にフォルダを作ってしまいます。\n"
                "scripts\\setup.ps1 を実行するか、環境変数 AGENT_ROOT を"
                "設定してから、PowerShellを開き直してください。"
            )

        for folder_name in STATE_FOLDERS:
            directory = getattr(self, f"{folder_name}_dir")
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "done").mkdir(parents=True, exist_ok=True)

        for directory in (
            self.logs_dir,
            self.locks_dir,
            self.processed_dir,
            self.archive_dir,
            self.worktree_root,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def validate(self, extra_required: tuple[str, ...] = ()) -> None:
        """必須環境変数と主要パスを検証する。不足時はプロセスを終了する。"""
        missing: list[str] = []

        print()
        print("=" * 60)
        print("環境変数チェック")
        print("=" * 60)

        for name in REQUIRED_ENV_VARS + extra_required:
            value = os.getenv(name)
            if not value or not value.strip():
                missing.append(name)
                print(f"[NG] {name}: 未設定")
                continue
            if "TOKEN" in name:
                print(f"[OK] {name}: 設定済み ({mask_secret(value)})")
            else:
                print(f"[OK] {name}: {value}")

        if missing:
            print()
            print("不足している環境変数があります。")
            print("PowerShellで以下を実行し、PowerShellを開き直してください。")
            print()
            for name in missing:
                print(ENV_SETUP_EXAMPLES.get(name, f'setx {name} "..."'))
            sys.exit(1)

        print("=" * 60)
        print("必要な環境変数はすべて設定されています。")
        print("=" * 60)
        print()

        if not (self.target_repo / ".git").exists():
            print(f"[ERROR] Gitリポジトリではありません: {self.target_repo}")
            sys.exit(1)


def mask_secret(secret: str) -> str:
    if len(secret) <= 8:
        return "********"
    return f"{secret[:4]}{'*' * 8}{secret[-4:]}"


#: 共有インスタンス
CONFIG = Config()
