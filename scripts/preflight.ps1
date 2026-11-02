# Checks before use: settings files, database, CV files, then the full test suite.
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "Python de l'environnement virtuel introuvable : $Python. Cree d'abord .venv puis : pip install -e .[dev]" }
& $Python -m fitly doctor
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Python -m pytest -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Preflight complete. No employer application was submitted."
