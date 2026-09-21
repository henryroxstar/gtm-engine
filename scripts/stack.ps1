# ─────────────────────────────────────────────────────────────────────────────
# gtm-engine — Unified Local Stack Controller for Windows (PowerShell)
#
# The Windows native twin of scripts/stack.sh.
# Manages the local development backend: Postgres 18, Redis 8, FastAPI, MCP.
# Zero external credentials needed (uses fake run executor & public dev secrets).
#
# Usage:
#   .\scripts\stack.ps1 start       # Start stack, build images, wait for health
#   .\scripts\stack.ps1 stop        # Stop stack (data volumes preserved)
#   .\scripts\stack.ps1 restart     # Recreate containers to apply config/env edits
#   .\scripts\stack.ps1 status      # Display container status & HTTP health probe
#   .\scripts\stack.ps1 logs [svc]  # Follow logs (default: api; or postgres, redis, mcp)
#   .\scripts\stack.ps1 seed        # Seed test workspace, user, and print auth tokens
#   .\scripts\stack.ps1 reset       # Stop stack and WIPE local database volumes + data\workspaces
# ─────────────────────────────────────────────────────────────────────────────

$ErrorActionPreference = "Stop"

# Resolve repo root from this script location
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$ProjectName = "gtm_infra_dev"
$EnvFile = "deploy\.env.dev"
$EnvExample = "deploy\.env.dev.example"
$ComposeBase = "deploy\docker-compose.yml"
$ComposeDev = "deploy\docker-compose.dev.yml"
$HealthUrl = "http://127.0.0.1:8000/health"
$DocsUrl = "http://127.0.0.1:8000/v1/docs"

function Invoke-NativeQuiet {
    param([scriptblock]$ScriptBlock)
    # PS 5.1 fix (LD-09): when $ErrorActionPreference = "Stop", redirecting stderr
    # of a native command creates an ErrorRecord that terminates execution unless
    # scoped under SilentlyContinue. The finally block guarantees restoration.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        & $ScriptBlock *>$null
    } finally {
        $ErrorActionPreference = $prev
    }
}

function Test-Docker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Host "X Error: Docker is not installed or not in PATH." -ForegroundColor Red
        Write-Host ""
        Write-Host "  To install Docker on Windows:"
        Write-Host "  * winget install Docker.DockerDesktop"
        exit 1
    }

    Invoke-NativeQuiet { & docker info }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "X Error: Docker daemon is not running." -ForegroundColor Red
        Write-Host ""
        Write-Host "  Please start Docker Desktop and try again."
        exit 1
    }
}

function Ensure-Env {
    if (-not (Test-Path $EnvFile)) {
        if (Test-Path $EnvExample) {
            Write-Host "> Initializing $EnvFile with zero-credential local defaults..." -ForegroundColor Cyan
            Copy-Item $EnvExample $EnvFile
        } else {
            Write-Host "X Error: Template $EnvExample not found." -ForegroundColor Red
            exit 1
        }
    }
}

function Ensure-WorkspacesDir {
    # Pre-create the bind-mount target as the calling user (parity with stack.sh; the
    # Linux root-owned-dir footgun stack.sh guards against is not a Windows concern,
    # but scripts/dev_seed.py still needs the directory to exist before it writes
    # pack fixtures into it).
    New-Item -ItemType Directory -Force -Path (Join-Path $RepoRoot "data\workspaces") | Out-Null
}

function Invoke-Compose {
    param([string[]]$ComposeArgs)
    $allArgs = @("--env-file", $EnvFile, "-f", $ComposeBase, "-f", $ComposeDev, "-p", $ProjectName) + $ComposeArgs
    & docker compose @allArgs
}

function Wait-ForHealth {
    $maxWait = 60
    $interval = 2
    $elapsed = 0

    Write-Host "> Waiting for API healthcheck ($HealthUrl) ... " -NoNewline -ForegroundColor Cyan

    while ($elapsed -lt $maxWait) {
        try {
            $resp = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue
            if ($resp.StatusCode -eq 200) {
                Write-Host "Ready!" -ForegroundColor Green
                return $true
            }
        } catch {}
        Write-Host "." -NoNewline -ForegroundColor DarkGray
        Start-Sleep -Seconds $interval
        $elapsed += $interval
    }

    Write-Host "Timed out waiting for /health." -ForegroundColor Yellow
    Write-Host "  The containers may still be applying migrations or booting."
    Write-Host "  Inspect logs: .\scripts\stack.ps1 logs api"
    return $false
}

function Print-Endpoints {
    Write-Host ""
    Write-Host "[OK] GTM Local Stack is UP" -ForegroundColor Green
    Write-Host "  API:            http://127.0.0.1:8000"
    Write-Host "  Interactive Docs: $DocsUrl"
    Write-Host "  Health Probe:     $HealthUrl"
    Write-Host ""
    Write-Host "  Next step: Seed a test workspace and get API tokens:" -ForegroundColor Gray
    Write-Host "  .\scripts\stack.ps1 seed"
    Write-Host ""
}

function Show-Help {
    Write-Host @"
gtm-engine local stack controller (Windows PowerShell)

NOTE: If you are using Claude Code, Google Antigravity, Cursor, or Codex
directly (Cowork mode), you do NOT need this stack! All skills run directly
in your workspace with zero infrastructure. This stack is ONLY for developing
or testing client applications against the REST API.

Usage:
  .\scripts\stack.ps1 <command> [options]

Commands:
  start        Start containers in background, build images, wait for health
  stop         Stop containers cleanly (database state preserved)
  restart      Recreate containers to reload configuration or env edits
  status       Show container table and check API HTTP health
  logs [svc]   Follow container logs (default: api; options: postgres, redis, mcp)
  seed [args]  Seed local workspace & print test bearer tokens (calls scripts/dev_seed.py)
  reset [-f]   Stop stack and WIPE all local dev database volumes + data\workspaces (fresh start)
  help         Show this help message

Examples:
  .\scripts\stack.ps1 start
  .\scripts\stack.ps1 status
  .\scripts\stack.ps1 logs api
  .\scripts\stack.ps1 seed
  .\scripts\stack.ps1 stop
"@
}

$Command = if ($args.Count -gt 0) { $args[0] } else { "help" }
$RemainingArgs = if ($args.Count -gt 1) { $args[1..($args.Count - 1)] } else { @() }

switch ($Command.ToLower()) {
    "start" {
        Test-Docker
        Ensure-Env
        Write-Host "> Starting GTM local dev stack (FastAPI, Postgres 18, Redis 8, MCP) ..." -ForegroundColor Cyan
        Ensure-WorkspacesDir
        Invoke-Compose (@("up", "-d", "--build") + $RemainingArgs)
        Write-Host ""
        if (-not (Wait-ForHealth)) {
            exit 1
        }
        Print-Endpoints
    }

    "stop" {
        Test-Docker
        Ensure-Env
        Write-Host "> Stopping GTM local dev stack ..." -ForegroundColor Cyan
        Invoke-Compose @("stop")
        Write-Host ""
        Write-Host "[OK] Dev stack stopped. (Data volumes preserved)" -ForegroundColor Green
    }

    "restart" {
        Test-Docker
        Ensure-Env
        Write-Host "> Rebuilding and recreating GTM dev containers to pick up code/config/env changes ..." -ForegroundColor Cyan
        Ensure-WorkspacesDir
        Invoke-Compose (@("up", "-d", "--build", "--force-recreate") + $RemainingArgs)
        Write-Host ""
        if (-not (Wait-ForHealth)) {
            exit 1
        }
        Print-Endpoints
    }

    "status" {
        Test-Docker
        Ensure-Env
        Write-Host "=== Containers ($ProjectName) ===" -ForegroundColor Cyan
        Invoke-Compose @("ps", "--format", "table {{.Name}}	{{.Status}}	{{.Ports}}")
        Write-Host ""
        Write-Host "=== HTTP Health Probe ===" -ForegroundColor Cyan
        try {
            $resp = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
            if ($resp.StatusCode -eq 200) {
                Write-Host "[OK] $HealthUrl returned HTTP 200 OK" -ForegroundColor Green
            } else {
                Write-Host "! $HealthUrl returned HTTP $($resp.StatusCode)" -ForegroundColor Yellow
            }
        } catch {
            Write-Host "X $HealthUrl connection refused (stack down or starting)" -ForegroundColor Red
        }
    }

    "logs" {
        Test-Docker
        Ensure-Env
        $svc = if ($RemainingArgs.Count -gt 0) { $RemainingArgs[0] } else { "api" }
        $logArgs = if ($RemainingArgs.Count -gt 1) { $RemainingArgs[1..($RemainingArgs.Count - 1)] } else { @() }
        Invoke-Compose (@("logs", "-f", $svc) + $logArgs)
    }

    "seed" {
        Write-Host "> Running dev_seed to create test workspace & tokens ..." -ForegroundColor Cyan
        Ensure-Env
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            Write-Host "uv not found. Running scripts\bootstrap.ps1 first..." -ForegroundColor Yellow
            powershell -ExecutionPolicy Bypass -File "scripts\bootstrap.ps1"
        }
        $env:PYTHONIOENCODING = "utf-8"
        uv run python scripts/dev_seed.py @RemainingArgs
    }

    "reset" {
        Test-Docker
        Ensure-Env
        $force = $RemainingArgs -contains "-f" -or $RemainingArgs -contains "--force"
        if (-not $force) {
            Write-Host "WARNING: This will stop all dev containers and WIPE all local dev database volumes and data\workspaces\." -ForegroundColor Yellow
            $confirm = Read-Host "Are you sure you want to proceed? [y/N]"
            if ($confirm -notmatch "^[yY]([eE][sS])?$") {
                Write-Host "Aborted."
                exit 0
            }
        }

        # data\workspaces\ is now a host BIND (not the `workspaces` named volume
        # `down -v` below removes), and the api container writes into it as root (no
        # USER in deploy/Dockerfile.backend) — fake-run artifacts under
        # <ws>/content/<profile>/fake-runs/, <ws>/profiles, <ws>/content. Clear it from
        # INSIDE the container so this works regardless of host-side permissions.
        $workspacesDir = Join-Path $RepoRoot "data\workspaces"
        $workspacesClearFailed = $false
        if ((Test-Path $workspacesDir) -and (Get-ChildItem -Force $workspacesDir -ErrorAction SilentlyContinue | Select-Object -First 1)) {
            # Stop api first — an in-flight fake run could otherwise write a new file
            # between this clear and `down -v` below, leaving the "clean state" claim false.
            Invoke-NativeQuiet { Invoke-Compose @("stop", "api") }
            Write-Host "> Clearing data\workspaces\ via the api container ..." -ForegroundColor Cyan
            Invoke-Compose @("run", "--rm", "--no-deps", "--entrypoint", "find", "api", "/app/data/workspaces", "-mindepth", "1", "-delete")
            if ($LASTEXITCODE -ne 0) {
                $workspacesClearFailed = $true
                Write-Host "! Could not clear data\workspaces\ via the api container." -ForegroundColor Yellow
                Write-Host "  Remove it by hand if needed: Remove-Item -Recurse -Force $workspacesDir" -ForegroundColor DarkGray
            }
        }

        Write-Host "> Tearing down dev stack and removing volumes ..." -ForegroundColor Cyan
        Invoke-Compose @("down", "-v")
        # `down -v` only removes a volume this compose config still MOUNTS somewhere.
        # `workspaces` stays DECLARED but unused now that the dev override binds the
        # host dir instead, so it survives `down -v` as an orphan. Best-effort: fine if
        # it's already gone.
        Invoke-NativeQuiet { docker volume rm "${ProjectName}_workspaces" }

        if ($workspacesClearFailed) {
            Write-Host "! Dev containers and database volumes are wiped, but data\workspaces\ was NOT fully cleared — see the warning above." -ForegroundColor Yellow
        } else {
            Write-Host "[OK] Dev stack wiped and reset to clean state." -ForegroundColor Green
        }
    }

    default {
        Show-Help
    }
}
