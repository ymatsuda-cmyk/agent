"""
運用コマンド。

    python agent_cli.py status          状態一覧を表示する
    python agent_cli.py doctor          環境と前提条件を点検する
    python agent_cli.py show 12         Issue #12 の詳細を表示する
    python agent_cli.py retry 12        Issue #12 を着手待ちへ戻す
    python agent_cli.py approve 12      手動で承認する
    python agent_cli.py reject 12       手動で却下する
    python agent_cli.py rework 12 "指示" 修正指示を出して同じworktreeで再実装させる
    python agent_cli.py unlock          残存ロックを掃除する
    python agent_cli.py clean-worktrees 不要なworktreeを掃除する
    python agent_cli.py test-click      Cline入力欄の座標を確認する
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from agent_core import cline, ghcli, gitops, statefile
from agent_core.config import CONFIG
from agent_core.jsonio import now_iso
from agent_core.locks import _current_host, _process_alive, lock_info
from agent_core.logs import get_logger

LOGGER = get_logger("cli")
SCRIPT_DIR = Path(__file__).resolve().parent


def command_status(args) -> int:
    rows = statefile.iter_states()
    if not rows:
        print("対象のIssueはありません。")
        return 0

    print()
    print(f"{'Issue':>6}  {'状態':<12}  {'ブランチ':<40}  タイトル")
    print("-" * 110)

    for issue_number, state in rows:
        status = str(state.get("status", ""))
        if args.status and status != args.status:
            continue
        print(
            f"{('#' + str(issue_number)):>6}  "
            f"{statefile.label(status):<12}  "
            f"{str(state.get('branch', '-'))[:40]:<40}  "
            f"{str(state.get('issueTitle', ''))[:40]}"
        )

    print()
    active = statefile.active_issue_numbers()
    print(f"処理中: {len(active)} 件 / 同時実行上限: {CONFIG.max_parallel}")
    return 0


def command_show(args) -> int:
    state = statefile.load(args.issue)
    if not state:
        print(f"Issue #{args.issue} のstateがありません。")
        return 1

    for key, value in state.items():
        if key == "history":
            continue
        print(f"{key:<24}: {value}")

    print("\n--- 履歴 ---")
    for entry in state.get("history", []):
        print(f"{entry.get('at', '')}  {statefile.label(entry.get('status', ''))}")

    return 0


def command_retry(args) -> int:
    state = statefile.load(args.issue)
    if not state:
        # rejected/completed は state/done/ へ退避されているので、
        # まずそちらからの復元を試みる（却下されたIssueはGitHub上では
        # openのまま残る設計なので、こちらで再着手できないと片手落ちになる）。
        if statefile.restore_from_archive(args.issue):
            print(f"Issue #{args.issue} のstateを archive から復元しました。")
            state = statefile.load(args.issue)
        else:
            print(f"Issue #{args.issue} のstateがありません。")
            return 1

    statefile.update(
        args.issue,
        {
            "status": statefile.Status.CREATED,
            "error": "",
            "retriedAt": now_iso(),
        },
    )
    print(f"Issue #{args.issue} を着手待ちへ戻しました。")
    return 0


def command_decision(args, approve: bool) -> int:
    command = [
        sys.executable,
        str(SCRIPT_DIR / "finish_agent.py"),
        str(args.issue),
        "--approve" if approve else "--reject",
    ]
    return subprocess.call(command, cwd=str(SCRIPT_DIR))


def command_rework(args) -> int:
    """
    修正指示を出して再実装させる。

    Teamsの承認カードで「再実装」を押したときに動く処理
    （approval_agent.handle_rework）をそのまま呼び出す。
    そのため、承認カードが既に無い状態（failed等）からでも、
    同じブランチ・同じworktreeでClineへ修正指示を再送できる。
    """
    from approval_agent import handle_rework

    handle_rework(args.issue, args.comment)

    print(f"Issue #{args.issue} を再実装待ち(rework)へ戻しました。")
    print("orchestratorが起動していれば、自動的に拾われて再開します。")
    print("起動していなければ、次で単体実行できます:")
    print(
        f"  python orchestrator.py --once --min-issue {args.issue} "
        f"--max-issue {args.issue}"
    )
    return 0


def command_unlock(args) -> int:
    """
    残存ロックを掃除する。

    ロックのPIDは、そのロックを作ったホスト上でのみ意味を持つ。
    別ホストが持つロックは、このマシンからは生死を判定できないため
    （PIDの番号空間がホストごとに独立している）、--force を付けない限り
    自動では削除しない。同一ホストのロックだけ、通常どおり生存確認する。
    """
    removed = 0
    here = _current_host()

    for path in sorted(CONFIG.locks_dir.glob("*.lock")):
        owner = lock_info(path)
        pid = int(owner.get("pid", -1) or -1)
        host = str(owner.get("host", "")).strip()

        if host and host != here:
            if not args.force:
                print(f"別ホストのロックのためスキップ: {path.name} (host={host}, PID={pid})")
                continue
            print(f"別ホストのロックを強制削除: {path.name} (host={host}, PID={pid})")
        else:
            alive = _process_alive(pid) if pid > 0 else False
            if alive and not args.force:
                print(f"使用中のためスキップ: {path.name} (PID={pid})")
                continue

        path.unlink(missing_ok=True)
        removed += 1
        print(f"削除: {path.name}")

    print(f"{removed} 件のロックを削除しました。")
    return 0


def command_clean_worktrees(args) -> int:
    gitops.git("worktree", "prune", check=False)
    result = gitops.git("worktree", "list", check=False)
    print(result.stdout)

    if not args.remove_completed:
        print("完了済みIssueのworktreeも消す場合は --remove-completed を付けてください。")
        return 0

    for path in sorted(CONFIG.worktree_root.glob("issue-*")):
        try:
            issue_number = int(path.name.split("-", 1)[1])
        except (IndexError, ValueError):
            continue

        status = statefile.status_of(issue_number)
        if status in statefile.TERMINAL_STATUSES or not status:
            gitops.remove_worktree(issue_number, logger=LOGGER)

    return 0


def command_doctor(args) -> int:
    print()
    print("=" * 60)
    print("環境点検")
    print("=" * 60)

    ok = True

    # AGENT_ROOT / TARGET_REPO_PATH が未設定だと pathlib.Path("") が
    # 暗黙的にカレントディレクトリを指してしまい、以降のフォルダ・
    # リポジトリ存在チェックが「たまたま今いる場所に何かがある」ことを
    # 理由に誤って[OK]になりうる。ここで先に明示的に検出する。
    print("[必須環境変数]")
    for name in ("GITHUB_TOKEN", "GITHUB_OWNER", "GITHUB_REPO", "AGENT_ROOT", "TARGET_REPO_PATH"):
        value = os.environ.get(name, "").strip()
        if value:
            shown = f"{value[:4]}{'*' * 8}" if "TOKEN" in name else value
            print(f"[OK] {name} = {shown}")
        else:
            print(f"[NG] {name} が未設定です")
            ok = False

    if not ok:
        print()
        print(
            "必須環境変数が未設定のままです。この状態で他のコマンドを実行すると、"
        )
        print(
            f"カレントディレクトリ（{Path('.').resolve()}）の直下に"
        )
        print("誤ってフォルダを作ってしまう可能性があります。")
        print("scripts\\setup.ps1 を実行し、PowerShellを開き直してください。")
        print()
        print("=" * 60)
        print("点検結果: 要対応（必須環境変数が未設定）")
        print("=" * 60)
        return 1

    print()
    for command in ("git", "gh", "code", "python"):
        found = shutil.which(command) or shutil.which(f"{command}.cmd")
        print(f"{'[OK]' if found else '[NG]'} コマンド {command}: {found or '未検出'}")
        if command in ("git", "gh") and not found:
            ok = False

    for module in ("requests", "watchdog", "bs4", "pyautogui", "pyperclip"):
        try:
            __import__(module)
            print(f"[OK] Pythonモジュール {module}")
        except ImportError:
            required = module in ("requests", "watchdog")
            print(f"{'[NG]' if required else '[--]'} Pythonモジュール {module}: 未インストール")
            if required:
                ok = False

    print()
    for name in ("request", "question", "decision", "waiting", "approval", "reply", "state"):
        directory = getattr(CONFIG, f"{name}_dir")
        done = directory / "done"
        exists = directory.exists() and done.exists()
        print(f"{'[OK]' if exists else '[NG]'} {name:<9}: {directory}")
        if not exists:
            ok = False

    print()
    print(f"{'[OK]' if (CONFIG.target_repo / '.git').exists() else '[NG]'} 本体リポジトリ: {CONFIG.target_repo}")
    print(f"{'[OK]' if CONFIG.worktree_root.exists() else '[NG]'} worktree置き場: {CONFIG.worktree_root}")
    print(f"{'[OK]' if ghcli.auth_status() else '[NG]'} gh 認証")

    print()
    print("=" * 60)
    print("点検結果: " + ("問題なし" if ok else "要対応"))
    print("=" * 60)
    return 0 if ok else 1


def command_test_click(args) -> int:
    cline.test_click(LOGGER)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="AIエージェント運用コマンド")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("status", help="状態一覧")
    p.add_argument("--status", default="", help="状態で絞り込む")
    p.set_defaults(func=command_status)

    p = sub.add_parser("show", help="Issueの詳細")
    p.add_argument("issue", type=int)
    p.set_defaults(func=command_show)

    p = sub.add_parser("retry", help="着手待ちへ戻す")
    p.add_argument("issue", type=int)
    p.set_defaults(func=command_retry)

    p = sub.add_parser("approve", help="手動承認")
    p.add_argument("issue", type=int)
    p.set_defaults(func=lambda a: command_decision(a, True))

    p = sub.add_parser("reject", help="手動却下")
    p.add_argument("issue", type=int)
    p.set_defaults(func=lambda a: command_decision(a, False))

    p = sub.add_parser("rework", help="修正指示を出して再実装させる（同じブランチ・worktreeで再開）")
    p.add_argument("issue", type=int)
    p.add_argument("comment", help="Clineへの修正指示（そのままプロンプトに使われます）")
    p.set_defaults(func=command_rework)

    p = sub.add_parser("unlock", help="ロックの掃除")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=command_unlock)

    p = sub.add_parser("clean-worktrees", help="worktreeの掃除")
    p.add_argument("--remove-completed", action="store_true")
    p.set_defaults(func=command_clean_worktrees)

    p = sub.add_parser("doctor", help="環境点検")
    p.set_defaults(func=command_doctor)

    p = sub.add_parser("test-click", help="Cline入力欄の座標確認")
    p.set_defaults(func=command_test_click)

    args = parser.parse_args()

    if args.command == "doctor":
        # doctorは「設定が壊れている状態」を見るための診断コマンド。
        # ここでvalidate()（未設定なら強制終了）やensure_directories()
        # （フォルダを実行前に作ってしまう）を先に通すと、
        # 診断そのものが機能しなくなる（常に[OK]に見えてしまう）ため、
        # 唯一この2つを呼ばずにそのまま実行する。
        return args.func(args)

    CONFIG.validate()
    CONFIG.ensure_directories()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
