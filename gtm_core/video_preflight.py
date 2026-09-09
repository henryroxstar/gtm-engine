"""Resolve which creator-pack video lanes a profile can actually run, before any creative work.

Why this module exists
----------------------
On 2026-08-28 a video concept was written, revised twice, and routed — and only then dead-ended,
because the lane it needed did not exist and the lane it fell back to required four identity
handles the profile did not have. Every one of those facts was a free read available before a
single creative word was written. The ordering was the defect, not the concept.

`video-router` already runs a preflight, but it audits a FIXED ``soul_id``/``voice_id`` pair. That
is correct for the Higgsfield-era lanes and wrong for ``presenter-video``, which renders on HeyGen
and needs ``heygen_avatar_id`` / ``heygen_voice_grade`` instead. So Step 0 passes clean on a
profile that cannot render the lane it is about to route to. The fix is to key the requirement to
the LANE rather than to a fixed pair, which is what :data:`LANE_REQUIREMENTS` does.

What this is not
----------------
It spends nothing and writes nothing. Every field is a deterministic read of the brand kit, the
pack directory, the tenant's ``packs.toml`` and the render-engine registry. Provider liveness
(is this ``heygen_avatar_id`` still trained?) is deliberately NOT checked here: that needs a
network call, and `identity-kit` owns it. Presence is what is knowable for free, and presence is
what fails first.

Design: the 2026-08-28 video-brief-template note.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import captions, render_engines, shots_lint
from .brandkit import load_brand_kit, lookup
from .paths import resolve_profiles_root

__all__ = [
    "LANE_REQUIREMENTS",
    "LOOK_KEY_BY_ORIENTATION",
    "LaneRequirement",
    "LaneStatus",
    "LookProposal",
    "Constraints",
    "Preflight",
    "lane_status",
    "look_key",
    "look_proposals",
    "preflight",
    "resolve_look",
]

#: Image suffixes worth surfacing as candidate ``screen``/``broll`` shots.
_ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".mp4", ".mov"}

#: Directories under a profile whose images are identity TRAINING material, not shot candidates.
#: Offering a Soul training photo as b-roll is a category error, so they are excluded by name.
_ASSET_EXCLUDE_PARTS = {"soul-training", "fonts", "logos"}

#: Cap on surfaced assets. The point is "you already own these", not an exhaustive inventory.
_ASSET_LIMIT = 20

#: The HeyGen rate — per SECOND of delivered footage, varying by engine — and the estimate
#: basis live in their own module, because seven files had restated a rate and several had it
#: wrong. Import; never re-derive. Re-exported here so `vp.HEYGEN_CREDITS_PER_MINUTE` and
#: `vp.ESTIMATE_BASIS_MINUTES` keep resolving for existing callers and tests.
from .heygen_cost import (  # noqa: E402
    ESTIMATE_BASIS_MINUTES,
    HEYGEN_CREDITS_PER_MINUTE,
    HEYGEN_CREDITS_PER_MINUTE_MIN,
)

#: ── Reap plan facts ───────────────────────────────────────────────────────────────────────────
#: Read live from `get_plan_usage` on this date. These are PLAN-TIER properties: they move when
#: the subscription tier moves, not on every call, which is what makes them safe to hold as
#: constants at all. A credit BALANCE is deliberately NOT here — that changes on every job, the
#: live read is free, and a stale balance stated as fact is the drift class C3 exists to stop.
#: Re-read live before any large job.
REAP_PLAN_VERIFIED_ON = "2026-08-29"

#: Rate card, published on the `get_plan_usage` tool itself, per BILLED MINUTE of source.
REAP_CLIPPING_CREDITS_PER_MINUTE = 1  # clipping, captions, transcription
REAP_REFRAME_CREDITS_PER_MINUTE = 2  # reframe / editor
REAP_DUBBING_AI_CREDITS_PER_MINUTE = 0.5  # dubbing, against the separate AI-credit pool

#: How many Reap projects may run at once. A fan-out wider than this QUEUES rather than failing,
#: which is the correct behaviour to plan for — erroring is not.
REAP_MAX_CONCURRENT_PROJECTS = 3

#: How long Reap keeps a project's footage. The live hazard for any run parked at a capture gate:
#: resume past this window and the footage the run was waiting for has been purged.
REAP_PROJECT_RETENTION_DAYS = 60

#: Requests per minute per API key, from Reap's published REST docs — a second fan-out bound on
#: top of the concurrency ceiling above.
REAP_REQUESTS_PER_MINUTE = 10


@dataclass(frozen=True)
class LaneRequirement:
    """What one creator-pack variant needs before it can run.

    ``handles`` are brand-kit dotted keys that must be present AND non-empty. ``needs_footage``
    marks a lane that starts from material the operator already has — the one input this module
    genuinely cannot determine, because nothing on disk proves whether someone has filmed
    anything. It is reported as an operator question, never guessed.
    """

    variant: str
    handles: tuple[str, ...] = ()
    needs_footage: bool = False
    presenter_role: str | None = None
    disclosed: bool = False
    summary: str = ""
    unlock: str = ""
    #: True when this lane's spoken audio is SYNTHESISED from the kit's ``identity.voice_id``.
    #: False for the footage lanes, which bring their own audio, and for ``presenter-video``,
    #: whose speech is rendered by HeyGen off ``identity.heygen_voice_id`` — a different handle
    #: and a different provider. Keying the caveat to this flag is what stops the preflight
    #: warning about a missing voice on a lane that was never going to use it.
    vo_from_kit: bool = False
    #: Credits per finished minute on this lane's dominant paid surface, where a rate has been
    #: MEASURED or published. ``None`` means no rate is derivable without a provider call, and
    #: the lane reports no estimate at all — a wrong number is worse than none. Both providers
    #: that fill this scale with duration: Reap bills a published per-billed-minute rate, and
    #: HeyGen bills per second (``gtm_core.heygen_cost``, ×60 here), so ``value * minutes`` is
    #: the right estimate on every lane.
    credits_per_minute: int | None = None
    #: The CHEAP end of the rate band, where a lane's cost is known to move with parameters the
    #: router cannot see (HeyGen's engine and resolution). ``None`` means the rate is a single
    #: figure, not a band — the Reap lanes bill a published per-minute rate that does not vary.
    credits_per_minute_min: int | None = None


#: The lane → requirement matrix. Verified against ``packs/creator/graphs/*.toml`` and
#: ``gtm_core/render_engines.toml`` on 2026-08-28.
#:
#: ``short-form-video`` requires NO identity handles: its shots resolve through the ``broll`` role,
#: which binds ``higgsfield_i2v`` with ``requires = {output = "video"}`` and no identity
#: constraint at all. ``video-storyboard`` generates the stills and ``video-render`` animates
#: them, so the lane is fully self-supplying. That makes it the correct cold-start default — a new
#: profile has no footage and no handles by definition, which is day one for every tenant rather
#: than an edge case.
LANE_REQUIREMENTS: dict[str, LaneRequirement] = {
    "short-form-video": LaneRequirement(
        variant="short-form-video",
        handles=(),
        needs_footage=False,
        presenter_role="broll",
        summary="Faceless short — generated b-roll, your existing screenshots, burned captions.",
        unlock="",
        vo_from_kit=True,
    ),
    "presenter-video": LaneRequirement(
        variant="presenter-video",
        handles=("identity.heygen_avatar_id", "identity.heygen_voice_grade", "disclosure.line"),
        needs_footage=False,
        presenter_role="presenter",
        disclosed=True,
        summary="You on camera as a disclosed synthetic presenter, cut against b-roll.",
        unlock="Run identity-kit to create the HeyGen digital twin and a professional voice clone.",
        credits_per_minute=HEYGEN_CREDITS_PER_MINUTE,
        credits_per_minute_min=HEYGEN_CREDITS_PER_MINUTE_MIN,
    ),
    "live-action-video": LaneRequirement(
        variant="live-action-video",
        handles=(),
        needs_footage=False,
        summary="Write the script, then shoot it yourself on a real camera — Reap clips it.",
        unlock="",
        # NOT `needs_footage`. `repurpose-clips` needs footage UP FRONT; this lane PRODUCES the
        # footage as a step — the run parks at a capture gate until the operator has filmed. That
        # is what makes it ready on a bare profile and a legitimate cold-start answer rather than
        # an advanced one.
        #
        # No handles at all: nothing is synthesised, so no trained identity is involved and no
        # Art. 50 disclosure duty attaches. This is the documented PREFERRED lane for anything
        # showing a face, and until 2026-08-29 it was the only one you could not start from
        # scratch.
        #
        # Reap does the clipping, so the published rate card applies.
        credits_per_minute=REAP_CLIPPING_CREDITS_PER_MINUTE,
    ),
    "repurpose-clips": LaneRequirement(
        variant="repurpose-clips",
        handles=(),
        needs_footage=True,
        summary="Clip long-form footage you already recorded into shorts.",
        credits_per_minute=REAP_CLIPPING_CREDITS_PER_MINUTE,
        unlock="Record or supply the long-form footage first.",
    ),
    "demo-clips": LaneRequirement(
        variant="demo-clips",
        handles=(),
        needs_footage=True,
        summary="Turn a screen recording into clips.",
        credits_per_minute=REAP_CLIPPING_CREDITS_PER_MINUTE,
        unlock="Capture the screen recording first.",
    ),
    "restyle-shorts": LaneRequirement(
        variant="restyle-shorts",
        handles=("identity.restyle_preset_id",),
        needs_footage=True,
        summary="Restyle footage you already have to match the brand.",
        unlock="Supply the footage and set identity.restyle_preset_id via identity-kit.",
    ),
}


@dataclass(frozen=True)
class LaneStatus:
    """One lane's feasibility. ``ready`` means nothing blocks it except operator input."""

    variant: str
    exists: bool
    pack_active: bool
    missing_handles: tuple[str, ...]
    needs_footage: bool
    engine: str | None
    engine_error: str | None
    summary: str
    unlock: str
    #: Things this lane WILL and WILL NOT do, disclosed at routing time. A caveat is NOT a
    #: blocker: a lane that can render but cannot speak still ships a real deliverable (captions
    #: and music), so downgrading it to blocked would refuse work the operator legitimately
    #: wants. What it must not do is stay silent — the failure that motivated this field renders
    #: every shot and surfaces at `video_lint` V10 after the whole spend.
    caveats: tuple[str, ...] = ()
    #: Order-of-magnitude credits for one nominal minute on this lane, or ``None`` where no rate
    #: is derivable for free. EXPLICITLY NOT A QUOTE: the real figure depends on shot count,
    #: retries and ratio, none of which exist at routing time.
    estimated_credits: int | None = None
    #: The low end of the estimate when this lane's cost is a BAND rather than a figure. ``None``
    #: means there is no band and ``estimated_credits`` stands alone. Reporting a band as a single
    #: number is the failure C5 exists to prevent — it reads as a quote.
    estimated_credits_min: int | None = None
    #: When the CAPABILITY CLAIMS of this lane's render engine were last probed against the live
    #: provider (``render_engines.toml`` ``verified_on``). ``""`` for a lane that resolves no
    #: engine — the footage lanes have no engine claim to date, and inventing one for them would
    #: make the field meaningless where it matters.
    engine_verified_on: str = ""

    @property
    def ready(self) -> bool:
        """True when the lane can run today, given footage the operator may already hold.

        ``needs_footage`` does NOT clear readiness — it is an operator question, not a blocker
        this module can settle. A lane needing footage is reported ready-if-you-have-it, and the
        router asks. Everything else here is a hard precondition read off disk.
        """
        return (
            self.exists
            and self.pack_active
            and not self.missing_handles
            and self.engine_error is None
        )

    @property
    def blocked_reason(self) -> str | None:
        if not self.exists:
            return f"no graph file for variant {self.variant!r}"
        if not self.pack_active:
            return "the creator pack is not active in this profile's packs.toml"
        if self.missing_handles:
            return "missing brand-kit handle(s): " + ", ".join(self.missing_handles)
        if self.engine_error:
            return self.engine_error
        return None


#: The brand-kit key holding the operator-APPROVED avatar look, keyed by orientation.
#:
#: Two keys, and the split is load-bearing rather than cosmetic. No single look serves both a
#: 16:9 master and a 9:16 cut: a 608x1080 portrait look rendered landscape fills 608 of 1920
#: columns and pads the rest, which is what shipped on 2026-08-27. `video-avatar`'s dual-ratio
#: rule already calls those two separate decisions, so the storage models them as two.
#:
#: There is deliberately NO fallback between them and no "default" entry. A landscape target that
#: could fall back to the portrait key would reintroduce that padding the moment only one
#: orientation had been approved — silently, and after the spend.
LOOK_KEY_BY_ORIENTATION: dict[str, str] = {
    "landscape": "identity.heygen_look_landscape",
    "portrait": "identity.heygen_look_portrait",
}


def look_key(orientation: str) -> str:
    """The brand-kit key for ``orientation``. ``ValueError`` on anything else.

    Refusing an unrecognised orientation ("square", "4:5", "") is the whole guard: mapping it to
    "whichever key is set" is a cross-orientation fallback wearing a different hat.
    """
    try:
        return LOOK_KEY_BY_ORIENTATION[orientation]
    except KeyError:
        raise ValueError(
            f"unknown orientation {orientation!r} — expected one of "
            f"{sorted(LOOK_KEY_BY_ORIENTATION)}"
        ) from None


def resolve_look(kit: dict[str, Any], orientation: str) -> str:
    """The approved look id for ``orientation``, or ``""`` when the kit records none.

    Reads exactly one key. An empty stored value counts as unset — in a product kit a
    present-but-empty value is an override to empty (see :mod:`gtm_core.brandkit`), so an empty
    look is a real gap rather than an inherited one, and either way the answer is "ask".
    """
    key = look_key(orientation)
    return str(lookup(kit, key)) if _present(kit, key) else ""


@dataclass(frozen=True)
class LookProposal:
    """One orientation's stored avatar look — and the ask that goes with it, either way.

    **This is a proposal, never a default.** The router asks about it at routing time, every
    run, before any spend. Storage exists to make the ask CHEAP — a one-line confirm naming the
    look instead of a menu of forty — not to skip it.

    The distinction is the whole point of V-3. The 2026-08-29 render was not wrong because the
    look was badly chosen; it was wrong because **nobody was asked**. A stored look that applied
    itself would reproduce that exactly, with a config file in front of it. So :attr:`ask` is
    non-empty whether or not a look is stored: what changes is the SHAPE of the ask (confirm vs
    pick), not whether one happens.

    Nothing here fetches a preview or ranks a candidate. The pick is the operator's aesthetic
    call about their own face (V-3 decision (c)), and a module that proposed a favourite would
    be making it for them one step earlier.
    """

    orientation: str
    key: str
    look_id: str

    @property
    def stored(self) -> bool:
        """True when the kit records an approved look for this orientation."""
        return bool(self.look_id)

    @property
    def ask(self) -> str:
        """The question to put to the operator — always one, never none."""
        if self.stored:
            return (
                f"CONFIRM (every run, before spend): {self.orientation} renders propose look "
                f"{self.look_id}, recorded in {self.key}. Name it and its native pixels "
                "(`list_avatar_looks` — free) and get an explicit yes. A recorded look is a "
                "proposal, never a silent default."
            )
        return (
            f"ASK (every run, before spend): no approved {self.orientation} look is recorded in "
            f"{self.key}. List the {self.orientation} candidates with their native pixels "
            "(`list_avatar_looks` — free) and let the operator pick. Do not pre-select, and do "
            "not borrow the other orientation's look."
        )


def look_proposals(kit: dict[str, Any]) -> tuple[LookProposal, ...]:
    """One proposal per orientation, in a stable order. Always both — never only the set one.

    Reporting only the stored orientation is how a single-key assumption creeps back: the
    orientation nobody has approved becomes invisible, and the render for it gets chosen the old
    way, by geometry alone.
    """
    return tuple(
        LookProposal(orientation=orientation, key=key, look_id=resolve_look(kit, orientation))
        for orientation, key in LOOK_KEY_BY_ORIENTATION.items()
    )


#: The one sentence that says WHY a story item is shot rather than rendered. Kept here rather than
#: in the router body on purpose: the body's rule is to relay what this module resolves, never to
#: restate it, and a reason duplicated into prose is a reason that goes stale where nobody looks.
_STORY_CAPTURE_REASON = (
    "the emotional payoff beat needs a real face, visibly moved, beside another person — the "
    "faceless lane has no face and the avatar lane a performed expression on a disclosed "
    "synthetic, which is the opposite of the ingredient"
)


@dataclass(frozen=True)
class StoryCapture:
    """The capture default a story item derives, and the reason it derives it.

    A DERIVED default, not a gate and not a question: it is filled and labelled, which is the
    opposite of the approved-look decision that must be asked every run. Nothing here resolves an
    engine, and nothing here touches the Article 50 disclosure path — a story item shot for real
    has no synthetic component to disclose, which is the point rather than a loophole.
    """

    value: str = "live_action"
    source: str = "derived"
    reason: str = _STORY_CAPTURE_REASON


def story_capture(item: dict | None) -> StoryCapture | None:
    """The capture default for one ContentItem, or ``None`` when it is not a story item.

    Pure: no I/O, no content root, no profile. ``brief.protagonist`` is the whole input — its
    presence is what `content-plan` Step 1 uses to mark an item as story-format, and a blank or
    whitespace-only value counts as ABSENT rather than as set, matching how an empty identity
    handle is read everywhere else in this module.
    """
    if not isinstance(item, dict):
        return None
    brief = item.get("brief")
    if not isinstance(brief, dict):
        return None
    protagonist = brief.get("protagonist")
    if not isinstance(protagonist, str) or not protagonist.strip():
        return None
    return StoryCapture()


@dataclass(frozen=True)
class Constraints:
    """Block B of the brief — the kit-derived envelope the creative must be written inside.

    Each field carries the key it came from so a stale value is visible rather than silent. A
    field without provenance is an assumption, and assumptions are what produced the 2026-08-28
    rewrite: the concept violated ``imagery.negative`` and ``imagery.style`` in ways that read
    past a human and would not have read past a checklist.
    """

    palette: dict[str, Any] = field(default_factory=dict)
    typography: dict[str, Any] = field(default_factory=dict)
    assets: dict[str, Any] = field(default_factory=dict)
    pronunciation: dict[str, Any] = field(default_factory=dict)
    imagery_style: str = ""
    imagery_negative: str = ""
    captions_preset: str | None = None
    disclosure_line: str = ""
    voice_bans_path: str | None = None
    existing_assets: tuple[str, ...] = ()

    #: Can anything actually SPEAK the script's ``[SPOKEN]`` lines? Resolved from
    #: ``identity.voice_id``. False on every bare profile, which is why this matters: the lane
    #: the router recommends most confidently is the one that would render silent.
    vo_available: bool = False
    #: How the VO gets made when it can — ``"<engine> on identity.voice_id"`` — or the empty
    #: string. Named rather than assumed, because ``identity.voice_engine`` picks the TTS engine
    #: and a kit can carry a voice for an engine nobody wired up.
    vo_route: str = ""

    #: `gtm_core.shots_lint` fails a shot list above this presenter share. Read off
    #: `shots_lint.MAX_PRESENTER_SHARE` at resolve time — never a literal, because the literal is
    #: what went stale in the router body.
    presenter_share_ceiling: float = shots_lint.MAX_PRESENTER_SHARE
    #: …and warns when no shot carries this role.
    required_shot_role: str = "screen"

    #: The plan-time rules the linter ACTUALLY runs, relayed from `shots_lint --rules`. A summary
    #: of the rule set copied into prose is what let a 2026-08-28 script be written to a target
    #: the linter had already moved past; this is the derived replacement.
    shot_rules: tuple[dict[str, str], ...] = ()

    #: Reap plan ceilings, relayed so a fan-out is planned against them rather than discovered by
    #: hitting them. `reap_project_retention_days` additionally bounds how long a run may sit
    #: parked at a capture gate before the footage it is waiting on is purged.
    reap_max_concurrent_projects: int = REAP_MAX_CONCURRENT_PROJECTS
    reap_project_retention_days: int = REAP_PROJECT_RETENTION_DAYS
    reap_requests_per_minute: int = REAP_REQUESTS_PER_MINUTE

    #: Caption placement resolved for THIS tenant's actual preset, through
    #: `gtm_core.captions.resolve_placement` — the module that owns the mapping.
    captions_placement: str = ""
    #: The operator-approved avatar look, per orientation — a Step 0.5 DECISION, never a gate.
    #: Always both orientations, each carrying its own ask. A lane is never blocked for lacking
    #: one (the same decision/gate boundary as the C11c staleness caveat): a missing look changes
    #: the ask from a confirm to a pick, and nothing else.
    look_proposals: tuple[LookProposal, ...] = ()

    #: The full upper-placement set, from `captions._UPPER_PLACEMENT_PRESETS`. Exposed so the
    #: router can name it without keeping a copy: the copy in the body held EIGHT of the nine and
    #: had been missing `system_ember_duo` silently — a tenant on that preset would have had its
    #: captions placed as lower, across the speaker's face, which is precisely what the
    #: placement rule exists to prevent.
    upper_placement_presets: tuple[str, ...] = ()

    #: The derived capture default for a story item, or None when this run has no item or the item
    #: carries no protagonist. Set only from :func:`story_capture`; the brief records the decision,
    #: this only reports it, and nothing downstream is blocked by either state.
    story_capture: StoryCapture | None = None


@dataclass(frozen=True)
class Preflight:
    profile: str
    product: str | None
    lanes: tuple[LaneStatus, ...]
    constraints: Constraints

    @property
    def ready_lanes(self) -> tuple[LaneStatus, ...]:
        return tuple(lane for lane in self.lanes if lane.ready)

    @property
    def default_lane(self) -> str | None:
        """The lane to offer when the operator has nothing — footage, handles, or an opinion.

        Prefers a ready lane that needs no footage, because a cold-start profile has none. This
        is why ``short-form-video`` is ordered first in :data:`LANE_REQUIREMENTS`.
        """
        for lane in self.lanes:
            if lane.ready and not lane.needs_footage:
                return lane.variant
        return None


def _present(kit: dict[str, Any], dotted: str) -> bool:
    """True when ``dotted`` resolves to a non-empty value.

    An empty string and an empty list both count as ABSENT. In a product kit a present-but-empty
    value is an override-to-empty rather than "inherit" (see :mod:`gtm_core.brandkit`), so an
    empty handle is a real gap, not an unset one.
    """
    try:
        value = lookup(kit, dotted)
    except KeyError:
        return False
    if value is None:
        return False
    if isinstance(value, (str, list, tuple, dict)):
        return len(value) > 0
    return True


def _graphs_dir(repo_root: Path) -> Path:
    override = os.getenv("GTM_PACKS_ROOT")
    base = Path(override) if override else repo_root / "packs"
    return base / "creator" / "graphs"


def _active_packs(profiles_root: Path, profile: str) -> frozenset[str]:
    """Read ``profiles/<p>/packs.toml``. A profile with no file activates nothing (fail-closed)."""
    import tomllib

    path = profiles_root / profile / "packs.toml"
    if not path.is_file():
        return frozenset()
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return frozenset()
    active = data.get("active", [])
    return frozenset(str(p) for p in active) if isinstance(active, list) else frozenset()


def _existing_assets(profiles_root: Path, profile: str) -> tuple[str, ...]:
    """Images/clips already in the profile that could serve a ``screen`` or ``broll`` shot.

    The highest-leverage line in the whole preflight. On 2026-08-28 the profile held 8 unused
    product screenshots while the brief was weighing whether to GENERATE abstract b-roll. Reuse
    beats capture beats generate, and reuse is free — but only if something surfaces it.
    """
    base = profiles_root / profile
    if not base.is_dir():
        return ()
    found: list[str] = []
    for path in sorted(base.rglob("*")):
        if len(found) >= _ASSET_LIMIT:
            break
        if not path.is_file() or path.suffix.lower() not in _ASSET_SUFFIXES:
            continue
        if _ASSET_EXCLUDE_PARTS & set(path.parts):
            continue
        found.append(str(path.relative_to(profiles_root.parent)))
    return tuple(found)


#: What the operator loses, and how they get it back. Named here rather than at the call site so
#: the menu text and the dataclass cannot drift apart.
_NO_VO_CAVEAT = (
    "Captions and music, no voice-over — the kit records no identity.voice_id "
    "(add one anytime with identity-kit, ~5 min)."
)


def lane_status(
    variant: str,
    *,
    kit: dict[str, Any],
    graphs_dir: Path,
    active_packs: frozenset[str],
    vo_available: bool = True,
) -> LaneStatus:
    """Feasibility for one lane. Pure: every input is already resolved."""
    req = LANE_REQUIREMENTS[variant]
    exists = (graphs_dir / f"{variant}.toml").is_file()
    missing = tuple(h for h in req.handles if not _present(kit, h))

    caveats: tuple[str, ...] = ()
    if req.vo_from_kit and not vo_available:
        caveats = (_NO_VO_CAVEAT,)

    estimated = (
        req.credits_per_minute * ESTIMATE_BASIS_MINUTES
        if req.credits_per_minute is not None
        else None
    )
    estimated_min = (
        req.credits_per_minute_min * ESTIMATE_BASIS_MINUTES
        if req.credits_per_minute_min is not None
        else None
    )

    engine: str | None = None
    engine_error: str | None = None
    verified_on = ""
    if req.presenter_role:
        try:
            spec = render_engines.resolve_engine(req.presenter_role, disclosed=req.disclosed)
            engine = spec.name
            verified_on = spec.verified_on
        except render_engines.EngineUnavailable as exc:
            engine_error = str(exc)
        except render_engines.EngineError as exc:  # pragma: no cover - registry bug, not state
            engine_error = f"registry error: {exc}"

    # C11c — staleness is REPORTED, never enforced. A capability ban outlives the probe that
    # justified it, and the only thing that makes an old probe visible is saying how old it is.
    # This deliberately does not touch `ready`: a stale probe does not make a render wrong, it
    # makes the claim behind the render unverified, and turning that into a gate would block work
    # to punish a documentation lapse.
    if engine is not None:
        age = render_engines.days_since_verification(verified_on)
        if age is None or age > render_engines.VERIFICATION_STALE_DAYS:
            when = f"{age} days ago" if age is not None else "on no recorded date"
            caveats += (
                f"engine {engine} was last verified {when}, past the "
                f"{render_engines.VERIFICATION_STALE_DAYS}-day review cadence — its capability "
                "claims, including what it is BANNED from doing, rest on a probe nobody has "
                "repeated. Not a blocker: run "
                "`uv run python -m gtm_core.render_engines --audit` against a fresh "
                "models_explore read when convenient.",
            )

    return LaneStatus(
        variant=variant,
        exists=exists,
        pack_active="creator" in active_packs,
        missing_handles=missing,
        needs_footage=req.needs_footage,
        engine=engine,
        engine_error=engine_error,
        summary=req.summary,
        unlock=req.unlock,
        caveats=caveats,
        estimated_credits=estimated,
        estimated_credits_min=estimated_min,
        engine_verified_on=verified_on,
    )


def preflight(
    profile: str,
    product: str | None = None,
    *,
    profiles_root: Path | None = None,
    repo_root: Path | None = None,
    item: dict | None = None,
) -> Preflight:
    """Blocks A and B of the video brief, resolved from disk. Spends nothing, writes nothing.

    ``item`` is one ContentItem, and it is optional because most callers have none: the router
    runs this before an item exists. When it is given, the only thing read from it is whether it
    carries ``brief.protagonist``, which decides the story-capture default.
    """
    root = profiles_root or resolve_profiles_root()
    repo = repo_root or Path(__file__).resolve().parents[1]
    kit = load_brand_kit(root, profile, product)

    graphs_dir = _graphs_dir(repo)
    active = _active_packs(root, profile)

    bans = root / profile / "knowledge" / "voice-bans.txt"

    def _get(dotted: str, default: Any = "") -> Any:
        try:
            return lookup(kit, dotted)
        except KeyError:
            return default

    # Resolved BEFORE the lanes, because a lane's caveats depend on it. `video-render` Step 4a
    # generates the VO only when this is non-empty and has no else-branch, so an empty voice_id
    # is the difference between a narrated cut and a silent one.
    vo_available = _present(kit, "identity.voice_id")
    vo_engine = str(_get("identity.voice_engine") or "").strip()
    vo_route = f"{vo_engine or 'default TTS'} on identity.voice_id" if vo_available else ""

    lanes = tuple(
        lane_status(
            v,
            kit=kit,
            graphs_dir=graphs_dir,
            active_packs=active,
            vo_available=vo_available,
        )
        for v in LANE_REQUIREMENTS
    )

    constraints = Constraints(
        palette=_get("palette", {}) or {},
        typography=_get("typography", {}) or {},
        assets=_get("assets", {}) or {},
        pronunciation=_get("pronunciation", {}) or {},
        imagery_style=str(_get("imagery.style") or ""),
        imagery_negative=str(_get("imagery.negative") or ""),
        captions_preset=(str(_get("captions.preset")) or None)
        if _present(kit, "captions.preset")
        else None,
        disclosure_line=str(_get("disclosure.line") or ""),
        voice_bans_path=str(bans.relative_to(root.parent)) if bans.is_file() else None,
        existing_assets=_existing_assets(root, profile),
        vo_available=vo_available,
        vo_route=vo_route,
        shot_rules=tuple(shots_lint.active_rules()),
        look_proposals=look_proposals(kit),
        captions_placement=captions.resolve_placement(kit),
        upper_placement_presets=tuple(sorted(captions._UPPER_PLACEMENT_PRESETS)),
        story_capture=story_capture(item),
    )
    return Preflight(profile=profile, product=product, lanes=lanes, constraints=constraints)


def _render_cost(lane: LaneStatus) -> str:
    """The cost hint beside a lane in the menu — a band when the rate is one.

    A band is rendered as a band. Collapsing it to either endpoint is how a coarse figure
    starts reading as a quote, which is the single thing C5 forbids.
    """
    high = lane.estimated_credits
    low = lane.estimated_credits_min
    if low is not None and low != high:
        return f"  [est. ~{low}-{high} credits/render, varies by engine]"
    unit = "" if high == 1 else "s"
    return f"  [est. ~{high} credit{unit}/min]"


def _render_text(pf: Preflight) -> str:
    lines: list[str] = []
    scope = f"{pf.profile}" + (f" / {pf.product}" if pf.product else "")
    lines.append(f"Video preflight — {scope}")
    lines.append("")
    lines.append("LANES")
    for lane in pf.lanes:
        cost = _render_cost(lane) if lane.estimated_credits is not None else ""
        if lane.ready:
            mark = "READY" if not lane.needs_footage else "READY IF YOU HAVE FOOTAGE"
            lines.append(f"  [{mark}] {lane.variant} — {lane.summary}{cost}")
        else:
            lines.append(f"  [BLOCKED] {lane.variant} — {lane.summary}{cost}")
            lines.append(f"            why: {lane.blocked_reason}")
            if lane.unlock:
                lines.append(f"            unlock: {lane.unlock}")
        for caveat in lane.caveats:
            lines.append(f"            caveat: {caveat}")
    lines.append("")
    lines.append(f"DEFAULT (nothing in hand): {pf.default_lane or 'none — every lane is blocked'}")
    c = pf.constraints
    # A DECISION block, deliberately printed apart from CONSTRAINTS and above it: a constraint
    # bounds what may be written, this is a question that has to be put to a person. It sits
    # beside the presenter engine and the cost estimate in the router's Step 0.5, and like both
    # of those it never blocks a lane.
    lines.append("")
    lines.append("APPROVED AVATAR LOOK — a decision, never a gate. ASK EVERY RUN, before spend.")
    for proposal in c.look_proposals:
        state = proposal.look_id if proposal.stored else "(none recorded)"
        lines.append(f"  {proposal.orientation:<10} {state}   [{proposal.key}]")
        lines.append(f"             {proposal.ask}")
    lines.append("")
    lines.append("CONSTRAINTS")
    if c.story_capture is not None:
        # A constraint, not a DECISION: filled and labelled from the item, never asked. It bounds
        # what may be written the same way the shot mix does, and the reason travels with it so the
        # router can relay one line instead of restating the rule from memory.
        sc = c.story_capture
        lines.append(f"  story capture     {sc.value} ({sc.source}) — {sc.reason}")
    if c.typography:
        fonts = c.typography.get("font_files") or {}
        lines.append(
            f"  typography        display={c.typography.get('display', '?')} "
            f"body={c.typography.get('body', '?')} "
            f"font_files={','.join(sorted(fonts)) or '(none — captions will raise)'}"
        )
    else:
        lines.append("  typography        (unset)")
    if c.assets:
        lines.append(f"  assets            {', '.join(f'{k}={v}' for k, v in c.assets.items())}")
    else:
        lines.append("  assets            (unset)")
    if c.pronunciation:
        lines.append(
            f"  pronunciation     {', '.join(f'{k}={v}' for k, v in c.pronunciation.items())}"
        )
    else:
        lines.append("  pronunciation     (unset)")
    lines.append(f"  imagery.style     {c.imagery_style or '(unset)'}")
    lines.append(f"  imagery.negative  {c.imagery_negative or '(unset)'}")
    lines.append(f"  captions.preset   {c.captions_preset or '(unset — declare one in the brief)'}")
    lines.append(f"  captions placement {c.captions_placement} (resolved by gtm_core.captions)")
    lines.append(f"  disclosure.line   {c.disclosure_line or '(unset — fails closed downstream)'}")
    lines.append(f"  voice bans        {c.voice_bans_path or '(none)'}")
    lines.append(
        f"  voice-over        {c.vo_route if c.vo_available else 'UNAVAILABLE — no identity.voice_id'}"
    )
    lines.append(
        f"  shot mix          presenter < {c.presenter_share_ceiling:.0%}, "
        f">=1 role:{c.required_shot_role!r}"
    )
    lines.append(
        f"  shot rules        {len(c.shot_rules)} active "
        "(uv run python -m gtm_core.shots_lint --rules)"
    )
    lines.append(
        f"  reap ceilings     {c.reap_max_concurrent_projects} concurrent projects, "
        f"{c.reap_requests_per_minute} req/min, footage kept {c.reap_project_retention_days} days "
        f"(plan read {REAP_PLAN_VERIFIED_ON} — re-read live before a large job)"
    )
    if c.existing_assets:
        lines.append(f"  existing assets   {len(c.existing_assets)} reusable (reuse > generate):")
        for path in c.existing_assets[:8]:
            lines.append(f"                      {path}")
        if len(c.existing_assets) > 8:
            lines.append(f"                      … and {len(c.existing_assets) - 8} more")
    else:
        lines.append("  existing assets   (none found)")
    return "\n".join(lines)


def _as_dict(pf: Preflight) -> dict[str, Any]:
    return {
        "profile": pf.profile,
        "product": pf.product,
        "default_lane": pf.default_lane,
        "lanes": [
            {
                "variant": lane.variant,
                "ready": lane.ready,
                "needs_footage": lane.needs_footage,
                "summary": lane.summary,
                "blocked_reason": lane.blocked_reason,
                "unlock": lane.unlock or None,
                "engine": lane.engine,
                "missing_handles": list(lane.missing_handles),
                "caveats": list(lane.caveats),
                "estimated_credits": lane.estimated_credits,
                "estimated_credits_min": lane.estimated_credits_min,
                "engine_verified_on": lane.engine_verified_on,
            }
            for lane in pf.lanes
        ],
        "constraints": {
            "palette": pf.constraints.palette,
            "typography": pf.constraints.typography,
            "assets": pf.constraints.assets,
            "pronunciation": pf.constraints.pronunciation,
            "imagery_style": pf.constraints.imagery_style,
            "imagery_negative": pf.constraints.imagery_negative,
            "captions_preset": pf.constraints.captions_preset,
            "disclosure_line": pf.constraints.disclosure_line,
            "voice_bans_path": pf.constraints.voice_bans_path,
            "shot_rules": [dict(r) for r in pf.constraints.shot_rules],
            "look_proposals": [
                {
                    "orientation": p.orientation,
                    "key": p.key,
                    "look_id": p.look_id,
                    "stored": p.stored,
                    "ask": p.ask,
                }
                for p in pf.constraints.look_proposals
            ],
            "reap_max_concurrent_projects": pf.constraints.reap_max_concurrent_projects,
            "reap_project_retention_days": pf.constraints.reap_project_retention_days,
            "reap_requests_per_minute": pf.constraints.reap_requests_per_minute,
            "captions_placement": pf.constraints.captions_placement,
            "upper_placement_presets": list(pf.constraints.upper_placement_presets),
            "vo_available": pf.constraints.vo_available,
            "vo_route": pf.constraints.vo_route,
            "presenter_share_ceiling": pf.constraints.presenter_share_ceiling,
            "required_shot_role": pf.constraints.required_shot_role,
            "existing_assets": list(pf.constraints.existing_assets),
            # Hand-enumerated like every sibling above: a Constraints field added without a line
            # here is silently absent from --json, which is the one consumer the router reads.
            "story_capture": (
                None
                if pf.constraints.story_capture is None
                else {
                    "value": pf.constraints.story_capture.value,
                    "source": pf.constraints.story_capture.source,
                    "reason": pf.constraints.story_capture.reason,
                }
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    """``python -m gtm_core.video_preflight --profile <p> [--product <s>] [--json]``.

    Exit 0 = at least one lane is runnable. Exit 2 = every lane is blocked, which is a REPORTABLE
    STATE rather than a crash: the output names each blocker and its unlock. Exit 1 = a real
    error (unreadable kit, unsafe segment).
    """
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.video_preflight",
        description="Which creator-pack video lanes this profile can run, and what bounds them.",
    )
    parser.add_argument("--profile", required=True, help="active profile slug")
    parser.add_argument("--product", default=None, help="active product slug")
    parser.add_argument("--profiles-root", default=None, type=Path)
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument(
        "--item-json",
        default=None,
        type=Path,
        help="path to one ContentItem as JSON; only brief.protagonist is read from it",
    )
    args = parser.parse_args(argv)

    try:
        item = None
        if args.item_json is not None:
            # An unreadable or non-object item is a REAL error, not an absent story default:
            # returning null here would report "not a story item" for a path that was simply
            # mistyped, and the routing decision would be taken on a silence.
            loaded = json.loads(args.item_json.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError(
                    f"{args.item_json} does not hold a single ContentItem object "
                    f"(read a {type(loaded).__name__})"
                )
            item = loaded
        pf = preflight(args.profile, args.product, profiles_root=args.profiles_root, item=item)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        return 1

    print(json.dumps(_as_dict(pf), indent=2) if args.json else _render_text(pf))
    return 0 if pf.ready_lanes else 2


if __name__ == "__main__":
    raise SystemExit(main())
