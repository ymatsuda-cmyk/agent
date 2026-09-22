<#
.SYNOPSIS
    AIエージェント基盤の初期セットアップ。

.DESCRIPTION
    1. 環境変数を設定する（setx：ユーザー環境変数）
    2. OneDrive上の状態別フォルダとdoneフォルダを作成する
    3. このプロジェクト専用のPython仮想環境を作り、依存パッケージを入れる
    4. tools / tools-beta のクローンを確認する

    仮想環境は既定でこのリポジトリ直下の .venv に作る。
    「たまたま別プロジェクトのvenvがアクティベートされていた」ことに気づかず
    そちらへ依存パッケージを入れてしまう事故（別の仮想環境を掴んで
    常駐プロセスが二重に起動する等）を防ぐため、python.exe を
    PATH上のもの任せにせず、このvenv内のものを明示して使う。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\setup.ps1

.EXAMPLE
    # 既存の.venvを作り直したい場合
    powershell -ExecutionPolicy Bypass -File .\setup.ps1 -RecreateVenv
#>

param(
    [string]$GitHubOwner   = "ymatsuda-cmyk",
    [string]$GitHubRepo    = "tools",
    [string]$GitHubBeta    = "tools-beta",
    [string]$AgentRoot     = "$env:OneDriveCommercial\work\agent",
    [string]$TargetRepo    = "C:\repo\tools",
    [string]$BetaRepo      = "C:\repo\tools-beta",
    [string]$WorktreeRoot  = "C:\repo\worktrees",
    [string]$VenvPath      = (Join-Path $PSScriptRoot "..\.venv"),
    [int]   $MaxParallel   = 3,
    [switch]$SkipPip,
    [switch]$RecreateVenv
)

$ErrorActionPreference = "Stop"

function Write-Section($text) {
    Write-Host ""
    Write-Host ("=" * 60)
    Write-Host $text
    Write-Host ("=" * 60)
}

# ------------------------------------------------------------
Write-Section "1. 環境変数の設定"

if (-not $env:OneDriveCommercial -and $AgentRoot -like "*\work\agent") {
    Write-Warning "OneDriveCommercial が未設定です。-AgentRoot で明示してください。"
}

$variables = [ordered]@{
    "GITHUB_OWNER"        = $GitHubOwner
    "GITHUB_REPO"         = $GitHubRepo
    "GITHUB_BETA_REPO"    = $GitHubBeta
    "AGENT_ROOT"          = $AgentRoot
    "TARGET_REPO_PATH"    = $TargetRepo
    "BETA_REPO_PATH"      = $BetaRepo
    "AGENT_WORKTREE_ROOT" = $WorktreeRoot
    "AGENT_MAX_PARALLEL"  = "$MaxParallel"
    "BASE_BRANCH"         = "main"
}

foreach ($key in $variables.Keys) {
    [Environment]::SetEnvironmentVariable($key, $variables[$key], "User")
    Set-Item -Path "env:$key" -Value $variables[$key]
    Write-Host ("[OK] {0} = {1}" -f $key, $variables[$key])
}

if (-not [Environment]::GetEnvironmentVariable("GITHUB_TOKEN", "User")) {
    Write-Host ""
    Write-Warning "GITHUB_TOKEN が未設定です。以下を実行してください。"
    Write-Host '    setx GITHUB_TOKEN "github_pat_xxxxxxxxxxxx"'
}

# ------------------------------------------------------------
Write-Section "2. 状態別フォルダの作成"

$folders = @("request", "question", "decision", "waiting", "approval", "reply", "state")

foreach ($folder in $folders) {
    $path = Join-Path $AgentRoot $folder
    $done = Join-Path $path "done"
    New-Item -ItemType Directory -Force -Path $path | Out-Null
    New-Item -ItemType Directory -Force -Path $done | Out-Null
    Write-Host "[OK] $path (+done)"
}

foreach ($sub in @("logs", "state\locks", "state\processed", "state\archive")) {
    $path = Join-Path $AgentRoot $sub
    New-Item -ItemType Directory -Force -Path $path | Out-Null
    Write-Host "[OK] $path"
}

New-Item -ItemType Directory -Force -Path $WorktreeRoot | Out-Null
Write-Host "[OK] $WorktreeRoot"

# ------------------------------------------------------------
Write-Section "3. Python仮想環境と依存パッケージ"

if ($SkipPip) {
    Write-Host "スキップしました。"
} else {
    $venvPython = Join-Path $VenvPath "Scripts\python.exe"

    if ($RecreateVenv -and (Test-Path $VenvPath)) {
        Write-Host "既存の仮想環境を削除します: $VenvPath"
        Remove-Item -Recurse -Force $VenvPath
    }

    if (-not (Test-Path $venvPython)) {
        Write-Host "仮想環境を作成します: $VenvPath"

        # 「今アクティベートされている別プロジェクトのvenv」を誤って
        # 参照しないよう、可能なら py ランチャーで素のPythonから作る。
        # py が無い環境では python にフォールバックする
        # （その場合でも python -m venv 自体は常に独立した新しい環境を作る
        #   ので、作成そのものは安全）。
        if (Get-Command py -ErrorAction SilentlyContinue) {
            py -3 -m venv $VenvPath
        } else {
            python -m venv $VenvPath
        }

        if (-not (Test-Path $venvPython)) {
            Write-Error "仮想環境の作成に失敗しました: $VenvPath"
            exit 1
        }
    } else {
        Write-Host "[OK] 既存の仮想環境を使用します: $VenvPath"
    }

    $requirements = Join-Path $PSScriptRoot "..\agent\requirements.txt"

    # 常にこのvenv内のpython.exeをフルパスで指定する。
    # 「python」とだけ書くと、実行時にPATH上の別のpython
    # （＝アクティベート中の別プロジェクトのvenv）を拾ってしまう。
    & $venvPython -m pip install --upgrade pip
    & $venvPython -m pip install -r $requirements

    Write-Host "[OK] 依存パッケージをインストールしました: $VenvPath"
}

# ------------------------------------------------------------
Write-Section "4. リポジトリの確認"

if (-not (Test-Path (Join-Path $TargetRepo ".git"))) {
    Write-Warning "$TargetRepo が未クローンです。以下を実行してください。"
    Write-Host "    gh repo clone $GitHubOwner/$GitHubRepo $TargetRepo"
} else {
    Write-Host "[OK] $TargetRepo"
}

if (-not (Test-Path (Join-Path $BetaRepo ".git"))) {
    Write-Warning "$BetaRepo が未クローンです。以下を実行してください。"
    Write-Host "    gh repo clone $GitHubOwner/$GitHubBeta $BetaRepo"
} else {
    Write-Host "[OK] $BetaRepo"
}

# ------------------------------------------------------------
Write-Section "セットアップ完了"

$activateScript = Join-Path $VenvPath "Scripts\Activate.ps1"

Write-Host "新しいPowerShellを開き直し、このプロジェクト専用の仮想環境を"
Write-Host "アクティベートしてから作業してください。"
Write-Host "（別プロジェクトのvenvがアクティベートされたままだと、"
Write-Host "  依存パッケージやpythonの参照先が混線します。）"
Write-Host ""
Write-Host "    cd $(Split-Path $PSScriptRoot -Parent)"
Write-Host "    $activateScript"
Write-Host "    python agent\agent_cli.py doctor"
Write-Host ""
Write-Host "プロンプトの先頭に '(.venv)' と出ていること、"
Write-Host "そのパスがこのプロジェクト（$(Split-Path $PSScriptRoot -Parent)）配下"
Write-Host "であることを必ず確認してください。"
Write-Host ""
