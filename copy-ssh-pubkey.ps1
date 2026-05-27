$ErrorActionPreference = "Stop"
$sshDir = Join-Path $env:USERPROFILE ".ssh"
$pubEd = Join-Path $sshDir "id_ed25519.pub"
$pubRsa = Join-Path $sshDir "id_rsa.pub"

if (-not (Test-Path $sshDir)) {
    New-Item -ItemType Directory -Path $sshDir -Force | Out-Null
}

$needNew = (-not (Test-Path $pubEd)) -and (-not (Test-Path $pubRsa))
if ($needNew) {
    $key = Join-Path $sshDir "id_ed25519"
    ssh-keygen -t ed25519 -f $key -N '""' -C "" 2>$null
}

$pub = $null
if (Test-Path $pubEd) { $pub = $pubEd }
elseif (Test-Path $pubRsa) { $pub = $pubRsa }
if (-not $pub) { throw "No .pub file" }

$line = ((Get-Content -LiteralPath $pub -Encoding utf8 | Select-Object -First 1) -as [string]).Trim()
if ([string]::IsNullOrEmpty($line)) { throw "Empty .pub" }

$parts = $line -split '\s+', 3
if ($parts.Length -ge 2) {
    $line = "$($parts[0]) $($parts[1])"
}

Set-Clipboard -Value $line
