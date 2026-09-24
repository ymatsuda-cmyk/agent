"""
[補助] Issue状態ダッシュボード

state/ フォルダを監視し、変化のたびに（ただし最短間隔を空けて）
Issueの生データをJSONとして tools-beta（GitHub Pages）へ公開する。
表示側は静的なHTML/JSアプリで、起動時と定期的にこのJSONを取得して
ブラウザ側でカンバン表示を組み立てる（サーバー側は色分け等の
見た目のロジックを一切持たない）。

    データ: tools-beta の data/agent/dashboard.json
    表示  : tools-beta の utility/agent.html
    URL   : https://<owner>.github.io/<beta-repo>/utility/agent.html

この分離により、utility/agent.html は初回作成後ほぼ固定になり、
以降のpushはJSONの差分だけになる（コミットが軽い）。
既存の `data/minutes/index.json` と同じ「data/<用途>/にJSONを置く」
という、このリポジトリ自体の慣習に合わせている。

deploy_preview.py と同じ tools-beta クローン・同じロックを使うため、
プレビュー公開とこのダッシュボード更新が競合しない。

注意: tools-beta が公開リポジトリの場合、このURLを知っていれば
誰でもIssueのタイトルや状態を閲覧できる（既存のプレビューURLと
同じ公開範囲）。社外に見せたくない場合は tools-beta を非公開にするか、
このスクリプト自体を使わない（案A・Bだけに留める）こと。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from agent_core import statefile
from agent_core.config import CONFIG
from agent_core.jsonio import read_json_when_ready
from agent_core.locks import acquire_or_wait, daemon_lock_path, release, try_acquire
from agent_core.logs import get_logger, print_banner

from deploy_preview import (
    BETA_LOCK_TIMEOUT_SECONDS,
    beta_lock_path,
    commit_and_push,
    ensure_beta_repo,
)

LOGGER = get_logger("dashboard_agent")

DATA_DIR_NAME = "data/agent"
DATA_FILENAME = "dashboard.json"
APP_DIR_NAME = "utility"
APP_FILENAME = "agent.html"

MIN_PUSH_INTERVAL_SECONDS = 30
FORCE_REFRESH_SECONDS = 300  # 変化イベントを取りこぼしても5分ごとに必ず更新する
DONE_ISSUES_SHOWN = 20  # 終了済みIssueは直近分だけ表示する（肥大化防止）


# ============================================================
# データ収集
# ============================================================

def _collect_rows() -> list[tuple[int, dict]]:
    """未終了のIssue全件 + 終了済みIssueの直近分をまとめて返す。"""
    rows = statefile.iter_states()  # state/直下(未終了。failedもここに残る)

    done_dir = CONFIG.state_dir / "done"
    if done_dir.exists():
        done_paths = sorted(
            done_dir.glob("issue-*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:DONE_ISSUES_SHOWN]

        for path in done_paths:
            data = read_json_when_ready(path)
            if not data:
                continue
            try:
                number = int(data.get("issueNumber"))
            except (TypeError, ValueError):
                continue
            rows.append((number, data))

    return rows


# ============================================================
# 時刻表示（generatedAtの付与にのみ使用。elapsed計算等はJS側で行う）
# ============================================================

def _now_aware() -> datetime:
    return datetime.now().astimezone()


# ============================================================
# JSON組み立て
# ============================================================

def build_dashboard_json(rows: list[tuple[int, dict]]) -> dict:
    """
    Issue一覧から公開用JSONを組み立てる。

    フェーズ分類・色分け・経過時間の表示・放置警告は、すべて
    utility/agent.html 側のJavaScriptで行う。ここでは生データを
    渡すだけにする（サーバー側とクライアント側でロジックが
    二重管理になるのを避けるため）。
    """
    issues = []
    for issue_number, data in sorted(rows, key=lambda item: item[0]):
        issues.append(
            {
                "number": issue_number,
                "title": str(data.get("issueTitle") or "(タイトルなし)"),
                "status": str(data.get("status", "")).strip().lower(),
                "updatedAt": data.get("updatedAt"),
                "issueUrl": data.get("issueUrl"),
                "pullRequestUrl": data.get("pullRequestUrl"),
                "previewUrl": data.get("previewUrl"),
            }
        )

    return {
        "generatedAt": _now_aware().isoformat(timespec="seconds"),
        "issues": issues,
    }


# ============================================================
# 静的アプリ（utility/agent.html）
#
# データパス "../data/agent/dashboard.json" は DATA_DIR_NAME/DATA_FILENAME と
# 手動で対応させている（このテンプレートは文字列埋め込みをしていないため、
# 定数を変更した場合はここも合わせて直すこと）。
# ============================================================

AGENT_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Issue状態ダッシュボード</title>
<style>
* { box-sizing: border-box; }
body { margin:0; font-family: -apple-system, "Segoe UI", "Hiragino Sans", "Noto Sans JP", Meiryo, sans-serif;
  background:#F5F4F0; color:#2C2C2A; }
header { padding:16px 20px 8px; }
h1 { font-size:18px; font-weight:500; margin:0; }
.updated { font-size:12px; color:#5F5E5A; margin:4px 0 0; }
.error { font-size:12px; color:#791F1F; margin:4px 0 0; }
.board { display:grid; grid-template-columns:repeat(4,1fr); gap:12px; padding:8px 20px 24px; }
@media (max-width:640px) { .board { grid-template-columns:1fr; } }
.column-title { font-size:13px; font-weight:500; color:#5F5E5A; margin-bottom:8px; }
.count { color:#888780; font-weight:400; }
.card { border-radius:0 8px 8px 0; border-left:3px solid; padding:10px 12px; margin-bottom:8px; font-size:13px; }
.card-title { font-weight:500; }
.card-status { font-size:11px; margin-top:4px; }
.links { font-size:11px; margin-top:6px; }
.links a { color:#0C447C; text-decoration:none; margin-right:8px; }
.empty { font-size:12px; color:#888780; padding:8px 0; }
.warn { color:#791F1F; font-weight:500; }
</style>
</head>
<body>
<header>
  <h1>Issue状態ダッシュボード</h1>
  <p class="updated" id="updated">読み込み中...</p>
  <p class="error" id="error" style="display:none;"></p>
</header>
<main class="board" id="board"></main>

<script>
const DATA_URL = "../data/agent/dashboard.json";
const POLL_INTERVAL_MS = 30000;

const PHASES = [
  ["待機中", ["created", "queued", "rework"]],
  ["実装中", ["implementing", "waiting_decision", "preview_deploying"]],
  ["承認待ち", ["waiting_approval", "approved"]],
  ["終了", ["completed", "rejected", "failed"]]
];

const STATUS_COLORS = {
  created:           ["#F1EFE8", "#888780", "#5F5E5A"],
  queued:            ["#F1EFE8", "#888780", "#5F5E5A"],
  rework:            ["#FAEEDA", "#BA7517", "#633806"],
  implementing:      ["#E6F1FB", "#378ADD", "#0C447C"],
  waiting_decision:  ["#FAEEDA", "#BA7517", "#633806"],
  preview_deploying: ["#E6F1FB", "#378ADD", "#0C447C"],
  waiting_approval:  ["#FAEEDA", "#BA7517", "#633806"],
  approved:          ["#E6F1FB", "#378ADD", "#0C447C"],
  completed:         ["#EAF3DE", "#639922", "#173404"],
  rejected:          ["#F1EFE8", "#888780", "#5F5E5A"],
  failed:            ["#FCEBEB", "#E24B4A", "#791F1F"]
};
const DEFAULT_COLOR = STATUS_COLORS.created;

const STUCK_THRESHOLD_SECONDS = {
  waiting_approval: 2 * 3600,
  waiting_decision: 1 * 3600
};

function phaseOf(status) {
  for (const [name, statuses] of PHASES) {
    if (statuses.includes(status)) return name;
  }
  return PHASES[PHASES.length - 1][0];
}

function formatElapsed(isoString) {
  if (!isoString) return { label: "", seconds: null };
  const parsed = new Date(isoString);
  if (isNaN(parsed.getTime())) return { label: "", seconds: null };

  let seconds = Math.max(0, Math.floor((Date.now() - parsed.getTime()) / 1000));
  if (seconds < 60) return { label: seconds + "秒前", seconds };
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return { label: minutes + "分前", seconds };
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return { label: hours + "時間" + (minutes % 60) + "分前", seconds };
  const days = Math.floor(hours / 24);
  return { label: days + "日前", seconds };
}

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = String(value);
  return div.innerHTML;
}

function buildCard(issue) {
  const status = (issue.status || "").toLowerCase();
  const [bg, border, textColor] = STATUS_COLORS[status] || DEFAULT_COLOR;
  const elapsed = formatElapsed(issue.updatedAt);
  const threshold = STUCK_THRESHOLD_SECONDS[status];
  const isStuck = threshold != null && elapsed.seconds != null && elapsed.seconds >= threshold;

  let statusLine = escapeHtml(status);
  if (elapsed.label) statusLine += " ・ " + escapeHtml(elapsed.label);
  if (isStuck) statusLine += ' <span class="warn">⚠ 放置</span>';

  const links = [];
  if (issue.issueUrl) links.push('<a href="' + escapeHtml(issue.issueUrl) + '" target="_blank" rel="noopener">Issue</a>');
  if (issue.pullRequestUrl) links.push('<a href="' + escapeHtml(issue.pullRequestUrl) + '" target="_blank" rel="noopener">PR</a>');
  if (issue.previewUrl) links.push('<a href="' + escapeHtml(issue.previewUrl) + '" target="_blank" rel="noopener">プレビュー</a>');
  const linksHtml = links.length ? '<div class="links">' + links.join("") + "</div>" : "";

  return (
    '<div class="card" style="background:' + bg + ';border-left-color:' + border + ';">' +
    '<div class="card-title" style="color:' + textColor + ';">#' + issue.number + " " + escapeHtml(issue.title) + "</div>" +
    '<div class="card-status" style="color:' + border + ';">' + statusLine + "</div>" +
    linksHtml +
    "</div>"
  );
}

function render(data) {
  const groups = {};
  for (const [name] of PHASES) groups[name] = [];
  for (const issue of data.issues || []) {
    groups[phaseOf((issue.status || "").toLowerCase())].push(issue);
  }

  const board = document.getElementById("board");
  board.innerHTML = "";

  for (const [name] of PHASES) {
    const items = groups[name];
    const column = document.createElement("div");
    const cardsHtml = items.length
      ? items.map(buildCard).join("")
      : '<div class="empty">なし</div>';
    column.innerHTML =
      '<div class="column-title">' + escapeHtml(name) +
      ' <span class="count">' + items.length + "</span></div>" +
      cardsHtml;
    board.appendChild(column);
  }

  const generated = data.generatedAt ? new Date(data.generatedAt) : null;
  document.getElementById("updated").textContent =
    "最終更新: " + (generated ? generated.toLocaleString("ja-JP") : "不明") +
    "（" + Math.round(POLL_INTERVAL_MS / 1000) + "秒ごとに自動更新）";
  document.getElementById("error").style.display = "none";
}

async function load() {
  try {
    const response = await fetch(DATA_URL + "?t=" + Date.now(), { cache: "no-store" });
    if (!response.ok) throw new Error("HTTP " + response.status);
    const data = await response.json();
    render(data);
  } catch (error) {
    document.getElementById("error").textContent = "データの取得に失敗しました: " + error.message;
    document.getElementById("error").style.display = "block";
  }
}

load();
setInterval(load, POLL_INTERVAL_MS);
</script>
</body>
</html>
"""


def ensure_agent_html(beta_repo) -> bool:
    """
    utility/agent.html が無ければ作成する。

    既にある場合は上書きしない（手で調整されている可能性があるため）。
    作成した場合は True を返す。
    """
    app_dir = beta_repo / APP_DIR_NAME
    app_dir.mkdir(parents=True, exist_ok=True)
    app_path = app_dir / APP_FILENAME

    if app_path.exists():
        return False

    app_path.write_text(AGENT_HTML_TEMPLATE, encoding="utf-8")
    return True


# ============================================================
# 公開
# ============================================================

def publish_once(logger=LOGGER) -> bool:
    """1回分の生成とpushを行う。呼び出し側でtools-betaロックを取得済みであること。"""
    beta_repo = ensure_beta_repo()

    data_dir = beta_repo / DATA_DIR_NAME
    data_dir.mkdir(parents=True, exist_ok=True)

    rows = _collect_rows()
    payload = build_dashboard_json(rows)
    (data_dir / DATA_FILENAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    app_created = ensure_agent_html(beta_repo)
    if app_created:
        logger.info(f"{APP_DIR_NAME}/{APP_FILENAME} を新規作成しました。")

    message = f"dashboard: update data ({len(rows)} issues)"
    if app_created:
        message = f"dashboard: add {APP_FILENAME} + update data ({len(rows)} issues)"

    pushed = commit_and_push(beta_repo, message)
    logger.info(f"ダッシュボードを{'更新' if pushed else '確認'}しました（{len(rows)}件）。")
    return pushed


def publish_with_lock(logger=LOGGER) -> bool:
    lock = beta_lock_path()
    acquired = acquire_or_wait(
        lock,
        timeout_seconds=BETA_LOCK_TIMEOUT_SECONDS,
        on_wait=lambda owner: logger.info(
            f"tools-betaロック待機中（使用中: {owner.get('host') or '不明'}）"
        ),
    )
    if not acquired:
        logger.warn("tools-betaロックが取得できず、今回の更新をスキップします。")
        return False

    try:
        return publish_once(logger)
    finally:
        release(lock)


# ============================================================
# 監視ループ
# ============================================================

class StateChangeHandler(FileSystemEventHandler):
    """state/ 配下の変化を検知するたびに dirty フラグを立てるだけの軽量ハンドラ。"""

    def __init__(self) -> None:
        self.dirty = True  # 起動直後は必ず1回発行する

    def _mark(self, event) -> None:
        if event.is_directory:
            return
        if str(event.src_path).endswith(".json"):
            self.dirty = True

    def on_created(self, event) -> None:
        self._mark(event)

    def on_modified(self, event) -> None:
        self._mark(event)

    def on_moved(self, event) -> None:
        self._mark(event)

    def on_deleted(self, event) -> None:
        self._mark(event)


def run_watch_loop(interval: int, logger=LOGGER) -> None:
    handler = StateChangeHandler()
    observer = Observer()
    observer.schedule(handler, str(CONFIG.state_dir), recursive=True)
    observer.start()
    logger.info(f"{CONFIG.state_dir} の変化を監視します。終了するには Ctrl+C を押してください。")

    last_pushed_at = 0.0

    try:
        while True:
            now = time.time()
            due_by_interval = now - last_pushed_at >= interval
            due_by_force = now - last_pushed_at >= FORCE_REFRESH_SECONDS

            if (handler.dirty or due_by_force) and due_by_interval:
                handler.dirty = False
                try:
                    publish_with_lock(logger)
                except Exception as error:  # 1回の失敗で監視自体は止めない
                    logger.error(f"ダッシュボード更新に失敗しました: {error}")
                last_pushed_at = time.time()

            time.sleep(2)
    finally:
        observer.stop()
        observer.join()


# ============================================================
# エントリポイント
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Issue状態ダッシュボードをtools-beta(GitHub Pages)へ公開し続けます。"
    )
    parser.add_argument(
        "--interval", type=int, default=MIN_PUSH_INTERVAL_SECONDS,
        help=f"最短更新間隔・秒（既定: {MIN_PUSH_INTERVAL_SECONDS}）",
    )
    parser.add_argument("--once", action="store_true", help="1回だけ生成・pushして終了する")
    args = parser.parse_args()

    CONFIG.validate()
    CONFIG.ensure_directories()

    lock = daemon_lock_path("dashboard_agent")
    if not try_acquire(lock, note="dashboard_agent"):
        LOGGER.fail("dashboard_agent.py は既に起動しています。")

    url = f"{CONFIG.pages_base_url}/{APP_DIR_NAME}/{APP_FILENAME}"
    print_banner(
        "補助: Issue状態ダッシュボード",
        {
            "データ公開先": f"tools-betaリポジトリの {DATA_DIR_NAME}/{DATA_FILENAME}",
            "表示アプリ": f"tools-betaリポジトリの {APP_DIR_NAME}/{APP_FILENAME}",
            "URL": url,
            "更新間隔": f"最短{args.interval}秒 / 最長{FORCE_REFRESH_SECONDS}秒",
        },
    )
    LOGGER.info(f"外出先からもこのURLで確認できます: {url}")

    try:
        if args.once:
            publish_with_lock(LOGGER)
            return 0

        run_watch_loop(args.interval, LOGGER)
        return 0
    finally:
        release(lock)
        LOGGER.info("dashboard_agent を終了しました。")


if __name__ == "__main__":
    sys.exit(main())
