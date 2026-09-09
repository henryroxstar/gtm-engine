# bootstrap.ps1 — one-command local bootstrap on Windows (PowerShell).
#
# The Windows twin of scripts/bootstrap.sh. Same three steps, same end state:
#   1. ensures `uv` is installed (installs it if missing)
#   2. `uv sync` — uv provisions the right Python (3.11+) and all deps
#   3. prints the environment self-check (which capability tier is unlocked)
#
# Safe to re-run (idempotent). Run it from anywhere:
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
#
# WHY THIS FILE EXISTS AND IS NOT JUST A README NOTE. bootstrap.sh is bash, so on Windows
# it needs Git Bash or WSL — and the onboarding guide told Windows users to say "yes" when
# the assistant offered to install Python. On Windows that lands you on the **App Execution
# Alias**: `C:\Users\<you>\AppData\Local\Microsoft\WindowsApps\python.exe` is a zero-byte
# stub that opens the Microsoft Store instead of running anything. It is on PATH by default
# and it SHADOWS a real interpreter installed later, so `winget install Python.Python.3.12`
# on its own does not fix it.
#
# On 2026-09-07 a prospecting run on exactly that machine spent ~209 premium contact
# lookups while every `python -m gtm_core.*` command — the budget guard, the merge writer,
# the account-integrity gate — failed silently and the run degraded into hand-written
# markdown that looked like a finished result. `uv` is the fix rather than a workaround:
# it ships its own interpreter, so the alias never gets a vote.

$ErrorActionPreference = 'Stop'

# Resolve repo root from this script's location (works from any CWD).
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
Write-Host "==> gtm-engine bootstrap (repo: $RepoRoot)"

# --- 1. ensure uv -----------------------------------------------------------
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "==> uv not found - installing (astral.sh) ..."
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    # uv installs to %USERPROFILE%\.local\bin; make it visible for THIS session so the
    # rest of this script works without the user opening a new shell.
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error @"
uv install did not land on PATH. Open a new PowerShell window, or add
  $env:USERPROFILE\.local\bin
to PATH, then re-run: powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
"@
    exit 1
}
Write-Host "==> uv: $(uv --version)"

# --- 2. install deps (uv provisions Python 3.11+ automatically) -------------
Write-Host "==> uv sync ..."
uv sync
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# --- 2b. prove the interpreter is REAL, not the Store alias ------------------
# This is the check bootstrap.sh does not need. `uv run python` uses uv's own
# interpreter, so if this prints a path the alias has been routed around; if it fails,
# the failure is loud HERE rather than eleven steps into a metered pipeline run.
Write-Host "==> interpreter probe ..."
# Out-String collapses the native command's output (which PowerShell hands back as a
# string ARRAY when it spans lines) to one trimmed string, so the emptiness test below is
# testing what it looks like it is testing.
$contentRoot = (uv run python -m gtm_core.paths | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($contentRoot)) {
    # SINGLE-quoted here-string: a backtick is PowerShell's escape character, so the
    # command name below would be parsed as an escape sequence inside a @" "@ block.
    Write-Error @'
`uv run python -m gtm_core.paths` did not print a content root.

If you saw "Python was not found; run without arguments to install from the Microsoft
Store", a real interpreter is still being shadowed by the Windows App Execution Alias.
Turn it off: Settings > Apps > Advanced app settings > App execution aliases, and toggle
OFF both python.exe and python3.exe. Then open a NEW PowerShell window and re-run this.

Do NOT continue to a pipeline run until this probe passes: the engine's budget guard,
ledger writers and integrity gates are all Python, and they fail one at a time and
quietly rather than stopping the run.
'@
    exit 1
}
Write-Host "==> content root: $contentRoot"

# --- 3. environment self-check ----------------------------------------------
Write-Host ""
uv run python -m gtm_core.check_env
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "==> Bootstrap finished, but TIER 0 is not set yet."
    Write-Host "    Copy .env.example to .env and set ANTHROPIC_API_KEY, then re-run this."
    exit 0   # not a hard failure - deps are installed; the user just needs a key
}

Write-Host ""
Write-Host "==> Ready. Open the folder in Claude Code and say `"set me up`"."
