Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

New-NetFirewallRule -DisplayName "AlgoBot Caddy HTTP" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 80 | Out-Null
New-NetFirewallRule -DisplayName "AlgoBot Caddy HTTPS" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 443 | Out-Null

Write-Host "Firewall rules created for ports 80 and 443"
