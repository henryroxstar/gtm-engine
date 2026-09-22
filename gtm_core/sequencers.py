"""Sequencer capability registry resolver for gtm_core — stdlib-only, env-overridable.

Mirrors :mod:`gtm_core.models`: reference a *capability* by (provider, name) and resolve
it through committed config (:mod:`gtm_core.sequencers.toml`) instead of trusting vendor
prose in a skill or a doc. See that file's header comment for the CAPABILITY vs
CONFIGURATION distinction this registry exists to enforce — this module is the resolver,
the toml is the data, and :mod:`gtm_core.email_compliance` is the only caller that turns a
resolved :class:`Capability` into a preflight verdict.

  GTM_SEQUENCERS_REGISTRY — absolute path to the registry toml (default: co-located
                            gtm_core/sequencers.toml)

THE ONE GRANTING TEST (rule 1 in the toml header): only the literal boolean ``True``
grants. ``False``, the string ``"unknown"``, a missing key, an empty string, the STRING
``"true"``, an integer, a list — anything else — refuses. :func:`_grants` is that test,
in one place, so no caller can re-derive it more permissively.

NOT TENANT-WRITABLE (rule 3): :func:`_resolve_registry_path` refuses a path that resolves
under the profiles root or the content root, so a tenant can never author its own
capability claim — the same posture the backend holds for entitlement and publish
settings. A tenant may only *tighten* an already-granted capability via
:func:`apply_tenant_override`, never widen one.
"""

from __future__ import annotations

import datetime
import os
import re
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from gtm_core.models import _SECRET_SHAPE  # single source of truth for the secret-shape guard
from gtm_core.paths import _safe_segment, resolve_content_root, resolve_profiles_root

_HTTPS_SOURCE = re.compile(r"^https://")


class SequencerOverrideError(ValueError):
    """Raised when a tenant override attempts to name an unknown capability or widen one.

    Carries ``.rule`` (``unknown_capability_override`` / ``override_widen``) so a test can
    assert *which* guard fired, mirroring :class:`gtm_core.packs.loader.PackValidationError`.
    """

    def __init__(self, rule: str, message: str) -> None:
        self.rule = rule
        super().__init__(f"[{rule}] {message}")


@dataclass(frozen=True)
class Capability:
    """A resolved capability — the vendor FACT plus whether it is granted.

    ``granted`` is the only field a caller should branch on; ``supported`` /
    ``readable_via_api`` / ``settable_via_api`` are carried through for the preflight
    ladder (:mod:`gtm_core.email_compliance`) and for rendering, never for a caller to
    re-derive a granting decision from directly.
    """

    provider: str
    name: str
    supported: object  # True / False / "unknown" — the vendor CAPABILITY fact
    readable_via_api: object  # True / False / "unknown" / None (absent = unknown)
    settable_via_api: object
    source: str | None
    verified_on: str | None
    ui_path: str | None
    granted: bool
    reason: str


def _grants(value: object) -> bool:
    """The one granting test for the whole registry — a closed list of exactly one value."""
    return value is True


def _assert_not_tenant_writable(path: Path) -> None:
    """Refuse a registry path that resolves under profiles/ or content/ (rule 3)."""
    for root in (resolve_profiles_root(), resolve_content_root()):
        try:
            path.relative_to(root)
        except ValueError:
            continue
        raise ValueError(
            f"sequencer registry must not resolve under a tenant-writable root ({root}): {path}"
        )


def _resolve_registry_path(registry_path: Path | None = None) -> Path:
    """Return the registry toml path, honouring an arg / env override / co-located default."""
    if registry_path is not None:
        path = Path(registry_path).expanduser().resolve()
    else:
        override = os.getenv("GTM_SEQUENCERS_REGISTRY")
        path = (
            Path(override).expanduser().resolve()
            if override
            else Path(__file__).resolve().parent / "sequencers.toml"
        )
    _assert_not_tenant_writable(path)
    return path


def _validate_capability_row(provider: str, name: str, table: dict) -> None:
    """Fail the whole registry load on a capability row that isn't citeable (rule 2)."""
    label = f"providers.{provider}.capabilities.{name}"
    if "supported" not in table:
        raise ValueError(f"{label}: missing required 'supported'")
    source = table.get("source")
    if not isinstance(source, str) or not _HTTPS_SOURCE.match(source):
        raise ValueError(f"{label}: 'source' must be an https:// URL, got {source!r}")
    verified_on = table.get("verified_on")
    if not verified_on:
        raise ValueError(f"{label}: missing required 'verified_on'")
    try:
        datetime.date.fromisoformat(str(verified_on))
    except ValueError as exc:
        raise ValueError(f"{label}: 'verified_on' is not an ISO date: {verified_on!r}") from exc
    for key, value in table.items():
        if isinstance(value, str) and _SECRET_SHAPE.search(value):
            raise ValueError(
                f"{label}: value for {key!r} looks like a secret (sk-…) — this registry is "
                "non-secret config."
            )


def _load_registry(registry_path: Path | None = None) -> dict:
    """Read + validate the registry toml. Raises ValueError on a missing/uncited/secret row."""
    path = _resolve_registry_path(registry_path)
    if not path.is_file():
        raise ValueError(f"sequencer registry not found: {path}")
    with path.open("rb") as f:
        registry = tomllib.load(f)
    for provider_name, provider_table in registry.get("providers", {}).items():
        for cap_name, cap_table in provider_table.get("capabilities", {}).items():
            _validate_capability_row(provider_name, cap_name, cap_table)
    return registry


def _resolve_from_registry(registry: dict, provider: str, name: str) -> Capability:
    """Resolve one (provider, name) against an already-loaded registry dict."""
    provider = _safe_segment(provider, "provider")
    name = _safe_segment(name, "capability")
    prov_table = registry.get("providers", {}).get(provider)
    if prov_table is None:
        return Capability(
            provider=provider,
            name=name,
            supported="unknown",
            readable_via_api=None,
            settable_via_api=None,
            source=None,
            verified_on=None,
            ui_path=None,
            granted=False,
            reason=f"unknown provider {provider!r} — refused",
        )
    cap_table = prov_table.get("capabilities", {}).get(name)
    if cap_table is None:
        return Capability(
            provider=provider,
            name=name,
            supported="unknown",
            readable_via_api=None,
            settable_via_api=None,
            source=None,
            verified_on=None,
            ui_path=None,
            granted=False,
            reason=(
                f"no verified capability row for {provider}/{name} — an absent row refuses "
                "exactly as 'unknown' does (rule 2)"
            ),
        )
    supported = cap_table.get("supported", "unknown")
    granted = _grants(supported)
    reason = (
        "granted: registry supported=true"
        if granted
        else f"refused: registry supported={supported!r} (only the literal boolean true grants)"
    )
    return Capability(
        provider=provider,
        name=name,
        supported=supported,
        readable_via_api=cap_table.get("readable_via_api"),
        settable_via_api=cap_table.get("settable_via_api"),
        source=cap_table.get("source"),
        verified_on=cap_table.get("verified_on"),
        ui_path=cap_table.get("ui_path"),
        granted=granted,
        reason=reason,
    )


def resolve_capability(
    provider: str,
    name: str,
    *,
    overrides: dict[str, object] | None = None,
    registry_path: Path | None = None,
) -> Capability:
    """Resolve a logical (provider, capability) pair to its :class:`Capability`.

    ``overrides`` — a tenant's per-capability tightening map (``{capability_name: False}``),
    applied via :func:`apply_tenant_override` after the base registry resolution. An unknown
    provider or an unverified/absent capability row REFUSES rather than raising, so a caller
    cannot mistake "nobody read the vendor's docs" for an exception to catch and ignore.

    Raises ``ValueError`` on an unsafe provider/capability segment, a missing registry, or a
    registry row that fails to load (rule 2).
    """
    registry = _load_registry(registry_path)
    cap = _resolve_from_registry(registry, provider, name)
    if overrides and cap.name in overrides:
        cap = apply_tenant_override({cap.name: cap}, {cap.name: overrides[cap.name]})[cap.name]
    return cap


def apply_tenant_override(
    base: dict[str, Capability], override: dict[str, object]
) -> dict[str, Capability]:
    """Apply a tenant's per-capability overrides to ``base`` (capability name -> Capability).

    Monotone-stricter only, mirroring :func:`gtm_core.packs.tenant.merge_pack_override`'s three
    techniques:

      - **closed key set** — every key in ``override`` must name a capability present in
        ``base``; an unknown key raises (rule ``unknown_capability_override``).
      - **the join** — an override may only tighten: the resolved ``granted`` is
        ``base.granted and override_value is not False`` restricted to ``override_value``
        literally being ``False`` (rule 4 / rule 1's closed list applies here too — ``true``,
        ``"unknown"``, or any other value is refused outright, never silently ignored).
      - **post-merge assertion** — nothing may end up MORE granted than it started; this is
        enforced by construction above, and asserted again here as defence in depth.

    Raises :class:`SequencerOverrideError` on an unknown key or a non-``False`` override value.
    """
    unknown = set(override) - set(base)
    if unknown:
        raise SequencerOverrideError(
            "unknown_capability_override",
            f"override names capability/capabilities not in the base set: {sorted(unknown)}",
        )
    merged: dict[str, Capability] = {}
    for cap_name, cap in base.items():
        if cap_name not in override:
            merged[cap_name] = cap
            continue
        ov = override[cap_name]
        if ov is not False:
            raise SequencerOverrideError(
                "override_widen",
                f"{cap.provider}/{cap_name}: tenant override must be literal false "
                f"(tighten-only, never true/'unknown'/anything else) — got {ov!r}",
            )
        new_granted = cap.granted and (ov is not False)  # ov is False here => always False
        if new_granted and not cap.granted:
            raise SequencerOverrideError(  # unreachable by construction — defence in depth
                "override_widen", f"{cap.provider}/{cap_name}: merge would widen a capability"
            )
        merged[cap_name] = replace(
            cap,
            granted=new_granted,
            reason=f"{cap.reason}; tenant override tightened to false",
        )
    return merged


def render_summary(capabilities: Sequence[Capability], *, fmt: str = "text") -> str:
    """Render resolved capabilities as ``fmt`` (``"text"`` or ``"html"``).

    The terminal preflight, the Telegram gate preview, and the dashboard inbound panel all
    call this so the three surfaces agree by construction (test-plan §3.E) instead of each
    re-deriving its own formatting of the same :class:`Capability` list.
    """
    if fmt not in ("text", "html"):
        raise ValueError(f"unknown render fmt: {fmt!r}")
    if fmt == "text":
        lines = [
            f"{cap.provider}/{cap.name}: {'GRANTED' if cap.granted else 'REFUSED'} — {cap.reason}"
            for cap in capabilities
        ]
        return "\n".join(lines)
    import html as _html

    rows = [
        "<div><b>{provider}/{name}</b>: {status} — {reason}</div>".format(
            provider=_html.escape(cap.provider),
            name=_html.escape(cap.name),
            status=_html.escape("GRANTED" if cap.granted else "REFUSED"),
            reason=_html.escape(cap.reason),
        )
        for cap in capabilities
    ]
    return "".join(rows)


# --- CLI ---------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Print a provider's resolved capability/capabilities via the registry.

    Exit codes mirror ``gtm_core.models``: 0 success, 2 on a bad provider/capability,
    unsafe segment, or a registry that fails to load.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.sequencers",
        description="Resolve a sequencer provider's capability/capabilities via the registry.",
    )
    parser.add_argument("provider", help="e.g. saleshandy")
    parser.add_argument("capability", nargs="?", default=None, help="e.g. stop_on_reply")
    parser.add_argument(
        "--registry",
        default=None,
        help="override registry path (default: GTM_SEQUENCERS_REGISTRY / co-located toml)",
    )
    parser.add_argument("--json", action="store_true", help="print resolved capabilities as JSON")
    args = parser.parse_args(argv)
    registry_path = Path(args.registry) if args.registry else None

    try:
        registry = _load_registry(registry_path)
    except ValueError as exc:
        print(f"[sequencers] {exc}", file=sys.stderr)
        return 2

    try:
        provider = _safe_segment(args.provider, "provider")
    except ValueError as exc:
        print(f"[sequencers] {exc}", file=sys.stderr)
        return 2

    if args.capability:
        names = [args.capability]
    else:
        names = sorted(registry.get("providers", {}).get(provider, {}).get("capabilities", {}))
        if not names:
            print(
                f"[sequencers] no verified capability rows for provider {provider!r}",
                file=sys.stderr,
            )
            return 2

    try:
        caps = [_resolve_from_registry(registry, provider, n) for n in names]
    except ValueError as exc:
        print(f"[sequencers] {exc}", file=sys.stderr)
        return 2

    if args.json:
        import dataclasses
        import json

        print(json.dumps([dataclasses.asdict(c) for c in caps], indent=2))
    else:
        print(render_summary(caps, fmt="text"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
