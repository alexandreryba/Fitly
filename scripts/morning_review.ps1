# Morning review: collect public offers, rebuild the review files, show indicators and follow-ups due.
# Read-only towards job sites; the database is backed up before any write. Never applies.
$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "Python de l'environnement virtuel introuvable : $Python" }
$env:PYTHONIOENCODING = "utf-8"

& $Python -m fitly doctor
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# Collect public offers (database backed up automatically before any write).
& $Python -m fitly morning
if ($LASTEXITCODE -eq 3) {
    Write-Warning "No source answered (network?). Rebuilding the review queue from the existing database."
} elseif ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# Imports go/no-go decisions still pending in review_queue.csv, then rebuilds JSON + CSV + HTML.
& $Python -m fitly review
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Python -m fitly metrics
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Python -m fitly followups
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Morning review ready: data\morning_review.html / data\review_queue.csv. No application was submitted."
