"""Funnel sizing for the prospect skill — turn a DELIVERY target into a DISCOVERY target.

The gap this closes (2026-08-12): an operator asking for "500 accounts, end to end" means
500 *sequence-ready contacts*. The skill read it as 500 *discovered accounts*, ran the whole
funnel, and delivered 7 — because every stage has a yield and nothing multiplied them out
beforehand. The narrowing was invisible until the end.

Two jobs:

* :func:`size` — given a delivery target and the profile's measured yields, return the
  discovery target, the per-stage expected counts, and the metered units required. Fail
  closed when the available net-new pool or the remaining budget cannot support it.
* :func:`check_stage` — a per-stage tripwire. Compare a stage's *actual* yield to the
  modelled one; a stage running materially cold stops the run instead of quietly
  shrinking the delivery.

Yields live in ``profiles/<profile>/knowledge/funnel-yields.toml`` so they are tenant
facts, not code, and :func:`record_actuals` writes measured values back after a run so the
model self-corrects instead of drifting.

Stdlib only. No I/O beyond reading/writing the profile's yields file.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Ordered funnel. Each stage consumes the prior stage's output.
#
# Stages 1-5 are DISCOVERY: how many rows the research half produces. Stages 6-11 are the
# REFUSAL gates that decide how many of those may actually be enrolled — every one of them
# already runs today, at the end, one at a time, after the spend. Modelling only the first
# five is what made the narrowing arrive as six separate late surprises: *"blocks occur at
# the end and I keep fighting this tool to unlock more emails."* None of those gates is
# wrong to refuse and none is relaxed here; the model simply multiplies them out beforehand
# so the operator reads one table before the spend instead of six refusals after it.
STAGES: tuple[str, ...] = (
    # --- discovery ---
    "scored",  # discovered rows that survive ingest + off-ICP screening
    "qualified",  # survive the profile's gate at the requested tier
    "seat_found",  # a real buyer seat exists at the account
    "contact_usable",  # seat resolves to a deliverable, grade-gated email
    "why_now",  # carries a usable why-now of the requested KIND
    # --- the refusal gates, in the order a real run meets them ---
    "compliant",  # out-of-market, no postal address, no opt-out  (email_compliance preflight)
    "unsuppressed",  # prior contact, DNC, unsubscribed           (suppression verify)
    "list_fit",  # role fit, signal grade, source hit rate        (list_fit)
    "integrity",  # missing record, stale dossier, competitor     (account_integrity)
    "lane_enrollable",  # routed to hold rather than an enrollable lane (lanes route)
    "cell_assignable",  # no matrix cell for this row's persona x segment (hook_coverage)
)

#: The six gates added 2026-09-23. Named as a set because their defining property is an
#: ABSENCE — see :data:`DEFAULT_YIELDS`.
LATE_GATES: frozenset[str] = frozenset(STAGES[5:])

# Fallback yields, measured on a live 2026-08-11/12 run. A profile's own
# funnel-yields.toml overrides these; these exist so a first run is not blocked.
#
# ⚠️ THE SIX LATE GATES DELIBERATELY HAVE NO ENTRY HERE, AND THAT ABSENCE IS LOAD-BEARING.
#
# `load_yields` merges this dict per key, so an entry of 1.00 for an unmeasured gate does
# not mean "unknown" — it means **forecast zero loss at that gate**, on every profile that
# has never measured it, while looking as authoritative as a measured number. That is the
# exact surprise this extension exists to remove, reproduced by the extension. An unmeasured
# stage is a stated unknown; a stage defaulted to 1.0 is a lie with a number on it.
#
# So a stage arrives here only once a real run has recorded it through `record_actuals`,
# and until then `size()` renders it `unmeasured` and leaves it OUT of the product. Adding
# `"integrity": 1.00` here to quiet the renderer is the one change to this file that would
# undo the phase; `test_the_late_gates_have_no_default_yield` fails if anyone does.
DEFAULT_YIELDS: dict[str, float] = {
    "scored": 1.00,
    "qualified": 1.00,  # tier="any" — see tier_yield()
    "seat_found": 0.82,
    "contact_usable": 0.68,
    "why_now": 1.00,  # structural mode — see WHY_NOW_YIELDS
}

# The dominant lever: demanding a dated public news event costs ~3x the discovery of a
# structural clause. 08-11 measured 8/25 verified, and only 2/286 signals still fresh later.
WHY_NOW_YIELDS: dict[str, float] = {
    "structural": 1.00,
    "hybrid": 0.60,
    "news": 0.32,
}

# Fraction of PUBLISHED accounts that clear the requested bar.
#
# Tier A and Tier B are the same rubric at different thresholds (icp-personas.md §Gates:
# startup publish >=6 / Tier-A >=7; enterprise publish >=5 / Tier-A >=6). They are NOT
# different treatment classes — copy policy, CTA style and compliance are identical, and
# no tier ever carries a meeting ask (docs/email-optimization.md: interest CTA only).
# The only treatment difference is depth: Tier-A earns a hand-written 1:1 pack, Tier-B
# goes in the merge sequence. So "a+b" is the normal cut for a volume send.
#
# Measured on the 2026-08-11 run: 84 Tier-A of 500 published = 16%.
TIER_YIELDS: dict[str, float] = {"a+b": 1.00, "a": 0.16}

MIN_STAGE_RATIO = 0.70  # a stage below 70% of modelled trips the wire


class FunnelInfeasible(RuntimeError):
    """The requested delivery target cannot be met from the available pool or budget."""


@dataclass
class Plan:
    target_delivered: int
    tier: str
    why_now_mode: str
    yields: dict[str, float]
    discovery_needed: int
    stage_counts: dict[str, int]
    lookups_needed: int
    pool_available: int
    backlog_ready: int
    warnings: list[str] = field(default_factory=list)
    #: Stages this profile has never measured, in pipeline order. Their yield is absent from
    #: ``yields`` and their survivor count is absent from ``stage_counts`` — the renderer
    #: prints them as ``unmeasured`` rather than inventing either. A forecast that hides its
    #: own uncertainty is how an operator learns to distrust the forecast.
    unmeasured: tuple[str, ...] = ()

    @property
    def is_floor(self) -> bool:
        """Whether ``discovery_needed`` is a FLOOR rather than an estimate.

        True whenever any stage is unmeasured: the product skips that stage, so the number
        is what the measured stages alone require. It can only go up once the gate is
        measured, never down.
        """
        return bool(self.unmeasured)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_delivered": self.target_delivered,
            "tier": self.tier,
            "why_now_mode": self.why_now_mode,
            "yields": self.yields,
            "discovery_needed": self.discovery_needed,
            # Every stage, always, with `None` where there is no measurement — so a consumer
            # indexing by stage name cannot KeyError on the six gates added 2026-09-23, and
            # cannot mistake an absent measurement for a zero.
            "stage_counts": {s: self.stage_counts.get(s) for s in STAGES},
            "unmeasured": list(self.unmeasured),
            "discovery_is_floor": self.is_floor,
            "lookups_needed": self.lookups_needed,
            "pool_available": self.pool_available,
            "backlog_ready": self.backlog_ready,
            "warnings": self.warnings,
        }


def valid_yield(val: Any) -> bool:
    """Is ``val`` an admissible yield — a real number in ``(0, 1]``?

    **One rule, three readers.** Until 2026-09-24 the three places that read this file each
    had their own idea of what a yield is, and they disagreed in both directions:
    :func:`load_yields` range-checked but accepted a TOML ``true`` (``float(True)`` is
    ``1.0``, which passes ``0 < v <= 1``) and served it as a 100% yield;
    :func:`_read_yields_file` excluded bools but accepted any magnitude, so an out-of-range
    value survived a merge, was written back, and was then silently dropped by
    ``load_yields`` — the file said one thing and the model used another, with nothing
    reporting the gap; and :func:`record_actuals` validated stage *names* against a closed
    set while validating no value at all, so ``{"qualified": True}`` landed on disk as a
    convincing ``1.0000``.

    A bool is rejected rather than coerced because ``qualified = true`` is not a
    measurement — it is a typo for a number, and 1.0 is the most dangerous value it could
    silently become: the one that says "this gate refuses nobody".
    """
    return isinstance(val, (int, float)) and not isinstance(val, bool) and 0 < float(val) <= 1


def load_yields(profile_root: Path | None) -> dict[str, float]:
    """Profile-measured yields, falling back to the module defaults per key."""
    merged = dict(DEFAULT_YIELDS)
    if profile_root is None:
        return merged
    path = Path(profile_root) / "knowledge" / "funnel-yields.toml"
    if not path.is_file():
        return merged
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    for key in STAGES:
        val = data.get("yields", {}).get(key)
        if valid_yield(val):
            merged[key] = float(val)
    return merged


def size(
    target_delivered: int,
    *,
    tier: str = "a+b",
    why_now_mode: str = "structural",
    profile_root: Path | None = None,
    pool_available: int = 0,
    backlog_ready: int = 0,
    lookup_credits_remaining: int | None = None,
    no_fallback: bool = False,
) -> Plan:
    """Size the top of the funnel for a DELIVERY target.

    ``pool_available`` is net-new discoverable accounts (after exclusions);
    ``backlog_ready`` is already-qualified accounts that only need a contact — always the
    cheapest source, so it is subtracted from the discovery requirement first.

    Raises :class:`FunnelInfeasible` when the pool or the metered budget cannot cover it.
    """
    if target_delivered <= 0:
        raise ValueError("target_delivered must be positive")
    if why_now_mode not in WHY_NOW_YIELDS:
        raise ValueError(f"why_now_mode must be one of {sorted(WHY_NOW_YIELDS)}")
    if tier not in TIER_YIELDS:
        raise ValueError(f"tier must be one of {sorted(TIER_YIELDS)}")

    y = load_yields(profile_root)
    y = dict(y)
    y["qualified"] = TIER_YIELDS[tier]
    y["why_now"] = WHY_NOW_YIELDS[why_now_mode]

    warnings: list[str] = []

    # Backlog first: an already-qualified account needs only seat+contact+why_now.
    backlog_rate = y["seat_found"] * y["contact_usable"] * y["why_now"]
    # Round the backlog draw UP: flooring here left a 1-row remainder that forced a
    # pointless discovery pass even when the backlog could cover the whole target.
    need_from_backlog = -(-target_delivered // backlog_rate) if backlog_rate else 0
    from_backlog = min(backlog_ready, int(need_from_backlog)) if backlog_rate else 0
    delivered_from_backlog = min(target_delivered, int(from_backlog * backlog_rate + 0.5))
    remaining = max(0, target_delivered - delivered_from_backlog)
    if from_backlog:
        warnings.append(
            f"{from_backlog} backlog accounts cover ~{delivered_from_backlog} of the target "
            f"at {backlog_rate:.0%} — cheaper than discovery; work these before discovering"
        )

    # Unmeasured stages are skipped rather than assumed. They are NOT multiplied in as 1.0:
    # that would forecast zero loss at a real gate, which is the surprise this model exists
    # to remove. The consequence is stated rather than hidden — `discovery_needed` becomes a
    # floor, and the plan says so.
    unmeasured = tuple(stage for stage in STAGES if stage not in y)
    overall = 1.0
    for stage in STAGES:
        if stage in y:
            overall *= y[stage]
    if overall <= 0:
        raise FunnelInfeasible("modelled yield is zero; refusing to size a run")

    discovery_needed = 0 if remaining == 0 else int(-(-remaining // overall))

    # The survivor chain stops at the first unmeasured gate, because every count after it
    # would be the count you get by pretending that gate refuses nobody. The YIELDS of later
    # measured gates still render; only their absolute survivor counts are withheld.
    counts: dict[str, int] = {}
    n = float(discovery_needed)
    for stage in STAGES:
        if stage not in y:
            break
        n *= y[stage]
        counts[stage] = int(n)

    if unmeasured:
        warnings.append(
            f"{len(unmeasured)} gate(s) have never been measured on this profile "
            f"({', '.join(unmeasured)}) — they are NOT modelled, so ~{discovery_needed:,} is "
            f"a floor and the real requirement is higher. Run `record_actuals` at the end of "
            f"this run to measure them; until then the table marks them unmeasured rather "
            f"than forecasting that they refuse nobody."
        )

    # One metered lookup per account that has a seat, across both sources.
    lookups_needed = int(discovery_needed * y["scored"] * y["qualified"] * y["seat_found"]) + int(
        from_backlog * y["seat_found"]
    )

    if discovery_needed > pool_available:
        raise FunnelInfeasible(
            f"need to discover ~{discovery_needed:,} accounts to deliver {target_delivered:,} "
            f"at tier={tier}/{why_now_mode} (overall yield {overall:.1%}), but only "
            f"{pool_available:,} net-new accounts are available. Lower the target, relax the "
            f"tier, switch why_now_mode to 'structural', or widen discovery."
        )
    if lookup_credits_remaining is not None and lookups_needed > lookup_credits_remaining:
        msg = f"need ~{lookups_needed:,} contact lookups but only {lookup_credits_remaining:,} credits remain"
        if no_fallback:
            raise FunnelInfeasible(msg + " (--no-fallback is active)")
        else:
            warnings.append(
                f"DEGRADATION WARNING: {msg}. The pipeline will fall back to unverified web sources when credits exhaust."
            )

    return Plan(
        target_delivered=target_delivered,
        tier=tier,
        why_now_mode=why_now_mode,
        yields=y,
        discovery_needed=discovery_needed,
        stage_counts=counts,
        lookups_needed=lookups_needed,
        pool_available=pool_available,
        backlog_ready=backlog_ready,
        warnings=warnings,
        unmeasured=unmeasured,
    )


def check_stage(stage: str, actual_in: int, actual_out: int, plan: Plan) -> dict[str, Any]:
    """Per-stage tripwire. A stage running materially cold must stop the run.

    Returns a verdict dict; ``ok=False`` means STOP and report, rather than carry a
    silently smaller set forward and surprise the operator at the end.
    """
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}")
    actual = (actual_out / actual_in) if actual_in else 0.0
    if stage not in plan.yields:
        # Nothing to trip against: this run IS the measurement. Reporting `ok=False` here
        # would stop a run on the grounds that we had never measured it before, which is a
        # refusal the forecast is explicitly not allowed to make (it predicts; the gates
        # decide). Reporting `ok=True` with a modelled rate would be worse — it would invent
        # the number the whole phase exists to stop inventing.
        return {
            "stage": stage,
            "modelled": None,
            "actual": round(actual, 4),
            "ratio": None,
            "ok": True,
            "projected_delivery": plan.target_delivered,
            "message": (
                f"{stage}: {actual:.0%} actual, unmeasured before this run — nothing to "
                f"compare it to. Pass it to record_actuals so the next plan can model it."
            ),
        }
    modelled = plan.yields[stage]
    ratio = (actual / modelled) if modelled else 0.0
    ok = ratio >= MIN_STAGE_RATIO
    projected = plan.target_delivered
    if modelled:
        projected = int(plan.target_delivered * min(1.0, ratio))
    return {
        "stage": stage,
        "modelled": round(modelled, 4),
        "actual": round(actual, 4),
        "ratio": round(ratio, 3),
        "ok": ok,
        "projected_delivery": projected,
        "message": (
            f"{stage}: {actual:.0%} actual vs {modelled:.0%} modelled"
            + (
                ""
                if ok
                else f" — STOP. At this rate the run delivers ~{projected} of "
                f"{plan.target_delivered}. Re-size or widen before continuing."
            )
        ),
    }


_YIELDS_TABLE = "[yields]"

#: The preamble a brand-new yields file is seeded with. An existing file's own preamble is
#: never replaced by this — see :func:`_read_yields_file`.
_SEEDED_PREAMBLE: tuple[str, ...] = (
    "# Measured funnel yields for this profile. Written by gtm_core.funnel.record_actuals",
    "# after a run; hand-edit only to seed a new profile. Used by the prospect skill to",
    "# turn a DELIVERY target into a DISCOVERY target (Step 2 funnel sizing).",
    "",
)


def _read_yields_file(path: Path) -> tuple[list[str], dict[str, float], list[str]]:
    """``(preamble, current values, trailing)`` for an existing yields file.

    ``preamble`` is every line above the ``[yields]`` table, verbatim. That is where the
    tenant's *derivation* lives — an 18-line block recording which run each seed value was
    measured on and how it was blended — and it is what a full rewrite silently deletes.
    ``trailing`` is every line from the next table header onward, so a file that grows a
    second section keeps it rather than losing it to the same rewrite.
    """
    if not path.is_file():
        return list(_SEEDED_PREAMBLE), {}, []
    raw = path.read_text(encoding="utf-8")
    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        # Refuse, never skip. Parsing failure used to be invisible here because nothing
        # parsed: the old writer overwrote whatever was on disk, so a corrupt file was
        # destroyed by the next run rather than reported.
        raise ValueError(f"{path} is not readable TOML, refusing to overwrite it: {exc}") from exc
    lines = raw.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip() == _YIELDS_TABLE), None)
    if start is None:
        # A file with no table at all is all preamble; the table goes below it.
        return lines, {}, []
    end = len(lines)
    for i in range(start + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            end = i
            break
    current = {
        key: float(val) for key, val in (data.get("yields") or {}).items() if valid_yield(val)
    }
    return lines[:start], current, lines[end:]


def record_actuals(profile_root: Path, measured: dict[str, float]) -> Path:
    """Merge measured yields into the profile's file so the model self-corrects run over run.

    **Merge, not rewrite.** Until 2026-09-23 this rebuilt the file from a 5-line header and
    only the stages present in ``measured``, which had two consequences that were live and
    invisible: a tenant's 18-line provenance block was destroyed on the first call, and a
    partial ``measured`` dict dropped every omitted stage — so the next :func:`load_yields`
    silently fell back to :data:`DEFAULT_YIELDS` for them. Both matter more now that
    :data:`STAGES` carries gates nobody has measured yet: a stage recorded once and then
    omitted from a later partial call would revert, and the reversion would look like a
    measurement.

    An unknown stage name raises rather than being dropped, matching :func:`check_stage` —
    the stage set is closed, and a typo that silently writes nothing is how a run reports a
    yield it never recorded. **An inadmissible VALUE raises for the same reason** (2026-09-24):
    this function validated names and not numbers, so ``{"qualified": True}`` was coerced by
    ``float()`` into a convincing ``1.0000`` on disk, and an out-of-range float was written
    and then silently ignored by :func:`load_yields`. A yield that cannot be read back is not
    a yield; see :func:`valid_yield`.
    """
    unknown = sorted(set(measured) - set(STAGES))
    if unknown:
        raise ValueError(f"unknown stage(s) {unknown}; STAGES are {list(STAGES)}")

    bad = sorted(key for key, val in measured.items() if not valid_yield(val))
    if bad:
        raise ValueError(
            f"inadmissible yield(s) {bad}: a yield is a real number in (0, 1] — "
            "a bool is rejected rather than coerced"
        )

    path = Path(profile_root) / "knowledge" / "funnel-yields.toml"
    preamble, current, trailing = _read_yields_file(path)
    merged = {**current, **{key: float(val) for key, val in measured.items()}}

    body = [_YIELDS_TABLE]
    body += [f"{stage} = {merged[stage]:.4f}" for stage in STAGES if stage in merged]
    # A key the file carries that STAGES no longer names is a retired stage, not garbage.
    # Keep it: deleting tenant data to tidy a table is the failure this function just fixed.
    body += [f"{key} = {merged[key]:.4f}" for key in sorted(set(merged) - set(STAGES))]

    path.write_text("\n".join([*preamble, *body, *trailing]).rstrip("\n") + "\n", encoding="utf-8")
    return path


def render_stage_table(plan: Plan) -> list[str]:
    """The stage table, **ordered by leak size** — biggest loss first, not pipeline order.

    The operator's question is "where did my 500 go?", and the answer is the biggest number.
    A table read in pipeline order buries it behind four stages that lose nothing: on the
    default card, `scored`, `qualified` and `why_now` are all 1.00 and the line that matters
    is fourth. Pipeline position is still printed, so the order is legible as a re-sort of a
    known sequence rather than an arbitrary list.

    Unmeasured stages sort last and carry no number at all. Giving them a placeholder rate
    would put them back in the reader's arithmetic, which is what an entry of 1.00 in
    :data:`DEFAULT_YIELDS` would have done silently.
    """
    position = {stage: i for i, stage in enumerate(STAGES, 1)}
    measured = [s for s in STAGES if s in plan.yields]

    # Loss is per stage, in rows: what this gate removes from what reached it. Sorting on
    # the RATE would rank a 90%-refusing gate that 10 rows reach above a 30%-refusing gate
    # that 500 reach, which is the wrong end of the operator's question.
    def loss(stage: str) -> int:
        n_out = plan.stage_counts.get(stage)
        if n_out is None:
            return -1
        idx = STAGES.index(stage)
        prior = STAGES[idx - 1] if idx else None
        n_in = (
            plan.stage_counts.get(prior, plan.discovery_needed) if prior else plan.discovery_needed
        )
        return max(0, n_in - n_out)

    # Three bands, in this order. Within the first, biggest loss first.
    counted = [s for s in measured if s in plan.stage_counts]
    downstream = [s for s in measured if s not in plan.stage_counts]

    lines = []
    for stage in sorted(counted, key=lambda s: (-loss(s), position[s])):
        drop = loss(stage)
        tail = f"  (-{drop:,})" if drop > 0 else ""
        lines.append(
            f"  {position[stage]:>2}. {stage:16s} x{plan.yields[stage]:.2f}  -> "
            f"{plan.stage_counts[stage]:,}{tail}"
        )
    # Measured, but sitting behind an unmeasured gate: the RATE is known and the absolute
    # survivor count is not, because computing it would assume the gate in front of it
    # refuses nobody. Both facts are stated rather than one of them quietly chosen.
    for stage in downstream:
        lines.append(
            f"  {position[stage]:>2}. {stage:16s} x{plan.yields[stage]:.2f}  -> count "
            f"unknown (an unmeasured gate sits in front of it)"
        )
    for stage in plan.unmeasured:
        lines.append(
            f"  {position[stage]:>2}. {stage:16s} unmeasured  -> not modelled; this gate "
            f"refuses rows and we do not yet know how many"
        )
    return lines


def _cli(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m gtm_core.funnel")
    ap.add_argument("--target", type=int, required=True, help="DELIVERED contacts wanted")
    ap.add_argument("--tier", default="a+b", choices=sorted(TIER_YIELDS))
    ap.add_argument("--why-now-mode", default="structural", choices=sorted(WHY_NOW_YIELDS))
    ap.add_argument("--profile-root", type=Path, default=None)
    ap.add_argument("--pool-available", type=int, default=0)
    ap.add_argument("--backlog-ready", type=int, default=0)
    ap.add_argument("--lookup-credits", type=int, default=None)
    ap.add_argument(
        "--no-fallback", action="store_true", help="Fail if credits exhaust instead of warning"
    )
    args = ap.parse_args(argv)

    try:
        plan = size(
            args.target,
            tier=args.tier,
            why_now_mode=args.why_now_mode,
            profile_root=args.profile_root,
            pool_available=args.pool_available,
            backlog_ready=args.backlog_ready,
            lookup_credits_remaining=args.lookup_credits,
            no_fallback=args.no_fallback,
        )
    except FunnelInfeasible as exc:
        print("FUNNEL INFEASIBLE\n  " + str(exc))
        return 2

    print("FUNNEL PLAN")
    print("=" * 62)
    print(
        f"  deliver            {plan.target_delivered:,} contacts (tier={plan.tier}, {plan.why_now_mode})"
    )
    print(f"  discover           {plan.discovery_needed:,} accounts")
    print(f"  contact lookups    ~{plan.lookups_needed:,}")
    print("-" * 62)
    for line in render_stage_table(plan):
        print(line)
    print("=" * 62)
    for w in plan.warnings:
        print("  [NOTE] " + w)
    print(json.dumps(plan.to_dict(), indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
