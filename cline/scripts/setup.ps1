<#
.SYNOPSIS
    AIエージェント基盤の初期セットアップ。

.DESCRIPTION
    1. 環境変数を設定する（setx：ユーザー環境変数）
    2. OneDrive上の状態別フォルダとdoneフォルダを作成する
    3. Pythonの依存パッケージをインストールする
    4. tools / tools-beta のクローンを確認する

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\setup.ps1
#>

param(
    [string]$GitHubOwner   = "ymatsuda-cmyk",
    [string]$GitHubRepo    = "tools",
    [string]$GitHubBeta    = "tools-beta",
    [string]$AgentRoot     = "$env:OneDriveCommercial\work\agent",
    [string]$TargetRepo    = "C:\repo\tools",
    [string]$BetaRepo      = "C:\repo\tools-beta",
    [string]$WorktreeRoot  = "C:\repo\worktrees",
    [int]   $MaxParallel   = 3,
    [switch]$SkipPip
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
Write-Section "3. Python依存パッケージ"

if ($SkipPip) {
    Write-Host "スキップしました。"
} else {
    $requirements = Join-Path $PSScriptRoot "..\agent\requirements.txt"
    python -m pip install --upgrade pip
    python -m pip install -r $requirements
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
Write-Host "新しいPowerShellを開き直してから、次を実行してください。"
Write-Host "    python agent\agent_cli.py doctor"
Write-Host ""
