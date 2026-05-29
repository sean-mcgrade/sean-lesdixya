# launch.ps1 — start the dyslexia-themed Claude Code on the Opus 4.8 model,
# with extended thinking ON. Run setup.ps1 first.
param(
  [string]$Model = "claude-opus-4-8"
)

Remove-Item Env:CLAUDECODE -ErrorAction SilentlyContinue
Remove-Item Env:MAX_THINKING_TOKENS -ErrorAction SilentlyContinue   # thinking ON

# Relaunch inside Windows Terminal for truecolor + box drawing
if ($null -eq $env:WT_SESSION) {
  $wt = Get-Command wt -ErrorAction SilentlyContinue
  if ($wt) {
    Write-Host "Relaunching in Windows Terminal..." -ForegroundColor Yellow
    wt new-tab --title "Claude (dyslexia)" pwsh -NoLogo -NoExit -File $PSCommandPath -Model $Model
    return
  }
  Write-Host "Tip: run inside Windows Terminal for best colours." -ForegroundColor DarkYellow
}

$env:COLORTERM = "truecolor"
$env:TERM = "xterm-256color"

# Accessibility system prompt: red echo of your words, plain confirmations, no walls of text.
$Accessibility = "ACCESSIBILITY MODE - OBEY EVERY RESPONSE: 1. FIRST action: a single bash printf echoing the user's words in a solid RED block: printf '\n\033[41;1;37m  YOU SAID: <their words>  \033[0m\n'. 2. SECOND: one plain line starting 'I understand: ' in simple words. 3. Never paste code in chat - save to files and confirm in one line. 4. Never use markdown code fences for visual output. 5. Keep prose short."

$cli = Join-Path $PSScriptRoot "cc-2.1.112\node_modules\@anthropic-ai\claude-code\cli.js"
if (-not (Test-Path $cli)) { Write-Host "Not set up yet. Run .\setup.ps1 first." -ForegroundColor Red; exit 1 }

Write-Host "Claude Code 2.1.112 | $Model | thinking ON | dyslexia theme" -ForegroundColor DarkCyan
& node $cli --model $Model --append-system-prompt $Accessibility
