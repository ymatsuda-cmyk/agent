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

### Python仮想環境について（重要）

**このプロジェクト専用の仮想環境を必ず使ってください。**
別プロジェクトの仮想環境がアクティベートされたまま `setup.ps1` や
`pip install` を実行すると、依存パッケージがそちらに入ってしまい、
`python` コマンドの参照先も混線します。実際に、別プロジェクトの
仮想環境から常駐エージェントを誤って起動してしまい、このプロジェクトの
常駐エージェントと二重に動作して `git` の状態を壊した事例があります
（`docs/04_operations.md` 参照）。

`scripts/setup.ps1` は、このリポジトリ直下に専用の `.venv` を
自動で作り、以後の `pip install` は常にそのvenv内の `python.exe` を
フルパスで指定して実行します。**手動で仮想環境をアクティベートしてから
`setup.ps1` を実行する必要はありません**（むしろ、他プロジェクトの
venvがアクティベートされたままでも、venv作成自体には影響しません）。

セットアップ後は、必ずこのプロジェクトの `.venv` をアクティベートしてから
`agent_cli.py` 等を実行してください。

```powershell
cd C:\repo\agent\cline   # このリポジトリのルート
.\.venv\Scripts\Activate.ps1
```

プロンプトの先頭に `(.venv)` と出ますが、**それだけでは「どのプロジェクトの
venvか」は分かりません。** 迷ったら次で確認してください。

```powershell
# 今アクティブなpythonの実体パスを確認する
Get-Command python | Select-Object Source
# このプロジェクトの .venv 配下になっているか（例: C:\repo\agent\cline\.venv\Scripts\python.exe）
```

既存の `.venv` を作り直したい場合は `-RecreateVenv` を付けます。

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -RecreateVenv
```

`scripts/start_all.ps1`（常駐エージェントの起動）は、このvenvの
`python.exe` を絶対パスで直接指定して起動するため、起動元シェルの
アクティベート状態に依存しません。手動で `agent_cli.py` 等を実行する
ときだけ、上記のアクティベートを忘れないようにしてください。

### `.ps1` ファイルの文字コードについて（重要）

`scripts/*.ps1` には日本語コメントが含まれています。
**Windows PowerShell 5.1**（`powershell.exe`。`pwsh.exe` ではない方）は、
UTF-8だがBOMが無いファイルを、UTF-8ではなくシステムのロケール
（日本語Windowsなら Shift-JIS）で読み込もうとします。
その結果、日本語部分が誤読され、

```
式またはステートメントのトークン '}' を使用できません。
文字列に終端記号 " がありません。
```

のような、一見無関係な構文エラーが連鎖して出ます。

このリポジトリの `.ps1` ファイルは最初から **UTF-8 with BOM** で保存されているため、
そのまま使う分には問題ありません。ただし、エディタで編集して保存し直すと
BOMが失われることがあります（VS Codeは既定でBOM無し保存です）。
編集した場合は、保存形式を「UTF-8 (BOM付き)」に指定し直してください。

同じエラーが出た場合は、まずこれを疑ってください。

```powershell
# ファイルにBOMが付いているか確認する
[System.IO.File]::ReadAllBytes(".\scripts\setup.ps1")[0..2] -join ","
# 239,187,191 ならBOM付き。それ以外ならBOM無し。
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
| `AGENT_CLINE_TAB_X` | | `1240` | Clineタブの座標（他の拡張とタブ共有時のみ使用） |
| `AGENT_CLINE_TAB_Y` | | `50` | 同上。`0`（既定）ならタブクリックをスキップする |
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

**Claude Codeなど、他の拡張機能とサイドバーのタブを共有している場合**、
VS Code起動直後はCline以外のタブがアクティブなことがあり、
入力欄をクリックしても実際にはCline以外の場所に貼り付けてしまいます。
その場合は `AGENT_CLINE_TAB_X` / `AGENT_CLINE_TAB_Y` に、
Clineタブ自体の座標を設定してください。入力欄をクリックする前に、
自動でこのタブへ切り替えるようになります。未設定（既定値`0`）の場合は
タブ切り替えを行いません。

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
