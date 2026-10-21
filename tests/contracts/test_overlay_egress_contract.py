"""The shipped CLAUDE.md must name every registered non-MCP egress module.

Why this exists
---------------
The public CLAUDE.md is an OSS *overlay* — a hand-maintained file outside the codegen
path, so nothing regenerates it when reality moves. Twice now its egress claim went
stale the same way: "you never make raw HTTP calls / zero backend egress" was written,
then a legitimate, allowlisted egress module was added (backend/oidc.py + push.py the
first time, gtm_core/dataset_fetch.py the second), and the overlay kept shipping the
old absolute claim. The de-brand lint can't see this (no tenant token involved) and
codegen-sync can't either (overlays aren't generated).

The §R6 semgrep rule's exclude list IS the machine-readable registry of deliberate
egress. This test holds the shipped doc to it: every file-level gtm_core/ or backend/
entry in that allowlist must be named in the CLAUDE.md that ships. Register a new
egress point without documenting it publicly → this fails in the same PR.

Runs identically in both trees: in the private repo the shipped doc is
oss/overlays/CLAUDE.md; in the public carve (where oss/ is never carved) the overlay
has already become the root CLAUDE.md.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SEMGREP = REPO / ".semgrep" / "gtm-invariants.yml"
RULE_ID = "gtm-no-raw-egress-in-brain"

# agent/ transport modules (publish/reply/voice/gate_notify, MCP workers) are described
# functionally in the doc's gate/tool-layer sections; the drift that actually happened —
# twice — was in these two packages, where a module is real egress *infrastructure* the
# public doc's absolute claim silently contradicts.
CONTRACT_PACKAGES = ("gtm_core/", "backend/")


def _shipped_claude_md() -> Path:
    overlay = REPO / "oss" / "overlays" / "CLAUDE.md"
    return overlay if overlay.exists() else REPO / "CLAUDE.md"


def _r6_rule_block() -> str:
    text = SEMGREP.read_text(encoding="utf-8")
    start = text.find(f"id: {RULE_ID}")
    assert start != -1, f"{RULE_ID} not found in {SEMGREP} — renamed? Update this test."
    rest = text[start:]
    nxt = re.search(r"\n\s*- id: ", rest)
    return rest[: nxt.start()] if nxt else rest


def _registered_egress_files() -> list[str]:
    """File-level (non-glob) exclude entries under the contract packages."""
    block = _r6_rule_block()
    entries = re.findall(r'-\s*"(/[^"*]+\.py)"', block)
    return sorted({e.lstrip("/") for e in entries if e.lstrip("/").startswith(CONTRACT_PACKAGES)})


def test_the_r6_registry_is_where_this_test_expects_it():
    files = _registered_egress_files()
    # If the allowlist moves or empties, fail loudly rather than passing on nothing.
    assert files, "no file-level gtm_core//backend/ entries in the §R6 allowlist — moved?"
    assert "gtm_core/dataset_fetch.py" in files  # the entry that motivated this contract


def test_every_registered_egress_module_is_named_in_the_shipped_claude_md():
    doc = _shipped_claude_md().read_text(encoding="utf-8")
    missing = [f for f in _registered_egress_files() if f not in doc]
    assert not missing, (
        f"{_shipped_claude_md()} does not name registered egress module(s) {missing}. "
        "The public doc claims all external I/O is MCP-only except a pinned, named set — "
        "a module in the §R6 allowlist but absent from the doc makes that claim false. "
        "Name it in the egress section (with its pinned destination) in the same PR that "
        "registers it."
    )


def test_registered_egress_modules_actually_exist():
    """A stale allowlist entry (module deleted, entry kept) would quietly widen §R6."""
    missing = [f for f in _registered_egress_files() if not (REPO / f).exists()]
    assert not missing, f"§R6 allowlist names module(s) that no longer exist: {missing}"
