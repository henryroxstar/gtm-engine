"""Build the gate a pack graph's declared ``egress_scope`` requires (operator-side runners only).

A graph that declares ``egress_scope`` (``gtm_core.packs.loader._ALLOWED_EGRESS_SCOPES``) may only
run under the gate its scope names, on every node. :func:`build_scope_gate` turns the scope plus
the run's inputs into that gate, and **refuses** (raises :class:`EgressScopeError`) when it cannot:
an unreadable, foreign or unnamed manifest must stop the run before any model call, because a
scoped run with no gate is exactly the run the scope exists to prevent.

The table :data:`BUILDERS` is keyed by scope, never by pack or variant: nothing here knows which
graph asked. ``tests/contracts/test_internal_pack_graphs.py`` asserts its keys equal the loader's
closed set, so a scope cannot be admitted without a builder, or built without being admitted.

The gate is stateful (its page cap counts calls), so one is built per run and shared by every node.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping

from gtm_core import capture_manifest
from gtm_core.signal_obs import switch

#: A run id is a file-name segment of ``manifest-<id>.json``: a short plain token, nothing else.
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def valid_run_id(value: object) -> bool:
    """True for a run id that is safe as the ``<id>`` in ``manifest-<id>.json``."""
    return isinstance(value, str) and _RUN_ID.fullmatch(value) is not None


class EgressScopeError(RuntimeError):
    """A scoped run cannot start: what is missing or wrong, and what to do about it."""


def _capture_manifest_gate(cfg, profile: str, run_inputs: Mapping[str, object]):
    """A :class:`~gtm_core.capture_manifest.CaptureGate` over ``manifest-<manifest_run_id>.json``."""
    if not switch.sources_enabled():
        raise EgressScopeError(switch.DISABLED_MESSAGE)
    run_id = run_inputs.get("manifest_run_id")
    if not valid_run_id(run_id):
        raise EgressScopeError(
            "a capture run needs the run input manifest_run_id: the run id its manifest was "
            "written under (letters, digits, dot, dash, underscore); "
            "`python -m agent.source_capture` writes the manifest and passes it"
        )
    manifest_file = capture_manifest.manifest_path_for(profile, run_id, cfg.content_root)
    try:
        manifest = capture_manifest.load_manifest(manifest_file)
    except ValueError as exc:  # ManifestError is a ValueError; so is an unsafe segment
        raise EgressScopeError(
            f"cannot use the capture manifest for run {run_id!r}: {exc}"
        ) from exc
    if manifest.profile != profile:
        raise EgressScopeError(
            f"the manifest for run {run_id!r} was written for profile {manifest.profile!r}, "
            f"not {profile!r}: refusing it"
        )
    if manifest.run_id != run_id:
        raise EgressScopeError(
            f"the manifest file for run {run_id!r} names run id {manifest.run_id!r}: refusing it"
        )

    def _under_monthly_cap() -> bool:
        from . import budget  # §R2: the guard PipelineRunner uses; read at call time

        return budget.vps_budget_ok(cfg, profile)

    return capture_manifest.CaptureGate(
        manifest, budget_ok=_under_monthly_cap, manifest_path=manifest_file
    )


#: One builder per admitted scope: ``(cfg, profile, run_inputs) -> gate``.
BUILDERS: dict[str, Callable[..., object]] = {"capture_manifest": _capture_manifest_gate}


def build_scope_gate(scope: str, cfg, profile: str, run_inputs: Mapping[str, object] | None):
    """The gate ``scope`` requires for this run, or :class:`EgressScopeError`."""
    builder = BUILDERS.get(scope) if isinstance(scope, str) else None
    if builder is None:
        raise EgressScopeError(f"unknown egress scope {scope!r}: refusing to run ungated")
    return builder(cfg, profile, run_inputs or {})
