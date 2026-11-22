# Keeps the Frame Log sync server running: restarts it if it stops, and logs to data\server.log.
# Started at boot by the "Frame Log sync server" scheduled task (install-startup-task.ps1).
# Safe to run by hand too.
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# Same uv and Python as an interactive terminal (a scheduled task doesn't get a login shell's PATH).
$env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
$env:UV_PYTHON_INSTALL_DIR = [Environment]::GetEnvironmentVariable("UV_PYTHON_INSTALL_DIR", "User")
$env:PYTHONUNBUFFERED = "1"

$data = Join-Path $root "data"
New-Item -ItemType Directory -Force $data | Out-Null
$log = Join-Path $data "server.log"

function Write-Log($text) { "$(Get-Date -Format s) $text" | Out-File -Append -Encoding utf8 $log }

while ($true) {
    if ((Test-Path $log) -and (Get-Item $log).Length -gt 10MB) { Move-Item -Force $log "$log.old" }
    Write-Log "starting server"
    & uv run --frozen python -m framelog_sync.cli 2>&1 | ForEach-Object { "$_" } | Out-File -Append -Encoding utf8 $log
    Write-Log "server stopped (exit code $LASTEXITCODE); restarting in 30 seconds"
    Start-Sleep -Seconds 30
}
