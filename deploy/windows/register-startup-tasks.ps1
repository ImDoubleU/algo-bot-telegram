Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$PowerShell = (Get-Command powershell.exe).Source
$WebScript = Join-Path $Root "deploy\windows\start-webapp.ps1"
$WorkerScript = Join-Path $Root "deploy\windows\start-worker.ps1"

schtasks /Create /TN "AlgoBot WebApp" /SC ONLOGON /TR "`"$PowerShell`" -NoProfile -ExecutionPolicy Bypass -File `"$WebScript`"" /F | Out-Null
schtasks /Create /TN "AlgoBot Worker" /SC ONLOGON /TR "`"$PowerShell`" -NoProfile -ExecutionPolicy Bypass -File `"$WorkerScript`"" /F | Out-Null

Write-Host "Startup tasks created: AlgoBot WebApp, AlgoBot Worker"
