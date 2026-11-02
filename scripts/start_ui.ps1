# Starts the Fitly interface on http://localhost:8501 (local only, see .streamlit/config.toml).
# If an older Fitly interface is still running on that port, it is stopped first so the
# freshly updated code is used. Any other program on the port is left alone.
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "Python venv not found: $Python" }

$Port = 8501
$listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
foreach ($l in $listeners) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($l.OwningProcess)"
    if ($proc -and $proc.CommandLine -like "*streamlit*app.py*" -and $proc.CommandLine -like "*fitly*") {
        Write-Host "Ancienne interface Fitly detectee (PID $($proc.ProcessId)) : arret pour relancer la version a jour..."
        Stop-Process -Id $proc.ProcessId -Force
        Start-Sleep -Seconds 2
    } elseif ($proc) {
        Write-Warning "Le port $Port est utilise par un autre programme ($($proc.Name)). Interface lancee sur le port 8502."
        $Port = 8502
    }
}
& $Python -m streamlit run src\fitly\ui\app.py --server.port $Port
