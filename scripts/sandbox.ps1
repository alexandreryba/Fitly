<#
Sandbox: copies the real database to data\sandbox\ and opens the interface on it (port 8502).
Everything you click there (GO, "J'ai envoye", relances...) only changes the copy.
  .\scripts\sandbox.ps1            # create/refresh the copy and open the interface
  .\scripts\sandbox.ps1 -Reset     # delete the sandbox
In another PowerShell window, CLI commands use the copy after:  $env:FITLY_DB = "data\sandbox\fitly.db"
#>
param([switch]$Reset)
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot
$Box = Join-Path $ProjectRoot "data\sandbox"
if ($Reset) { Remove-Item -Recurse -Force $Box -ErrorAction SilentlyContinue; Write-Host "Sandbox deleted."; exit 0 }
New-Item -ItemType Directory -Force -Path $Box | Out-Null
Copy-Item (Join-Path $ProjectRoot "data\fitly.db") (Join-Path $Box "fitly.db") -Force
$env:FITLY_DB = Join-Path $Box "fitly.db"
Write-Host "SANDBOX: $env:FITLY_DB  (real database untouched)"
& (Join-Path $ProjectRoot ".venv\Scripts\python.exe") -m streamlit run src\fitly\ui\app.py --server.port 8502
