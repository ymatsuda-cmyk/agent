<#
.SYNOPSIS
    常駐エージェントを停止し、残存ロックを掃除する。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\stop_all.ps1
#>

param([switch]$Force)

$ErrorActionPreference = "Continue"
$agentDir = Resolve-Path (Join-Path $PSScriptRoot "..\agent")
$venvPython = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    $venvPython = (Resolve-Path $venvPython).Path
}

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

# 意図的に "このvenvから起動されたものだけ" に絞らない。
# 別プロジェクトのvenvから誤って起動された同名スクリプトも拾って止める
# （実際にそれが原因で二重起動が起きた事例があるため、安全側に倒す）。
# ただし別venv由来を止めた場合は、気づけるようはっきり警告する。
$processes = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'"

foreach ($process in $processes) {
    $commandLine = $process.CommandLine
    if (-not $commandLine) { continue }

    foreach ($target in $targets) {
        if ($commandLine -like "*$target*") {
            $isOwnVenv = $venvPython -and ($commandLine -like "*$venvPython*")
            if ($isOwnVenv) {
                Write-Host "[停止] PID=$($process.ProcessId) $target"
            } else {
                Write-Warning (
                    "[停止] PID=$($process.ProcessId) $target " +
                    "（このプロジェクトのvenvではない場所から起動されています: $commandLine）"
                )
            }
            Stop-Process -Id $process.ProcessId -Force:$Force -ErrorAction SilentlyContinue
            break
        }
    }
}

Start-Sleep -Seconds 2

Write-Host ""
Write-Host "ロックを掃除します。"
Push-Location $agentDir
if ($venvPython -and (Test-Path $venvPython)) {
    & $venvPython agent_cli.py unlock
} else {
    Write-Warning "このプロジェクトのvenvが見つからないため、PATH上のpythonで実行します。"
    python agent_cli.py unlock
}
Pop-Location

Write-Host "停止しました。"
