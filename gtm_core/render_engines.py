"""Resolve a shot ROLE to the render engine allowed to serve it — fail-closed.

The single source of truth is :data:`REGISTRY_FILENAME` (``render_engines.toml``), in the same
shape :mod:`gtm_core.models` uses for LLM roles: non-secret config, an ``api_key_env`` naming an
env var rather than carrying a key.

Why a registry instead of a rule in a skill body
------------------------------------------------
Two finished videos were rejected in August 2026 for face distortion, absent lip sync, and a voice
that did not sound like the operator. A live ``models_explore`` query proved ``soul_id`` is
accepted only by *image* models — no video model on the provider's MCP accepts a trained Soul — so
identity cannot cross the image→video boundary, and general image-to-video models approximate
mouth motion from prose instead of generating it from audio.

Prose already forbade both mistakes (the ``sync_so`` prohibition and the storyboard gate) and both
were bypassed anyway. So the rule became a capability requirement checked before any spend:
:func:`resolve_engine` raises rather than returning an engine that cannot do the job.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

__all__ = [
    "EngineError",
    "EngineUnavailable",
    "EngineSpec",
    "AuditFinding",
    "resolve_engine",
    "engine_for_shot_role",
    "load_registry",
    "audit_registry",
    "engine_verification_dates",
    "oldest_verification",
    "VERIFICATION_STALE_DAYS",
    "days_since_verification",
]

REGISTRY_FILENAME = "render_engines.toml"
ENV_OVERRIDE = "GTM_RENDER_ENGINES_REGISTRY"

#: Shot-list ``role`` values (schemas/shots.schema.json) mapped to registry roles. A presenter
#: shot that actually SPEAKS is the regulated case; see :func:`engine_for_shot_role`.
_SHOT_ROLE_TO_REGISTRY_ROLE = {
    "presenter": "presenter",
    "broll": "broll",
    "screen": "broll",
}


class EngineError(ValueError):
    """The registry is unusable, or the request is malformed."""


class EngineUnavailable(EngineError):
    """No engine may serve this role right now.

    Distinct from :class:`EngineError` because it is a *reportable operating state*, not a bug:
    the ``presenter`` role ships deliberately unbound until a purpose-built avatar engine passes
    its evaluation. Callers should catch this and offer the lanes that do work, rather than
    treating it as a crash.
    """


@dataclass(frozen=True)
class EngineSpec:
    name: str
    provider: str
    model: str
    output: str
    identity_faithful: bool
    lip_sync: str
    api_key_env: str = ""
    notes: str = ""
    retired: bool = False
    available: bool = True
    requires_disclosure: bool = False
    #: When this engine's CAPABILITY CLAIMS were last probed against the live provider — not when
    #: the file was edited, and not when a model id was last seen in a catalog listing.
    verified_on: str = ""


def _registry_path(registry_path: Path | None = None) -> Path:
    if registry_path is not None:
        return registry_path
    override = os.getenv(ENV_OVERRIDE)
    if override:
        return Path(override)
    return Path(__file__).resolve().parent / REGISTRY_FILENAME


def load_registry(registry_path: Path | None = None) -> dict:
    path = _registry_path(registry_path)
    if not path.is_file():
        raise EngineError(f"render-engine registry not found: {path}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise EngineError(f"render-engine registry is unreadable: {path} ({exc})") from exc
    if not isinstance(data.get("engines"), dict) or not isinstance(data.get("roles"), dict):
        raise EngineError(f"registry must define [engines] and [roles]: {path}")
    return data


def _spec(name: str, table: dict) -> EngineSpec:
    # A registry that carries a secret is a registry someone will paste into a log.
    for key in table:
        if key.endswith("_key") or key in {"api_key", "token", "secret"}:
            raise EngineError(
                f"engine {name!r} carries a secret-shaped key {key!r}. The registry is non-secret "
                "config: store the env-var NAME in `api_key_env`, never the value."
            )
    missing = {"provider", "model", "output"} - set(table)
    if missing:
        raise EngineError(f"engine {name!r} is missing required field(s): {sorted(missing)}")
    return EngineSpec(
        name=name,
        provider=str(table["provider"]),
        model=str(table["model"]),
        output=str(table["output"]),
        identity_faithful=bool(table.get("identity_faithful", False)),
        lip_sync=str(table.get("lip_sync", "none")),
        api_key_env=str(table.get("api_key_env", "")),
        notes=str(table.get("notes", "")),
        retired=bool(table.get("retired", False)),
        available=bool(table.get("available", True)),
        requires_disclosure=bool(table.get("requires_disclosure", False)),
        verified_on=str(table.get("verified_on", "")),
    )


def resolve_engine(
    role: str, *, disclosed: bool = False, registry_path: Path | None = None
) -> EngineSpec:
    """Return the engine bound to ``role``, or raise.

    ``disclosed`` states that the render this engine would serve will carry the tenant's
    configured synthetic-media disclosure (EU AI Act Art. 50). It defaults to ``False`` so every
    caller that has not thought about disclosure gets the closed answer.

    Raises :class:`EngineUnavailable` when the role has no engine, when its engine is marked
    unavailable or retired, when the bound engine requires disclosure and the caller did not
    declare it, or when the bound engine fails the role's declared ``requires``.
    """
    data = load_registry(registry_path)
    roles, engines = data["roles"], data["engines"]

    if role not in roles:
        raise EngineError(f"unknown render role {role!r}; known roles: {sorted(roles)}")
    role_table = roles[role]
    engine_name = str(role_table.get("engine", "") or "")
    requires = role_table.get("requires", {}) or {}

    if not engine_name:
        raise EngineUnavailable(
            f"no engine is bound to the {role!r} role, so this shot cannot be rendered. An "
            "unbound role is a deliberate, reportable state, not a misconfiguration: no engine "
            "in the registry meets what this role requires. For a speaking presenter the "
            "two lanes that work today are: (1) REAL FOOTAGE, the primary lane, which also needs "
            "no synthetic-media disclosure; or (2) the FACELESS format — b-roll plus held soul_2 "
            "stills, voice-over and captions. Offer the operator one of those instead of failing "
            "later, after a script has already been written."
        )
    if engine_name not in engines:
        raise EngineError(
            f"role {role!r} names engine {engine_name!r}, which is not in the registry"
        )

    spec = _spec(engine_name, engines[engine_name])
    if spec.retired:
        raise EngineUnavailable(
            f"engine {engine_name!r} is retired and may not be selected. {spec.notes.strip()}"
        )
    if not spec.available:
        raise EngineUnavailable(
            f"engine {engine_name!r} is not yet available for the {role!r} role. "
            f"{spec.notes.strip()}"
        )
    if spec.requires_disclosure and not disclosed:
        raise EngineUnavailable(
            f"engine {engine_name!r} may only render DISCLOSED synthetic media, and this caller "
            f"did not declare disclosure for the {role!r} role. This is the W4 panel gate, kept "
            "where it actually bites: the pre-registered panel asks whether judges who know the "
            "operator can tell the avatar from real footage, so it governs the case where the "
            "viewer is NOT told. A render that carries the tenant's [disclosure].line is not "
            "making that claim and is not gated on it; an undisclosed one is, and stays shut "
            "until the panel in gtm_core/panel_eval.py returns PASS. Three ways forward: declare "
            "disclosure (--disclosed, or `synthetic_disclosure` on the shot list); shoot REAL "
            "FOOTAGE, which carries no disclosure duty at all; or use the FACELESS format — "
            "b-roll plus held soul_2 stills, voice-over and captions."
        )

    for key, wanted in requires.items():
        actual = getattr(spec, key, None)
        if actual != wanted:
            raise EngineUnavailable(
                f"engine {engine_name!r} cannot serve role {role!r}: {key}={actual!r}, "
                f"required {wanted!r}. This is the check that makes the August 2026 failure "
                "unrepresentable — a talking head rendered on a general image-to-video model "
                "drifts the face and cannot lip-sync, and no prompt fixes either."
            )
    return spec


def engine_for_shot_role(
    shot_role: str,
    *,
    speaks: bool,
    disclosed: bool = False,
    registry_path: Path | None = None,
) -> EngineSpec:
    """Resolve the engine for a shot-list ``role``, given whether the shot has spoken lines.

    A presenter shot that speaks is the regulated case: it needs a real identity AND native lip
    sync. A presenter shot with no spoken line is a held identity still, which the far cheaper
    ``identity_still`` role serves correctly.
    """
    key = _SHOT_ROLE_TO_REGISTRY_ROLE.get(shot_role)
    if key is None:
        raise EngineError(
            f"unknown shot role {shot_role!r}; known: {sorted(_SHOT_ROLE_TO_REGISTRY_ROLE)}"
        )
    if key == "presenter" and not speaks:
        key = "identity_still"
    return resolve_engine(key, disclosed=disclosed, registry_path=registry_path)


# ── Audit (C11) ────────────────────────────────────────────────────────────────────────────────

#: Past this many days a `verified_on` is *reported* as stale. It is never a gate: a stale probe
#: does not make a render wrong, it makes the claim behind the render unverified. 90 days matches
#: the PRD `review:` cadence.
VERIFICATION_STALE_DAYS = 90

#: The ONLY registry capability key a model catalog can establish on its own.
#:
#: This is the load-bearing constant of the whole audit, and the reason the `presenter` ban cannot
#: be relaxed by a tag match. `output` is a fact the catalog states outright (`output_type`).
#: `identity_faithful` and `lip_sync` are ARCHITECTURAL properties nobody advertises: the live
#: catalog carries video models tagged `identity`, meaning reference-driven consistency across
#: shots — not the ability to hold one real person's trained likeness, and emphatically not the
#: ability to generate a mouth from an audio track. Both of those were established by rendering
#: something and looking at it. A tag is not that, so the audit refuses to speak to them and says
#: so, rather than quietly finding nothing.
_CATALOG_ESTABLISHES = frozenset({"output"})


def days_since_verification(date_str: str, *, today: date | None = None) -> int | None:
    """Age of an ISO date in days, or ``None`` when there is no usable date.

    ``None`` is not zero. An engine with no ``verified_on`` is *more* suspect than an old one, not
    less, so callers must handle the missing case explicitly rather than defaulting it to fresh.
    """
    try:
        probed = date.fromisoformat(date_str.strip())
    except (AttributeError, ValueError):
        return None
    return ((today or date.today()) - probed).days


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


def _catalog_capabilities(item: dict) -> dict[str, object]:
    """What this catalog entry actually establishes — deliberately almost nothing."""
    return {"output": str(item.get("output_type", ""))}


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


def engine_verification_dates(registry_path: Path | None = None) -> dict[str, str]:
    """``{engine name: verified_on}`` for every engine that is selectable today."""
    data = load_registry(registry_path)
    return {
        name: str(table.get("verified_on", ""))
        for name, table in data["engines"].items()
        if not table.get("retired", False)
    }


def oldest_verification(role: str, registry_path: Path | None = None) -> str:
    """The oldest ``verified_on`` among the engines ``role`` could resolve to — ``""`` if unknown.

    Coarse on purpose: a role binds one engine, but a *lane* built on that role also leans on the
    registry as a whole having been looked at recently. What the caller does with the date is
    report it (C11c), never gate on it.
    """
    data = load_registry(registry_path)
    roles = data["roles"]
    if role not in roles:
        return ""
    engine_name = str(roles[role].get("engine", "") or "")
    table = data["engines"].get(engine_name)
    if not table:
        return ""
    return str(table.get("verified_on", ""))


def main(argv: list[str] | None = None) -> int:
    """`python -m gtm_core.render_engines --role presenter` — report the engine, or why there is none.

    Skills are markdown executed by the brain; they cannot import Python, and the least-privilege
    policy allows `python -m …` but not `python -c …`. So the registry needs a CLI to be reachable
    from a skill body at all.

    Exit 0 = an engine is available (printed as JSON). Exit 2 = no engine may serve this role,
    which is a REPORTABLE STATE, not a crash: the message names the lanes that do work.
    """
    import argparse
    import json

    parser = argparse.ArgumentParser(prog="uv run python -m gtm_core.render_engines")
    parser.add_argument("--role", help="presenter | broll | identity_still")
    parser.add_argument(
        "--audit",
        action="store_true",
        help=(
            "diff the registry against a live model catalog read on stdin (the raw models_explore "
            "result). Read-only: it never edits the registry. With no catalog it reports UNKNOWN."
        ),
    )
    parser.add_argument("--speaks", action="store_true", help="treat --role as a SPEAKING shot")
    parser.add_argument(
        "--disclosed",
        action="store_true",
        help="the render will carry the tenant's synthetic-media disclosure line (Art. 50)",
    )
    parser.add_argument("--registry", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.audit:
        import sys

        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        try:
            live = json.loads(raw) if raw.strip() else None
        except json.JSONDecodeError:
            # Unparseable is a failed probe, not an empty provider. Fall through to UNKNOWN.
            live = None
        findings = audit_registry(live, registry_path=args.registry)
        stale = {
            name: date
            for name, date in engine_verification_dates(args.registry).items()
            if days_since_verification(date) is None
            or days_since_verification(date) > VERIFICATION_STALE_DAYS
        }
        print(
            json.dumps(
                {
                    "audited": live is not None,
                    "verified_on": engine_verification_dates(args.registry),
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
        # would turn a reporting tool into a gate, which is the one thing C11 is not.
        return 0

    if not args.role:
        parser.error("--role is required unless --audit is given")

    try:
        if args.speaks:
            spec = engine_for_shot_role(
                args.role,
                speaks=True,
                disclosed=args.disclosed,
                registry_path=args.registry,
            )
        else:
            spec = resolve_engine(args.role, disclosed=args.disclosed, registry_path=args.registry)
    except EngineUnavailable as exc:
        print(json.dumps({"role": args.role, "available": False, "reason": str(exc)}, indent=2))
        return 2
    except EngineError as exc:
        print(json.dumps({"role": args.role, "error": str(exc)}, indent=2))
        return 1

    print(
        json.dumps(
            {
                "role": args.role,
                "available": True,
                "engine": spec.name,
                "provider": spec.provider,
                "model": spec.model,
                "identity_faithful": spec.identity_faithful,
                "lip_sync": spec.lip_sync,
                "requires_disclosure": spec.requires_disclosure,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
