"""
[工程3] Issue実装ワーカー（Issue 1件分）

オーケストレーターから1Issueにつき1プロセス起動される。
このプロセスの中だけで完結するため、複数Issueを並走できる。

処理の流れ:
 1. git worktree と トピックブランチを用意する
 2. Clineへ実装プロンプトを投入する（GUIロックで直列化）
 3. Clineが .agent-question.json を作ったらTeamsへ質問し、回答を待つ
 4. Clineが .agent-summary.json を作ったら実装完了とみなす
 5. 変更をコミットし、ブランチをpushしてドラフトPRを作る
 6. tools-beta へ検証用プレビューを公開する
 7. waiting/issue-<N>.json を出力して承認待ちにする
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time

from agent_core import attachments, cline, ghcli, gitops, prompt, statefile, teams
from agent_core.config import CONFIG
from agent_core.jsonio import (
    move_to_done,
    now_iso,
    read_json,
    read_json_when_ready,
    write_json,
)
from agent_core.locks import gui_lock, issue_lock_path, release, try_acquire
from agent_core.logs import get_logger

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent

POLL_INTERVAL_SECONDS = 5
STABLE_DURATION_SECONDS = 45


class Worker:
    def __init__(self, issue_number: int, args) -> None:
        self.issue_number = issue_number
        self.args = args
        self.log = get_logger("implement", issue_number)
        self.worktree: pathlib.Path = CONFIG.worktree_path(issue_number)
        self.branch = ""
        self.issue: dict = {}
        self.session_started_at = time.time()
        #: worktreeに配置した参考画像（worktreeからの相対パス）
        self.attachment_paths: list[str] = []

    # --------------------------------------------------------
    # 制御ファイル
    # --------------------------------------------------------

    @property
    def question_file(self) -> pathlib.Path:
        return self.worktree / prompt.QUESTION_FILE_NAME

    @property
    def summary_file(self) -> pathlib.Path:
        return self.worktree / prompt.SUMMARY_FILE_NAME

    @property
    def rework_file(self) -> pathlib.Path:
        return self.worktree / prompt.REWORK_FILE_NAME

    def clear_control_files(self) -> None:
        """前回セッションの残骸を消す（別Issueの質問を拾わないため）。"""
        for name in prompt.CONTROL_FILES:
            path = self.worktree / name
            if path.exists():
                try:
                    path.unlink()
                    self.log.info(f"前回の制御ファイルを削除しました: {name}")
                except OSError as error:
                    self.log.warn(f"制御ファイルを削除できません: {name} / {error}")

    # --------------------------------------------------------
    # 1. 準備
    # --------------------------------------------------------

    def prepare(self) -> bool:
        self.log.rule(f"Issue #{self.issue_number} 実装ワーカー起動")

        self.issue = ghcli.view_issue(self.issue_number)

        if self.issue.get("state") != "OPEN":
            self.log.error(f"Issue #{self.issue_number} はOPENではありません。")
            statefile.update(
                self.issue_number,
                {"status": statefile.Status.FAILED, "error": "Issue is not OPEN"},
            )
            return False

        self.worktree, self.branch = gitops.prepare_worktree(
            self.issue_number,
            self.issue["title"],
            CONFIG.base_branch,
            logger=self.log,
        )

        statefile.update(
            self.issue_number,
            {
                "status": statefile.Status.IMPLEMENTING,
                "issueTitle": self.issue["title"],
                "issueUrl": self.issue["url"],
                "branch": self.branch,
                "worktree": str(self.worktree),
                "implementationStartedAt": now_iso(),
            },
        )

        self.log.info(f"ブランチ : {self.branch}")
        self.log.info(f"worktree : {self.worktree}")

        # 制御ファイルと参考画像は、配置した瞬間から変更として数えないよう
        # 先にコミット対象外へ登録しておく（変更検知の誤判定を防ぐ）。
        self.exclude_control_files()
        self.attachment_paths = attachments.copy_into_worktree(
            statefile.load(self.issue_number).get("attachments") or [],
            self.worktree,
            logger=self.log,
        )
        if self.attachment_paths:
            self.log.info(f"参考画像を配置しました: {len(self.attachment_paths)}件")
        return True

    # --------------------------------------------------------
    # 2. プロンプト投入
    # --------------------------------------------------------

    def build_prompt(self) -> str:
        if self.args.mode == "rework":
            comment = ""
            if self.rework_file.exists():
                try:
                    comment = self.rework_file.read_text(encoding="utf-8").strip()
                except OSError:
                    comment = ""
            if not comment:
                comment = str(statefile.load(self.issue_number).get("reworkComment", ""))

            return prompt.build_rework_prompt(
                self.issue_number, comment, self.branch, str(self.worktree),
                attachment_paths=self.attachment_paths,
            )

        return prompt.build_implementation_prompt(
            self.issue,
            self.branch,
            str(self.worktree),
            f"{CONFIG.pages_base_url}/preview/issue-{self.issue_number}/",
            attachment_paths=self.attachment_paths,
        )

    def send_to_cline(self, text: str, first_time: bool) -> None:
        """
        Clineへテキストを送る。

        GUI操作は全体で1件ずつしか行えないため、ロックで直列化する。
        ロック保持時間を短くするため、VS Codeの起動はロック外で行う。
        """
        if first_time and not self.args.no_vscode:
            cline.launch_vscode(self.worktree, issue_number=self.issue_number, logger=self.log)

        if self.args.manual or CONFIG.gui_mode == "manual":
            cline.copy_to_clipboard(text, logger=self.log)
            cline.show_manual_instruction(self.issue_number, self.worktree, self.log)
            return

        with gui_lock(self.issue_number, logger=self.log):
            self.log.info("GUIロックを取得しました。Clineへ投入します。")
            injected = cline.inject_prompt(self.issue_number, text, logger=self.log)

        if not injected:
            cline.show_manual_instruction(self.issue_number, self.worktree, self.log)

    # --------------------------------------------------------
    # 3. 実装待ち（質問ループを含む）
    # --------------------------------------------------------

    def wait_for_implementation(self) -> str | bool:
        """
        .agent-summary.json ができるまで待つ。

        途中で .agent-question.json が作られた場合はTeamsへ質問し、
        回答を受け取ってからClineへ返して待機を継続する。

        戻り値:
            "summary"  … .agent-summary.json を検出した（正常な完了報告）
            "fallback" … 変更は安定したがサマリーは作られなかった
                         （Clineが完了報告を書き忘れた、または途中で止まった）
            False      … 変更もサマリーも無いままタイムアウトした

        "fallback" と "summary" を同じ扱いにしてはいけない。
        以前は両方とも True を返して以降の処理が「正常完了」として
        進んでいたが、これは実装が本当に終わったのか、単に途中で
        停止しただけなのかを区別できず、承認者がそれを知らないまま
        マージまで進んでしまう危険があった。呼び出し側で
        "fallback" のときは要確認の警告をサマリーに埋め込む。
        """
        deadline = time.time() + CONFIG.implementation_timeout
        last_change_at = time.time()
        last_signature = self.changes_signature()

        self.log.info(
            f"実装完了（{prompt.SUMMARY_FILE_NAME}）を待機します。"
            f" 最大 {CONFIG.implementation_timeout // 60} 分。"
        )

        while time.time() < deadline:
            if self.summary_file.exists():
                self.log.info("実装完了サマリーを検出しました。")
                return "summary"

            if self.question_file.exists():
                if self.handle_question():
                    deadline = time.time() + CONFIG.implementation_timeout
                    last_change_at = time.time()
                continue

            signature = self.changes_signature()
            if signature != last_signature:
                last_signature = signature
                last_change_at = time.time()
                self.log.info(f"変更を検出: {len(signature)} ファイル")

            idle = time.time() - last_change_at
            if signature and idle > STABLE_DURATION_SECONDS * 4:
                self.log.warn(
                    "サマリーは未作成ですが、変更が安定したためフォールバックとして扱います"
                    "（要確認としてTeamsカードに警告を出します）。"
                )
                return "fallback"

            time.sleep(POLL_INTERVAL_SECONDS)

        self.log.error("実装待機がタイムアウトしました。")
        return False

    @staticmethod
    def build_fallback_summary() -> dict:
        """
        完了報告(.agent-summary.json)が無いまま進む場合の、警告付きサマリー。

        「未実施の確認」欄は承認カードで常に目立つ色で表示されるため、
        ここに警告を入れることで、承認者が気づかず承認してしまう事故を防ぐ。
        """
        return {
            "summaryText": (
                "⚠️ Clineによる完了報告（.agent-summary.json）が作成されない"
                "まま待機がタイムアウトしました。実装が本当に完了しているか、"
                "途中で停止しただけかは自動判定できていません。"
                "マージ前に必ず内容を確認してください。"
            ),
            "implementation": [],
            "verification": [],
            "notPerformed": [
                "Clineによる完了報告がありません。実装が完了したか、"
                "途中で停止しただけかは未確認です。",
            ],
            "notes": [
                "自動フォールバックにより、変更のコミットとドラフトPR作成までは"
                "進めていますが、内容の妥当性は未検証です。",
            ],
        }

    def changes_signature(self) -> tuple[str, ...]:
        files = [
            path
            for path in gitops.changed_files(self.worktree)
            if not prompt.is_control_path(path)
        ]
        return tuple(sorted(files))

    # --------------------------------------------------------
    # 4. 質問 → Teams → 回答
    # --------------------------------------------------------

    def handle_question(self) -> bool:
        question = self.read_question()
        if question is None:
            # 不正な質問ファイルは退避して実装待機を継続する。
            self.archive_control_file(self.question_file)
            return False

        self.log.info(f"Clineからの質問を検出: {question.get('questionId')}")

        question_path = CONFIG.question_dir / f"issue-{self.issue_number}.json"
        write_json(question_path, question)

        statefile.update(
            self.issue_number,
            {
                "status": statefile.Status.WAITING_DECISION,
                "questionId": question.get("questionId", ""),
                "questionAskedAt": now_iso(),
            },
        )
        self.log.info(f"Teams質問用JSONを出力しました: {question_path.name}")

        decision = self.wait_for_decision(question)

        # 質問ファイルは必ず後片付けする。
        self.archive_control_file(self.question_file)
        move_to_done(question_path, CONFIG.question_dir / "done")

        if decision is None:
            statefile.update(
                self.issue_number,
                {"status": statefile.Status.FAILED, "error": "回答待ちタイムアウト"},
            )
            teams.notify_failed(
                self.issue_number, "Teamsからの回答待ちがタイムアウトしました。", self.log
            )
            return False

        statefile.update(
            self.issue_number,
            {
                "status": statefile.Status.IMPLEMENTING,
                "lastDecision": decision.get("selectedChoice", ""),
                "decisionAt": now_iso(),
            },
        )

        text = prompt.build_decision_prompt(question, decision)
        self.send_to_cline(text, first_time=False)
        self.log.info("回答をClineへ返しました。実装を継続します。")
        return True

    def read_question(self) -> dict | None:
        """Clineが作った質問JSONを検証して整形する。"""
        raw = read_json(self.question_file)
        if raw is None:
            self.log.warn("質問JSONを解析できません。")
            return None

        try:
            source_issue = int(raw.get("issueNumber"))
        except (TypeError, ValueError):
            self.log.warn("質問JSONのissueNumberが不正です。")
            return None

        if source_issue != self.issue_number:
            self.log.warn(
                f"別Issueの質問（#{source_issue}）のため無視します。"
            )
            return None

        choices_raw = raw.get("choices")
        if not isinstance(choices_raw, list) or not choices_raw:
            self.log.warn("質問JSONにchoicesがありません。")
            return None

        choices: list[dict] = []
        for index, choice in enumerate(choices_raw[:3], start=1):
            if not isinstance(choice, dict):
                continue
            choice_id = str(choice.get("id") or f"choice_{index}").strip()
            choices.append(
                {
                    "id": choice_id,
                    "title": str(choice.get("title") or choice_id).strip(),
                    "description": str(choice.get("description") or "").strip(),
                }
            )

        if not choices:
            self.log.warn("有効な選択肢がありません。")
            return None

        question = {
            "type": "cline_question",
            "issueNumber": self.issue_number,
            "issueTitle": self.issue.get("title", ""),
            "issueUrl": self.issue.get("url", ""),
            "messageId": statefile.message_id_of(self.issue_number),
            "questionId": str(
                raw.get("questionId") or f"issue-{self.issue_number}-question"
            ),
            "question": str(raw.get("question") or "Clineから確認があります。"),
            "choices": choices,
            "status": statefile.Status.WAITING_DECISION,
            "createdAt": now_iso(),
        }

        # Power Automateのカードで扱いやすいよう平坦化する。
        for index in range(1, 4):
            choice = choices[index - 1] if index <= len(choices) else {}
            question[f"choice{index}Id"] = str(choice.get("id", ""))
            question[f"choice{index}Title"] = str(choice.get("title", ""))
            question[f"choice{index}Description"] = str(choice.get("description", ""))

        return question

    def wait_for_decision(self, question: dict) -> dict | None:
        """
        decision/issue-<N>.json が置かれるのを待つ。

        同一Issueで2回以上質問が発生した場合、前回の回答ファイルが
        タイミングによって decision/ に残っていることがある。
        questionId を照合せずに受理すると、古い回答を今回の質問への
        回答として誤って採用してしまう。そのため questionId が
        一致しない回答は「無関係」として捨て、待機を継続する。
        """
        decision_path = CONFIG.decision_dir / f"issue-{self.issue_number}.json"
        valid_ids = {str(choice["id"]) for choice in question["choices"]}
        expected_question_id = str(question["questionId"])
        deadline = time.time() + CONFIG.decision_timeout

        self.log.info(f"Teamsからの回答を待機します: {decision_path.name}")

        while time.time() < deadline:
            data = read_json_when_ready(decision_path) if decision_path.exists() else None

            if data:
                received_question_id = str(data.get("questionId", "")).strip()

                if received_question_id != expected_question_id:
                    self.log.warn(
                        "questionIdが一致しない回答のため無視します"
                        f"（期待={expected_question_id!r} 受信={received_question_id!r}）。"
                    )
                    move_to_done(decision_path, CONFIG.decision_dir / "done")
                    time.sleep(3)
                    continue

                selected = str(data.get("selectedChoice", "")).strip()
                custom = self.decode_custom_response(data)

                if selected in valid_ids or custom:
                    self.log.info(f"回答を受信しました: {selected or 'カスタム回答'}")
                    data["selectedChoice"] = selected
                    data["customResponse"] = custom
                    return data

                self.log.warn(f"不正な回答のため無視します: {selected}")
                move_to_done(decision_path, CONFIG.decision_dir / "done")

            time.sleep(3)

        return None

    @staticmethod
    def decode_custom_response(data: dict) -> str:
        """customResponseはBase64で渡される場合がある。"""
        import base64
        import binascii

        encoded = str(data.get("customResponseBase64", "")).strip()
        if encoded:
            try:
                return base64.b64decode(encoded, validate=True).decode("utf-8").strip()
            except (binascii.Error, UnicodeDecodeError, ValueError):
                pass

        return str(data.get("customResponse", "")).strip()

    def archive_control_file(self, path: pathlib.Path) -> None:
        if not path.exists():
            return
        try:
            path.unlink()
        except OSError as error:
            self.log.warn(f"制御ファイルを削除できません: {path.name} / {error}")

    # --------------------------------------------------------
    # 5. コミット・push・ドラフトPR
    # --------------------------------------------------------

    def read_summary(self) -> dict:
        data = read_json(self.summary_file) or {}
        try:
            self.archive_control_file(self.summary_file)
        except OSError:
            pass
        return data

    def commit_and_push(self, summary: dict) -> bool:
        """制御ファイルを除外してコミットし、ブランチをpushする。"""
        self.exclude_control_files()

        files = self.changes_signature()
        if files:
            message = (
                f"{self.issue['title']} (refs #{self.issue_number})\n\n"
                f"{str(summary.get('summaryText', '')).strip()[:500]}"
            )
            committed = gitops.commit_all(self.worktree, message)
            if not committed:
                self.log.warn("コミット対象がありませんでした。")
        else:
            self.log.info("未コミットの変更はありません。")

        if not gitops.has_commits_ahead(self.worktree, CONFIG.base_branch, self.branch):
            self.log.error("baseブランチからの差分がありません。")
            return False

        gitops.push_branch(self.worktree, self.branch)
        self.log.info(f"ブランチをpushしました: {self.branch}")
        return True

    def exclude_control_files(self) -> None:
        """
        制御ファイルと参考画像フォルダを info/exclude に登録し、コミットされないようにする。

        以前は worktree個別の管理フォルダ（.git/worktrees/issue-N/info/exclude）に
        書いていたが、Gitは worktree でも info/exclude を本体と共通の管理フォルダ
        からしか読まないため、書いても無視されていた。Git自身にパスを解決させる。
        """
        try:
            exclude_file = gitops.git_path(self.worktree, "info/exclude")
        except gitops.GitError as error:
            self.log.warn(f"除外設定の場所を特定できませんでした: {error}")
            return
        exclude_file.parent.mkdir(parents=True, exist_ok=True)

        existing = ""
        if exclude_file.exists():
            try:
                existing = exclude_file.read_text(encoding="utf-8")
            except OSError:
                existing = ""

        entries = list(prompt.CONTROL_FILES) + [f"/{attachments.WORKTREE_DIR_NAME}/"]
        additions = [name for name in entries if name not in existing]
        if additions:
            with exclude_file.open("a", encoding="utf-8") as stream:
                stream.write("\n# AIエージェント制御ファイル\n")
                for name in additions:
                    stream.write(f"{name}\n")

    def ensure_draft_pr(self, summary: dict, changed_files: list[str]) -> tuple[int, str]:
        """ドラフトPRを作る（既にあれば本文を最新化して再利用する）。"""
        file_list = "\n".join(f"- `{path}`" for path in changed_files) or "- (変更なし)"
        implementation = "\n".join(
            f"- {item}" for item in summary.get("implementation", []) if item
        ) or "- (記載なし)"
        verification = "\n".join(
            f"- {item}" for item in summary.get("verification", []) if item
        ) or "- (記載なし)"
        not_performed = "\n".join(
            f"- {item}" for item in summary.get("notPerformed", []) if item
        ) or "- (なし)"

        body = f"""Closes #{self.issue_number}

## 実装内容

{implementation}

## 変更ファイル

{file_list}

## 実施した確認

{verification}

## 未実施の確認

{not_performed}

## 検証環境

{CONFIG.pages_base_url}/preview/issue-{self.issue_number}/

---

本PRはAIエージェントが作成しました。Teamsでの承認後に自動でマージされます。
"""

        existing = ghcli.find_open_pr(self.branch)
        if existing:
            self.log.info(f"既存PRを再利用します: #{existing['number']}")
            ghcli.update_pull_request_body(int(existing["number"]), body)
            return int(existing["number"]), str(existing["url"])

        url = ghcli.create_pull_request(
            self.issue_number,
            self.issue["title"],
            self.branch,
            CONFIG.base_branch,
            body,
            draft=True,
            cwd=self.worktree,
        )

        pr = ghcli.find_open_pr(self.branch)
        number = int(pr["number"]) if pr else 0
        self.log.info(f"ドラフトPRを作成しました: #{number} {url}")
        return number, url or (pr["url"] if pr else "")

    # --------------------------------------------------------
    # 6. 検証環境（tools-beta）への公開
    # --------------------------------------------------------

    def deploy_preview(self, changed_files: list[str]) -> str:
        statefile.update(
            self.issue_number, {"status": statefile.Status.PREVIEW_DEPLOYING}
        )

        script = SCRIPT_DIR / "deploy_preview.py"
        if not script.exists():
            self.log.warn("deploy_preview.py が見つかりません。URLのみ生成します。")
            return self.fallback_preview_url(changed_files)

        command = [
            sys.executable,
            str(script),
            "--issue",
            str(self.issue_number),
            "--worktree",
            str(self.worktree),
        ]
        for path in changed_files:
            command += ["--changed-file", path]

        self.log.info("検証用サイト（tools-beta）へ公開します。")
        result = subprocess.run(
            command,
            cwd=str(SCRIPT_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
        )

        if result.returncode != 0:
            self.log.warn(f"tools-betaへの公開に失敗しました。\n{result.stderr[-2000:]}")
            return self.fallback_preview_url(changed_files)

        try:
            payload = json.loads(result.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            self.log.warn("deploy_preview.py の出力を解析できません。")
            return self.fallback_preview_url(changed_files)

        url = str(payload.get("previewUrl", "")).strip()
        if url:
            self.log.info(f"検証URL: {url}")
            return url

        return self.fallback_preview_url(changed_files)

    def fallback_preview_url(self, changed_files: list[str]) -> str:
        base = f"{CONFIG.pages_base_url}/preview/issue-{self.issue_number}"
        for path in changed_files:
            normalized = path.replace("\\", "/")
            if not normalized.lower().endswith("index.html"):
                continue
            # 移動・削除された側のパスはプレビューに存在しないので候補から外す。
            if not (self.worktree / normalized).exists():
                continue
            directory = "/".join(normalized.split("/")[:-1])
            return f"{base}/{directory}/".replace("//", "/").replace(":/", "://")
        return f"{base}/"

    # --------------------------------------------------------
    # 7. 承認待ちへ
    # --------------------------------------------------------

    def write_waiting(
        self,
        summary: dict,
        changed_files: list[str],
        preview_url: str,
        pr_number: int,
        pr_url: str,
    ) -> None:
        summary_text = str(summary.get("summaryText", "")).strip()
        if not summary_text:
            summary_text = (
                f"Issue #{self.issue_number} の実装が完了しました。"
                "内容を確認のうえ承認してください。"
            )

        payload = {
            "type": "implementation_completed",
            "issueNumber": self.issue_number,
            "issueTitle": self.issue["title"],
            "issueUrl": self.issue["url"],
            "messageId": statefile.message_id_of(self.issue_number),
            "branch": self.branch,
            "pullRequestNumber": pr_number,
            "pullRequestUrl": pr_url,
            "changedFilesText": "\n".join(f"- {path}" for path in changed_files)
            or "- (変更なし)",
            "changedFiles": changed_files,
            "summaryText": summary_text,
            "implementationText": "\n".join(
                f"- {item}" for item in summary.get("implementation", []) if item
            ),
            "verificationText": "\n".join(
                f"- {item}" for item in summary.get("verification", []) if item
            ),
            "notPerformedText": "\n".join(
                f"- {item}" for item in summary.get("notPerformed", []) if item
            ),
            "notesText": "\n".join(
                f"- {item}" for item in summary.get("notes", []) if item
            ),
            "previewUrl": preview_url,
            "status": statefile.Status.WAITING_APPROVAL,
            "createdAt": now_iso(),
        }

        path = CONFIG.waiting_dir / f"issue-{self.issue_number}.json"

        # 再実装の場合は前回の承認待ちJSONを退避してから出す。
        move_to_done(path, CONFIG.waiting_dir / "done")
        write_json(path, payload)

        statefile.update(
            self.issue_number,
            {
                "status": statefile.Status.WAITING_APPROVAL,
                "pullRequestNumber": pr_number,
                "pullRequestUrl": pr_url,
                "previewUrl": preview_url,
                "changedFiles": changed_files,
                "summaryText": summary_text,
                "waitingAt": now_iso(),
            },
        )

        self.log.info(f"承認待ちJSONを出力しました: {path.name}")

    # --------------------------------------------------------
    # 実行
    # --------------------------------------------------------

    def run(self) -> int:
        if not self.prepare():
            return 1

        # 前回セッションの質問(.agent-question.json)がまだ残っている場合、
        # Clineのウィンドウ側ではその質問（ネイティブの追質問UIの場合もある）が
        # 未回答のまま開いている可能性が高い。ここで初回プロンプトを再送すると、
        # その未回答の質問へ誤って「実装してください」が回答として
        # 貼り付けられてしまうため、再送せず質問処理を優先する。
        resuming_question = None
        if self.args.mode == "new":
            if self.question_file.exists():
                resuming_question = self.read_question()
            if resuming_question is not None:
                self.archive_control_file(self.summary_file)
                self.archive_control_file(self.rework_file)
            else:
                self.clear_control_files()
        else:
            # 再実装では rework 指示だけ残し、質問/サマリーは消す。
            self.archive_control_file(self.question_file)
            self.archive_control_file(self.summary_file)

        teams.notify_started(
            self.issue_number, self.issue["title"], self.branch, self.log
        )

        if resuming_question is None:
            self.send_to_cline(self.build_prompt(), first_time=True)
        else:
            self.log.info(
                "前回セッションの未回答の質問を検出しました。"
                "実装プロンプトは再送せず、質問処理を継続します。"
            )

        completion = self.wait_for_implementation()
        if not completion:
            statefile.update(
                self.issue_number,
                {"status": statefile.Status.FAILED, "error": "実装待機タイムアウト"},
            )
            teams.notify_failed(
                self.issue_number, "実装の完了を検出できませんでした。", self.log
            )
            return 1

        is_fallback = completion == "fallback"

        if is_fallback:
            # サマリーファイルは存在しない想定だが、念のため読めるものは残す。
            summary = self.read_summary() or self.build_fallback_summary()
            statefile.update(
                self.issue_number,
                {"completionFallback": True, "completionFallbackAt": now_iso()},
            )
        else:
            summary = self.read_summary()

        if self.rework_file.exists():
            self.archive_control_file(self.rework_file)

        if not self.commit_and_push(summary):
            statefile.update(
                self.issue_number,
                {"status": statefile.Status.FAILED, "error": "変更が検出できません"},
            )
            teams.notify_failed(
                self.issue_number, "実装の変更が検出できませんでした。", self.log
            )
            return 1

        changed_files = gitops.committed_files(
            self.worktree, CONFIG.base_branch, self.branch
        )
        changed_files = [
            path
            for path in changed_files
            if not prompt.is_control_path(path)
        ]

        preview_url = self.deploy_preview(changed_files)
        pr_number, pr_url = self.ensure_draft_pr(summary, changed_files)
        self.write_waiting(summary, changed_files, preview_url, pr_number, pr_url)

        self.log.rule("実装フェーズ完了。Teams承認待ちへ移行します。")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Issue 1件分の実装ワーカー。")
    parser.add_argument("issue", type=int)
    parser.add_argument("--mode", choices=("new", "rework"), default="new")
    parser.add_argument("--no-vscode", action="store_true")
    parser.add_argument("--manual", action="store_true")
    parser.add_argument(
        "--locked-by-orchestrator",
        action="store_true",
        help="オーケストレーターが既にIssueロックを取得している",
    )
    args = parser.parse_args()

    CONFIG.validate()
    CONFIG.ensure_directories()
    gitops.require_command("git")
    gitops.require_command("gh")

    lock_path = None
    if not args.locked_by_orchestrator:
        lock_path = issue_lock_path(args.issue)
        if not try_acquire(lock_path, note="implement_agent"):
            print(f"Issue #{args.issue} は他プロセスが処理中です。")
            return 0

    try:
        return Worker(args.issue, args).run()
    finally:
        if lock_path is not None:
            release(lock_path)


if __name__ == "__main__":
    sys.exit(main())
