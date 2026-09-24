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
import re
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
            # state/done/ にあるものは、status の文字列が何であれ
            # オーケストレーターの対象外（退避済み）。表示側で「対応待ち」等の
            # 列に出さないよう印を付ける。
            rows.append((number, {**data, "archived": True}))

    return rows


# ============================================================
# 時刻表示（generatedAt等の付与にのみ使用。経過時間の計算はJS側で行う）
# ============================================================

def _now_aware() -> datetime:
    return datetime.now().astimezone()


# ============================================================
# JSON組み立て
# ============================================================

def _pending_notifications(issue_number: int) -> dict:
    """
    reply/ に残っている（Power Automate④がまだTeamsへ送っていない）通知を数える。

    フロー④は投稿後に reply/done/ へ移動するため、reply/ 直下に古い
    ファイルが残っていれば「Power Automate待ち」と判断できる。
    何分で「待ち」とみなすかの閾値は表示側（agent.html）で判断する。

    質問カード（②）・承認カード（③）については、Teamsへ投稿済みかどうかを
    ファイルから観測する手段が無いため、ここでは扱わない。
    """
    if not CONFIG._agent_root_was_set:
        return {"count": 0, "oldestAt": None}

    mtimes: list[float] = []
    for path in CONFIG.reply_dir.glob(f"issue-{issue_number}-*.json"):
        try:
            mtimes.append(path.stat().st_mtime)
        except OSError:
            continue  # globとstatの間にフロー④が移動した場合

    if not mtimes:
        return {"count": 0, "oldestAt": None}

    oldest = datetime.fromtimestamp(min(mtimes)).astimezone()
    return {"count": len(mtimes), "oldestAt": oldest.isoformat(timespec="seconds")}


def build_dashboard_json(rows: list[tuple[int, dict]]) -> dict:
    """
    Issue一覧から公開用JSONを組み立てる。

    担当（人/Cline/Power Automate/システム）の分類・並び替え・色分け・
    経過時間・放置警告は、すべて utility/agent.html 側のJavaScriptで行う。
    ここでは判断材料となる生データだけを渡す。
    """
    issues = []
    for issue_number, data in sorted(rows, key=lambda item: item[0]):
        status = str(data.get("status", "")).strip().lower()
        issues.append(
            {
                "number": issue_number,
                "title": str(data.get("issueTitle") or "(タイトルなし)"),
                "status": status,
                "statusLabel": statefile.label(status),
                "error": str(data.get("error") or ""),
                "updatedAt": data.get("updatedAt"),
                "issueUrl": data.get("issueUrl"),
                "pullRequestUrl": data.get("pullRequestUrl"),
                "previewUrl": data.get("previewUrl"),
                "pullRequestNumber": data.get("pullRequestNumber"),
                "branch": str(data.get("branch") or ""),
                "pendingNotifications": _pending_notifications(issue_number),
                "archived": bool(data.get("archived")),
            }
        )

    return {
        "generatedAt": _now_aware().isoformat(timespec="seconds"),
        "repository": CONFIG.repository,
        "issues": issues,
    }


# ============================================================
# 静的アプリ（utility/agent.html）
#
# データパス "../data/agent/dashboard.json" は DATA_DIR_NAME/DATA_FILENAME と
# 手動で対応させている（このテンプレートは文字列埋め込みをしていないため、
# 定数を変更した場合はここも合わせて直すこと）。
#
# テンプレートを変更したら AGENT_HTML_VERSION を上げること。
# 公開済みの自動生成版は、次回の更新時に自動で置き換わる。
# ============================================================

AGENT_HTML_VERSION = 3
_VERSION_META_NAME = "agent-dashboard-version"

AGENT_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="agent-dashboard-version" content="3">
<title>Issue状態ダッシュボード</title>
<style>
* { box-sizing: border-box; }
body { margin:0; font-family: -apple-system, "Segoe UI", "Hiragino Sans", "Noto Sans JP", Meiryo, sans-serif;
  background:#F5F4F0; color:#2C2C2A; }
header { padding:16px 20px 4px; }
h1 { font-size:18px; font-weight:500; margin:0; }
h2 { font-size:14px; font-weight:500; margin:0 0 8px; color:#5F5E5A; }
.updated { font-size:12px; color:#5F5E5A; margin:4px 0 0; }
.error { font-size:12px; color:#791F1F; margin:4px 0 0; }
.legend { display:flex; flex-wrap:wrap; gap:6px 18px; font-size:12px; color:#5F5E5A; padding:8px 20px 4px; }
.legend span { display:inline-flex; align-items:center; gap:6px; }
.dot { width:10px; height:10px; border-radius:50%; display:inline-block; }
.board { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; padding:8px 20px 16px; }
@media (max-width:760px) { .board { grid-template-columns:1fr; } }
.column-title { font-size:13px; font-weight:500; margin-bottom:8px; }
.count { color:#888780; font-weight:400; }
.card { border-left:3px solid; padding:10px 12px; margin-bottom:8px; font-size:13px; background:#FFFFFF; }
.card-title { font-weight:500; }
.card-note { font-size:12px; margin-top:4px; }
.card-meta { font-size:11px; color:#888780; margin-top:4px; }
.links { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }
.links a { font-size:12px; color:#0C447C; text-decoration:none; background:#FFFFFF;
  border:0.5px solid #B5D4F4; border-radius:6px; padding:3px 8px; }
.links a:hover { background:#E6F1FB; }
.links a.search { color:#5F5E5A; border-color:#D3D1C7; }
.empty { font-size:12px; color:#888780; padding:8px 0; }
.warn { color:#A32D2D; font-weight:500; }
.done { padding:0 20px 24px; }
.done-row { font-size:13px; padding:8px 0; border-bottom:0.5px solid #D3D1C7; }
.done-head { display:flex; justify-content:space-between; gap:12px; }
.done-row .links { margin-top:4px; }
.done-row .meta { font-size:12px; color:#888780; white-space:nowrap; }
</style>
</head>
<body>
<header>
  <h1>Issue状態ダッシュボード</h1>
  <p class="updated" id="updated">読み込み中...</p>
  <p class="error" id="error" style="display:none;"></p>
</header>
<div class="legend" id="legend"></div>
<main class="board" id="board"></main>
<section class="done">
  <h2>終了（新しい順）</h2>
  <div id="done"></div>
</section>

<script>
const DATA_URL = "../data/agent/dashboard.json";
const POLL_INTERVAL_MS = 30000;
// reply/ の通知がこの秒数を超えて未送信なら「Power Automate待ち」とみなす
const PA_STALE_SECONDS = 180;

const ACTORS = [
  { key: "human",  label: "人の対応待ち",   sub: "Teamsでの回答・承認・修正指示", color: ["#FAEEDA", "#BA7517", "#633806"] },
  { key: "cline",  label: "Cline作業中",    sub: "VS Code上で実装中",             color: ["#E6F1FB", "#378ADD", "#0C447C"] },
  { key: "pa",     label: "自動連携待ち",   sub: "Power AutomateのTeams送信待ち", color: ["#EEEDFE", "#7F77DD", "#3C3489"] },
  { key: "system", label: "システム処理中", sub: "Pythonの着手・公開・マージ",     color: ["#F1EFE8", "#888780", "#444441"] }
];

const TERMINAL = ["completed", "rejected"];

// status -> [担当, 説明, 警告を出す秒数(null=出さない), 警告文]
const STATUS_MAP = {
  created:           ["system", "着手待ち（空き枠待ち）",     1800, "停滞"],
  queued:            ["system", "着手準備中",                 1800, "停滞"],
  rework:            ["system", "再実装の着手待ち",           1800, "停滞"],
  preview_deploying: ["system", "検証サイトへ公開中",         1800, "停滞"],
  approved:          ["system", "マージ処理中",               1800, "停滞"],
  implementing:      ["cline",  "Clineが実装中",              3600, "長時間・停止の可能性"],
  waiting_decision:  ["human",  "Teamsで質問の回答待ち",      3600, "放置"],
  waiting_approval:  ["human",  "Teamsで承認待ち",            7200, "放置"],
  failed:            ["human",  "停止・要対応",               null, ""]
};

function secondsSince(isoString) {
  if (!isoString) return null;
  const t = new Date(isoString).getTime();
  if (isNaN(t)) return null;
  return Math.max(0, Math.floor((Date.now() - t) / 1000));
}

function formatElapsed(seconds) {
  if (seconds == null) return "";
  if (seconds < 60) return seconds + "秒前";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return minutes + "分前";
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return hours + "時間" + (minutes % 60) + "分前";
  return Math.floor(hours / 24) + "日前";
}

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = String(value == null ? "" : value);
  return div.innerHTML;
}

function classify(issue) {
  const status = (issue.status || "").toLowerCase();
  const pending = issue.pendingNotifications || {};
  const pendingAge = secondsSince(pending.oldestAt);

  // 退避済み・完了済みは、未送信の通知が残っていても「終了」に回す
  // （過去の通知ファイルの取り残しで、対応待ちの列が埋まるのを防ぐ）
  if (issue.archived || TERMINAL.includes(status)) return { actor: "done" };

  if (pending.count > 0 && pendingAge != null && pendingAge >= PA_STALE_SECONDS) {
    return {
      actor: "pa",
      note: "Teams通知 " + pending.count + "件が未送信",
      warn: formatElapsed(pendingAge).replace("前", "") + " 滞留"
    };
  }

  const entry = STATUS_MAP[status] || ["system", issue.statusLabel || status, null, ""];
  const age = secondsSince(issue.updatedAt);
  let note = entry[1];
  if (status === "failed" && issue.error) note += "：" + issue.error;
  const warn = entry[2] != null && age != null && age >= entry[2] ? entry[3] : "";
  return { actor: entry[0], note, warn };
}

function byUpdatedDesc(a, b) {
  const ta = new Date(a.updatedAt || 0).getTime() || 0;
  const tb = new Date(b.updatedAt || 0).getTime() || 0;
  return tb - ta;
}

let REPOSITORY = "";

function linkItems(issue) {
  const items = [];
  const repoBase = REPOSITORY ? "https://github.com/" + REPOSITORY : "";

  const issueUrl = issue.issueUrl || (repoBase ? repoBase + "/issues/" + issue.number : "");
  if (issueUrl) items.push({ label: "Issue #" + issue.number, url: issueUrl, cls: "" });

  if (issue.pullRequestUrl) {
    const m = String(issue.pullRequestUrl).match(new RegExp("/pull/([0-9]+)"));
    const num = issue.pullRequestNumber || (m ? m[1] : "");
    items.push({ label: num ? "PR #" + num : "PR", url: issue.pullRequestUrl, cls: "" });
  } else if (issue.branch && repoBase) {
    // PRのURLが状態ファイルに無い場合は、ブランチ名でPRを検索するリンクにする
    items.push({
      label: "PRを探す",
      url: repoBase + "/pulls?q=" + encodeURIComponent("is:pr head:" + issue.branch),
      cls: "search"
    });
  }

  if (issue.previewUrl) items.push({ label: "プレビュー", url: issue.previewUrl, cls: "" });
  return items;
}

function buildLinks(issue) {
  const items = linkItems(issue);
  if (!items.length) return "";
  return '<div class="links">' + items.map(function (it) {
    return '<a class="' + it.cls + '" href="' + escapeHtml(it.url) + '" target="_blank" rel="noopener">' +
      escapeHtml(it.label) + " ↗</a>";
  }).join("") + "</div>";
}

function buildCard(issue, result, actor) {
  const [bg, border, text] = actor.color;
  const elapsed = formatElapsed(secondsSince(issue.updatedAt));
  const warnHtml = result.warn ? ' <span class="warn">' + escapeHtml(result.warn) + "</span>" : "";
  return (
    '<div class="card" style="background:' + bg + ';border-left-color:' + border + ';">' +
    '<div class="card-title" style="color:' + text + ';">#' + issue.number + " " + escapeHtml(issue.title) + "</div>" +
    '<div class="card-note" style="color:' + text + ';">' + escapeHtml(result.note) + warnHtml + "</div>" +
    '<div class="card-meta">' + escapeHtml(elapsed) + " ・ " + escapeHtml(issue.status || "") + "</div>" +
    buildLinks(issue) +
    "</div>"
  );
}

function renderLegend() {
  document.getElementById("legend").innerHTML =
    ACTORS.map(function (a) {
      return '<span><span class="dot" style="background:' + a.color[1] + ';"></span>' +
        escapeHtml(a.label) + "（" + escapeHtml(a.sub) + "）</span>";
    }).join("") +
    '<span><span class="warn">赤字</span>＝長時間そのまま</span>';
}

function render(data) {
  REPOSITORY = data.repository || "";
  const groups = { human: [], cline: [], pa: [], system: [], done: [] };
  const issues = (data.issues || []).slice().sort(byUpdatedDesc);

  for (const issue of issues) {
    const result = classify(issue);
    groups[result.actor].push({ issue, result });
  }

  const board = document.getElementById("board");
  board.innerHTML = ACTORS.map(function (actor) {
    const items = groups[actor.key];
    const cards = items.length
      ? items.map(function (item) { return buildCard(item.issue, item.result, actor); }).join("")
      : '<div class="empty">なし</div>';
    return '<div><div class="column-title" style="color:' + actor.color[2] + ';">' +
      escapeHtml(actor.label) + ' <span class="count">' + items.length + "</span></div>" + cards + "</div>";
  }).join("");

  const done = groups.done;
  document.getElementById("done").innerHTML = done.length
    ? done.map(function (item) {
        const issue = item.issue;
        const ok = issue.status === "completed";
        const orphan = !TERMINAL.includes(issue.status);
        const label = orphan
          ? "退避済み（" + (issue.statusLabel || issue.status) + "のまま）"
          : (issue.statusLabel || issue.status);
        const pending = (issue.pendingNotifications || {}).count || 0;
        const pendingNote = pending ? " ・ 未送信通知" + pending + "件" : "";
        return '<div class="done-row"><div class="done-head"><span>#' + issue.number + " " +
          escapeHtml(issue.title) + "</span>" +
          '<span class="meta" style="color:' + (ok ? "#3B6D11" : orphan ? "#A32D2D" : "#888780") + ';">' +
          escapeHtml(label) + " ・ " +
          escapeHtml(formatElapsed(secondsSince(issue.updatedAt))) + escapeHtml(pendingNote) + "</span></div>" +
          buildLinks(issue) + "</div>";
      }).join("")
    : '<div class="empty">なし</div>';

  const generated = data.generatedAt ? new Date(data.generatedAt) : null;
  document.getElementById("updated").textContent =
    "最終更新: " + (generated ? generated.toLocaleString("ja-JP") : "不明") +
    "（" + Math.round(POLL_INTERVAL_MS / 1000) + "秒ごとに自動更新・新しい順）";
  document.getElementById("error").style.display = "none";
}

async function load() {
  try {
    const response = await fetch(DATA_URL + "?t=" + Date.now(), { cache: "no-store" });
    if (!response.ok) throw new Error("HTTP " + response.status);
    render(await response.json());
  } catch (error) {
    document.getElementById("error").textContent = "データの取得に失敗しました: " + error.message;
    document.getElementById("error").style.display = "block";
  }
}

renderLegend();
load();
setInterval(load, POLL_INTERVAL_MS);
</script>
</body>
</html>
"""

# バージョン番号を持たない初代テンプレート（v1）を識別するための目印。
# これを含むファイルは「自動生成された旧版」とみなして置き換えてよい。
_V1_SIGNATURE = 'const DATA_URL = "../data/agent/dashboard.json";'


def _installed_version(text: str) -> int | None:
    """公開済みagent.htmlのバージョンを返す。自動生成版でなければNone。"""
    match = re.search(
        rf'<meta name="{_VERSION_META_NAME}" content="(\d+)">', text
    )
    if match:
        return int(match.group(1))
    if _V1_SIGNATURE in text:
        return 1
    return None


def ensure_agent_html(beta_repo, logger=None) -> str:
    """
    utility/agent.html を用意する。戻り値は "created" / "updated" / "kept"。

    - 無ければ作成する
    - 自動生成された古いバージョンなら、最新テンプレートで置き換える
    - 手で作り替えられたファイル（バージョン情報も初代の目印も無いもの）や、
      同じか新しいバージョンのものは触らない

    自動生成版を手直しして、今後も上書きされたくない場合は、
    ファイル先頭の agent-dashboard-version の meta タグを削除すること。
    """
    app_dir = beta_repo / APP_DIR_NAME
    app_dir.mkdir(parents=True, exist_ok=True)
    app_path = app_dir / APP_FILENAME

    if not app_path.exists():
        app_path.write_text(AGENT_HTML_TEMPLATE, encoding="utf-8")
        return "created"

    try:
        current = app_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "kept"

    installed = _installed_version(current)
    if installed is None:
        if logger is not None:
            logger.info(
                f"{APP_DIR_NAME}/{APP_FILENAME} は手動で作り替えられているため上書きしません。"
            )
        return "kept"

    if installed >= AGENT_HTML_VERSION:
        return "kept"

    app_path.write_text(AGENT_HTML_TEMPLATE, encoding="utf-8")
    return "updated"


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

    app_result = ensure_agent_html(beta_repo, logger=logger)
    if app_result == "created":
        logger.info(f"{APP_DIR_NAME}/{APP_FILENAME} を新規作成しました。")
    elif app_result == "updated":
        logger.info(
            f"{APP_DIR_NAME}/{APP_FILENAME} を新しい表示（v{AGENT_HTML_VERSION}）に更新しました。"
        )

    message = f"dashboard: update data ({len(rows)} issues)"
    if app_result == "created":
        message = f"dashboard: add {APP_FILENAME} + update data ({len(rows)} issues)"
    elif app_result == "updated":
        message = (
            f"dashboard: update {APP_FILENAME} to v{AGENT_HTML_VERSION} "
            f"+ update data ({len(rows)} issues)"
        )

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
    # 未送信のTeams通知（Power Automate待ちの判定材料）の増減にも追従する。
    # recursive=False なので、フロー④が reply/done/ へ移動した瞬間も
    # 「reply/ からの移動」として検知できる。
    observer.schedule(handler, str(CONFIG.reply_dir), recursive=False)
    observer.start()
    logger.info(
        f"{CONFIG.state_dir} と {CONFIG.reply_dir} の変化を監視します。"
        "終了するには Ctrl+C を押してください。"
    )

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
