# Keeps the live dashboard viewer alive. Relaunches it if its window/process ever dies.
$ErrorActionPreference = "SilentlyContinue"
while ($true) {
    try {
        $running = Get-CimInstance Win32_Process -Filter "Name='pwsh.exe' OR Name='powershell.exe'" |
            Where-Object { $_.CommandLine -match 'view-dashboard\.ps1' }
        if (-not $running) {
            Start-Process wt -ArgumentList '-w new pwsh -NoProfile -ExecutionPolicy Bypass -File C:/ClaudeCode/view-dashboard.ps1'
            Start-Sleep -Seconds 5
        }
    } catch { }
    Start-Sleep -Seconds 15
}
