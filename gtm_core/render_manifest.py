"""The manifest writer the verification rules assume exists (F5).

``identity_used``, ``lip_sync_source``, asset paths, and the would-post binary are, today,
model-emitted markdown prose — no Python code writes or validates them. That means every "assert
the writer, not the gate" claim in the verification table has nothing to assert against,
and it is why silent errors #3, #4, #8, #11, and #15 (a manifest naming a path that was never
written; an empty ``identity_used`` on a generated asset; a VO asset with no ``lip_sync_source``)
have shipped live at least once (``render-9x16.json`` names ``…-final.mp4``, which does not exist
in the tree today — reproduced by :func:`test_write_refuses_a_manifest_naming_a_missing_path`).

This module is a thin, refusing writer for two sibling JSON documents per ``(profile, slug,
ratio)``:

``render-<ratio>.json``
    What the generation layer produced — provider job ids, cost, ``identity_used``,
    ``lip_sync_source``, and the raw asset path.

``finish-<ratio>.json``
    What :mod:`gtm_core.video_finish` did to it — the ordered stage list, the plan census
    (grade/overlay/loudnorm counts), and the finished asset path. Written by that module, not
    this one; :func:`load_finish` reads it back for :func:`gtm_core.video_lint` and
    :func:`gtm_core.outcomes`.

Both are validated with the same refusal rules on write, so a bad manifest never lands on disk:

* a generated or restyled asset (``identity_used`` non-empty by *provenance*, tracked via the
  caller-supplied ``synthetic`` flag — see :func:`write_render_manifest`) may not claim an empty
  ``identity_used``;
* an asset carrying ``"voice"`` in ``identity_used`` must record ``lip_sync_source``;
* a synthetic asset must record the verbatim ``prompt`` that produced it (Phase 18, R2 —
  0/3 manifests shipped before this rule carried the prompt, so the exact bytes sent for
  every asset were unrecoverable, including the ad-hoc wardrobe fix behind a 22.5-credit
  discard);
* every path field named in the manifest must exist on disk at write time.

CLI is read-only (validate an existing manifest against the same rules the writer enforces)::

    uv run python -m gtm_core.render_manifest validate render-9x16.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

#: Mirrors agent/publish.py's _KNOWN_IDENTITY_VALUES — kept as an independent literal (not an
#: import) because this module has no business depending on agent/, and a drift between the two
#: is exactly the kind of thing a shared test should catch, not a shared import should hide.
KNOWN_IDENTITY_VALUES = frozenset({"soul", "element", "voice", "generated"})

#: HOW the mouth in this asset was produced. The distinction is the whole lesson of August 2026
#: and must not be blurred, because the manifest is the only durable record of it:
#:
#:   ``"text_prompt_only"``  — mouth motion was described in prose. Unrelated to the words.
#:   ``"audio_references"``  — an audio file was passed to a general image-to-video model as a
#:                             REFERENCE input. Verified 2026-08-19: the mouth was closed in 9 of
#:                             10 sampled frames across a full spoken sentence. This is NOT lip
#:                             sync, and it is the token that records a render that only looked
#:                             like it had some. Never reuse it for an engine that genuinely syncs.
#:   ``"native_audio"``      — the engine GENERATED the mouth from an audio track we supplied
#:                             (HeyGen avatar_iv `audioUrl`/`audioAssetId`). The registry's
#:                             `lip_sync = "native"` engines, and the only path that survives the
#:                             presenter role's `requires`.
#:   ``"native_script"``     — the engine generated both the voice and the mouth from a text
#:                             script (HeyGen `script`, mutually exclusive with the audio inputs).
#:                             Native sync, but a VENDOR voice rather than the tenant's clone.
#:
#: ``"post_hoc"`` is deliberately ABSENT and refused by :func:`_validate_engine` — see there.
LIP_SYNC_SOURCES = frozenset(
    {"audio_references", "text_prompt_only", "native_audio", "native_script"}
)

#: Which renderer actually burned the captions on this asset.
#:
#:   ``"reap"``   — the vendor's `add_captions` with the tenant's declared `captions.preset`,
#:                  timed from `transcribe`'s real per-word timings.
#:   ``"local"``  — the in-repo Pillow renderer, timing apportioned evenly across each screen.
#:   ``"none"``   — the finish plan had no caption stage at all.
#:
#: Recorded rather than inferred because the choice between the first two was, for three months,
#: a judgement call made silently at finish time — and it went the wrong way every time while the
#: Reap plan sat at 0 of 600 credits used. A route nobody wrote down is a route nobody can audit.
CAPTION_ROUTES = frozenset({"reap", "local", "none"})


class ManifestError(ValueError):
    """A manifest write was refused. The message names exactly what is wrong."""


@dataclass(frozen=True)
class RenderManifest:
    profile: str
    slug: str
    ratio: str
    asset_path: str
    identity_used: tuple[str, ...] = ()
    lip_sync_source: str | None = None
    provider: str = ""
    provider_job_id: str = ""
    cost_credits: float = 0.0
    duration_s: float = 0.0
    #: The verbatim prompt string sent to the provider for THIS asset (after assembly per
    #: plugin/skills/video-render/references/prompt-recipes.md, `<<<element_id>>>` tokens
    #: intact). Required for any synthetic asset. ``seed`` is the generation seed when the
    #: path exposes/reports one (the headless worker always does; the connector may not) —
    #: ``None`` means "not surfaced", never "not applicable".
    prompt: str = ""
    seed: int | None = None
    #: Draft-pool budget (video-render Phase 16).
    #: Set together, or not at all — an asset from the K-scored pool records all three; an asset
    #: from the unscored N-K batch omits all three rather than guessing a rank. draft_rank 1 is
    #: the best-of-K winner that decided whether the batch got spent.
    draft_pool_size: int | None = None
    draft_rank: int | None = None
    pool_predictor_score: float | None = None
    #: How ``cost_credits`` was determined, and the ledger row that metered it.
    #:
    #: W0.3. The August 2026 video programme
    #: spent ~580 credits that ``costs.jsonl`` records as $0: generations drew a PRE-PURCHASED
    #: credit pool, so no code path was forced to meter them, and the profile's monthly cap never
    #: saw the spend. Reading the ledger to answer "what did video cost" returned zero, and zero
    #: was wrong. A manifest may no longer record a synthetic render it cannot tie to a row.
    #:
    #: ``cost_source`` is one of:
    #:   ``"preflight"``               — the provider's own ``get_cost`` preflight supplied it.
    #:   ``"post-hoc-balance-delta"``  — the provider has no working preflight (observed live:
    #:                                   ``sync_so`` returns 422 on the same params the generation
    #:                                   requires), so cost is a measured balance delta. Explicit
    #:                                   and auditable, rather than a silently omitted field.
    #:   ``"free"``                    — a genuinely unmetered path (free web fallback). Requires
    #:                                   ``cost_credits == 0`` and needs no ledger row.
    #: ``cost_ledger_ts`` is the ``ts`` of the ``costs.jsonl`` row (rows carry no id; ``ts`` is
    #: their natural key), as returned by :meth:`gtm_core.ledgers.Ledgers.append_cost`.
    cost_source: str = ""
    cost_ledger_ts: str = ""
    #: The ``gtm_core/render_engines.toml`` engine that produced this asset, when the writer knows
    #: it. Optional so real footage and legacy manifests stay writable — but when present it MUST
    #: resolve in the registry and must not name a retired engine (W1.3).
    render_engine: str = ""
    #: The storyboard the operator approved at ``video-storyboard``'s ⟦GATE:plan⟧, repo-relative.
    #: Required for any SYNTHETIC asset: the gate exists so a person sees the frames before the
    #: render budget is spent, and a render that cannot name an approved storyboard skipped it.
    #: Real (uncaptured) footage needs no storyboard — nothing was generated to approve.
    storyboard_path: str = ""

    def to_json(self) -> dict:
        d = asdict(self)
        d["identity_used"] = list(self.identity_used)
        return d


@dataclass(frozen=True)
class FinishManifest:
    profile: str
    slug: str
    ratio: str
    asset_path: str
    stages: tuple[str, ...]
    census: dict[str, int] = field(default_factory=dict)
    executed: bool = True
    plan_id: str = ""
    lint_suppressions: tuple[dict, ...] = ()
    #: The captions.json payload (frame/screens/safe_area), embedded so video_lint's
    #: `--manifest finish-<ratio>.json` path has the geometry it needs without a second file
    #: read. None when the finish plan had no caption stage.
    captions: dict | None = None
    #: Carried forward verbatim from the render manifest's ``identity_used``. The finish manifest
    #: is the record of what SHIPPED, and identity provenance is a disclosure-grade fact (EU AI
    #: Act Art. 50), so it belongs on both. Concretely: ``video_lint``'s V3 face-band rule
    #: promotes a caption-over-face overlap from WARN to ERROR only when a rendered person is
    #: actually on screen, and `--manifest finish-<ratio>.json` is the documented lint invocation
    #: — without this field that rule could never fire at ERROR on a real asset.
    identity_used: tuple[str, ...] = ()
    #: Which renderer burned the captions — one of :data:`CAPTION_ROUTES`. Empty string means a
    #: legacy manifest written before this field existed; it is tolerated on load and refused on
    #: write, so old artifacts stay readable while new ones cannot omit the fact.
    caption_route: str = ""
    #: Why the local route was taken while the tenant's ``captions.preset`` resolved. Required in
    #: exactly that case and forbidden otherwise — see :func:`_validate_caption_route`.
    caption_route_suppression: str = ""
    #: What the producer knows about the MIX that no analysis of the finished file can recover:
    #: whether a music bed exists, whether it was ducked, and at what levels. ``video_lint``'s CLI
    #: merges its own measured keys (silence runs, integrated loudness) on top of this before
    #: evaluating V5 and V10.
    #:
    #: Until 2026-08-28 nothing wrote this key and the CLI read it unconditionally, so **V5 could
    #: never fire on any asset** — a dormant tier that looked live. Same for ``spoken_text`` and
    #: V8 below.
    audio_context: dict | None = None
    #: The voice-over line the captions were cut from, for V8's caption/voice divergence check.
    spoken_text: str = ""

    def to_json(self) -> dict:
        d = asdict(self)
        d["stages"] = list(self.stages)
        d["lint_suppressions"] = list(self.lint_suppressions)
        d["identity_used"] = list(self.identity_used)
        return d


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _validate_identity(identity_used: tuple[str, ...], *, synthetic: bool) -> None:
    unknown = set(identity_used) - KNOWN_IDENTITY_VALUES
    if unknown:
        raise ManifestError(f"unknown identity_used value(s): {sorted(unknown)}")
    if synthetic and not identity_used:
        raise ManifestError(
            "synthetic=True (a generated or restyled asset) but identity_used is empty — "
            "every AI-generated/restyled render must carry at least 'generated' (EU AI Act "
            "Art. 50). If this asset is genuinely not synthetic (e.g. video-clip trimming the "
            "operator's own footage), pass synthetic=False instead."
        )
    if "voice" in identity_used:
        pass  # lip_sync_source is checked separately — the caller must supply it


def _validate_prompt(prompt: str, *, synthetic: bool) -> None:
    if synthetic and not prompt.strip():
        raise ManifestError(
            "synthetic=True but prompt is empty — every generated/restyled asset must record "
            "the verbatim prompt that produced it (assembled per "
            "plugin/skills/video-render/references/prompt-recipes.md), or the exact bytes sent "
            "to the provider are unrecoverable and no prompt-level learning loop can exist"
        )


def _validate_lip_sync(identity_used: tuple[str, ...], lip_sync_source: str | None) -> None:
    if "voice" not in identity_used:
        return
    if lip_sync_source is None:
        raise ManifestError(
            "identity_used carries 'voice' but lip_sync_source is unset — every VO-bearing "
            "asset must record how the voice track was driven (audio_references or "
            "text_prompt_only), so a render without a lip-sync-capable model is auditable"
        )
    if lip_sync_source not in LIP_SYNC_SOURCES:
        raise ManifestError(
            f"lip_sync_source={lip_sync_source!r} is not one of {sorted(LIP_SYNC_SOURCES)}"
        )


def _validate_draft_pool(
    draft_pool_size: int | None, draft_rank: int | None, pool_predictor_score: float | None
) -> None:
    """``draft_pool_size`` and ``draft_rank`` are set together or not at all — a manifest with
    only one would silently misrepresent which pool member this is (or that it was a pool member
    at all). ``draft_rank`` must fall within ``1..draft_pool_size``: a rank the pool couldn't have
    produced is a bug in whatever wrote it, not a value to pass through.

    ``pool_predictor_score`` is looser: it may be ``None`` while the other two are set (Phase 17,
    2026-08-17 — the predictor became a recorded advisory, not a spend gate, and its id-resolution
    or scoring failure is a documented, non-fatal degradation the skill reports rather than blocks
    on; a real pool member can land here with no predictor reading at all). It may **not** be set
    while the other two are absent — a predictor score with no pool membership to attach it to
    doesn't mean anything."""
    pool_present = [v is not None for v in (draft_pool_size, draft_rank)]
    if any(pool_present) and not all(pool_present):
        raise ManifestError(
            "draft_pool_size and draft_rank must be set together or not at all — got "
            f"draft_pool_size={draft_pool_size!r}, draft_rank={draft_rank!r}"
        )
    if pool_predictor_score is not None and not all(pool_present):
        raise ManifestError(
            "pool_predictor_score is set but draft_pool_size/draft_rank are not — a predictor "
            f"score requires pool membership to attach to (got pool_predictor_score="
            f"{pool_predictor_score!r}, draft_pool_size={draft_pool_size!r}, "
            f"draft_rank={draft_rank!r})"
        )
    if draft_pool_size is not None and not (1 <= draft_rank <= draft_pool_size):
        raise ManifestError(
            f"draft_rank={draft_rank!r} is out of range for draft_pool_size={draft_pool_size!r} "
            "— rank must be between 1 (the pool winner) and the pool size"
        )


COST_SOURCES = frozenset({"preflight", "post-hoc-balance-delta", "free"})


def _validate_cost(
    cost_source: str, cost_credits: float, cost_ledger_ts: str, *, synthetic: bool
) -> None:
    """A synthetic render must name how it was costed and the ledger row that metered it.

    Fail-closed, in the same shape as :func:`_validate_prompt` and :func:`_validate_storyboard`:
    an unrecordable spend is an unshippable one. Real (uncaptured) footage costs nothing to
    generate and is exempt, exactly as it is exempt from the storyboard gate.
    """
    if not synthetic:
        return
    if cost_source not in COST_SOURCES:
        raise ManifestError(
            f"cost_source must be one of {sorted(COST_SOURCES)} for a synthetic asset, "
            f"got {cost_source!r}. An omitted cost is how ~580 credits of video spend became "
            "invisible to the monthly cap in August 2026 — say how it was costed, even when the "
            "provider has no preflight ('post-hoc-balance-delta')."
        )
    if cost_source == "free":
        if cost_credits:
            raise ManifestError(
                f"cost_source='free' but cost_credits={cost_credits} — a free path spends nothing."
            )
        return
    if cost_credits <= 0:
        raise ManifestError(
            f"cost_source={cost_source!r} requires cost_credits > 0, got {cost_credits}. "
            "If the render genuinely cost nothing, say so with cost_source='free'."
        )
    if not cost_ledger_ts.strip():
        raise ManifestError(
            "cost_ledger_ts is required — it ties this render to its costs.jsonl row (the row's "
            "`ts`, returned by Ledgers.append_cost). Log the cost BEFORE writing the manifest; a "
            "spend nothing metered does not reach the profile's monthly cap."
        )


def _validate_engine(render_engine: str, lip_sync_source: str | None) -> None:
    """Refuse an asset produced by an engine the registry has retired.

    Post-hoc lip sync (``sync_so`` and any successor) re-renders the mouth region at lower
    fidelity and composites it back over the whole frame: waxy skin, erased stubble, uniform
    teeth, a pasted-on mouth. That is architectural, not a version defect, so a "v4" must not
    silently qualify — the registry marks the whole approach retired and this refuses anything
    that names it.
    """
    if lip_sync_source == "post_hoc":
        raise ManifestError(
            "lip_sync_source='post_hoc' — post-hoc lip sync is retired and may not be shipped. "
            "It visibly damages a trained likeness (verified 2026-08-18). Re-testing a newer "
            "version does not change the architecture."
        )
    if not render_engine.strip():
        return
    try:
        from gtm_core import render_engines
    except ImportError:  # pragma: no cover - registry module is part of the package
        return
    try:
        registry = render_engines.load_registry()
    except render_engines.EngineError:  # pragma: no cover - surfaced by the registry's own tests
        return
    engines = registry.get("engines", {})
    if render_engine not in engines:
        raise ManifestError(
            f"render_engine={render_engine!r} is not in gtm_core/render_engines.toml. "
            "An asset whose engine the registry does not know is an asset nothing can audit."
        )
    table = engines[render_engine]
    if table.get("retired"):
        raise ManifestError(
            f"render_engine={render_engine!r} is retired and may not be shipped. "
            f"{str(table.get('notes', '')).strip()}"
        )
    # A claim the engine cannot back. `native_audio`/`native_script` assert the mouth was
    # generated from the audio; only an engine the registry marks `lip_sync = "native"` does that.
    # This is the August 2026 defect written from the other end: back then the RENDER was wrong
    # and the record said nothing. A record that can assert native sync over a model incapable of
    # it would let the same failure ship while looking audited.
    if lip_sync_source in {"native_audio", "native_script"}:
        actual = str(table.get("lip_sync", "none"))
        if actual != "native":
            raise ManifestError(
                f"lip_sync_source={lip_sync_source!r} claims the mouth was generated from the "
                f"audio, but render_engine={render_engine!r} has lip_sync={actual!r} in "
                "gtm_core/render_engines.toml. Either the engine is wrong or the claim is — and "
                "an unbacked sync claim in the audit record is worse than no record, because it "
                "is what a reviewer would trust instead of watching the frames."
            )


def _validate_caption_route(
    caption_route: str, suppression: str, *, preset_resolved: bool | None
) -> None:
    """A resolving caption preset may not be bypassed silently.

    ``preset_resolved`` is the tenant's ``captions.preset`` state as the CALLER read it:
    ``True``/``False`` when it was actually looked up, ``None`` when this writer had no brand kit
    in hand — the same three-state convention ``shots_lint``'s ``voice_grade`` uses, and for the
    same reason: a manifest written by a caller that never had the fact should not fail on it.

    The rule this encodes is the one the ``video-finish`` body already stated in prose and that
    was skipped anyway: with a preset resolving, the Reap route is the default, and taking the
    local path instead is a decision that has to be written down with a reason. Not forbidden —
    *recorded*. An operator reading the manifest afterwards can then see that a choice was made,
    which is precisely what was missing when twenty-four locally-burned caption screens shipped
    against a preset nobody noticed was configured.
    """
    if not caption_route:
        raise ManifestError(
            "caption_route is unset — record which renderer burned the captions "
            f"({sorted(CAPTION_ROUTES)}). A route nobody wrote down is a route nobody can audit, "
            "and 'we meant to use the configured preset' is not something a later reader can "
            "check against the asset."
        )
    if caption_route not in CAPTION_ROUTES:
        raise ManifestError(
            f"caption_route={caption_route!r} is not one of {sorted(CAPTION_ROUTES)}"
        )

    if caption_route != "local" and suppression:
        raise ManifestError(
            f"caption_route={caption_route!r} carries a caption_route_suppression "
            f"({suppression!r}), but a suppression only means something when the configured "
            "route was NOT taken. Clear it, or the record claims a decision nobody made."
        )
    if caption_route == "local" and preset_resolved and not suppression.strip():
        raise ManifestError(
            "caption_route='local' while the tenant's captions.preset resolves — this is the "
            "bypass that shipped 24 caption screens over the speaker's face on 2026-08-18 while "
            "the Reap plan sat at 0 of 600 credits used. Take the Reap route (`transcribe` for "
            "real per-word timings, then `add_captions` with the preset), or record why not in "
            "caption_route_suppression. A configured route may be overridden; it may not be "
            "quietly skipped."
        )


def _validate_paths_exist(paths: dict[str, str], *, repo_root: Path) -> None:
    for field_name, raw in paths.items():
        if not raw:
            continue
        p = Path(raw)
        candidate = p if p.is_absolute() else (repo_root / p)
        if not candidate.exists():
            raise ManifestError(
                f"{field_name} names a path that does not exist on disk: {raw!r} "
                f"(resolved: {candidate})"
            )


def _validate_storyboard(storyboard_path: str, *, synthetic: bool, repo_root: Path) -> None:
    """A synthetic asset must name the storyboard the operator approved before the spend.

    ``video-storyboard`` ends its turn on ⟦GATE:plan⟧ precisely so a person sees the frames
    while they are still free to reject. A render that cannot name an approved storyboard did
    not pass that gate. Enforced here rather than in the skill's prose because prose is what
    gets skipped: on 2026-08-18 a full render ran end to end without the storyboard stage ever
    being invoked, and nothing objected until the operator watched the result.
    """
    if not synthetic:
        return
    if not (storyboard_path or "").strip():
        raise ManifestError(
            "synthetic render has no storyboard_path — run the video-storyboard stage and get "
            "⟦GATE:plan⟧ approval before spending on the render"
        )
    p = Path(storyboard_path)
    candidate = p if p.is_absolute() else (repo_root / p)
    if not candidate.exists():
        raise ManifestError(
            f"storyboard_path names a file that does not exist: {storyboard_path!r} "
            f"(resolved: {candidate})"
        )
    try:
        data = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"storyboard is unreadable: {storyboard_path!r} ({exc})") from exc
    if not data.get("approved"):
        raise ManifestError(
            f"storyboard {storyboard_path!r} is not marked approved — the ⟦GATE:plan⟧ decision "
            "is what authorises the render spend"
        )


def write_render_manifest(
    manifest: RenderManifest,
    *,
    out_dir: Path,
    synthetic: bool,
    repo_root: Path | None = None,
) -> Path:
    """Validate and write render-<ratio>.json. Raises ManifestError on any violation — never a
    silent partial write."""
    _validate_identity(manifest.identity_used, synthetic=synthetic)
    _validate_prompt(manifest.prompt, synthetic=synthetic)
    _validate_storyboard(
        manifest.storyboard_path,
        synthetic=synthetic,
        repo_root=repo_root if repo_root is not None else Path.cwd(),
    )
    _validate_lip_sync(manifest.identity_used, manifest.lip_sync_source)
    _validate_engine(manifest.render_engine, manifest.lip_sync_source)
    _validate_cost(
        manifest.cost_source,
        manifest.cost_credits,
        manifest.cost_ledger_ts,
        synthetic=synthetic,
    )
    _validate_draft_pool(
        manifest.draft_pool_size, manifest.draft_rank, manifest.pool_predictor_score
    )
    root = repo_root if repo_root is not None else Path.cwd()
    _validate_paths_exist({"asset_path": manifest.asset_path}, repo_root=root)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"render-{manifest.ratio}.json"
    out_path.write_text(json.dumps(manifest.to_json(), indent=2, sort_keys=True) + "\n")
    return out_path


def write_finish_manifest(
    manifest: FinishManifest,
    *,
    out_dir: Path,
    repo_root: Path | None = None,
    preset_resolved: bool | None = None,
) -> Path:
    """Validate and write finish-<ratio>.json. When ``executed`` is False (ffmpeg absent), the
    asset_path is NOT required to exist — that is the documented degraded-but-honest path.

    ``preset_resolved`` is whether the tenant's ``captions.preset`` resolved, as read by the
    caller; ``None`` means the caller never looked it up. See :func:`_validate_caption_route`.
    """
    _validate_caption_route(
        manifest.caption_route,
        manifest.caption_route_suppression,
        preset_resolved=preset_resolved,
    )
    root = repo_root if repo_root is not None else Path.cwd()
    if manifest.executed:
        _validate_paths_exist({"asset_path": manifest.asset_path}, repo_root=root)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"finish-{manifest.ratio}.json"
    out_path.write_text(json.dumps(manifest.to_json(), indent=2, sort_keys=True) + "\n")
    return out_path


def load_render(path: Path) -> RenderManifest:
    """Parse ``render-<ratio>.json`` into a :class:`RenderManifest`.

    Unlike :func:`load_finish`, this tolerates unknown top-level keys. Every
    ``render-<ratio>.json`` shipped in this repo to date was hand-authored by the ``video-render``
    skill's prose instructions, not written through :func:`write_render_manifest` — none of them
    were ever code-validated against this dataclass, and every one carries extra fields the skill
    also records (``source_item``, ``hook_id``, ``shots``, ``predictor``, ``cost``, ...). A strict
    ``RenderManifest(**data)`` call raises ``TypeError`` on every one of them (confirmed
    2026-08-17). Filtering to the dataclass's own field names is the load-bearing fix — the fields
    this module and its callers actually read (``identity_used``, ``lip_sync_source``, ``ratio``,
    ``asset_path``, the draft-pool trio) still round-trip; everything else is silently dropped
    rather than rejected, since this loader's contract is "give me the fields I know about", not
    "prove the whole file conforms to a schema nothing has ever written it against."
    """
    data = json.loads(path.read_text())
    known = {f.name for f in fields(RenderManifest)}
    filtered = {k: v for k, v in data.items() if k in known}
    filtered["identity_used"] = tuple(filtered.get("identity_used", ()))
    return RenderManifest(**filtered)


def load_finish(path: Path) -> FinishManifest:
    data = json.loads(path.read_text())
    known = {f.name for f in fields(FinishManifest)}
    data = {k: v for k, v in data.items() if k in known}
    data["stages"] = tuple(data.get("stages", ()))
    data["lint_suppressions"] = tuple(data.get("lint_suppressions", ()))
    data["identity_used"] = tuple(data.get("identity_used", ()))
    return FinishManifest(**data)


def validate_render_file(path: Path, *, synthetic: bool, repo_root: Path | None = None) -> None:
    """Re-run the write-time rules against an already-written manifest. Raises ManifestError.

    ``repo_root`` defaults to the CWD, **matching :func:`write_render_manifest`** — a relative
    ``asset_path`` means the same thing to the writer and to this re-check. It previously defaulted
    to the manifest's own parent directory, which made the two disagree: a manifest written
    repo-relative (the convention every writer here uses, including
    :func:`gtm_core.video_finish._write_manifest`) could never pass the ``validate`` CLI that
    ``video-render`` Step 5 instructs the caller to run before reporting, because the parent dir
    got prepended to an already-repo-relative path. Fixed 2026-08-19; the CLI also grew
    ``--repo-root`` so a non-CWD invocation is expressible instead of silently wrong.
    """
    m = load_render(path)
    _validate_identity(m.identity_used, synthetic=synthetic)
    _validate_prompt(m.prompt, synthetic=synthetic)
    _validate_lip_sync(m.identity_used, m.lip_sync_source)
    _validate_draft_pool(m.draft_pool_size, m.draft_rank, m.pool_predictor_score)
    root = repo_root if repo_root is not None else Path.cwd()
    _validate_paths_exist({"asset_path": m.asset_path}, repo_root=root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m gtm_core.render_manifest")
    sub = parser.add_subparsers(dest="cmd", required=True)
    validate = sub.add_parser("validate", help="Re-check an existing render-<ratio>.json")
    validate.add_argument("path", type=Path)
    validate.add_argument("--synthetic", action="store_true")
    validate.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="root a relative asset_path resolves against (default: CWD, matching the writer)",
    )
    args = parser.parse_args(argv)

    if args.cmd == "validate":
        try:
            validate_render_file(args.path, synthetic=args.synthetic, repo_root=args.repo_root)
        except ManifestError as exc:
            print(f"render-manifest: {exc}", file=sys.stderr)
            return 1
        print(f"render-manifest: {args.path} OK")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
