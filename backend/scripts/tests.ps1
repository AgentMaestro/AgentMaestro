$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
$projectRoot = Split-Path -Parent $scriptRoot
Set-Location $projectRoot
$base = Join-Path $projectRoot ".pytest-temp"
if (Test-Path $base) { Remove-Item -Recurse -Force $base -ErrorAction SilentlyContinue }
.\scripts\test.ps1 -- --basetemp="$base"
