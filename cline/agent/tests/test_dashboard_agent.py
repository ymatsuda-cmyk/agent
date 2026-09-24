from __future__ import annotations

from html.parser import HTMLParser

import dashboard_agent as da
from agent_core import statefile
from agent_core.jsonio import now_iso

VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta"}


class _BalanceChecker(HTMLParser):
    """agent.htmlのタグ対応チェック用（agent_core/checks.pyと同じ手法）。"""

    def __init__(self):
        super().__init__()
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID_TAGS:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in VOID_TAGS:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"unexpected </{tag}>")
        else:
            self.stack.pop()


# ============================================================
# build_dashboard_json: サーバー側は生データのみを渡す
# ============================================================


def test_build_dashboard_json_contains_generated_at_and_issues(agent_env):
    payload = da.build_dashboard_json(
        [(70, {"issueTitle": "customer9ページ追加", "status": "created"})]
    )
    assert "generatedAt" in payload
    assert payload["issues"] == [
        {
            "number": 70,
            "title": "customer9ページ追加",
            "status": "created",
            "statusLabel": "着手待ち",
            "error": "",
            "updatedAt": None,
            "issueUrl": None,
            "pullRequestUrl": None,
            "previewUrl": None,
            "pullRequestNumber": None,
            "branch": "",
            "pendingNotifications": {"count": 0, "oldestAt": None},
            "archived": False,
        }
    ]
    assert payload["repository"] == agent_env.repository


def test_build_dashboard_json_passes_through_links_and_timestamps():
    payload = da.build_dashboard_json(
        [
            (
                68,
                {
                    "issueTitle": "疎通確認",
                    "status": "waiting_approval",
                    "updatedAt": "2026-09-23T13:23:13+09:00",
                    "issueUrl": "https://github.com/x/y/issues/68",
                    "pullRequestUrl": "https://github.com/x/y/pull/69",
                    "previewUrl": "https://x.github.io/y-beta/preview/issue-68/",
                },
            )
        ]
    )
    issue = payload["issues"][0]
    assert issue["updatedAt"] == "2026-09-23T13:23:13+09:00"
    assert issue["issueUrl"] == "https://github.com/x/y/issues/68"
    assert issue["pullRequestUrl"] == "https://github.com/x/y/pull/69"
    assert issue["previewUrl"] == "https://x.github.io/y-beta/preview/issue-68/"


def test_build_dashboard_json_normalizes_missing_title_and_status_case():
    payload = da.build_dashboard_json([(1, {"status": "Waiting_Approval"})])
    issue = payload["issues"][0]
    assert issue["title"] == "(タイトルなし)"
    assert issue["status"] == "waiting_approval"  # 小文字化される


def test_build_dashboard_json_sorts_by_issue_number():
    payload = da.build_dashboard_json(
        [(30, {"status": "created"}), (5, {"status": "created"}), (12, {"status": "created"})]
    )
    numbers = [issue["number"] for issue in payload["issues"]]
    assert numbers == [5, 12, 30]


def test_build_dashboard_json_is_json_serializable():
    import json

    payload = da.build_dashboard_json(
        [(1, {"issueTitle": "テスト<script>", "status": "created"})]
    )
    # 例外なくシリアライズできること（クライアント側でescapeHtmlするため、
    # サーバー側でHTMLエスケープする必要は無い）
    text = json.dumps(payload, ensure_ascii=False)
    assert "テスト<script>" in text


# ============================================================
# agent.html（静的アプリ）: タグバランスと必須ロジックの存在確認
# ============================================================

def test_agent_html_template_is_well_formed():
    checker = _BalanceChecker()
    checker.feed(da.AGENT_HTML_TEMPLATE)
    assert checker.errors == []
    assert checker.stack == []


def test_agent_html_template_fetches_correct_relative_data_path():
    # utility/agent.html から見て ../data/agent/dashboard.json であること
    expected = f"../{da.DATA_DIR_NAME}/{da.DATA_FILENAME}"
    assert expected in da.AGENT_HTML_TEMPLATE



def test_agent_html_template_includes_client_side_logic():
    """担当分類・警告・降順ソート・XSS対策がJS側に存在すること。"""
    template = da.AGENT_HTML_TEMPLATE
    assert "STATUS_MAP" in template
    assert "classify" in template
    assert "byUpdatedDesc" in template
    assert "PA_STALE_SECONDS" in template
    assert "escapeHtml" in template
    for label in ("人の対応待ち", "Cline作業中", "自動連携待ち", "システム処理中"):
        assert label in template


def test_agent_html_template_polls_periodically():
    assert "setInterval(load, POLL_INTERVAL_MS)" in da.AGENT_HTML_TEMPLATE



def test_agent_html_template_is_mobile_responsive():
    assert "@media (max-width:760px)" in da.AGENT_HTML_TEMPLATE
    assert "viewport" in da.AGENT_HTML_TEMPLATE


# ============================================================
# ensure_agent_html: 初回のみ作成し、以降は上書きしない
# ============================================================


def test_ensure_agent_html_creates_file_when_missing(tmp_path):
    result = da.ensure_agent_html(tmp_path)

    app_path = tmp_path / da.APP_DIR_NAME / da.APP_FILENAME
    assert result == "created"
    assert app_path.read_text(encoding="utf-8") == da.AGENT_HTML_TEMPLATE



def test_ensure_agent_html_does_not_overwrite_hand_made_file(tmp_path):
    """バージョン情報も初代の目印も無い＝手で作り替えたファイルは触らない。"""
    app_dir = tmp_path / da.APP_DIR_NAME
    app_dir.mkdir(parents=True)
    app_path = app_dir / da.APP_FILENAME
    app_path.write_text("手で編集済みの内容", encoding="utf-8")

    result = da.ensure_agent_html(tmp_path)

    assert result == "kept"
    assert app_path.read_text(encoding="utf-8") == "手で編集済みの内容"


def test_ensure_agent_html_upgrades_auto_generated_v1(tmp_path):
    """
    初代(v1)テンプレートはバージョン情報を持たないが、自動生成版なので
    新しいデザインへ置き換える（置き換えないと、公開環境に新しい表示が
    永久に反映されない）。
    """
    app_dir = tmp_path / da.APP_DIR_NAME
    app_dir.mkdir(parents=True)
    app_path = app_dir / da.APP_FILENAME
    app_path.write_text(
        '<html><script>const DATA_URL = "../data/agent/dashboard.json";</script></html>',
        encoding="utf-8",
    )

    result = da.ensure_agent_html(tmp_path)

    assert result == "updated"
    assert app_path.read_text(encoding="utf-8") == da.AGENT_HTML_TEMPLATE


def test_ensure_agent_html_keeps_current_version(tmp_path):
    da.ensure_agent_html(tmp_path)
    assert da.ensure_agent_html(tmp_path) == "kept"


def test_ensure_agent_html_upgrades_older_numbered_version(tmp_path):
    app_dir = tmp_path / da.APP_DIR_NAME
    app_dir.mkdir(parents=True)
    old = da.AGENT_HTML_TEMPLATE.replace(
        f'content="{da.AGENT_HTML_VERSION}"', 'content="1"'
    )
    (app_dir / da.APP_FILENAME).write_text(old, encoding="utf-8")

    assert da.ensure_agent_html(tmp_path) == "updated"


def test_template_version_meta_matches_constant():
    """テンプレートを更新したらバージョン定数も上げる運用の、取り違え防止。"""
    assert da._installed_version(da.AGENT_HTML_TEMPLATE) == da.AGENT_HTML_VERSION


# ============================================================
# state収集（変更なし・パス構成変更の影響を受けない部分）
# ============================================================

def test_collect_rows_includes_active_and_recent_done_issues(agent_env):
    statefile.create(70, {"issueTitle": "待機中Issue"})
    statefile.update(70, {"status": statefile.Status.IMPLEMENTING})

    statefile.create(66, {"issueTitle": "終了済みIssue", "status": statefile.Status.REJECTED})
    statefile.update(66, {"status": statefile.Status.REJECTED, "updatedAt": now_iso()})
    statefile.archive(66)

    rows = da._collect_rows()
    numbers = {number for number, _ in rows}

    assert 70 in numbers
    assert 66 in numbers  # done/ からも拾える


def test_collect_rows_limits_done_issues_to_recent_n(agent_env):
    for i in range(30):
        statefile.create(i + 1, {"issueTitle": f"issue{i}", "status": statefile.Status.COMPLETED})
        statefile.update(i + 1, {"status": statefile.Status.COMPLETED})
        statefile.archive(i + 1)

    rows = da._collect_rows()
    done_count = sum(
        1 for _, data in rows if data.get("status") == statefile.Status.COMPLETED
    )
    assert done_count <= da.DONE_ISSUES_SHOWN


# ============================================================
# StateChangeHandler: dirtyフラグの立ち方（変更なし）
# ============================================================

class _FakeEvent:
    def __init__(self, path: str, is_directory: bool = False):
        self.src_path = path
        self.is_directory = is_directory


def test_state_change_handler_starts_dirty():
    handler = da.StateChangeHandler()
    assert handler.dirty is True


def test_state_change_handler_marks_dirty_on_json_event():
    handler = da.StateChangeHandler()
    handler.dirty = False
    handler.on_modified(_FakeEvent("/agent-root/state/issue-1.json"))
    assert handler.dirty is True


def test_state_change_handler_ignores_directory_events():
    handler = da.StateChangeHandler()
    handler.dirty = False
    handler.on_created(_FakeEvent("/agent-root/state/done", is_directory=True))
    assert handler.dirty is False


def test_state_change_handler_ignores_non_json_files():
    handler = da.StateChangeHandler()
    handler.dirty = False
    handler.on_modified(_FakeEvent("/agent-root/state/locks/issue-1.lock"))
    assert handler.dirty is False


# ============================================================
# Power Automate待ちの判定材料（reply/ に残った未送信通知）
# ============================================================

def test_pending_notifications_counts_unsent_reply_files(agent_env):
    import os
    import time

    old = agent_env.reply_dir / "issue-76-question-1.json"
    new = agent_env.reply_dir / "issue-76-approved-2.json"
    old.write_text("{}", encoding="utf-8")
    new.write_text("{}", encoding="utf-8")
    stamp = time.time() - 600
    os.utime(old, (stamp, stamp))

    result = da._pending_notifications(76)

    assert result["count"] == 2
    assert result["oldestAt"] is not None
    # 最も古いファイルの時刻が使われること（約10分前）
    from datetime import datetime

    age = (datetime.now().astimezone() - datetime.fromisoformat(result["oldestAt"])).total_seconds()
    assert 590 <= age <= 700


def test_pending_notifications_ignores_done_folder_and_other_issues(agent_env):
    (agent_env.reply_dir / "done" / "issue-76-x.json").write_text("{}", encoding="utf-8")
    (agent_env.reply_dir / "issue-761-x.json").write_text("{}", encoding="utf-8")

    assert da._pending_notifications(76) == {"count": 0, "oldestAt": None}


def test_build_dashboard_json_includes_status_label_and_error(agent_env):
    payload = da.build_dashboard_json(
        [(77, {"issueTitle": "t", "status": "failed", "error": "静的チェック不合格"})]
    )
    issue = payload["issues"][0]
    assert issue["statusLabel"] == "異常終了"
    assert issue["error"] == "静的チェック不合格"


# ============================================================
# agent.html のJavaScriptロジックそのものを検証する
# （Node.jsがある環境のみ。無ければスキップ）
# ============================================================

def _run_js_logic(case_script: str) -> str:
    import re
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("node が無いためJSロジックのテストをスキップします")

    script = re.search(r"<script>(.*)</script>", da.AGENT_HTML_TEMPLATE, re.S).group(1)
    logic = script.split("renderLegend();")[0]
    program = (
        "global.document={createElement:()=>({set textContent(v){this._v=v},"
        "get innerHTML(){return String(this._v)}})};\n"
        + logic
        + "\nconst ago=(s)=>new Date(Date.now()-s*1000).toISOString();\n"
        + "const none={count:0,oldestAt:null};\n"
        + case_script
    )
    result = subprocess.run(
        [node, "-e", program], capture_output=True, text=True, encoding="utf-8", timeout=30
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_js_classifies_statuses_into_actors():
    out = _run_js_logic(
        "const r=[['implementing',600],['waiting_decision',60],['waiting_approval',60],"
        "['failed',60],['created',60],['completed',60],['rejected',60]]"
        ".map(([s,a])=>classify({status:s,updatedAt:ago(a),pendingNotifications:none}).actor);"
        "console.log(r.join(','));"
    )
    assert out == "cline,human,human,human,system,done,done"


def test_js_marks_stale_notifications_as_power_automate_wait():
    out = _run_js_logic(
        "const fresh=classify({status:'waiting_approval',updatedAt:ago(60),"
        "pendingNotifications:{count:1,oldestAt:ago(60)}});"
        "const stale=classify({status:'waiting_approval',updatedAt:ago(300),"
        "pendingNotifications:{count:1,oldestAt:ago(300)}});"
        "console.log(fresh.actor+','+stale.actor);"
    )
    # 送信直後(1分)はまだ人待ち扱い、閾値(3分)を超えて残っていたらPower Automate待ち
    assert out == "human,pa"


def test_js_warns_on_long_running_states():
    out = _run_js_logic(
        "const a=classify({status:'implementing',updatedAt:ago(54000),pendingNotifications:none});"
        "const b=classify({status:'waiting_approval',updatedAt:ago(10800),pendingNotifications:none});"
        "const c=classify({status:'implementing',updatedAt:ago(600),pendingNotifications:none});"
        "console.log([a.warn,b.warn,c.warn||'-'].join('|'));"
    )
    assert out == "長時間・停止の可能性|放置|-"


def test_js_sorts_by_updated_at_descending():
    out = _run_js_logic(
        "const r=[{number:1,updatedAt:ago(5000)},{number:2,updatedAt:ago(10)},"
        "{number:3,updatedAt:ago(900)},{number:4,updatedAt:null}]"
        ".sort(byUpdatedDesc).map(i=>i.number);console.log(r.join(','));"
    )
    # 日時が無いものは最後に回ること
    assert out == "2,3,1,4"


# ============================================================
# 退避済み（state/done/）のIssueは、statusに関係なく「終了」扱い（回帰テスト）
#
# 実運用で、state/done/ に "implementing" や "waiting_approval" のまま
# 退避されたIssueが、ダッシュボードの「Cline作業中」「人の対応待ち」列に
# 表示され続けてしまう不具合があった。
# ============================================================

def test_collect_rows_marks_done_folder_rows_as_archived(agent_env):
    statefile.create(80, {"issueTitle": "稼働中", "status": statefile.Status.IMPLEMENTING})
    statefile.create(63, {"issueTitle": "退避済み", "status": statefile.Status.IMPLEMENTING})
    statefile.archive(63)

    rows = dict(da._collect_rows())

    assert not rows[80].get("archived")
    assert rows[63]["archived"] is True


def test_build_dashboard_json_passes_archived_flag(agent_env):
    payload = da.build_dashboard_json(
        [(63, {"status": "implementing", "archived": True}), (80, {"status": "implementing"})]
    )
    flags = {issue["number"]: issue["archived"] for issue in payload["issues"]}
    assert flags == {63: True, 80: False}


def test_js_routes_archived_issue_to_done_regardless_of_status():
    out = _run_js_logic(
        "const a=classify({status:'implementing',archived:true,updatedAt:ago(86400),pendingNotifications:none});"
        "const b=classify({status:'waiting_approval',archived:true,updatedAt:ago(86400),pendingNotifications:none});"
        "const c=classify({status:'implementing',archived:false,updatedAt:ago(60),pendingNotifications:none});"
        "console.log([a.actor,b.actor,c.actor].join(','));"
    )
    assert out == "done,done,cline"


# ============================================================
# リンク表示（Issue番号・PR番号・PR検索のフォールバック）
# ============================================================

def test_js_links_show_issue_and_pr_numbers():
    out = _run_js_logic(
        "REPOSITORY='owner/tools';"
        "const items=linkItems({number:68,issueUrl:'https://github.com/owner/tools/issues/68',"
        "pullRequestUrl:'https://github.com/owner/tools/pull/69',"
        "previewUrl:'https://owner.github.io/tools-beta/preview/issue-68/'});"
        "console.log(items.map(i=>i.label).join('|'));"
    )
    assert out == "Issue #68|PR #69|プレビュー"


def test_js_links_fall_back_to_pr_search_by_branch():
    out = _run_js_logic(
        "REPOSITORY='owner/tools';"
        "const items=linkItems({number:62,branch:'issue-62-customer8'});"
        "console.log(items.map(i=>i.label+'='+i.url).join('|'));"
    )
    assert "Issue #62=https://github.com/owner/tools/issues/62" in out
    assert "PRを探す=https://github.com/owner/tools/pulls?q=is%3Apr%20head%3Aissue-62-customer8" in out


def test_js_links_omit_pr_when_no_url_and_no_branch():
    out = _run_js_logic(
        "REPOSITORY='owner/tools';"
        "console.log(linkItems({number:80}).map(i=>i.label).join('|'));"
    )
    assert out == "Issue #80"


# ============================================================
# 未送信通知の取り残しで、終了済みIssueが「自動連携待ち」に出ないこと（回帰テスト）
#
# 実運用で、フロー④がTeams投稿後のファイル移動に失敗し続けた結果、
# reply/ に大量の通知ファイルが残り、退避済み・完了済みのIssueが
# 15件も「自動連携待ち」列に並んでしまった。
# ============================================================

def test_js_stale_notifications_on_archived_or_completed_issue_go_to_done():
    out = _run_js_logic(
        "const stale={count:68,oldestAt:ago(172800)};"
        "const a=classify({status:'waiting_approval',archived:true,updatedAt:ago(172800),pendingNotifications:stale});"
        "const b=classify({status:'completed',updatedAt:ago(86400),pendingNotifications:stale});"
        "const c=classify({status:'waiting_decision',updatedAt:ago(300),pendingNotifications:{count:1,oldestAt:ago(600)}});"
        "console.log([a.actor,b.actor,c.actor].join(','));"
    )
    assert out == "done,done,pa"
