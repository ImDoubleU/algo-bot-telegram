Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Python = Join-Path $Root "bot_env\Scripts\python.exe"

Set-Location $Root
& $Python "$Root\run_worker.py"
