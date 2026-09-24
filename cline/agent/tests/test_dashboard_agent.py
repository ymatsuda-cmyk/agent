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

def test_build_dashboard_json_contains_generated_at_and_issues():
    payload = da.build_dashboard_json(
        [(70, {"issueTitle": "customer9ページ追加", "status": "created"})]
    )
    assert "generatedAt" in payload
    assert payload["issues"] == [
        {
            "number": 70,
            "title": "customer9ページ追加",
            "status": "created",
            "updatedAt": None,
            "issueUrl": None,
            "pullRequestUrl": None,
            "previewUrl": None,
        }
    ]


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
    """色分け・フェーズ分け・放置警告・経過時間計算がJS側に存在すること。"""
    template = da.AGENT_HTML_TEMPLATE
    assert "STATUS_COLORS" in template
    assert "STUCK_THRESHOLD_SECONDS" in template
    assert "phaseOf" in template
    assert "formatElapsed" in template
    assert "escapeHtml" in template  # XSS対策がクライアント側にある


def test_agent_html_template_polls_periodically():
    assert "setInterval(load, POLL_INTERVAL_MS)" in da.AGENT_HTML_TEMPLATE


def test_agent_html_template_is_mobile_responsive():
    assert "@media (max-width:640px)" in da.AGENT_HTML_TEMPLATE
    assert "viewport" in da.AGENT_HTML_TEMPLATE


# ============================================================
# ensure_agent_html: 初回のみ作成し、以降は上書きしない
# ============================================================

def test_ensure_agent_html_creates_file_when_missing(tmp_path):
    created = da.ensure_agent_html(tmp_path)

    app_path = tmp_path / da.APP_DIR_NAME / da.APP_FILENAME
    assert created is True
    assert app_path.exists()
    assert app_path.read_text(encoding="utf-8") == da.AGENT_HTML_TEMPLATE


def test_ensure_agent_html_does_not_overwrite_existing_file(tmp_path):
    app_dir = tmp_path / da.APP_DIR_NAME
    app_dir.mkdir(parents=True)
    app_path = app_dir / da.APP_FILENAME
    app_path.write_text("手で編集済みの内容", encoding="utf-8")

    created = da.ensure_agent_html(tmp_path)

    assert created is False
    assert app_path.read_text(encoding="utf-8") == "手で編集済みの内容"


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
