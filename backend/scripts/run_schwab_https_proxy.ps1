param(
    [int]$Port = 8443,
    [string]$BackendUrl = "http://127.0.0.1:8000",
    [string]$CertFile = "",
    [string]$KeyFile = ""
)

$env:PYTHONUNBUFFERED = "1"
$backendRoot = Split-Path $PSScriptRoot -Parent
$pythonExe = Join-Path $backendRoot ".venv\Scripts\python.exe"

if (-not $CertFile) {
    $CertFile = Join-Path $backendRoot ".certs\127.0.0.1.pem"
}

if (-not $KeyFile) {
    $KeyFile = Join-Path $backendRoot ".certs\127.0.0.1-key.pem"
}

if (-not (Test-Path $CertFile)) {
    Write-Host "Missing cert file: $CertFile"
    Write-Host "Generate one with mkcert, for example:"
    Write-Host "  mkcert -install"
    Write-Host "  mkcert -key-file $KeyFile -cert-file $CertFile 127.0.0.1 localhost"
    exit 1
}

if (-not (Test-Path $KeyFile)) {
    Write-Host "Missing key file: $KeyFile"
    Write-Host "Generate one with mkcert, for example:"
    Write-Host "  mkcert -install"
    Write-Host "  mkcert -key-file $KeyFile -cert-file $CertFile 127.0.0.1 localhost"
    exit 1
}

Write-Host "Starting local HTTPS proxy on https://127.0.0.1:$Port -> $BackendUrl"
Set-Location $backendRoot
if (Test-Path $pythonExe) {
    & $pythonExe scripts\https_proxy.py --listen-host 127.0.0.1 --listen-port $Port --backend-url $BackendUrl --cert-file $CertFile --key-file $KeyFile
}
else {
    python scripts\https_proxy.py --listen-host 127.0.0.1 --listen-port $Port --backend-url $BackendUrl --cert-file $CertFile --key-file $KeyFile
}
