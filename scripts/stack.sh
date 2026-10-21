#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# gtm-engine — Unified Local Stack Controller (macOS / Linux)
#
# Manages the local development backend: Postgres 18, Redis 8, FastAPI, MCP.
# Zero external credentials needed (uses fake run executor & public dev secrets).
#
# Usage:
#   ./scripts/stack.sh start       # Start stack, build images, wait for health
#   ./scripts/stack.sh stop        # Stop stack (data volumes preserved)
#   ./scripts/stack.sh restart     # Rebuild images & recreate containers to apply code/config/env edits
#   ./scripts/stack.sh status      # Display container status & HTTP health probe
#   ./scripts/stack.sh logs [svc]  # Follow logs (default: api; or postgres, redis, mcp)
#   ./scripts/stack.sh seed        # Seed test workspace, user, and print auth tokens
#   ./scripts/stack.sh reset       # Stop stack and WIPE local database volumes + data/workspaces
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# Resolve repo root regardless of current working directory
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Terminal styling (disabled when non-interactive)
if [ -t 1 ]; then
  BOLD="\033[1m"
  GREEN="\033[0;32m"
  CYAN="\033[0;36m"
  YELLOW="\033[1;33m"
  RED="\033[0;31m"
  DIM="\033[2m"
  NC="\033[0m"
else
  BOLD=""
  GREEN=""
  CYAN=""
  YELLOW=""
  RED=""
  DIM=""
  NC=""
fi

PROJECT_NAME="gtm_infra_dev"
ENV_FILE="deploy/.env.dev"
ENV_EXAMPLE="deploy/.env.dev.example"
COMPOSE_BASE="deploy/docker-compose.yml"
COMPOSE_DEV="deploy/docker-compose.dev.yml"
HEALTH_URL="http://127.0.0.1:8000/health"
DOCS_URL="http://127.0.0.1:8000/v1/docs"

compose_cmd() {
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_BASE" -f "$COMPOSE_DEV" -p "$PROJECT_NAME" "$@"
}

check_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    echo -e "${RED}✗ Error: Docker is not installed or not in PATH.${NC}" >&2
    echo "" >&2
    echo "  To install Docker:" >&2
    echo "  • macOS (Homebrew):   brew install --cask docker" >&2
    echo "    (or Colima):        brew install colima docker && colima start" >&2
    echo "  • Linux (Ubuntu/Deb): curl -fsSL https://get.docker.com | sh" >&2
    echo "  • Windows:            winget install Docker.DockerDesktop" >&2
    exit 1
  fi

  if ! docker info >/dev/null 2>&1; then
    echo -e "${RED}✗ Error: Docker daemon is not running.${NC}" >&2
    echo "" >&2
    echo "  Please start Docker Desktop (or run 'colima start') and try again." >&2
    exit 1
  fi
}

ensure_env() {
  if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$ENV_EXAMPLE" ]; then
      echo -e "${CYAN}▸ Initializing ${ENV_FILE} with zero-credential local defaults...${NC}"
      cp "$ENV_EXAMPLE" "$ENV_FILE"
    else
      echo -e "${RED}✗ Error: Template ${ENV_EXAMPLE} not found.${NC}" >&2
      exit 1
    fi
  fi
}

ensure_workspaces_dir() {
  # Pre-create the bind-mount target as the calling user. Left to Docker, a missing
  # bind source is created by the daemon (root, on Linux) — scripts/dev_seed.py then
  # writes pack fixtures under it as the calling user and hits a permission error.
  mkdir -p "$REPO_ROOT/data/workspaces"
}

wait_for_health() {
  local max_wait=60
  local interval=2
  local elapsed=0

  echo -ne "${CYAN}▸ Waiting for API healthcheck (${HEALTH_URL}) ... ${NC}"

  while [ $elapsed -lt $max_wait ]; do
    if curl -s -f -o /dev/null "$HEALTH_URL" 2>/dev/null; then
      echo -e "${GREEN}Ready!${NC}"
      return 0
    fi
    echo -ne "${DIM}.${NC}"
    sleep $interval
    elapsed=$((elapsed + interval))
  done

  echo -e "${YELLOW}Timed out waiting for /health.${NC}"
  echo -e "  The containers may still be applying migrations or booting."
  echo -e "  Inspect logs: ${BOLD}./scripts/stack.sh logs api${NC}"
  return 1
}

print_endpoints() {
  echo ""
  echo -e "${GREEN}${BOLD}✓ GTM Local Stack is UP${NC}"
  echo -e "  ${BOLD}API:${NC}            http://127.0.0.1:8000"
  echo -e "  ${BOLD}Interactive Docs:${NC} ${DOCS_URL}"
  echo -e "  ${BOLD}Health Probe:${NC}     ${HEALTH_URL}"
  echo ""
  echo -e "  ${DIM}Next step: Seed a test workspace and get API tokens:${NC}"
  echo -e "  ${BOLD}./scripts/stack.sh seed${NC}"
  echo ""
}

show_help() {
  cat <<HELP
gtm-engine local stack controller (FastAPI + Postgres 18 + Redis 8 + MCP)

NOTE: If you are using Claude Code, Google Antigravity, Cursor, or Codex
directly (Cowork mode), you do NOT need this stack! All skills run directly
in your workspace with zero infrastructure. This stack is ONLY for developing
or testing client applications against the REST API.

Usage:
  ./scripts/stack.sh <command> [options]

Commands:
  start        Start containers in background, build images, wait for health
  stop         Stop containers cleanly (database state preserved)
  restart      Recreate containers to reload configuration or env edits
  status       Show container table and check API HTTP health
  logs [svc]   Follow container logs (default: api; options: postgres, redis, mcp)
  seed [args]  Seed local workspace & print test bearer tokens (calls scripts/dev_seed.py)
  reset [-f]   Stop stack and WIPE all local dev database volumes + data/workspaces (fresh start)
  help         Show this help message

Examples:
  ./scripts/stack.sh start
  ./scripts/stack.sh status
  ./scripts/stack.sh logs api
  ./scripts/stack.sh seed
  ./scripts/stack.sh stop
HELP
}

cmd="${1:-help}"
if [ $# -gt 0 ]; then shift; fi

case "$cmd" in
  start)
    check_docker
    ensure_env
    echo -e "${BOLD}▸ Starting GTM local dev stack (FastAPI, Postgres 18, Redis 8, MCP) ...${NC}"
    ensure_workspaces_dir
    compose_cmd up -d --build "$@"
    echo ""
    if ! wait_for_health; then
      exit 1
    fi
    print_endpoints
    ;;

  stop)
    check_docker
    ensure_env
    echo -e "${BOLD}▸ Stopping GTM local dev stack ...${NC}"
    compose_cmd stop
    echo ""
    echo -e "${GREEN}✓ Dev stack stopped. (Data volumes preserved)${NC}"
    ;;

  restart)
    check_docker
    ensure_env
    echo -e "${BOLD}▸ Rebuilding and recreating GTM dev containers to pick up code/config/env changes ...${NC}"
    ensure_workspaces_dir
    compose_cmd up -d --build --force-recreate "$@"
    echo ""
    if ! wait_for_health; then
      exit 1
    fi
    print_endpoints
    ;;

  status)
    check_docker
    ensure_env
    echo -e "${BOLD}=== Containers (${PROJECT_NAME}) ===${NC}"
    compose_cmd ps --format 'table {{.Name}}	{{.Status}}	{{.Ports}}'
    echo ""
    echo -e "${BOLD}=== HTTP Health Probe ===${NC}"
    http_code="$(curl -s -o /dev/null -w "%{http_code}" "$HEALTH_URL" 2>/dev/null || true)"
    if [ "$http_code" = "200" ]; then
      echo -e "${GREEN}✓ ${HEALTH_URL} returned HTTP 200 OK${NC}"
    elif [ -z "$http_code" ] || [ "$http_code" = "000" ]; then
      echo -e "${RED}✗ ${HEALTH_URL} connection refused (stack is stopped or starting)${NC}"
    else
      echo -e "${YELLOW}! ${HEALTH_URL} returned HTTP ${http_code}${NC}"
    fi
    ;;

  logs)
    check_docker
    ensure_env
    svc="${1:-api}"
    if [ $# -gt 0 ]; then shift; fi
    compose_cmd logs -f "$svc" "$@"
    ;;

  seed)
    echo -e "${BOLD}▸ Running dev_seed to create test workspace & tokens ...${NC}"
    ensure_env
    if ! command -v uv >/dev/null 2>&1; then
      echo -e "${YELLOW}uv not found. Running scripts/bootstrap.sh first...${NC}"
      bash scripts/bootstrap.sh
    fi
    PYTHONIOENCODING="utf-8" uv run python scripts/dev_seed.py "$@"
    ;;

  reset)
    check_docker
    ensure_env
    force=false
    for arg in "$@"; do
      if [ "$arg" = "-f" ] || [ "$arg" = "--force" ]; then
        force=true
      fi
    done

    if [ "$force" = false ]; then
      echo -e "${YELLOW}${BOLD}⚠ WARNING: This will stop all dev containers and WIPE all local dev database volumes and data/workspaces/.${NC}"
      read -rp "Are you sure you want to proceed? [y/N] " confirm
      case "$confirm" in
        [yY][eE][sS]|[yY])
          ;;
        *)
          echo "Aborted."
          exit 0
          ;;
      esac
    fi

    # data/workspaces/ is now a host BIND (not the `workspaces` named volume `down -v`
    # below removes), and the api container writes into it as root (no USER in
    # deploy/Dockerfile.backend) — fake-run artifacts under
    # <ws>/content/<profile>/fake-runs/, <ws>/profiles, <ws>/content. A plain host-side
    # `rm -rf` would need sudo on Linux, so clear it from INSIDE the container, where
    # root already has permission.
    workspaces_dir="$REPO_ROOT/data/workspaces"
    workspaces_clear_failed=false
    if [ -d "$workspaces_dir" ] && [ -n "$(ls -A "$workspaces_dir" 2>/dev/null)" ]; then
      # Stop api first — an in-flight fake run could otherwise write a new file
      # between this clear and `down -v` below, leaving the "clean state" claim false.
      compose_cmd stop api >/dev/null 2>&1 || true
      echo -e "${BOLD}▸ Clearing data/workspaces/ via the api container ...${NC}"
      if ! compose_cmd run --rm --no-deps --entrypoint find api /app/data/workspaces -mindepth 1 -delete; then
        workspaces_clear_failed=true
        echo -e "${YELLOW}! Could not clear data/workspaces/ via the api container.${NC}"
        echo -e "  ${DIM}Remove it by hand if needed: sudo rm -rf ${workspaces_dir}${NC}"
      fi
    fi

    echo -e "${BOLD}▸ Tearing down dev stack and removing volumes ...${NC}"
    compose_cmd down -v
    # `down -v` only removes a volume this compose config still MOUNTS somewhere.
    # `workspaces` stays DECLARED but unused now that the dev override binds the host
    # dir instead, so it survives `down -v` as an orphan. Best-effort: fine if it's
    # already gone.
    docker volume rm "${PROJECT_NAME}_workspaces" >/dev/null 2>&1 || true

    if [ "$workspaces_clear_failed" = true ]; then
      echo -e "${YELLOW}${BOLD}! Dev containers and database volumes are wiped, but data/workspaces/ was NOT fully cleared — see the warning above.${NC}"
    else
      echo -e "${GREEN}✓ Dev stack wiped and reset to clean state.${NC}"
    fi
    ;;

  help|--help|-h)
    show_help
    ;;

  *)
    echo -e "${RED}Unknown command: ${cmd}${NC}" >&2
    echo "Run './scripts/stack.sh help' for usage." >&2
    exit 1
    ;;
esac
