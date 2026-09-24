from __future__ import annotations

import argparse
import base64
import json
import threading
import time

from agent_core import gitops
from agent_core.config import CONFIG
from agent_core.jsonio import write_json
from implement_agent import Worker


def _worker(issue_number: int, mode: str = "new") -> Worker:
    args = argparse.Namespace(
        mode=mode, no_vscode=True, manual=True, locked_by_orchestrator=True
    )
    worker = Worker(issue_number, args)
    worker.issue = {
        "number": issue_number,
        "title": "テストIssue",
        "url": "https://github.com/x/y/issues/" + str(issue_number),
    }
    worker.branch = f"feature/issue-{issue_number}-test"
    return worker


# ============================================================
# read_question
# ============================================================


def test_read_question_parses_valid_question(agent_env):
    worker = _worker(10)
    worker.worktree.mkdir(parents=True, exist_ok=True)
    worker.question_file.write_text(
        json.dumps(
            {
                "issueNumber": 10,
                "questionId": "q1",
                "question": "取得元は?",
                "choices": [
                    {"id": "c1", "title": "JSON", "description": "customer.json"},
                    {"id": "c2", "title": "API", "description": "既存API"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    question = worker.read_question()

    assert question is not None
    assert question["questionId"] == "q1"
    assert question["choice1Title"] == "JSON"
    assert question["choice3Id"] == ""  # 3件目が無い場合は空欄


def test_read_question_rejects_mismatched_issue_number(agent_env):
    worker = _worker(10)
    worker.worktree.mkdir(parents=True, exist_ok=True)
    worker.question_file.write_text(
        json.dumps({"issueNumber": 999, "choices": [{"id": "c1"}]}), encoding="utf-8"
    )

    assert worker.read_question() is None


def test_read_question_rejects_missing_choices(agent_env):
    worker = _worker(10)
    worker.worktree.mkdir(parents=True, exist_ok=True)
    worker.question_file.write_text(
        json.dumps({"issueNumber": 10}), encoding="utf-8"
    )

    assert worker.read_question() is None


def test_read_question_handles_bom(agent_env):
    worker = _worker(10)
    worker.worktree.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"issueNumber": 10, "questionId": "q1", "choices": [{"id": "c1", "title": "A"}]},
        ensure_ascii=False,
    )
    worker.question_file.write_text("\ufeff" + payload, encoding="utf-8")

    question = worker.read_question()
    assert question is not None
    assert question["questionId"] == "q1"


# ============================================================
# wait_for_decision: questionId照合（弱点2の回帰テスト）
# ============================================================


def test_wait_for_decision_rejects_stale_question_id(agent_env, monkeypatch):
    worker = _worker(11)
    worker.worktree.mkdir(parents=True, exist_ok=True)

    question = {
        "issueNumber": 11,
        "questionId": "q2-current",
        "choices": [{"id": "c1", "title": "A"}, {"id": "c2", "title": "B"}],
    }
    decision_path = CONFIG.decision_dir / "issue-11.json"

    # 古い質問(q1)への回答が残っている状態を再現する
    write_json(decision_path, {"issueNumber": 11, "questionId": "q1-old", "selectedChoice": "c1"})

    monkeypatch.setattr(time, "sleep", lambda s: None)  # ポーリング待ちを高速化

    def replace_with_correct():
        write_json(
            decision_path, {"issueNumber": 11, "questionId": "q2-current", "selectedChoice": "c2"}
        )

    threading.Timer(0.01, replace_with_correct).start()

    result = worker.wait_for_decision(question)

    assert result is not None
    assert result["questionId"] == "q2-current"
    assert result["selectedChoice"] == "c2"


def test_wait_for_decision_accepts_matching_question_id(agent_env, monkeypatch):
    worker = _worker(12)
    worker.worktree.mkdir(parents=True, exist_ok=True)

    question = {
        "issueNumber": 12,
        "questionId": "q1",
        "choices": [{"id": "c1", "title": "A"}],
    }
    write_json(
        CONFIG.decision_dir / "issue-12.json",
        {"issueNumber": 12, "questionId": "q1", "selectedChoice": "c1"},
    )

    monkeypatch.setattr(time, "sleep", lambda s: None)

    result = worker.wait_for_decision(question)
    assert result["selectedChoice"] == "c1"


def test_wait_for_decision_times_out_without_valid_answer(agent_env, monkeypatch):
    worker = _worker(13)
    worker.worktree.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(CONFIG, "decision_timeout", 0)  # 即タイムアウト

    question = {"issueNumber": 13, "questionId": "q1", "choices": [{"id": "c1", "title": "A"}]}
    assert worker.wait_for_decision(question) is None


# ============================================================
# decode_custom_response
# ============================================================


def test_decode_custom_response_prefers_base64():
    encoded = base64.b64encode("追加で認証も入れて".encode()).decode()
    assert Worker.decode_custom_response({"customResponseBase64": encoded}) == "追加で認証も入れて"


def test_decode_custom_response_falls_back_to_plain():
    assert Worker.decode_custom_response({"customResponse": "そのまま"}) == "そのまま"


def test_decode_custom_response_empty_when_absent():
    assert Worker.decode_custom_response({}) == ""


# ============================================================
# 完了判定フォールバック（弱点4の回帰テスト）
# ============================================================


def test_build_fallback_summary_flags_missing_summary_prominently():
    summary = Worker.build_fallback_summary()

    assert "summaryText" in summary
    assert "完了報告" in summary["summaryText"]
    assert summary["notPerformed"]  # 承認カードのamber枠に必ず何か表示される
    assert "完了報告がありません" in summary["notPerformed"][0]


# ============================================================
# 制御ファイルの除外
# ============================================================


def test_changes_signature_excludes_control_files(git_repo):
    worker = _worker(14)
    path, branch = gitops.prepare_worktree(14, "テスト", "main")
    worker.worktree = path
    worker.branch = branch

    (path / "real.html").write_text("<p>x</p>", encoding="utf-8")
    (path / ".agent-summary.json").write_text("{}", encoding="utf-8")

    signature = worker.changes_signature()
    assert "real.html" in signature
    assert ".agent-summary.json" not in signature


def test_exclude_control_files_really_hides_them_from_git(git_repo):
    """
    除外設定を書いた場所ではなく、Gitが実際に無視するかどうかを確かめる。

    以前のテストは worktree個別の管理フォルダに文字列が書かれているかだけを
    見ていたため、「Gitはそこを読まない」という不具合を見逃していた。
    """
    worker = _worker(15)
    path, branch = gitops.prepare_worktree(15, "テスト", "main")
    worker.worktree = path
    worker.branch = branch

    worker.exclude_control_files()

    for name in (".agent-summary.json", ".agent-question.json", ".agent-rework.txt"):
        (path / name).write_text("x", encoding="utf-8")
    (path / ".agent-attachments").mkdir()
    (path / ".agent-attachments" / "a.png").write_bytes(b"img")
    (path / "real.html").write_text("<p>x</p>", encoding="utf-8")

    assert gitops.changed_files(path) == ["real.html"]


def test_exclude_control_files_is_idempotent(git_repo):
    worker = _worker(16)
    path, branch = gitops.prepare_worktree(16, "テスト", "main")
    worker.worktree = path

    worker.exclude_control_files()
    worker.exclude_control_files()

    content = gitops.git_path(path, "info/exclude").read_text(encoding="utf-8")
    assert content.count(".agent-summary.json") == 1


def test_read_summary_deletes_summary_file_after_reading(agent_env):
    worker = _worker(16)
    worker.worktree.mkdir(parents=True, exist_ok=True)
    worker.summary_file.write_text(
        json.dumps({"issueNumber": 16, "summaryText": "完了"}), encoding="utf-8"
    )

    summary = worker.read_summary()
    assert summary["summaryText"] == "完了"
    assert not worker.summary_file.exists()


# ============================================================
# run(): 未回答の質問が残ったまま再実行された場合、
# 実装プロンプトを再送しない安全策（回帰テスト）
#
# 実運用で、途中で止まった/再起動されたIssueがorchestratorに
# 再度拾われた際、Cline側のウィンドウには前回の未回答の質問
# （.agent-question.json、場合によってはCline標準の対話質問UI）が
# まだ残っている状態で、mode="new"の実装プロンプトを誤って
# 貼り付けてしまうと、その未回答質問への回答として誤爆する。
# ============================================================

def _prepared_worker(issue_number: int, mode: str = "new") -> Worker:
    """prepare()を済ませ、worktreeを実際に作った状態のWorkerを返す。"""
    worker = _worker(issue_number, mode=mode)
    worker.worktree.mkdir(parents=True, exist_ok=True)
    return worker


def test_run_does_not_resend_prompt_when_valid_question_is_pending(monkeypatch, agent_env):
    worker = _prepared_worker(66, mode="new")

    worker.question_file.write_text(
        json.dumps(
            {
                "issueNumber": 66,
                "questionId": "q1",
                "question": "前回の未回答質問",
                "choices": [{"id": "c1", "title": "A"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(Worker, "prepare", lambda self: True)
    monkeypatch.setattr("implement_agent.teams.notify_started", lambda *a, **k: None)

    send_calls = []
    monkeypatch.setattr(
        Worker, "send_to_cline",
        lambda self, text, first_time=False: send_calls.append((text, first_time)),
    )
    monkeypatch.setattr(Worker, "wait_for_implementation", lambda self: False)

    worker.run()

    # 未回答の質問が残っている間は、実装プロンプトを再送しないこと
    assert send_calls == []
    # 質問ファイル自体は消さず、そのまま残しておくこと
    assert worker.question_file.exists()


def test_run_resends_prompt_normally_when_no_pending_question(monkeypatch, agent_env):
    worker = _prepared_worker(67, mode="new")
    # 質問ファイルは存在しない（通常の初回実行）

    monkeypatch.setattr(Worker, "prepare", lambda self: True)
    monkeypatch.setattr("implement_agent.teams.notify_started", lambda *a, **k: None)

    send_calls = []
    monkeypatch.setattr(
        Worker, "send_to_cline",
        lambda self, text, first_time=False: send_calls.append((text, first_time)),
    )
    monkeypatch.setattr(Worker, "wait_for_implementation", lambda self: False)

    worker.run()

    # 質問が残っていなければ、通常通り初回プロンプトを送ること
    assert len(send_calls) == 1
    assert send_calls[0][1] is True  # first_time=True


def test_run_resends_prompt_when_leftover_question_is_invalid(monkeypatch, agent_env):
    """
    question_fileが存在しても、別Issue宛て等の無効な内容なら
    read_question()がNoneを返す。この場合は通常どおりクリアして再送する
    （無効なファイルのせいで永久に再送できなくなることを防ぐ）。
    """
    worker = _prepared_worker(68, mode="new")

    worker.question_file.write_text(
        json.dumps({"issueNumber": 999, "choices": [{"id": "c1"}]}),  # 別Issue宛て
        encoding="utf-8",
    )

    monkeypatch.setattr(Worker, "prepare", lambda self: True)
    monkeypatch.setattr("implement_agent.teams.notify_started", lambda *a, **k: None)

    send_calls = []
    monkeypatch.setattr(
        Worker, "send_to_cline",
        lambda self, text, first_time=False: send_calls.append((text, first_time)),
    )
    monkeypatch.setattr(Worker, "wait_for_implementation", lambda self: False)

    worker.run()

    assert len(send_calls) == 1
    assert send_calls[0][1] is True
    # 無効な質問ファイルはクリアされていること
    assert not worker.question_file.exists()


def test_run_rework_mode_always_archives_question_and_resends(monkeypatch, agent_env):
    """再実装(mode="rework")では、質問の有無に関わらず既存挙動（クリアして再送）のまま。"""
    worker = _prepared_worker(69, mode="rework")
    worker.question_file.write_text(
        json.dumps({"issueNumber": 69, "choices": [{"id": "c1"}]}), encoding="utf-8"
    )

    monkeypatch.setattr(Worker, "prepare", lambda self: True)
    monkeypatch.setattr("implement_agent.teams.notify_started", lambda *a, **k: None)

    send_calls = []
    monkeypatch.setattr(
        Worker, "send_to_cline",
        lambda self, text, first_time=False: send_calls.append((text, first_time)),
    )
    monkeypatch.setattr(Worker, "wait_for_implementation", lambda self: False)

    worker.run()

    assert len(send_calls) == 1
    assert not worker.question_file.exists()


# ============================================================
# ensure_draft_pr(): 既存PR再利用時に本文を最新化する（回帰テスト）
# ============================================================

def test_ensure_draft_pr_updates_body_when_reusing_existing_pr(monkeypatch, agent_env):
    worker = _prepared_worker(69, mode="new")

    existing_pr = {"number": 69, "url": "https://github.com/x/y/pull/69"}
    monkeypatch.setattr("implement_agent.ghcli.find_open_pr", lambda branch: existing_pr)

    update_calls = []
    monkeypatch.setattr(
        "implement_agent.ghcli.update_pull_request_body",
        lambda pr_number, body: update_calls.append((pr_number, body)),
    )

    summary = {
        "implementation": ["再実装で直した内容"],
        "verification": [],
        "notPerformed": [],
    }
    pr_number, pr_url = worker.ensure_draft_pr(summary, ["index.html"])

    assert pr_number == 69
    assert pr_url == "https://github.com/x/y/pull/69"
    assert len(update_calls) == 1
    assert update_calls[0][0] == 69
    assert "再実装で直した内容" in update_calls[0][1]


# ============================================================
# 参考画像の配置（prepare）
# ============================================================

def test_prepare_places_attachments_and_excludes_them_from_changes(git_repo, monkeypatch):
    from agent_core import statefile

    image = git_repo.request_dir / "attachments" / "m1" / "画面.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"img")
    statefile.create(
        90,
        {"issueTitle": "画像付き", "attachments": [
            {"name": "画面.png", "localPath": str(image), "url": ""}
        ]},
    )

    monkeypatch.setattr(
        "implement_agent.ghcli.view_issue",
        lambda n: {"number": n, "title": "画像付き", "url": "https://x/90", "state": "OPEN"},
    )
    worker = _worker(90)

    assert worker.prepare() is True
    assert worker.attachment_paths == [".agent-attachments/画面.png"]
    assert (worker.worktree / ".agent-attachments" / "画面.png").exists()
    # 配置した画像は「変更」として数えられない（コミットもされない）
    assert worker.changes_signature() == ()
    assert gitops.changed_files(worker.worktree) == []


def test_build_prompt_includes_placed_attachments(git_repo):
    worker = _worker(91)
    worker.worktree.mkdir(parents=True, exist_ok=True)
    worker.attachment_paths = [".agent-attachments/a.png"]

    text = worker.build_prompt()

    assert "- .agent-attachments/a.png" in text
