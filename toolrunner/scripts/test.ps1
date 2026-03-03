param(
  [Parameter(ValueFromRemainingArguments=$true)]
  [string[]]$Args
)

$projectRoot = Split-Path -Parent $PSScriptRoot
$dotenvPath = Join-Path $projectRoot ".env"

function Get-DotEnvValue {
    param(
        [string]$FilePath,
        [string]$KeyName
    )

    if (-not (Test-Path -Path $FilePath)) {
        return $null
    }

    foreach ($line in Get-Content -Path $FilePath -ErrorAction SilentlyContinue) {
        $trimmedLine = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($trimmedLine) -or $trimmedLine.StartsWith('#')) {
            continue
        }

        $separatorIndex = $trimmedLine.IndexOf('=')
        if ($separatorIndex -lt 0) {
            continue
        }

        $entryKey = $trimmedLine.Substring(0, $separatorIndex).Trim()
        if ($entryKey -ieq $KeyName) {
            $entryValue = $trimmedLine.Substring($separatorIndex + 1).Trim()
            $entryValue = $entryValue.Trim('"').Trim("'")
            return [Environment]::ExpandEnvironmentVariables($entryValue)
        }
    }

    return $null
}

$envBaseTemp = $env:PYTEST_DIR
if (-not $envBaseTemp) {
    $envBaseTemp = Get-DotEnvValue -FilePath $dotenvPath -KeyName "PYTEST_DIR"
}

$defaultBaseTemp = Join-Path $env:LOCALAPPDATA "Temp\toolrunner_pytest"
$baseTemp = if ($envBaseTemp) { $envBaseTemp } else { $defaultBaseTemp }

if (-not [System.IO.Path]::IsPathRooted($baseTemp)) {
    $baseTemp = Join-Path $projectRoot $baseTemp
}

$baseTemp = [System.IO.Path]::GetFullPath($baseTemp)

$legacyPaths = @(
    Join-Path $projectRoot "pytest_temp"
    Join-Path $projectRoot "pytest_LOCALAPPDATA\Temp\toolrunner_pytest"
    "C:\tmp\agentmaestro\sandbox\pytest_temp"
)

if (Test-Path -Path $baseTemp -PathType Container) {
    Try {
        Remove-Item -Path $baseTemp -Recurse -Force -ErrorAction Stop
        Write-Host "Temp folder removed:  '$baseTemp'" -ForegroundColor Green
    } Catch {
        Write-Warning "Failed to remove temp folder '$baseTemp': $_"
    }
}

foreach ($legacyPath in $legacyPaths) {
    if (-not $legacyPath) {
        continue
    }

    $resolvedLegacy = [System.IO.Path]::GetFullPath($legacyPath)
    if ($resolvedLegacy -ieq $baseTemp) {
        continue
    }

    if (Test-Path -Path $resolvedLegacy -PathType Container) {
        Remove-Item -Path $resolvedLegacy -Recurse -Force -ErrorAction SilentlyContinue
    }
}

New-Item -ItemType Directory -Force -Path $baseTemp | Out-Null
$env:TMP = $baseTemp
$env:TEMP = $baseTemp

$pytestArgs = @()
if ($Args) {
    $pytestArgs += $Args
}

$pytestArgs += "--basetemp=$baseTemp"

Push-Location $projectRoot
try {
    & .\.venv\Scripts\python -m pytest @pytestArgs
}
finally {
    Pop-Location
}
