"""Audit the render-engine registry against a live provider model catalog — read-only, always.

Split out of :mod:`gtm_core.render_engines` so the resolver a render path actually calls stays
small: this module is reporting machinery, reached by a human running ``--audit``. (The resolver
does re-export two of its names from its bottom line for back-compat, so it IS imported — lazily
on this side — but no render path calls into it.) It opens no socket (§R6) — the caller holds the MCP tool that reads the
catalog and pipes the raw result in.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["AuditFinding", "audit_registry", "main_audit"]


#: The ONLY registry capability keys a model catalog can establish on its own.
#:
#: This is the load-bearing constant of the whole audit, and the reason the `presenter` ban cannot
#: be relaxed by a tag match. `output` is a fact the catalog states outright (`output_type`).
#: `identity_faithful` and `lip_sync` are ARCHITECTURAL properties nobody advertises: the live
#: catalog carries video models tagged `identity`, meaning reference-driven consistency across
#: shots — not the ability to hold one real person's trained likeness, and emphatically not the
#: ability to generate a mouth from an audio track. Both of those were established by rendering
#: something and looking at it. A tag is not that, so the audit refuses to speak to them and says
#: so, rather than quietly finding nothing.
#:
#: ``accepts_end_frame`` joins ``output`` because it is the same KIND of fact: a declared input
#: role the catalog states outright (``medias[].roles`` carrying both ``start_image`` and
#: ``end_image``), not an architectural property somebody had to render something to discover.
#: What it establishes is narrow and worth saying out loud — that the provider will ACCEPT the
#: request, not that the model HONOURS the last frame. Whether the final frame actually lands on
#: the still you sent is a render-and-look question, and `verified_on` is where that answer lives.
_CATALOG_ESTABLISHES = frozenset({"output", "accepts_end_frame"})


@dataclass(frozen=True)
class AuditFinding:
    """One thing a human should look at. Never an instruction, never applied."""

    kind: str
    subject: str
    detail: str


def _live_index(live: object | None) -> dict[str, dict] | None:
    """Normalise a `models_explore` payload into ``{model_id: item}``, or ``None`` if unusable.

    ``None`` means *unknown* and is returned for every unusable shape — no payload, the wrong
    shape, an empty catalog. An empty catalog is not a real provider state; reading it as "every
    pinned model is gone" would turn a failed probe into a page of false findings.
    """
    items = live
    if isinstance(live, dict):
        items = live.get("items")
    if not isinstance(items, list) or not items:
        return None
    index: dict[str, dict] = {}
    for item in items:
        if isinstance(item, dict) and item.get("id"):
            index[str(item["id"])] = item
    return index or None


def _media_roles(item: dict) -> set[str]:
    """Every media role this catalog entry declares, flattened across its media slots."""
    roles: set[str] = set()
    for slot in item.get("medias") or []:
        if isinstance(slot, dict):
            roles.update(str(r) for r in (slot.get("roles") or []))
    return roles


def _catalog_capabilities(item: dict) -> dict[str, object]:
    """What this catalog entry actually establishes — deliberately almost nothing.

    ``accepts_end_frame`` needs BOTH keyframe roles present. A model that takes ``start_image``
    alone animates forward from one still; only one that also takes ``end_image`` is being told
    where to arrive, which is the whole point of the keyframe lane.
    """
    roles = _media_roles(item)
    return {
        "output": str(item.get("output_type", "")),
        "accepts_end_frame": {"start_image", "end_image"} <= roles,
    }


def audit_registry(
    live: object | None,
    *,
    provider: str = "higgsfield",
    registry_path: Path | None = None,
) -> list[AuditFinding]:
    """Diff the registry against a live model catalog. Read-only, always.

    ``live`` is the raw ``models_explore`` result (or its ``items`` list). The provider is reached
    by an MCP tool the caller already holds, never by this module — nothing here opens a socket
    (§R6). Pass ``None`` when the probe failed: the audit then reports **unknown**, which is the
    one thing it must never silently render as "unchanged".

    ``provider`` scopes which engines are auditable, because a model catalog belongs to one
    surface: HeyGen publishes no ``models_explore`` at all, so its engine is reported as
    not-auditable rather than as missing.
    """
    # Lazy on purpose: render_engines re-exports this module's public names from its bottom
    # line, so importing it here at module top is a cycle that crashes whenever this sibling
    # is imported first (proved by a cold `import gtm_core.render_engines_audit`).
    from .render_engines import (
        EngineError,
        EngineUnavailable,
        _spec,
        load_registry,
        resolve_engine,
    )

    data = load_registry(registry_path)
    engines, roles = data["engines"], data["roles"]
    index = _live_index(live)

    if index is None:
        return [
            AuditFinding(
                "unknown",
                provider,
                "no usable model catalog was supplied, so every registry claim is UNKNOWN, not "
                "unchanged. Re-run the provider's models_explore and pipe its raw result in; an "
                "audit that cannot reach the provider must not read as an all-clear.",
            )
        ]

    findings: list[AuditFinding] = []

    for name, table in sorted(engines.items()):
        spec = _spec(name, table)
        if spec.provider != provider:
            findings.append(
                AuditFinding(
                    "not_auditable",
                    name,
                    f"provider {spec.provider!r} publishes no model catalog on this surface, so "
                    f"model {spec.model!r} cannot be diffed here. Its claims rest on the "
                    f"{spec.verified_on or 'undated'} probe recorded in the registry.",
                )
            )
            continue
        if spec.retired:
            continue  # never selectable; drift on a banned engine is noise, not news
        if spec.model not in index:
            findings.append(
                AuditFinding(
                    "pinned_model_missing",
                    name,
                    f"pinned model {spec.model!r} is not in the live catalog. Every render routed "
                    "through this engine will fail at the provider, not at a gate here.",
                )
            )

    for role, table in sorted(roles.items()):
        requires = dict(table.get("requires", {}) or {})
        try:
            resolve_engine(role, disclosed=True, registry_path=registry_path)
        except EngineUnavailable:
            pass  # unservable — the interesting case, handled below
        except EngineError as exc:
            findings.append(AuditFinding("role_broken", role, str(exc)))
            continue
        else:
            continue  # servable today; the audit has nothing to propose

        unestablishable = sorted(set(requires) - _CATALOG_ESTABLISHES)
        if unestablishable:
            findings.append(
                AuditFinding(
                    "unestablishable_requirement",
                    role,
                    f"no candidate can be proposed from the catalog: this role requires "
                    f"{unestablishable}, and a model catalog does not advertise "
                    f"{'them' if len(unestablishable) > 1 else 'it'}. These are architectural "
                    "properties established by rendering something and looking at it — a tag "
                    "reading 'identity' is reference-driven consistency across shots, not a "
                    "trained likeness, and nothing in a catalog speaks to lip sync at all. "
                    "Re-opening this role means running that probe again, not reading this list.",
                )
            )
            continue

        matches = sorted(
            model_id
            for model_id, item in index.items()
            if all(_catalog_capabilities(item).get(k) == v for k, v in requires.items())
        )
        if matches:
            findings.append(
                AuditFinding(
                    "candidate_for_unservable_role",
                    role,
                    f"role is unservable, but {len(matches)} live model(s) advertise everything it "
                    f"requires: {matches}. REPORTED ONLY — binding one is a human decision with "
                    "its own evaluation, and this tool does not write to the registry.",
                )
            )

    return findings


def main_audit(args, *, json_mod, stdin) -> int:
    """The ``--audit`` branch of :func:`gtm_core.render_engines.main`.

    Lives here rather than in the CLI so the reporting code and the reporting command travel
    together; ``render_engines.main`` imports it lazily, only when ``--audit`` is given.
    """
    # Lazy on purpose: render_engines re-exports this module's public names from its bottom
    # line, so importing it here at module top is a cycle that crashes whenever this sibling
    # is imported first (proved by a cold `import gtm_core.render_engines_audit`).
    from .render_engines import (
        VERIFICATION_STALE_DAYS,
        days_since_verification,
        engine_verification_dates,
    )

    raw = stdin.read() if not stdin.isatty() else ""
    try:
        live = json_mod.loads(raw) if raw.strip() else None
    except json_mod.JSONDecodeError:
        # Unparseable is a failed probe, not an empty provider. Fall through to UNKNOWN.
        live = None
    findings = audit_registry(live, registry_path=args.registry)
    dates = engine_verification_dates(args.registry)
    stale = {}
    for name, date in dates.items():
        age = days_since_verification(date)
        if age is None or age > VERIFICATION_STALE_DAYS:
            stale[name] = date
    print(
        json_mod.dumps(
            {
                "audited": live is not None,
                "verified_on": dates,
                "stale_past_days": VERIFICATION_STALE_DAYS,
                "stale": stale,
                "findings": [
                    {"kind": f.kind, "subject": f.subject, "detail": f.detail} for f in findings
                ],
            },
            indent=2,
        )
    )
    # Exit 0 whichever way it lands: an audit reports, it does not adjudicate. A non-zero exit
    # would turn a reporting tool into a gate, which is the one thing this is not.
    return 0
