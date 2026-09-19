param([string]$Python = "$PSScriptRoot\.venv\Scripts\python.exe")
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    if (-not (Test-Path -LiteralPath $Python)) { throw 'Create .venv and install .[dev,build] first; see BUILD.md.' }
    & $Python -m PyInstaller --clean --noconfirm Athena.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
    & $Python package_release.py
    if ($LASTEXITCODE -ne 0) { throw 'Release verification failed.' }
} finally {
    Pop-Location
}
