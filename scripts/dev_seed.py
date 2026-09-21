#!/usr/bin/env python
"""Local-only bootstrap: an empty local stack → a workspace that can run, in one command.

For a client developer exercising the run lifecycle against the local Docker stack with
GTM_FAKE_RUNS=1 (no model key, no Doppler). It drives only the public API:

    register (or log in) → read the workspace id → grant a paid plan through the
    billing-sync route → register + activate a profile → print the tokens and
    copy-paste curl for create-run / stream / approve

Loopback only, checked before any request. The grant uses BILLING_SYNC_SECRET — the service
secret that GRANTS entitlement (backend/deps.py:require_service_auth). Pointed at staging
this would be a free self-upgrade, so a base URL whose host is not loopback is refused
before an HTTP client is even built. The secret is read from the environment or --env-file
and is never printed.

Why a grant at all: a new workspace is `free` with a $0 cap (V011), and the §R2 pre-check
refuses a run at or over its cap — a fake run included, since that check stays real.

Pack-mode ready: synthetic profile and knowledge files (packs.toml, PROFILE.md,
voice.md, icp-personas.md) are provisioned directly to disk under the workspace
profiles directory, enabling local testing of pack-mode runs without requiring an
LLM model key.

Idempotent: an existing user logs in; each grant carries a fresh sync_id, so a re-run
re-applies the plan rather than being dropped as a duplicate; profile registration is
ON CONFLICT DO NOTHING and activation is a plain update.

Usage:
    uv run python scripts/dev_seed.py
    uv run python scripts/dev_seed.py --base-url http://127.0.0.1:8000 \\
        --email dev@example.com --password local-dev-password --env-file deploy/.env.dev

Exit 0 = seeded; 1 = a step failed; 2 = refused before any request.
"""

from __future__ import annotations

import argparse
import contextlib
import ipaddress
import os
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx

# Force stdout/stderr to UTF-8 on Windows consoles to prevent UnicodeEncodeError (Issue #230)
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        with contextlib.suppress(Exception):
            _stream.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]
SECRET_ENV = "BILLING_SYNC_SECRET"  # nosec B105 — an env-var name, not a credential value
DEFAULT_PROFILE = "example-widgets"
DEFAULT_PASSWORD = "local-dev-password"  # noqa: S105 — local-only throwaway account  # nosec B105
_CAP_USD = 5.0
_LOOPBACK_NAMES = frozenset({"localhost"})


class SeedError(Exception):
    """A step failed; the message has already been printed."""


def _safe_text(text: str, stream: object | None = None) -> str:
    """Encode text safely for the target stream, falling back gracefully if unencodable."""
    target = stream if stream is not None else sys.stdout
    encoding = getattr(target, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
        return text
    except (UnicodeEncodeError, LookupError):
        return (
            text.replace("✓", "[ok]")
            .replace("✗", "[fail]")
            .replace("❌", "[fail]")
            .replace("→", "->")
            .replace("—", "--")
            .replace("─", "-")
            .encode(encoding, errors="replace")
            .decode(encoding, errors="replace")
        )


def _safe_print(text: str = "", file: object | None = None) -> None:
    target = file if file is not None else sys.stdout
    print(_safe_text(text, target), file=target)


def _ok(msg: str) -> None:
    _safe_print(f"  ✓ {msg}")


def _fail(msg: str) -> None:
    _safe_print(f"  ✗ {msg}")
    raise SeedError(msg)


def require_loopback(base_url: str) -> str:
    """Return ``base_url`` without a trailing slash, or raise ValueError unless its host is
    this machine.

    ``urlsplit().hostname`` is the host an HTTP client connects to, so a userinfo trick such
    as ``http://127.0.0.1@api.example.com`` resolves to the real host and is refused; a bare
    ``host:port`` has no scheme, hence no hostname, and is refused too.
    """
    parts = urlsplit(base_url or "")
    host = parts.hostname
    if parts.scheme not in ("http", "https") or not host:
        raise ValueError(f"refusing {base_url!r}: expected http(s)://<loopback host>[:port]")
    if host not in _LOOPBACK_NAMES and not _is_loopback_ip(host):
        raise ValueError(
            f"refusing {base_url!r}: dev_seed grants a paid plan with the billing-sync "
            "secret, so it only talks to loopback (127.0.0.1, localhost, ::1)"
        )
    return base_url.rstrip("/")


def _is_loopback_ip(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def read_secret(env_file: Path | None) -> str | None:
    """BILLING_SYNC_SECRET from the environment, else from ``env_file``. Never printed.

    Stripped: a trailing newline on a copied secret is a silent 401, not a visible typo.
    """
    value = os.environ.get(SECRET_ENV, "").strip()
    if value:
        return value
    if env_file is None or not env_file.is_file():
        return None
    return _env_file_values(env_file).get(SECRET_ENV) or None


def _env_file_values(path: Path) -> dict[str, str]:
    """KEY=VALUE lines (optional ``export``, optional quotes); comments and blanks skipped."""
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _tokens(client: httpx.Client, email: str, password: str) -> dict:
    r = client.post(
        "/v1/auth/register",
        json={"email": email, "password": password, "display_name": "Local Dev"},
    )
    if r.status_code == 201:
        _ok(f"registered {email}")
        return r.json()
    if r.status_code != 409:
        _fail(f"register → {r.status_code}: {r.text}")
    r = client.post("/v1/auth/login", json={"email": email, "password": password})
    if r.status_code != 200:
        _fail(f"login → {r.status_code}: {r.text} (the user exists — pass its --password)")
    _ok(f"logged in as existing user {email}")
    return r.json()


def _workspace_id(client: httpx.Client, auth: dict) -> str:
    r = client.get("/v1/workspace", headers=auth)
    if r.status_code != 200:
        _fail(f"GET /v1/workspace → {r.status_code}: {r.text}")
    workspace_id = r.json()["id"]
    _ok(f"workspace_id={workspace_id}")
    return workspace_id


def _grant_plan(client: httpx.Client, workspace_id: str, secret: str) -> None:
    # Raw secret, no "Bearer": require_service_auth compares the whole header value.
    r = client.put(
        f"/v1/entitlement/{workspace_id}",
        headers={"Authorization": secret},
        json={
            "entitlement": "pro",
            "cap_usd": _CAP_USD,
            "sync_id": f"dev-seed-{uuid.uuid4().hex}",
            "status": "active",
        },
    )
    if r.status_code == 500:
        _fail("entitlement sync → 500: is BILLING_SYNC_SECRET set in the api container's env?")
    if r.status_code != 200:
        _fail(f"entitlement sync → {r.status_code}: {r.text}")
    outcome = r.json().get("outcome")
    if outcome != "new":
        _fail(f"entitlement sync outcome {outcome!r}, expected 'new'")
    _ok(f"granted entitlement=pro cap_usd={_CAP_USD:g}")


def _ensure_profile(client: httpx.Client, auth: dict, profile: str) -> None:
    r = client.post("/v1/profiles", headers=auth, json={"profile_name": profile})
    if r.status_code != 201:
        _fail(f"POST /v1/profiles → {r.status_code}: {r.text}")
    r = client.post(f"/v1/profiles/{profile}/activate", headers=auth)
    if r.status_code != 200:
        _fail(f"activate profile → {r.status_code}: {r.text}")
    _ok(f"profile {profile!r} registered and active")


DEV_PROFILE_FIXTURES: dict[str, str] = {
    "packs.toml": """active = ["marketing", "prospecting", "creator"]
default = "marketing"
""",
    "PROFILE.md": """# Example Widgets Profile
brand_name: Example Widgets Ltd
company_name: Example Widgets Ltd
industry: Developer Tooling
website: https://widgets.example.com
output_folder: content/example-widgets
""",
    "BRAND.toml": """[palette]
primary = "#0F172A"
secondary = "#38BDF8"

[typography]
font_family = "Inter"

[disclosure]
line = "AI-assisted content created by Example Widgets Ltd."
""",
    "knowledge/voice.md": """# Brand Voice
Tone: Authoritative, engineering-first, concise.
Register: Direct, technical, no buzzwords.
""",
    "knowledge/icp-personas.md": """# Target Personas
- VP of Engineering
- Lead Platform Architect
- Head of Developer Platform
""",
    "knowledge/brand-notes.md": """# Brand Notes
Core values: Hermetic reproducibility, developer sovereignty, least privilege.
""",
    "knowledge/case-studies.md": """# Case Studies
- Acme Corp migrated 40 pipelines in 2 weeks.
""",
}


def _provision_profile_files(workspace_id: str, profile: str) -> None:
    """Populate synthetic mock profile and knowledge files on disk for pack-mode testing."""
    from gtm_core.paths import workspace_profiles_root

    profiles_dir = workspace_profiles_root(workspace_id, REPO)
    pdir = profiles_dir / profile
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "knowledge").mkdir(parents=True, exist_ok=True)

    for rel_path, content in DEV_PROFILE_FIXTURES.items():
        target = pdir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    _ok(f"provisioned pack files for {profile!r} under {pdir}")


def _print_summary(base_url: str, workspace_id: str, tokens: dict, profile: str) -> None:
    access = tokens["access_token"]
    _safe_print(
        f"""
workspace_id:  {workspace_id}
access_token:  {access}
refresh_token: {tokens["refresh_token"]}
profile_name:  {profile}

# -- copy-paste (bash/zsh) -----------------------------------------------------
export GTM_API={base_url}
export GTM_TOKEN={access}

# 1a. create a prompt-mode run -> 202 {{"run_id": "...", "status": "queued", ...}}
curl -sS -X POST "$GTM_API/v1/runs" \\
  -H "Authorization: Bearer $GTM_TOKEN" -H "Content-Type: application/json" \\
  -d '{{"profile_name": "{profile}", "prompt": "Draft a LinkedIn post for Example Widgets Ltd", "dry_run": true}}'
export RUN_ID=<run_id from the response>

# 1b. OR create a pack-mode run (marketing / linkedin-post) -> 202:
# curl -sS -X POST "$GTM_API/v1/runs" \\
#   -H "Authorization: Bearer $GTM_TOKEN" -H "Content-Type: application/json" \\
#   -d '{{"profile_name": "{profile}", "pack": "marketing", "variant": "linkedin-post"}}'

# 2. stream it (SSE): snapshot, then live frames until `done`
curl -sS -N "$GTM_API/v1/runs/$RUN_ID/stream" -H "Authorization: Bearer $GTM_TOKEN"

# 3. approve the gate: content_sha must equal the run's current pending_content_sha
SHA=$(curl -sS "$GTM_API/v1/runs/$RUN_ID" -H "Authorization: Bearer $GTM_TOKEN" \\
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["pending_content_sha"])')
curl -sS -X POST "$GTM_API/v1/runs/$RUN_ID/gate" \\
  -H "Authorization: Bearer $GTM_TOKEN" -H "Content-Type: application/json" \\
  -d "{{\\"decision\\": \\"approve\\", \\"content_sha\\": \\"$SHA\\"}}"
# then list the deliverable: curl -sS "$GTM_API/v1/runs/$RUN_ID/artifacts" -H "Authorization: Bearer $GTM_TOKEN"
"""
    )


def seed(base_url: str, email: str, password: str, profile: str, secret: str) -> None:
    # The client carries BILLING_SYNC_SECRET. trust_env=False: no HTTP(S)_PROXY / ALL_PROXY /
    # system proxy may see it, loopback URL or not. follow_redirects=False: a redirect must
    # never carry it to another host.
    with httpx.Client(
        base_url=base_url, timeout=30.0, trust_env=False, follow_redirects=False
    ) as client:
        print("1. register or log in")
        tokens = _tokens(client, email, password)
        auth = {"Authorization": f"Bearer {tokens['access_token']}"}
        print("2. resolve the workspace")
        workspace_id = _workspace_id(client, auth)
        print("3. grant a paid plan (billing-sync route, loopback only)")
        _grant_plan(client, workspace_id, secret)
        print("4. register + activate a profile")
        _ensure_profile(client, auth, profile)
        print("5. provision pack-mode profile fixtures (offline)")
        _provision_profile_files(workspace_id, profile)
    _print_summary(base_url, workspace_id, tokens, profile)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--email", default="dev@example.com")
    ap.add_argument("--password", default=DEFAULT_PASSWORD)
    ap.add_argument("--env-file", type=Path, default=REPO / "deploy" / ".env.dev")
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    args = ap.parse_args(argv)

    try:
        base_url = require_loopback(args.base_url)
    except ValueError as exc:
        _safe_print(f"✗ {exc}", file=sys.stderr)
        return 2
    secret = read_secret(args.env_file)
    if not secret:
        _safe_print(
            f"✗ {SECRET_ENV} not found in the environment or {args.env_file}",
            file=sys.stderr,
        )
        return 2

    try:
        seed(base_url, args.email, args.password, args.profile, secret)
    except SeedError:
        _safe_print("\n❌ dev seed FAILED", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        _safe_print(
            f"\n❌ HTTP error: {exc} — is the local stack up on {base_url}?",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
