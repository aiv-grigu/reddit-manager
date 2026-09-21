# WhoCanFindMe Reddit Manager - Windows setup. Run from the reddit-manager folder in PowerShell:
#   Set-ExecutionPolicy -Scope Process Bypass; .\setup.ps1
# Creates a venv, installs deps, creates .env from the example, opens the pages where you get each key,
# then runs a dry run once .env is filled. Nothing here posts to Reddit.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "`n== 1/4 Python venv + dependencies" -ForegroundColor Cyan
if (-not (Test-Path .\.venv)) { python -m venv .venv }
.\.venv\Scripts\python.exe -m pip install --upgrade pip -q
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -q
Write-Host "   deps installed"

Write-Host "`n== 2/4 .env" -ForegroundColor Cyan
if (-not (Test-Path .\.env)) {
    Copy-Item .env.example .env
    Write-Host "   created .env from .env.example"
} else { Write-Host "   .env already exists, leaving it" }

Write-Host "`n== 3/4 Opening the three places you get keys from" -ForegroundColor Cyan
Write-Host "   a) Reddit: create app, type SCRIPT, redirect http://localhost:8080 -> client id (under the name) + secret"
Start-Process "https://www.reddit.com/prefs/apps"
Start-Sleep 1
Write-Host "   b) Discord: New Application -> Bot -> Reset Token. OAuth2 URL Generator: scope bot, perms View Channels + Send Messages + Read Message History, invite to your server."
Start-Process "https://discord.com/developers/applications"
Start-Sleep 1
Write-Host "   c) Anthropic API key"
Start-Process "https://console.anthropic.com/settings/keys"
Start-Sleep 1
notepad .env
Write-Host "`n   Fill .env in Notepad, save, close it, then press Enter here." -ForegroundColor Yellow
Read-Host

Write-Host "`n== 4/4 Dry run (drafts printed, nothing sent, nothing posted)" -ForegroundColor Cyan
.\.venv\Scripts\python.exe run.py status
.\.venv\Scripts\python.exe run.py dry

Write-Host "`nDone. Next:" -ForegroundColor Green
Write-Host "  read the drafts above; add real samples of your own writing to voice.md"
Write-Host "  python run.py morning   -> sends drafts to Discord for approval"
Write-Host "  python run.py worker    -> posts what you approve, with pacing (keep it running)"
Write-Host "  Task Scheduler: run 'morning' daily at 07:00, 'worker' at logon"
