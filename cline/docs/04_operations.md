# 04. 運用とトラブルシュート

## 1. 日常の操作

```powershell
# 起動 / 停止
powershell -ExecutionPolicy Bypass -File .\scripts\start_all.ps1 -Parallel 3
powershell -ExecutionPolicy Bypass -File .\scripts\stop_all.ps1

# 状態確認
python agent\agent_cli.py status
python agent\agent_cli.py status --status waiting_approval
python agent\agent_cli.py show 12

# 手動操作
python agent\agent_cli.py retry 12       # failed / rejected を着手待ちへ戻す
python agent\agent_cli.py approve 12     # Teamsを使わず承認
python agent\agent_cli.py reject 12      # 却下（Issueはopenのままneeds-reworkを付与）
python agent\agent_cli.py unlock         # 残存ロックの掃除
python agent\agent_cli.py clean-worktrees --remove-completed
```

ログは `AGENT_ROOT\logs\` にあります。

| ファイル | 内容 |
| --- | --- |
| `issue-<N>.log` | Issue単位の全ログ（実装・マージ） |
| `orchestrator.log` | 割り当ての記録 |
| `issue_agent.log` | Issue作成の記録 |
| `approval_agent.log` | 承認結果の処理 |

## 2. 並走数の考え方

`AGENT_MAX_PARALLEL` は「同時に実装ワーカーを走らせる数」です。

| 値 | 向く状況 |
| --- | --- |
| 1 | まず動かして確認したいとき。従来の逐次実行と同じ |
| 2〜3 | 通常運用。GUI待ちが気にならない範囲 |
| 4以上 | `--manual` 運用や、Clineを複数ウィンドウで人が捌ける場合 |

ボトルネックは**Clineへの貼り付け（GUIロック）**ではなく、
実際には**Clineの実装時間**と**人の回答/承認待ち**です。
回答待ちのIssueはGUIを占有しないので、並走の効果はそこで出ます。

VS Codeウィンドウが増えて操作しづらい場合は、
`--no-vscode` で起動し、必要なときだけ人がworktreeを開く運用もできます。

## 3. よくあるトラブル

### `setup.ps1` / `start_all.ps1` / `stop_all.ps1` で無関係な構文エラーが大量に出る

```
式またはステートメントのトークン '}' を使用できません。
文字列に終端記号 " がありません。
```

のような、書き換えていない箇所でエラーが連鎖する場合、
`.ps1` ファイルのBOMが失われています（`docs/02_setup.md` 参照）。
Windows PowerShell 5.1 はBOM無しUTF-8を正しく読めず、
日本語コメントを誤読して以降の構文解析が崩れます。
エディタで保存し直した場合に起きやすい症状です。

```powershell
# UTF-8 (BOM付き) で保存し直す例
$content = Get-Content .\scripts\setup.ps1 -Raw -Encoding UTF8
[System.IO.File]::WriteAllText(
    (Resolve-Path .\scripts\setup.ps1),
    $content,
    (New-Object System.Text.UTF8Encoding($true))
)
```

### Issueが二重に作られる

- ①のTeams投稿JSONに `id`（Message ID）が入っているか確認する
- 依頼チャネルと通知チャネルが同じになっていないか確認する
- `state/processed/` にマーカーが残っているか確認する

### 承認しても何も起きない

1. ③フローの出力先が `/work/agent/approval-test` になっていないか
   （`docs/03_power_automate.md` 5-1 を参照）
2. `approval_agent.py` が起動しているか
3. `approval/` にJSONが残っていないか（残っていれば手動で1回動かす）

```powershell
python agent\approval_agent.py --once
```

### Teams通知が何度も届く

④フローに「ファイルの移動」アクションが入っていません。
`reply/done` への移動を追加してください。

### Clineにプロンプトが入らない

```powershell
python agent\agent_cli.py test-click
```

- Windowsの表示スケールを100%にする
- VS Codeを最大化する
- それでも安定しない場合は `AGENT_GUI_MODE=manual` にする

### worktreeの作成に失敗する

```powershell
git -C C:\repo\tools worktree list
git -C C:\repo\tools worktree prune
python agent\agent_cli.py clean-worktrees --remove-completed
```

同じブランチが2つのworktreeでチェックアウトされているとエラーになります。
`worktree list` で重複を確認してください。

### 「既に起動しています」と出るが起動していない

プロセスが強制終了されロックが残っています。

```powershell
python agent\agent_cli.py unlock
```

PIDが生きている場合はスキップされます。強制削除は `--force` です。

### マージが `BLOCKED` で止まる

- ブランチ保護でレビュー必須になっていないか
- 必須チェック（Actions）が落ちていないか

保護を残したままにする場合は、エージェント用に
「Allow specified actors to bypass required pull requests」を設定するか、
承認後に人がマージする運用（`finish_agent.py` のマージ処理を無効化）にします。

### 文字化けでマージが止まった

`finish_agent.py` は変更ファイルのUTF-8検査を行い、
文字化けを検出するとマージを中止して `failed` にします。

```powershell
# 該当ファイルをUTF-8 BOMなしで保存し直してから
python agent\agent_cli.py approve 12
```

Cline側の再発防止としては、プロンプトの
「ファイルはUTF-8 BOMなしで保存してください」を守らせることと、
VS Codeの `files.encoding` を `utf8` にしておくことです。

## 4. 障害時の復旧

プロセスが落ちても、`state/issue-<N>.json` に状態が残っています。

| 状態 | 復旧方法 |
| --- | --- |
| `queued` / `implementing` のまま止まった | `agent_cli.py retry <N>` で `created` へ戻す |
| `waiting_decision` のまま | `decision/issue-<N>.json` を手で置く |
| `waiting_approval` のまま | Teamsカードを押す、または `agent_cli.py approve <N>` |
| `approved` のまま | `python agent\finish_agent.py <N> --approve` を再実行 |
| `failed` | ログを見て原因を直し `retry` |
| `rejected`（GitHub Issueはopenのまま） | 内容を見直したうえで `retry <N>`。`state/done/` へ退避済みのstateを自動で復元してから `created` に戻す |

`retry` は `created` に戻すだけなので、worktreeと既存コミットは残ります。
まっさらからやり直したい場合は先にworktreeを消してください。
（`rejected` の場合は却下時に変更とブランチが既に破棄されているため、
worktreeは通常きれいな状態から再開します。）

```powershell
git -C C:\repo\tools worktree remove --force C:\repo\worktrees\issue-12
git -C C:\repo\tools branch -D feature/issue-12-xxx
python agent\agent_cli.py retry 12
```

## 5. 既存スクリプトからの移行

| 旧 | 新 | 備考 |
| --- | --- | --- |
| `issue_agent.py` | `agent/issue_agent.py` | 環境変数が `AGENT_ROOT` 集約に変更 |
| `run_agent_daemon.py` | `agent/orchestrator.py` | 逐次 → 並走 |
| `run_agent.py` | `agent/implement_agent.py` | worktree対応、PR作成まで担当 |
| `approval_agent.py` | `agent/approval_agent.py` | watchdog監視、done退避、rework対応 |
| `finish_agent.py` | `agent/finish_agent.py` | commit/pushは実装側へ移動、マージ担当に専念 |
| （なし） | `agent/deploy_preview.py` | tools-betaへの公開 |
| （なし） | `agent/agent_cli.py` | 運用コマンド |

### 主な違い

1. **コミットのタイミング**
   旧: 承認後に `finish_agent` がコミット
   新: 実装完了時点でコミット＆push＆ドラフトPR作成
   → 承認前にGitHub上で差分レビューできます。

2. **マージ**
   旧: PR作成まで（マージは人）
   新: 承認で `squash merge` → `Closes #N` でIssue自動クローズ

3. **作業ディレクトリ**
   旧: `TARGET_REPO_PATH` を直接 checkout（並走不可）
   新: `worktrees/issue-<N>`（並走可）

4. **環境変数**
   旧: `AGENT_REQUEST_DIR` などを個別に設定
   新: `AGENT_ROOT` だけで全フォルダを導出（個別上書きも可）

### 移行手順

1. 旧デーモンをすべて停止する
2. 進行中のIssueを片付ける（承認済みならマージまで終わらせる）
3. `scripts\setup.ps1` を実行する
4. 旧 `state/` のJSONは `status` の値が概ね互換なのでそのまま使えます
   （`implementation_started` → `implementing` へ手で直すか、`retry` で戻す）
5. Power Automate ③ の出力先を `/work/agent/approval` へ修正する
6. Power Automate ④ をインポートする
7. `start_all.ps1` で起動する

## 6. 拡張のヒント

| やりたいこと | 方法 |
| --- | --- |
| Issueにラベルを付けて分類 | `ghcli.create_issue(title, body, labels=[...])` |
| 特定ラベルだけ自動実装 | `orchestrator.dispatchable_issues()` に条件を追加 |
| CIの結果を承認カードに出す | `implement_agent.write_waiting()` に `gh pr checks` の結果を追加 |
| 夜間だけ動かす | `orchestrator.py` を Windows タスクスケジューラで起動/停止 |
| Cline以外のコーディングAI | `agent_core/cline.py` を差し替える（インターフェースは `inject_prompt` のみ） |
| 承認者を限定する | Power Automate ③ のカードを承認者へのメンション付きにし、`data` に承認者名を含める |

## 7. 自動テスト

```powershell
cd agent
pip install -r requirements-dev.txt
pytest
```

`agent/tests/` に133件の回帰テストがあります。OneDrive・GitHub・Teamsの
実物には触れず、隔離された一時ディレクトリとローカルのgitリポジトリだけで
完結するため、`gh` 未導入の開発機でもそのまま実行できます
（`gh` を実際に呼ぶ範囲— Issue作成・PR操作・マージ — はテスト対象外です）。

コードを変更したときは、次の観点でテストを見直してください。

| 変更する箇所 | 関連するテストファイル |
| --- | --- |
| `agent_core/jsonio.py` | `test_jsonio.py` |
| `agent_core/locks.py` | `test_locks.py`（特にホスト跨ぎの回収防止） |
| `agent_core/statefile.py` | `test_statefile.py` |
| `agent_core/gitops.py` | `test_gitops.py`（実gitリポジトリを使用） |
| `agent_core/cline.py` | `test_cline.py`（ウィンドウ特定の正規表現） |
| `agent_core/checks.py` | `test_checks.py` |
| `agent_core/prompt.py` | `test_prompt.py` |
| `deploy_preview.py` | `test_deploy_preview.py` |
| `issue_agent.py` | `test_issue_agent.py` |
| `approval_agent.py` | `test_approval_agent.py` |
| `implement_agent.py` | `test_implement_agent.py`（questionId照合、完了フォールバック） |
| `finish_agent.py` | `test_finish_agent.py`（静的チェック、却下フロー） |

新しい振る舞いを追加したときは、まずテストを書いてから実装すると、
`agent_cli.py doctor` では拾えない細かい条件分岐の崩れに気づきやすくなります。
