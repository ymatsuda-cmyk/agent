<#
.SYNOPSIS
    3つの常駐エージェントを別ウィンドウで起動する。

.DESCRIPTION
    issue_agent     : Teams投稿 → Issue作成
    orchestrator    : Issue → 実装ワーカーへ並走割り当て
    approval_agent  : Teams承認結果 → マージ/却下/再実装

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

function Start-Agent {
    param([string]$Title, [string]$Script, [string[]]$Arguments)

    $argumentLine = ($Arguments -join " ")
    $command = "cd '$agentDir'; `$Host.UI.RawUI.WindowTitle='$Title'; python $Script $argumentLine"

    Start-Process powershell -ArgumentList @(
        "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", $command
    )

    Write-Host "[起動] $Title"
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
Write-Host "状態確認: python agent\agent_cli.py status"
