[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Host.UI.RawUI.WindowTitle = "Live Dashboard Viewer"
$f = "C:\ClaudeCode\live-dashboard.ansi"
$lastGood = $null
while ($true) {
    try {
        $content = $null
        if ([System.IO.File]::Exists($f)) {
            try {
                $content = [System.IO.File]::ReadAllText($f, [System.Text.Encoding]::UTF8)
            } catch {
                # file is mid atomic-swap by the relay - keep showing last good frame
            }
        }
        if ($content) { $lastGood = $content }
        if ($lastGood) {
            Clear-Host
            [Console]::Write($lastGood)
        } else {
            Clear-Host
            Write-Host "Waiting for dashboard file..."
        }
    } catch {
        # nothing inside this loop is ever allowed to kill the viewer
    }
    Start-Sleep -Seconds 2
}
