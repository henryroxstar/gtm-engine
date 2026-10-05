"""Deterministic capture of the surfaces the one-product-per-run P1 work touches.

Written to run against **BASE code** (a ``git archive`` of the pinned commit) and against HEAD:
it only calls signatures that exist at BASE, and passes ``product=`` only when ``scoped`` is set
(HEAD, for a profile that has a second product). The committed golden was produced by BASE; the
test compares HEAD's capture with it after removing the named permitted additions.

Fixture tenants are fictional (``tests/fixtures/one_product/profiles``).
"""

from __future__ import annotations

import io
import json
import os
import re
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import patch

FIXED_RUN_ID = "run-19700101-000000-fixed0"
_GENERATED = re.compile(r"^Generated: .*$", re.MULTILINE)


def _kw(product: str | None, scoped: bool) -> dict[str, Any]:
    return {"product": product} if scoped and product else {}


def _norm_state(raw: dict) -> dict:
    """Keys and shape only: the ids and timestamps are the run's, not the contract's."""
    raw = dict(raw)
    raw["run_id"] = "<run-id>"
    raw["started_at"] = "<ts>"
    return {
        "keys": sorted(raw),
        "stages": {k: sorted(v) for k, v in raw["stages"].items()},
        "stage_order": list(raw["stages"]),
        "mode": raw.get("mode"),
        "profile": raw.get("profile"),
    }


def _capture(
    profiles_root: Path, work: Path, profile: str, *, product: str | None, scoped: bool
) -> dict[str, Any]:
    """Everything below is read through the same public entry points a run uses."""
    content_root = work / "content"
    content_root.mkdir(parents=True, exist_ok=True)
    kw = _kw(product, scoped)
    out: dict[str, Any] = {}

    from gtm_core.messaging import registry

    reg = registry.load(profile, profiles_root=profiles_root, product=product)
    out["registry"] = {
        "claims": {k: v.statement for k, v in sorted(reg.claims.items())},
        "proof": sorted(reg.proof),
        "angles": sorted(a.id for a in reg.angles.values())
        if hasattr(reg.angles, "values")
        else sorted(a.id for a in reg.angles),
        "seats": sorted(reg.seats),
    }

    from gtm_core import web_sweep_hits

    web_sweep_hits._regex_cache.clear()
    out["ai_vocab_regex"] = web_sweep_hits.get_ai_vocab_regex(profile, **kw).pattern

    from gtm_core.hook_coverage.premise import capability_vocab

    out["capability_vocab"] = capability_vocab(profile, profiles_root, **kw)

    spec = work / "spec.md"
    spec.write_text("# spec\n\ncapability: observability\n", encoding="utf-8")
    from agent.mcp.judge.server import judge_context

    out["judge_context"] = judge_context(str(spec), profile, **kw)

    from gtm_core.lanes.context import load_context

    ctx = load_context(profile, content_root=content_root, profiles_root=profiles_root, **kw)
    out["lanes_context"] = {
        "notes": list(ctx.notes),
        "competitors": sorted(str(c) for c in ctx.competitors),
        "matrix_type": type(ctx.matrix).__name__,
        "matrix_cells": len(getattr(ctx.matrix, "cells", []) or []) if ctx.matrix else 0,
    }

    from gtm_core.messaging_intake import export_profile_to_markdown

    text = export_profile_to_markdown(profile, profiles_root, **kw)
    out["intake_export"] = _GENERATED.sub("Generated: <ts>", text)

    from gtm_core import run_state
    from gtm_core.prospect_paths import run_state_json

    run_state.get_or_create_run_state(profile, content_root=content_root, **kw)
    if scoped:
        state_path = run_state.run_state_path(profile, content_root, product)
    else:  # BASE has no run_state_path; the default product's file is the legacy one
        state_path = run_state_json(profile, content_root)
    out["run_state_file"] = state_path.name
    out["run_state"] = _norm_state(json.loads(state_path.read_text(encoding="utf-8")))

    from gtm_core.prospects_state import load_latest, upsert_latest

    upsert_latest(
        profile,
        [
            {
                "company": "Fictional Alpha Co",
                "segment": "enterprise",
                "market": "singapore",
                "tier": "A",
                "score": "9",
                "verdict": "send",
                "lane": "champion",
            },
            {
                "company": "Fictional Gamma Co",
                "segment": "enterprise",
                "market": "singapore",
                "tier": "B",
                "score": "6",
            },
        ],
        source_run=FIXED_RUN_ID,
        generated_at="1970-01-01T00:00:00+00:00",
        content_root=content_root,
        **kw,
    )
    latest = load_latest(profile, content_root)
    latest.pop("generated_at", None)
    for item in latest.get("items", []):  # a random uuid: keep its presence, drop its value
        if "account_id" in item:
            item["account_id"] = "<account-id>"
    out["latest"] = latest

    from gtm_core import prospect_status_cli

    buf = io.StringIO()
    with redirect_stdout(buf):
        try:
            prospect_status_cli.main(
                ["--profile", profile, *(["--product", product] if kw else [])]
            )
        except SystemExit:
            pass
    out["status_block"] = buf.getvalue()

    # Every file the run wrote: a new sibling file is a change the value comparison cannot see.
    out["content_files"] = sorted(
        str(p.relative_to(content_root)) for p in content_root.rglob("*") if p.is_file()
    )

    return out


def capture(
    profiles_root: Path, work: Path, profile: str, *, product: str | None, scoped: bool
) -> dict[str, Any]:
    """Run :func:`_capture` with the two root variables set, restoring the environment after.

    The env is process-global, so a capture that left it set would repoint every later test in
    the session at the fixture tenants.
    """
    env = {
        "GTM_PROFILES_ROOT": str(profiles_root),
        "GTM_CONTENT_ROOT": str(work / "content"),
    }
    with patch.dict(os.environ, env):
        return _capture(profiles_root, work, profile, product=product, scoped=scoped)
