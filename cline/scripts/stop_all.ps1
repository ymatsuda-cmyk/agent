<#
.SYNOPSIS
    常駐エージェントを停止し、残存ロックを掃除する。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\stop_all.ps1
#>

param([switch]$Force)

$ErrorActionPreference = "Continue"
$agentDir = Resolve-Path (Join-Path $PSScriptRoot "..\agent")

$targets = @(
    "issue_agent.py",
    "orchestrator.py",
    "approval_agent.py",
    "implement_agent.py",
    "finish_agent.py",
    "deploy_preview.py"
)

Write-Host ("=" * 60)
Write-Host "AIエージェント基盤 停止"
Write-Host ("=" * 60)

$processes = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'"

foreach ($process in $processes) {
    $commandLine = $process.CommandLine
    if (-not $commandLine) { continue }

    foreach ($target in $targets) {
        if ($commandLine -like "*$target*") {
            Write-Host "[停止] PID=$($process.ProcessId) $target"
            Stop-Process -Id $process.ProcessId -Force:$Force -ErrorAction SilentlyContinue
            break
        }
    }
}

Start-Sleep -Seconds 2

Write-Host ""
Write-Host "ロックを掃除します。"
Push-Location $agentDir
python agent_cli.py unlock
Pop-Location

Write-Host "停止しました。"
