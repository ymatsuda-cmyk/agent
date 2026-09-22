<#
.SYNOPSIS
    3つの常駐エージェントを別ウィンドウで起動する。

.DESCRIPTION
    issue_agent     : Teams投稿 → Issue作成
    orchestrator    : Issue → 実装ワーカーへ並走割り当て
    approval_agent  : Teams承認結果 → マージ/却下/再実装

    新しく開く各ウィンドウは、このリポジトリ専用の .venv 内の python.exe を
    絶対パスで直接起動する。起動元シェルでPATH上に別プロジェクトのvenvが
    アクティベートされていても、それを引き継がない
    （子プロセスは親のPATHを継承するため、bare "python" を使うと
    起動元の環境次第で別の仮想環境を誤って掴む。実際にこれが原因で、
    別プロジェクトのvenvから常駐エージェントが二重起動した事例がある）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\start_all.ps1 -Parallel 3
#>

param(
    [int]$Parallel = 0,
    [switch]$NoVsCode,
    [switch]$Manual
)

$ErrorActionPreference = "Stop"
$agentDir = Resolve-Path (Join-Path $PSScriptRoot "..\agent")
$venvPython = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Error (
        "このプロジェクト専用の仮想環境が見つかりません: $venvPython`n" +
        "先に scripts\setup.ps1 を実行してください。"
    )
    exit 1
}
$venvPython = (Resolve-Path $venvPython).Path

function Start-Agent {
    param([string]$Title, [string]$Script, [string[]]$Arguments)

    $argumentLine = ($Arguments -join " ")
    # "python" ではなく、このvenvのpython.exeを絶対パスで直接指定する。
    $command = "cd '$agentDir'; `$Host.UI.RawUI.WindowTitle='$Title'; & '$venvPython' $Script $argumentLine"

    Start-Process powershell -ArgumentList @(
        "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", $command
    )

    Write-Host "[起動] $Title ($venvPython)"
    Start-Sleep -Seconds 2
}

Write-Host ("=" * 60)
Write-Host "AIエージェント基盤 起動"
Write-Host ("=" * 60)

Start-Agent -Title "AGENT: issue" -Script "issue_agent.py" -Arguments @()

$orchestratorArgs = @()
if ($Parallel -gt 0) { $orchestratorArgs += @("--parallel", "$Parallel") }
if ($NoVsCode)       { $orchestratorArgs += "--no-vscode" }
if ($Manual)         { $orchestratorArgs += "--manual" }

Start-Agent -Title "AGENT: orchestrator" -Script "orchestrator.py" -Arguments $orchestratorArgs
Start-Agent -Title "AGENT: approval" -Script "approval_agent.py" -Arguments @()

Write-Host ""
Write-Host "3つのウィンドウが起動しました。"
Write-Host "状態確認: & '$venvPython' agent\agent_cli.py status"
