#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$CertificatePath,
    [string]$AppHost = "geochem.lan",
    [string]$AuthHost = "auth.geochem.lan",
    [string]$Address = "127.0.0.1"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $CertificatePath -PathType Leaf)) {
    throw "GeoChem CA certificate was not found: $CertificatePath"
}
if ($AppHost -eq $AuthHost) {
    throw "The application and identity host names must be different."
}

$hostsPath = Join-Path $env:SystemRoot "System32\drivers\etc\hosts"
$startMarker = "# BEGIN GEOCHEM LAN"
$endMarker = "# END GEOCHEM LAN"
$content = [System.IO.File]::ReadAllText($hostsPath)
$pattern = "(?ms)^" + [regex]::Escape($startMarker) + ".*?^" + [regex]::Escape($endMarker) + "\r?\n?"
$content = [regex]::Replace($content, $pattern, "")
$block = @(
    $startMarker,
    "$Address`t$AppHost",
    "$Address`t$AuthHost",
    $endMarker,
    ""
) -join "`r`n"
if ($content.Length -gt 0 -and -not $content.EndsWith("`n")) {
    $content += "`r`n"
}
[System.IO.File]::WriteAllText(
    $hostsPath,
    $content + $block,
    [System.Text.UTF8Encoding]::new($false)
)

$certificate = Import-Certificate -FilePath $CertificatePath -CertStoreLocation "Cert:\LocalMachine\Root"
Clear-DnsClientCache | Out-Null

Write-Host "GeoChem LAN names were added to the Windows hosts file." -ForegroundColor Green
Write-Host "Trusted Caddy root certificate: $($certificate.Thumbprint)" -ForegroundColor Green
Write-Host "Open https://$AppHost in a new browser window." -ForegroundColor Cyan
