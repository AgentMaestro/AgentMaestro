param(
  [Parameter(ValueFromRemainingArguments=$true)]
  [string[]]$Args
)

 $tempRoot = "C:\Dev\AgentMaestro\toolrunner\pytest_temp"
 $baseTemp = $tempRoot

New-Item -ItemType Directory -Force $tempRoot | Out-Null

# Pre-delete so pytest doesn't have to
if (Test-Path $baseTemp) { Remove-Item -Recurse -Force $baseTemp -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Force $baseTemp | Out-Null

$env:TMP = $tempRoot
$env:TEMP = $tempRoot
$env:PYTEST_ADDOPTS = "--basetemp=$baseTemp --ignore-glob=**\pytest_* --ignore-glob=**\.pytest_* --ignore-glob=**\pytest-*"

Push-Location "C:\Dev\AgentMaestro\toolrunner"
.\.venv\Scripts\python -m pytest @Args
Pop-Location
