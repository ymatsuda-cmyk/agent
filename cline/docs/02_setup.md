# 02. 導入手順

## 1. 前提ソフトウェア

| ソフト | 用途 | 確認 |
| --- | --- | --- |
| Python 3.10以上 | エージェント本体 | `python --version` |
| Git 2.30以上 | worktree を使うため | `git --version` |
| GitHub CLI (gh) | Issue/PR/マージ操作 | `gh --version` |
| VS Code + Cline拡張 | 実装 | `code --version` |
| OneDrive 同期クライアント | 状態フォルダ | — |

`gh` は先に認証しておきます。

```powershell
gh auth login
gh auth status
```

## 2. GitHubの準備

### 2.1 リポジトリ

| リポジトリ | 役割 |
| --- | --- |
| `<owner>/tools` | 本体。`main` が本番 |
| `<owner>/tools-beta` | 承認前の動作確認用。GitHub Pages を有効化する |

`tools-beta` の **Settings → Pages** で、
Source を `Deploy from a branch`、Branch を `main / (root)` にします。

### 2.2 Personal Access Token

Issue作成に使います（Fine-grained推奨）。

- Repository access: `tools`
- Permissions: `Issues: Read and write`, `Contents: Read and write`, `Pull requests: Read and write`

### 2.3 クローン

```powershell
gh repo clone <owner>/tools      C:\repo\tools
gh repo clone <owner>/tools-beta C:\repo\tools-beta
```

`C:\repo\worktrees` は自動生成されるので作成不要です。

> OneDrive同期フォルダの中にリポジトリを置かないでください。
> `.git` が同期対象になると破損します。`C:\repo` のようなローカルパスを使います。

## 3. セットアップスクリプト

```powershell
cd ai-agent-system
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1 `
    -GitHubOwner "ymatsuda-cmyk" `
    -GitHubRepo "tools" `
    -GitHubBeta "tools-beta" `
    -TargetRepo "C:\repo\tools" `
    -BetaRepo "C:\repo\tools-beta" `
    -WorktreeRoot "C:\repo\worktrees" `
    -MaxParallel 3
```

トークンだけは別途設定します。

```powershell
setx GITHUB_TOKEN "github_pat_xxxxxxxxxxxx"
```

設定後、**PowerShellを開き直してください**（`setx` は既存プロセスに反映されません）。

## 4. 環境変数一覧

| 変数 | 必須 | 既定 | 説明 |
| --- | --- | --- | --- |
| `GITHUB_TOKEN` | ○ | — | Issue作成用PAT |
| `GITHUB_OWNER` | ○ | — | リポジトリのオーナー |
| `GITHUB_REPO` | ○ | — | 本体リポジトリ名 |
| `GITHUB_BETA_REPO` | | `tools-beta` | 検証用リポジトリ名 |
| `AGENT_ROOT` | ○ | — | OneDrive上の `work\agent` |
| `TARGET_REPO_PATH` | ○ | — | 本体クローンのパス |
| `BETA_REPO_PATH` | | 本体の隣 | 検証用クローンのパス |
| `AGENT_WORKTREE_ROOT` | | `<本体の親>\worktrees` | worktree置き場 |
| `BASE_BRANCH` | | `main` | ベースブランチ |
| `AGENT_MAX_PARALLEL` | | `3` | 同時実行数 |
| `AGENT_GUI_MODE` | | `auto` | `manual` にすると手動貼り付け |
| `AGENT_CLINE_INPUT_X` | | `1500` | Cline入力欄のX座標 |
| `AGENT_CLINE_INPUT_Y` | | `900` | Cline入力欄のY座標 |
| `AGENT_IMPLEMENTATION_TIMEOUT` | | `1800` | 実装待ち秒数 |
| `AGENT_DECISION_TIMEOUT` | | `7200` | 回答待ち秒数 |
| `AGENT_GUI_LOCK_TIMEOUT` | | `1800` | GUIロック待ち秒数 |

個別にフォルダを変えたい場合は `AGENT_REQUEST_DIR` など
`AGENT_<名前>_DIR` で上書きできます（既定は `AGENT_ROOT` 配下）。

## 5. Cline入力欄の座標を合わせる

自動投入（`AGENT_GUI_MODE=auto`）を使う場合、
Clineのチャット入力欄の座標を教える必要があります。

```powershell
python agent\agent_cli.py test-click
```

5秒後に指定座標をクリックして文字を打ちます。
入力欄に入らない場合は、Windowsの「拡大縮小」が100%か確認し、
`AGENT_CLINE_INPUT_X` / `AGENT_CLINE_INPUT_Y` を調整してください。

> 座標指定が安定しない環境では `AGENT_GUI_MODE=manual` を推奨します。
> Ctrl+V → Enter の2操作だけ人が行う運用になり、並走も問題なく動きます。

## 6. 点検

```powershell
python agent\agent_cli.py doctor
```

コマンド・Pythonモジュール・フォルダ・リポジトリ・gh認証を確認します。
すべて `[OK]` になれば準備完了です。

あわせて自動テストも通しておくと、環境固有の問題ではなく
コード側の問題かどうかの切り分けがしやすくなります。

```powershell
pip install -r agent\requirements-dev.txt
pytest agent
```

## 7. Power Automate

`docs/03_power_automate.md` を参照してください。

## 8. 起動

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_all.ps1 -Parallel 3
```

3つのPowerShellウィンドウが開きます。

- `AGENT: issue` … Teams投稿の監視
- `AGENT: orchestrator` … 実装ワーカーの割り当て
- `AGENT: approval` … 承認結果の処理

停止は `scripts\stop_all.ps1` です。

## 9. 動作確認

一気に全部（Teams投稿→承認）を試すと、途中で止まったときに
Python・Power Automate・Teams・GitHubのどこが原因か切り分けられません。

`docs/05_verification.md` に、Teamsを経由しないPython単体の確認から
段階的にレイヤーを足していくチェックリストがあります。
まずはそちらに沿って進めてください。

途中で止まった場合は次で状態を確認します。

```powershell
python agent\agent_cli.py status
python agent\agent_cli.py show <Issue番号>
```
