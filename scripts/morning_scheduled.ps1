# Wrapper used by the scheduled task: runs the morning review and keeps a log.
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$LogDir = Join-Path $ProjectRoot "data\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir ("morning_{0}.log" -f (Get-Date -Format "yyyyMMdd_HHmm"))
$env:PYTHONIOENCODING = "utf-8"
& (Join-Path $PSScriptRoot "morning_review.ps1") *>&1 | Out-File -FilePath $Log -Encoding utf8
exit $LASTEXITCODE
