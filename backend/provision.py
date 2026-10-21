"""Operator CLI for one-call agent setup (W1, PRD 2026-10-19a): ``python -m backend.provision``.

A thin wrapper over the SAME service the endpoint uses (``backend/services/provision.py``); it adds only what
a person at a terminal needs: it generates the ``Idempotency-Key`` once and remembers it, so running the same
command again resumes the same job instead of starting another, and it can wait while the onboarding job runs.

    python -m backend.provision --workspace <uuid> --name "Acme Agent" --url https://acme.example
    # ...review and promote the profile draft, then run the SAME command again:
    python -m backend.provision --workspace <uuid> --name "Acme Agent" --url https://acme.example

Provider keys are never accepted as arguments (they would sit in the process table); name the environment
variable that holds each one: ``--integration apollo=APOLLO_API_KEY``. The raw API key is printed once, by
the run that finishes setup; if that output is lost, add ``--reissue-key``.

Honours the same kill switch as the route (``GTM_AGENT_PROVISION_ENABLED``) and needs ``DATABASE_URL``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import uuid
from collections.abc import Mapping
from pathlib import Path

from .services import provision as svc

_STATE_ENV = "GTM_PROVISION_STATE_DIR"
_WAIT_POLL_S = 2.0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m backend.provision", description=__doc__.split("\n\n")[0]
    )
    p.add_argument("--workspace", required=True, help="the workspace id")
    p.add_argument(
        "--name", required=True, help="the agent's name (exact-match unique per workspace)"
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--profile", help="an already-promoted profile")
    src.add_argument("--url", help="a site to onboard a profile from")
    p.add_argument(
        "--packs", help="comma-separated pack names (default: every pack the profile activates)"
    )
    p.add_argument("--budget", type=float, help="the agent's monthly budget in USD")
    p.add_argument("--daily-cap", type=int, help="the agent's daily dispatch cap")
    p.add_argument("--key-label", default="provisioned", help="label for the API key")
    p.add_argument(
        "--integration",
        action="append",
        default=[],
        metavar="PROVIDER=ENV_VAR",
        help="a provider whose key is in the named environment variable (repeatable)",
    )
    p.add_argument("--reissue-key", action="store_true", help="replace the key this setup minted")
    p.add_argument(
        "--wait", type=int, default=0, metavar="SECONDS", help="poll while onboarding runs"
    )
    p.add_argument(
        "--state-dir", help=f"where the idempotency key is remembered (env {_STATE_ENV})"
    )
    return p.parse_args(argv)


def build_request(args: argparse.Namespace, environ: Mapping[str, str]) -> svc.ProvisionRequest:
    integrations: dict[str, str] = {}
    for item in args.integration:
        provider, sep, var = item.partition("=")
        value = environ.get(var, "") if sep else ""
        if not provider or not var:
            raise SystemExit(f"--integration wants PROVIDER=ENV_VAR, got {item!r}")
        if not value:
            raise SystemExit(
                f"environment variable {var} is not set (it should hold the {provider} key)"
            )
        integrations[provider] = value
    return svc.ProvisionRequest(
        name=args.name,
        profile_name=args.profile,
        source_url=args.url,
        packs=[s for s in (args.packs or "").split(",") if s] or None,
        monthly_budget_usd=args.budget,
        daily_dispatch_cap=args.daily_cap,
        integrations=integrations,  # type: ignore[arg-type]  # validated against the provider list by the model
        key_label=args.key_label,
        reissue_key=args.reissue_key,
    )


def idempotency_key_for(state_dir: Path, workspace_id: str, req: svc.ProvisionRequest) -> str:
    """The key for this exact request in this workspace: made once, then remembered (mode 0600).

    The file is named by the request hash, so a different request gets a different key and the same request
    always resumes its own job. Nothing secret is stored: the key is an opaque random id.
    """
    fingerprint = hashlib.sha256(
        f"{workspace_id}|{svc.request_hash(req, svc.normalise_source(req))}".encode()
    ).hexdigest()
    path = state_dir / f"{fingerprint}.key"
    try:
        remembered = path.read_text(encoding="utf-8").strip()
        if remembered:
            return remembered
    except OSError:
        pass
    key = f"cli-{uuid.uuid4().hex}"
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(key + "\n")
    return key


def _waiting(out: dict) -> bool:
    return (out.get("next_action") or {}).get("kind") == "wait"


async def drive(args: argparse.Namespace, environ: Mapping[str, str], out=None) -> int:
    from agent.config import Config

    from .database import create_pool
    from .deps import WorkspaceCtx, fetch_entitlement
    from .routers.provision import provision_enabled, workspace_cfg

    if not provision_enabled():
        print(
            "GTM_AGENT_PROVISION_ENABLED is not on; refusing (the endpoint is shut the same way).",
            file=sys.stderr,
        )
        return 2
    dsn = environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is required.", file=sys.stderr)
        return 2

    req = build_request(args, environ)
    state_dir = Path(
        args.state_dir or environ.get(_STATE_ENV) or Path.home() / ".gtm" / "provision"
    )
    key = idempotency_key_for(state_dir, args.workspace, req)

    pool = await create_pool(dsn, application_name="gtm-provision-cli")
    try:
        ws = WorkspaceCtx(
            user_id="operator-cli",
            workspace_id=args.workspace,
            entitlement=await fetch_entitlement(pool, args.workspace),
        )
        cfg = await workspace_cfg(pool, Config.from_env(), args.workspace)
        deadline = asyncio.get_running_loop().time() + args.wait
        while True:
            code, body = await svc.provision(pool, cfg, ws, idempotency_key=key, req=req)
            if not (_waiting(body) and asyncio.get_running_loop().time() < deadline):
                break
            await asyncio.sleep(_WAIT_POLL_S)
    except Exception as exc:  # noqa: BLE001 — an HTTPException from the service is the refusal to show
        detail = getattr(exc, "detail", None)
        print(
            json.dumps({"error": detail if detail is not None else str(exc)}, indent=2),
            file=sys.stderr,
        )
        return 1
    finally:
        await pool.close()

    print(json.dumps(body, indent=2, default=str), file=out or sys.stdout)
    if (body.get("key") or {}).get("raw_key"):
        print("\nThe API key above is shown ONCE. Store it now.", file=sys.stderr)
    return (
        0 if code == 200 else 3
    )  # 3 = not finished yet (waiting, or parked for the profile review)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(drive(parse_args(argv), os.environ))


if __name__ == "__main__":
    raise SystemExit(main())
