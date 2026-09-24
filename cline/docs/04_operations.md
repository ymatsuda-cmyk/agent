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

## 2.5 Issue状態ダッシュボード（外出先からも確認したい場合）

並走数が増えると、`agent_cli.py status` のテキスト出力だけでは
「今どのIssueがどこで詰まっているか」を把握しづらくなります。
`dashboard_agent.py` は、`state/` の変化を検知するたびに
Issueの生データをJSONとして `tools-beta`（GitHub Pages）へ
自動でpushし続けます。表示側は静的なHTML/JSアプリで、起動時と
30秒ごとにこのJSONを取得してブラウザ側でカンバン表示を組み立てます。

```powershell
# 単体で起動する場合
python agent\dashboard_agent.py

# start_all.ps1 と同時に起動する場合
powershell -ExecutionPolicy Bypass -File .\scripts\start_all.ps1 -Parallel 3 -Dashboard
```

公開先はプレビューと同じ `tools-beta` の別ディレクトリです。
既存の `data/minutes/index.json` と同じ「`data/<用途>/`にJSONを置く」
という、このリポジトリ自体の慣習に合わせています。

```
データ: tools-beta の data/agent/dashboard.json（更新のたびにpush）
表示  : tools-beta の utility/agent.html（初回のみ作成。以降は固定）
URL   : https://<owner>.github.io/tools-beta/utility/agent.html
```

この分離により、**HTML自体はほぼ動かず、以降のpushはJSONの差分だけ**
になります。`utility/agent.html` を見た目やレイアウトの都合で
手直ししても、次回以降のダッシュボード更新で上書きされません
（`ensure_agent_html()` は既にあるファイルを上書きしない設計です）。

このURLをスマホでブックマークしておけば、外出先や携帯回線からでも
確認できます。ページはJavaScriptが30秒ごとにJSONを再取得して
表示を更新するので、ページ全体の再読み込み（ちらつき）は起きません。

**注意点**

- `tools-beta` がGitHub上で公開リポジトリの場合、**このURLを知っていれば
  誰でもIssueのタイトルや状態を閲覧できます**（既存のプレビューURLと
  同じ公開範囲です）。社外に見せたくない情報が含まれる場合は、
  `tools-beta` を非公開にするか、このスクリプト自体を使わない運用に
  してください。
- 反映には数十秒〜1、2分のタイムラグがあります（更新間隔30秒 ＋
  GitHub Pagesのビルド時間）。厳密なリアルタイム性が必要な確認には
  `agent_cli.py status` を直接使ってください。
- `deploy_preview.py` と同じ `tools-beta` クローン・同じロックを
  使うため、プレビュー公開中でも安全に共存します。

### 表示の見方（担当者別カンバン）

列は「状態」ではなく**「今だれ待ちか」**で分かれています。各列の中と、
下部の「終了」一覧は、最終更新が新しい順に並びます。

| 列 | 含まれる状態 | 意味 |
| --- | --- | --- |
| 人の対応待ち | `waiting_decision` `waiting_approval` `failed` | Teamsでの回答・承認、または停止したIssueへの修正指示（`agent_cli.py rework`）が必要 |
| Cline作業中 | `implementing` | VS Code上でClineが実装中 |
| 自動連携待ち | 稼働中のIssueで、未送信の通知が3分以上残っている | Power AutomateがTeamsへまだ送っていない |
| システム処理中 | `created` `queued` `rework` `preview_deploying` `approved` | Pythonの着手・公開・マージ処理中 |

長時間そのままのものは赤字で警告します（Cline作業中が1時間超で
「長時間・停止の可能性」、質問回答待ち1時間超・承認待ち2時間超で
「放置」、システム処理中が30分超で「停滞」）。

**「自動連携待ち」の判定方法と限界**: フロー④（reply→Teams）は投稿後に
`reply/done/` へファイルを移動するので、`reply/` に3分以上残っている通知が
あれば「Power Automateが止まっている/遅れている」と判定できます。
一方、質問カード（フロー②）と承認カード（フロー③）は、Teamsへ投稿済みか
どうかをファイルから観測する手段がありません。そのため `waiting_decision`
`waiting_approval` は常に「人の対応待ち」として表示され、カード自体が
Teamsに届いていないケースとは区別できません。区別したい場合は、
フロー②③の最後に「投稿完了」を示すファイルを書き出す（または
`question/`・`waiting/` のファイルを `done/` へ移動する）処理を追加する
必要があります。

`state/done/` に退避済みのIssueと、完了・却下したIssueは、状態の文字列や
未送信通知の有無に関係なく、下部の「終了」一覧に表示されます。完了・却下
以外のまま退避されたものは「退避済み（実装中のまま）」のように赤字で表示
されるので、GitHub上のIssueがまだopenのまま取り残されていないか確認する
目安にしてください。

各カードと終了一覧の各行に「Issue」「PR」「プレビュー」のリンクを表示します。
状態ファイルにPRのURLが無い古いIssueでは、「PR」はブランチ名で
GitHubのPR一覧を検索するページへ飛びます。

**「未送信通知N件」が大量に残っている場合**: フロー④が「Teamsへの投稿は
成功するが、その後の `reply/done/` への移動に失敗している」可能性が高いです。
初期版の `04_reply_to_teams.zip` はファイル移動の設定名に誤り（`sourceFileId`、
正しくは `id`）があったため、そのままインポートしたフローは毎回この状態になります。
`tools/build_flows.py` で再生成したzipを再インポートするか、Power Automate上で
該当アクションの設定を修正してください。修正後、溜まっている古い通知は
次で `done/` へ移動できます（既にTeamsへ投稿済みのものを再送しないため）。

```powershell
Get-ChildItem "$env:AGENT_ROOT\reply" -Filter "*.json" |
    Where-Object LastWriteTime -lt (Get-Date).AddHours(-1) |
    Move-Item -Destination "$env:AGENT_ROOT\reply\done"
```

**表示HTMLの更新**: `utility/agent.html` は `dashboard_agent.py` 内の
テンプレートから生成され、テンプレートのバージョン（`AGENT_HTML_VERSION`）が
上がると、公開済みの自動生成版は次回の更新時に自動で置き換わります。
手で作り替えたファイル（バージョン情報のmetaタグが無いもの）は上書きしません。
自動生成版を手直しして今後も上書きされたくない場合は、ファイル先頭の
`<meta name="agent-dashboard-version" ...>` を削除してください。

## 3. よくあるトラブル

### `request` `question` `state` などのフォルダが、意図しない場所（リポジトリ直下など）にできている

**症状**: `AGENT_ROOT`（OneDrive上のはず）ではなく、
`agent_cli.py` を実行したときのカレントディレクトリに
`request/` `question/` `state/` 等が作られている。

**原因（根本原因・修正済み）**: `AGENT_ROOT` が未設定のとき、
Pythonの `pathlib.Path("")` は例外にならず、**暗黙的にカレント
ディレクトリを指します。** かつて `agent_cli.py` は、他のスクリプト
（`issue_agent.py` 等）が必ず行っている環境変数チェック
（`CONFIG.validate()`）を呼ばずに `CONFIG.ensure_directories()` を
実行していたため、`AGENT_ROOT` の設定漏れに気づかないまま、
実行時にいた場所へ静かにフォルダを作ってしまうことがありました。

**対策（このリポジトリでは対応済み）**:
`agent_cli.py` は `doctor` 以外の全コマンドで、実行前に必ず
`CONFIG.validate()` を通すようにしました。未設定なら、フォルダを
作る前に明確なエラーで止まります。あわせて `CONFIG.ensure_directories()`
自体にも二重の安全網を入れ、`validate()` の呼び忘れが今後どこかで
あっても事故らないようにしています。`doctor` コマンドは診断のため
あえてこのチェックを通さず、`[必須環境変数]` を最初に個別表示します。

**既にできてしまったフォルダの後始末**:

```powershell
# 中身が空、または明らかにこのプロジェクトのものだと確認できたら削除する
cd C:\repo\agent\cline   # 意図せずフォルダができていた場所
Remove-Item -Recurse -Force .\request, .\question, .\decision, .\waiting, .\approval, .\reply, .\state, .\logs -ErrorAction SilentlyContinue
```

削除前に、`waiting/` や `state/` に実際の作業内容（未承認のIssueの
記録など）が入っていないか、中身を確認してください。

### 別プロジェクトのvenvと混線し、常駐プロセスが二重起動する

**症状**: `git worktree` が `main` を掴んだまま残っている、
無関係なコミットが `feature/` の付かない古いブランチに紛れ込む、
`agent_cli.py status` と実際のIssueの状態が食い違う。

**原因**: `python` コマンドを何もパス指定せずに使うと、そのときPATH上に
たまたまアクティベートされている**どのプロジェクトのvenvか**に依存します。
別プロジェクトのvenvがアクティベートされたまま常駐エージェントを起動すると、
気づかないままそちらのvenvから起動してしまい、**同じ `AGENT_ROOT` ・
同じ `TARGET_REPO_PATH` を取り合う、もう1系統の常駐プロセス**が
生まれます。両者は互いを認識できないため、ロックが正しく機能せず、
`git checkout` の奪い合いやコミットの混入が起きます。

**対策（このリポジトリでは対応済み）**:
`scripts/setup.ps1` はプロジェクト専用の `.venv` を作り、
`scripts/start_all.ps1` はその `python.exe` を絶対パスで直接起動するため、
起動元シェルのアクティベート状態に依存しません。

**既に混線してしまった場合の切り分け方**:

```powershell
# 今動いている全python.exeの実体パスを確認する
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Select-Object ProcessId, CommandLine | Format-List
```

`issue_agent.py` / `orchestrator.py` / `approval_agent.py` が
**2組以上**、あるいは**このプロジェクトの `.venv` 以外**から
起動されていないか確認してください。見つかったら安全な方だけ残して
`Stop-Process -Id <PID> -Force` で個別に停止し、その後
`agent_cli.py unlock --force` でロックを掃除してください。

### `Get-Content` でログを見ると日本語が文字化けする

`.ps1` と同じ系統の問題です。`Get-Content` を `-Encoding` 指定無しで
使うと、BOM無しのUTF-8ファイルをシステムのロケール（日本語Windowsなら
Shift-JIS）で読もうとし、文字化けします。

`agent_core/logs.py` は、ログファイル作成時に先頭へBOMを付与するよう
修正済みです（新しく作られるログファイルから有効。既存のログファイルは
BOM無しのまま残ります）。それでも文字化けする場合は、次のいずれかで
回避してください。

```powershell
Get-Content .\logs\issue-12.log -Encoding UTF8
```

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

### `pytest` を実行すると、本番のOneDrive上にテストのIssueが混入する（原因確定・修正済み）

**症状**: `agent_cli.py status` や生成されたダッシュボードに、
タイトルが `"t"` や `"却下されたIssue"` など、実際に依頼した覚えのない
Issueが表示される。

**原因**: `Config` は `state` フォルダ等を個別に上書きできる
（`AGENT_STATE_DIR` 等、`docs/02_setup.md` 参照）。この上書きは
`AGENT_ROOT` より優先される。もし実行環境（Windowsのユーザー環境変数）に
`AGENT_STATE_DIR` が過去の設定作業などで実際に残っていると、
テストが `AGENT_ROOT` をどれだけ一時フォルダへ差し替えても、
**`state` フォルダだけは常に本番のOneDriveを指し続ける。**
テストのフィクスチャは以前 `AGENT_ROOT` 等の主要な変数しか
上書きしておらず、この個別上書きキーには無防備だった。

**対策（修正済み）**: `agent/tests/conftest.py` の `agent_env`
フィクスチャは、`AGENT_ROOT` を設定する前に、`STATE_FOLDERS` の
個別上書きキー（`AGENT_REQUEST_DIR` `AGENT_STATE_DIR` 等）を
すべて明示的に削除するようにした。実行環境にこれらが実在していても、
テスト中は無視される。

**もし既にこの経路で本番stateが汚染されている場合**:

```powershell
Get-ChildItem Env: | Where-Object { $_.Name -like 'AGENT_*' }
```

`AGENT_STATE_DIR` 等が実際に設定されていたら削除する
（`[Environment]::SetEnvironmentVariable("AGENT_STATE_DIR", $null, "User")`）。
そのうえで、`state/` に紛れ込んだテスト由来のIssue
（タイトルが `"t"` 等、明らかにテストコードのものだと分かるもの）を
個別に削除する。

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

### 静的チェック不合格（`failed`）から、Teamsを介さず修正を指示したい

`check_static()`（HTMLタグ対応・リンク切れ）で `failed` になったIssueは、
承認カードが既に消費済みで「再実装」ボタンが使えない。
`agent_cli.py rework` は、承認カードの「再実装」ボタンと**全く同じ処理**
（`approval_agent.handle_rework`）を直接呼び出すコマンドで、承認カード無しに
同じブランチ・同じworktreeでClineへ修正指示を出せる。

```powershell
python agent\agent_cli.py rework 77 "clipstock/index.htmlのHTMLタグの対応が壊れています。閉じタグに対応する開始タグが7箇所見つかりませんでした。修正してください。修正が終わったら教えてください。"
```

`status` が `rework` に戻り、常駐している `orchestrator` が拾って
自動的に再開する。常駐していなければ次で単体実行できる。

```powershell
python agent\orchestrator.py --once --min-issue 77 --max-issue 77
```

**Teamsに自由なテキストを打ち込んで、それを特定のIssueへの追加指示として
自動で拾う経路は、現時点では無い。** 依頼チャネルへの新規投稿は別のIssueを
作るだけで、既存のworktree/ブランチは再利用されない。この用途で
Teams発の自由入力を使えるようにするには、別途Power Automateフローの
追加が必要（`rework`と同じ状態遷移をトリガーする新しいフロー）。

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
| VS Codeが最大化されない | `code` CLIには最大化オプションが無いため、起動後にWin32 APIで明示的に最大化している（`cline.maximize_window`）。pywinauto/windllの無い環境では失敗し、前回のウィンドウサイズを引き継ぐ |
| VS Code起動時に「このフォルダを信頼しますか」ダイアログが出てClineに投入できない | worktreeは毎回新規フォルダのため、VS Codeが信頼確認ダイアログを出しClineの入力欄を塞ぐことがある。`launch_vscode()` は `--disable-workspace-trust` を付けて起動し、このダイアログ自体を出さないようにしている |
| 承認・却下・再実装の詳細をTeamsの元スレッドにも残したい | `agent_core/teams.py` の `notify_decision()` を使うと、実装内容・確認した内容・未実施の確認・修正指示を、元の依頼スレッドへ返信として残せる |
| 再実装（rework）でPRの本文が初回のまま古い | `finish_agent`/`implement_agent` は既存PRを再利用する際、`ghcli.update_pull_request_body()` で本文を最新の実装内容に上書きする |
| 承認・却下・再実装の結果をGitHub Issue側にも記録として残したい | `approval_agent.py` は承認結果ファイルを処理する際、Teamsへの返信（`notify_decision`）に加えて、同じ内容を `ghcli.comment_issue()` でIssueコメントとしても投稿する。Teamsのスレッドが流れても、GitHub側に恒久的な記録が残る |
| プレビュー公開後、ファイル移動をしたIssueでURLが404になる | `changed_files` に移動元（削除済み）のパスも含まれる場合、そちらを`index.html`候補として誤って選んでしまうことがあった。`build_preview_url()` に実在確認（`preview_root`/`worktree`基準）を追加し、実際に配置されているファイルだけを候補にするよう修正済み |
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
