from __future__ import annotations

from agent_core import prompt


def _sample_issue(number=12, title="顧客一覧ページを追加"):
    return {
        "number": number,
        "title": title,
        "url": "https://github.com/test-owner/tools/issues/12",
        "body": "顧客一覧を表示するページを作ってください。",
        "labels": [{"name": "feature"}],
    }


def test_build_implementation_prompt_includes_issue_details():
    text = prompt.build_implementation_prompt(
        _sample_issue(), "feature/issue-12-x", "/repo/worktrees/issue-12",
        "https://example.github.io/tools-beta/preview/issue-12/",
    )

    assert "Issue #12" in text
    assert "顧客一覧ページを追加" in text
    assert "feature/issue-12-x" in text
    assert "/repo/worktrees/issue-12" in text
    assert prompt.QUESTION_FILE_NAME in text
    assert prompt.SUMMARY_FILE_NAME in text
    assert "git操作、commit、push、PR作成は絶対に行わないでください" in text


def test_build_implementation_prompt_handles_missing_body_and_labels():
    issue = _sample_issue()
    issue["body"] = None
    issue["labels"] = []

    text = prompt.build_implementation_prompt(issue, "b", "/w", "https://x/")
    assert "(本文なし)" in text
    assert "なし" in text  # ラベル無し


def test_build_rework_prompt_includes_comment_and_contract():
    text = prompt.build_rework_prompt(12, "ボタンの色を青にしてください", "b", "/w")

    assert "差し戻されました" in text
    assert "ボタンの色を青にしてください" in text
    assert prompt.SUMMARY_FILE_NAME in text


def test_build_rework_prompt_handles_empty_comment():
    text = prompt.build_rework_prompt(12, "", "b", "/w")
    assert "(指示なし)" in text


def test_build_decision_prompt_with_selected_choice():
    question = {
        "issueNumber": 12,
        "question": "取得元は?",
        "choices": [
            {"id": "c1", "title": "JSON", "description": "customer.jsonから読む"},
            {"id": "c2", "title": "API", "description": "既存APIを使う"},
        ],
    }
    decision = {"selectedChoice": "c1", "customResponse": ""}

    text = prompt.build_decision_prompt(question, decision)

    assert "Issue #12" in text
    assert "JSON" in text
    assert "customer.jsonから読む" in text
    assert prompt.SUMMARY_FILE_NAME in text


def test_build_decision_prompt_with_custom_response_appended():
    question = {
        "issueNumber": 12,
        "question": "取得元は?",
        "choices": [{"id": "c1", "title": "JSON", "description": "d"}],
    }
    decision = {"selectedChoice": "c1", "customResponse": "認証も追加して"}

    text = prompt.build_decision_prompt(question, decision)
    assert "認証も追加して" in text


def test_build_decision_prompt_falls_back_to_id_when_choice_unknown():
    question = {
        "issueNumber": 12,
        "question": "取得元は?",
        "choices": [{"id": "c1", "title": "JSON", "description": "d"}],
    }
    decision = {"selectedChoice": "unknown_id", "customResponse": ""}

    text = prompt.build_decision_prompt(question, decision)
    assert "unknown_id" in text


def test_summary_contract_instructs_reporting_even_on_early_stop():
    """
    実環境の検証で、チャットへ「これ以上の検証は不要です」と直接指示したところ、
    Clineが implementation/verification を空欄のまま summaryText だけで
    済ませてしまう事例があった。契約文にこれを防ぐ一文が含まれること。
    """
    text = prompt.build_implementation_prompt(
        _sample_issue(), "b", "/w", "https://x/"
    )
    assert "打ち切る指示を受けた場合でも" in text
    assert "実際に実施した内容を必ず記載" in text


def test_control_files_do_not_include_summary_files_by_accident():
    assert prompt.QUESTION_FILE_NAME in prompt.CONTROL_FILES
    assert prompt.SUMMARY_FILE_NAME in prompt.CONTROL_FILES
    assert prompt.REWORK_FILE_NAME in prompt.CONTROL_FILES
    assert len(prompt.CONTROL_FILES) == 3
