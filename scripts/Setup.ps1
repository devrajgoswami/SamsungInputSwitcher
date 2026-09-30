<#
.SYNOPSIS
    One-time setup: creates the virtual environment and installs the package.

.EXAMPLE
    .\scripts\Setup.ps1
#>
[CmdletBinding()]
param(
    [string] $PythonExe = 'python'
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $root '.venv'
$venvPython = Join-Path $venv 'Scripts\python.exe'

Write-Host "Project root : $root"

if (-not (Test-Path $venvPython)) {
    Write-Host 'Creating virtual environment...'
    & $PythonExe -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create the virtual environment.' }
}
else {
    Write-Host 'Virtual environment already exists.'
}

Write-Host 'Installing dependencies...'
& $venvPython -m pip install --quiet --upgrade pip
& $venvPython -m pip install --quiet -e $root
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }

Write-Host ''
Write-Host 'Setup complete.' -ForegroundColor Green
Write-Host ''
Write-Host 'Next steps:'
Write-Host "  1. Pair with the monitor:   .\.venv\Scripts\python.exe -m samsung_input pair"
Write-Host "  2. Test a switch:           .\.venv\Scripts\python.exe -m samsung_input switch usbc"
Write-Host "  3. Install the watcher:     .\scripts\Install-Task.ps1"
