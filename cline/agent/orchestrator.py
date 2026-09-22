"""
[工程2] オーケストレーター（並走制御）

state フォルダを監視し、status が created / rework のIssueを
implement_agent.py の子プロセスへ割り当てる。

並走設計:
- Issueごとに独立した git worktree を使うため、同時実行できる
- 同時実行数は AGENT_MAX_PARALLEL で制限する
- Clineへの貼り付け（GUI操作）だけは implement_agent 側で
  GUIロックにより1件ずつ直列化される
- 同一Issueの二重起動は issue lock で防ぐ
"""

from __future__ import annotations

import argparse
import os
import pathlib
import signal
import subprocess
import sys
import time

from agent_core import statefile
from agent_core.config import CONFIG
from agent_core.gitops import require_command
from agent_core.jsonio import now_iso
from agent_core.locks import daemon_lock_path, issue_lock_path, release, try_acquire
from agent_core.logs import get_logger, print_banner

LOGGER = get_logger("orchestrator")

POLL_INTERVAL_SECONDS = 5
SCRIPT_DIR = pathlib.Path(__file__).resolve().parent

_shutdown = False


class Worker:
    def __init__(self, issue_number: int, process: subprocess.Popen, lock_path: pathlib.Path):
        self.issue_number = issue_number
        self.process = process
        self.lock_path = lock_path
        self.started_at = time.time()

    @property
    def elapsed_minutes(self) -> float:
        return (time.time() - self.started_at) / 60


def handle_shutdown(signum, frame) -> None:
    del signum, frame
    global _shutdown
    _shutdown = True
    LOGGER.info("終了要求を受け付けました。実行中の処理の完了を待ちます。")


def dispatchable_issues(min_issue: int, max_issue: int | None) -> list[tuple[int, dict]]:
    """着手対象のIssueを番号順に返す。"""
    results = []

    for issue_number, state in statefile.iter_states():
        if issue_number < min_issue:
            continue
        if max_issue is not None and issue_number > max_issue:
            continue

        status = str(state.get("status", "")).strip().lower()
        if status in statefile.DISPATCHABLE_STATUSES:
            results.append((issue_number, state))

    return results


def spawn_worker(
    issue_number: int,
    mode: str,
    no_vscode: bool,
    manual: bool,
) -> Worker | None:
    """implement_agent.py を子プロセスとして起動する。"""
    lock_path = issue_lock_path(issue_number)

    if not try_acquire(lock_path, note=f"orchestrator-{os.getpid()}"):
        LOGGER.info(f"Issue #{issue_number} は他プロセスが処理中のためスキップします。")
        return None

    command = [
        sys.executable,
        str(SCRIPT_DIR / "implement_agent.py"),
        str(issue_number),
        "--mode",
        mode,
        "--locked-by-orchestrator",
    ]
    if no_vscode:
        command.append("--no-vscode")
    if manual:
        command.append("--manual")

    statefile.update(
        issue_number,
        {
            "status": statefile.Status.QUEUED,
            "orchestratorPid": os.getpid(),
            "dispatchedAt": now_iso(),
        },
    )

    LOGGER.info(f"Issue #{issue_number} を起動します（mode={mode}）。")
    LOGGER.info(f"> {subprocess.list2cmdline(command)}")

    try:
        process = subprocess.Popen(command, cwd=str(SCRIPT_DIR))
    except OSError as error:
        release(lock_path)
        statefile.update(
            issue_number,
            {"status": statefile.Status.FAILED, "error": f"起動失敗: {error}"},
        )
        LOGGER.error(f"implement_agent.py を起動できません: {error}")
        return None

    return Worker(issue_number, process, lock_path)


def reap_workers(workers: list[Worker]) -> list[Worker]:
    """終了した子プロセスを回収する。"""
    alive: list[Worker] = []

    for worker in workers:
        code = worker.process.poll()
        if code is None:
            alive.append(worker)
            continue

        release(worker.lock_path)
        status = statefile.status_of(worker.issue_number)

        if code == 0:
            LOGGER.info(
                f"Issue #{worker.issue_number} のワーカーが正常終了しました "
                f"({worker.elapsed_minutes:.1f}分 / 状態={statefile.label(status)})"
            )
        else:
            LOGGER.error(
                f"Issue #{worker.issue_number} のワーカーが異常終了しました "
                f"(exit={code})"
            )
            if status not in statefile.TERMINAL_STATUSES:
                statefile.update(
                    worker.issue_number,
                    {
                        "status": statefile.Status.FAILED,
                        "error": f"implement_agent exit code {code}",
                    },
                )

    return alive


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "stateを監視し、created/reworkのIssueを"
            "implement_agent.pyへ並走で割り当てます。"
        )
    )
    parser.add_argument("--interval", type=int, default=POLL_INTERVAL_SECONDS)
    parser.add_argument(
        "--parallel",
        type=int,
        default=CONFIG.max_parallel,
        help=f"同時実行数 (既定: {CONFIG.max_parallel})",
    )
    parser.add_argument("--min-issue", type=int, default=0)
    parser.add_argument("--max-issue", type=int, default=None)
    parser.add_argument("--no-vscode", action="store_true", help="VS Code起動を省略する")
    parser.add_argument("--manual", action="store_true", help="プロンプトを手動貼り付けにする")
    parser.add_argument("--once", action="store_true", help="1件処理して終了する")
    parser.add_argument("--dry-run", action="store_true", help="対象表示のみ")
    args = parser.parse_args()

    CONFIG.validate()
    CONFIG.ensure_directories()
    require_command("git")
    require_command("gh")

    parallel = max(1, args.parallel)

    lock = daemon_lock_path("orchestrator")
    if not try_acquire(lock, note="orchestrator"):
        LOGGER.fail("orchestrator.py は既に起動しています。")

    signal.signal(signal.SIGINT, handle_shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_shutdown)

    print_banner(
        "工程2: オーケストレーター（並走制御）",
        {
            "state": CONFIG.state_dir,
            "worktree": CONFIG.worktree_root,
            "本体リポジトリ": CONFIG.repository,
            "検証リポジトリ": CONFIG.beta_repository,
            "同時実行数": parallel,
            "監視間隔": f"{args.interval}秒",
            "対象Issue": f"#{args.min_issue} 〜 "
            + (f"#{args.max_issue}" if args.max_issue else "上限なし"),
        },
    )

    workers: list[Worker] = []

    try:
        while True:
            workers = reap_workers(workers)

            if _shutdown:
                if not workers:
                    break
                time.sleep(2)
                continue

            running = {worker.issue_number for worker in workers}
            free_slots = parallel - len(workers)

            if free_slots > 0:
                for issue_number, state in dispatchable_issues(
                    args.min_issue, args.max_issue
                ):
                    if free_slots <= 0:
                        break
                    if issue_number in running:
                        continue

                    status = str(state.get("status", "")).strip().lower()
                    mode = "rework" if status == statefile.Status.REWORK else "new"

                    if args.dry_run:
                        LOGGER.info(
                            f"[dry-run] Issue #{issue_number} "
                            f"({statefile.label(status)}) を起動対象として検出"
                        )
                        continue

                    worker = spawn_worker(
                        issue_number, mode, args.no_vscode, args.manual
                    )
                    if worker is not None:
                        workers.append(worker)
                        running.add(issue_number)
                        free_slots -= 1

                        if args.once:
                            _wait_all(workers)
                            return

            if args.dry_run and args.once:
                return

            time.sleep(args.interval)
    finally:
        _wait_all(workers)
        release(lock)
        LOGGER.info("オーケストレーターを終了しました。")


def _wait_all(workers: list[Worker]) -> None:
    for worker in workers:
        try:
            worker.process.wait(timeout=7200)
        except subprocess.TimeoutExpired:
            LOGGER.warn(f"Issue #{worker.issue_number} のワーカーが応答しません。")
        finally:
            release(worker.lock_path)


if __name__ == "__main__":
    sys.exit(main())
