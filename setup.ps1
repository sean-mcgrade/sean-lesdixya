# setup.ps1 — install a private, patched copy of Claude Code 2.1.112 with the
# dyslexia-friendly theme. Run once. Re-run anytime to re-apply patches.
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$installDir = Join-Path $root "cc-2.1.112"

Write-Host "Installing Claude Code 2.1.112 into $installDir ..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $installDir | Out-Null
if (-not (Test-Path (Join-Path $installDir "package.json"))) {
  '{"name":"cc-dyslexia-sandbox","private":true}' | Set-Content (Join-Path $installDir "package.json")
}
Push-Location $installDir
# fresh package each run so patches apply to clean code
Remove-Item -Recurse -Force "node_modules\@anthropic-ai\claude-code" -ErrorAction SilentlyContinue
npm install "@anthropic-ai/claude-code@2.1.112" --no-audit --no-fund --loglevel=error
Pop-Location

$cli = Join-Path $installDir "node_modules\@anthropic-ai\claude-code\cli.js"
if (-not (Test-Path $cli)) { throw "cli.js not found at $cli" }

Write-Host "Applying patches..." -ForegroundColor Cyan
& node (Join-Path $root "patch-thinking.js") $cli
& node (Join-Path $root "patch-visual.js") $cli

Write-Host ""
Write-Host "Done. Launch with:  .\launch.ps1" -ForegroundColor Green
Write-Host "Make sure ANTHROPIC_API_KEY is set (or you are logged in to Claude)." -ForegroundColor Yellow
