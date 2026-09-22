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


def test_exclude_control_files_registers_git_exclude(git_repo):
    worker = _worker(15)
    path, branch = gitops.prepare_worktree(15, "テスト", "main")
    worker.worktree = path
    worker.branch = branch

    worker.exclude_control_files()

    exclude_path = path / ".git"
    gitdir = exclude_path.read_text(encoding="utf-8").split("gitdir:", 1)[1].strip()
    content = (__import__("pathlib").Path(gitdir) / "info" / "exclude").read_text(encoding="utf-8")
    assert ".agent-summary.json" in content
    assert ".agent-question.json" in content
    assert ".agent-rework.txt" in content


def test_read_summary_deletes_summary_file_after_reading(agent_env):
    worker = _worker(16)
    worker.worktree.mkdir(parents=True, exist_ok=True)
    worker.summary_file.write_text(
        json.dumps({"issueNumber": 16, "summaryText": "完了"}), encoding="utf-8"
    )

    summary = worker.read_summary()
    assert summary["summaryText"] == "完了"
    assert not worker.summary_file.exists()
