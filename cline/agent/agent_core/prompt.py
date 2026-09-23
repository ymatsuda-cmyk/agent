"""
Clineへ渡すプロンプトの生成。

Clineとの契約（ファイルインターフェース）:
- .agent-question.json : 質問したいときにClineが作る
- .agent-summary.json  : 実装完了時にClineが作る
- .agent-rework.txt    : 再実装時にこちらが置く修正指示

いずれもworktree直下に置くため、Issue間で混ざらない。
"""

from __future__ import annotations

QUESTION_FILE_NAME = ".agent-question.json"
SUMMARY_FILE_NAME = ".agent-summary.json"
REWORK_FILE_NAME = ".agent-rework.txt"

#: 実装成果物に含めてはいけない制御ファイル
CONTROL_FILES = (QUESTION_FILE_NAME, SUMMARY_FILE_NAME, REWORK_FILE_NAME)


COMMON_RULES = """\
作業ルール:
1. 素のHTML / CSS / JavaScriptで実装してください。
2. TypeScriptやフレームワークを追加しないでください。
3. package.jsonやビルド設定を追加しないでください。
4. 新規ページはリポジトリ直下のディレクトリにindex.htmlを配置してください。
5. コメントは日本語で記述してください。
6. ファイルはUTF-8 BOMなしで保存してください。
7. old/配下とIssueに関係しないファイルは変更しないでください。
8. git操作、commit、push、PR作成は絶対に行わないでください。
9. 判断に迷う場合は勝手に決めず、質問してください。
10. 作業ディレクトリの外（他のフォルダ）を変更しないでください。
"""


def _question_contract(issue_number: int) -> str:
    return f"""\
質問・確認ルール（最優先）:
- 要件が曖昧、矛盾、又は人の判断が必要な場合は、推測で進めないでください。
- Cline標準の質問UIだけで停止しないでください。
- 質問UIを表示する前に、必ず作業ディレクトリ直下へ {QUESTION_FILE_NAME} を新規作成してください。
- {QUESTION_FILE_NAME} はUTF-8 BOMなしで保存してください。
- issueNumber には必ず {issue_number} を設定してください。
- questionId は今回の質問に固有の値にしてください。
- question には確認したい内容の全文を記載してください。
- choices は最大3件。各要素に id / title / description を含めてください。
- カスタム回答が必要な場合も選択肢の1つとして明記してください。
- {QUESTION_FILE_NAME} 作成後は、回答が返るまで実装ファイルを変更しないでください。

必須JSON形式:
{{
  "type": "cline_question",
  "issueNumber": {issue_number},
  "questionId": "current-question-id",
  "question": "確認したい質問の全文",
  "choices": [
    {{ "id": "choice_1", "title": "選択肢1", "description": "選択肢1の詳細" }},
    {{ "id": "choice_2", "title": "選択肢2", "description": "選択肢2の詳細" }}
  ]
}}
"""


def _summary_contract(issue_number: int) -> str:
    return f"""\
実装完了時の必須報告:
- チャットへの回答だけでなく、作業ディレクトリ直下へ {SUMMARY_FILE_NAME} をUTF-8 BOMなしで作成してください。
- このファイルは成果物ではなく、Teams承認画面へ結果を返すための一時ファイルです。
- issueNumber には必ず {issue_number} を設定してください。
- 実際に実施した内容だけを書き、未確認の事項を確認済みとして書かないでください。
- ブラウザ確認やコンソール確認を行っていない場合は notPerformed に明記してください。
- 「これ以上の検証は不要です」「ここで完了としてください」のように、途中で
  作業を打ち切る指示を受けた場合でも、summaryText・implementation・
  verification には、指示を受けるまでに実際に実施した内容を必ず記載して
  ください。打ち切りの指示は「要約を省略してよい」という意味ではありません。
  何も実施していなかった場合のみ、その旨を正直に notPerformed へ書いてください。
- {SUMMARY_FILE_NAME} の作成をもって「実装完了」と判断します。最後に必ず作成してください。

必須JSON形式:
{{
  "issueNumber": {issue_number},
  "summaryText": "Teams承認画面に表示する実装完了報告の全文",
  "implementation": ["実装した内容1", "実装した内容2"],
  "verification": ["実際に確認した内容1"],
  "notPerformed": ["未実施の確認事項"],
  "notes": ["承認者へ伝える注意事項"]
}}
"""


def build_implementation_prompt(
    issue: dict,
    branch: str,
    worktree: str,
    preview_url_hint: str,
) -> str:
    """初回実装用のプロンプト。"""
    labels = ", ".join(
        item.get("name", "") for item in issue.get("labels", [])
    ) or "なし"
    body = issue.get("body") or "(本文なし)"
    issue_number = issue["number"]

    return f"""GitHub Issue #{issue_number} を実装してください。

タイトル: {issue['title']}
URL: {issue['url']}
ラベル: {labels}

本文:
{body}

前提:
- 作業ディレクトリは {worktree} です（git worktree）。
- 作業ブランチ {branch} は作成済みで、既にチェックアウト済みです。
- 本リポジトリはビルド不要の静的サイトです。
- ターミナルはPowerShellです。
- 実装後の動作確認は検証用サイト（{preview_url_hint}）で行います。

{COMMON_RULES}
{_question_contract(issue_number)}
{_summary_contract(issue_number)}
"""


def build_rework_prompt(issue_number: int, comment: str, branch: str, worktree: str) -> str:
    """Teamsで差し戻された場合の再実装プロンプト。"""
    return f"""GitHub Issue #{issue_number} の実装が差し戻されました。修正してください。

作業ディレクトリ: {worktree}
作業ブランチ: {branch}（既存の変更はそのまま残っています）

承認者からの修正指示:
{comment or '(指示なし)'}

対応方針:
- 既存の実装を土台に、指示された点だけを修正してください。
- 指示に無い箇所を作り直さないでください。
- 指示内容が不明瞭な場合は、推測せず質問してください。

{COMMON_RULES}
{_question_contract(issue_number)}
{_summary_contract(issue_number)}
"""


def build_decision_prompt(question: dict, decision: dict) -> str:
    """Teamsからの回答をClineへ返すプロンプト。"""
    issue_number = question.get("issueNumber")
    selected_id = str(decision.get("selectedChoice", "")).strip()
    custom = str(decision.get("customResponse", "")).strip()

    selected_title = ""
    selected_description = ""
    for choice in question.get("choices", []):
        if str(choice.get("id", "")) == selected_id:
            selected_title = str(choice.get("title", ""))
            selected_description = str(choice.get("description", ""))
            break

    lines = [
        f"Issue #{issue_number} の確認事項について、Teamsで回答が確定しました。",
        "",
        f"質問: {question.get('question', '')}",
        "",
        f"選択された回答: {selected_title or selected_id}",
    ]

    if selected_description:
        lines += ["", f"回答の内容: {selected_description}"]

    if custom:
        lines += ["", "承認者からの追加指示:", custom]

    lines += [
        "",
        "この回答の通りに実装を再開してください。",
        "回答と矛盾する実装を行わないでください。",
        "さらに判断が必要になった場合は、再度 .agent-question.json を作成して質問してください。",
        "実装が完了したら .agent-summary.json を作成してください。",
    ]

    return "\n".join(lines)
