"""Draft-pool membership on a render manifest — which member of which pool this asset is.

Split out of :mod:`gtm_core.render_manifest` by C5, which added the two fields a widened sampling
curve needs (`pool_succeeded`, and the rank check that depends on it) and found that file at
exactly its 680-line ceiling. Pool membership is a coherent thing to hold on its own: three
numbers and a criterion, and every rule about how they must agree.

The rules are enforced HERE as well as in :mod:`gtm_core.sampling_curve`, which produces them.
That is deliberate duplication rather than an oversight — the manifest is the durable record, and
a record that trusts whatever wrote it is not a check. A hand-edited manifest, or a future second
producer, meets the same refusals.
"""

from __future__ import annotations

__all__ = ["validate_draft_pool"]


def validate_draft_pool(
    draft_pool_size: int | None,
    draft_rank: int | None,
    pool_predictor_score: float | None,
    pool_succeeded: int | None = None,
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
    # Imported here, not at module top: render_manifest re-exports this function from its
    # bottom line, so a top-level import back into it is a cycle that crashes whenever THIS
    # module is imported first (proved by a cold `import gtm_core.render_manifest_pool`).
    from .render_manifest import ManifestError

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

    # C5 — how many of the requested pool actually came back.
    if pool_succeeded is None:
        return
    if not all(pool_present):
        raise ManifestError(
            f"pool_succeeded={pool_succeeded!r} is set with no pool membership to attach it to "
            "— it counts members of a pool this manifest does not claim to be in"
        )
    if not 1 <= pool_succeeded <= draft_pool_size:
        raise ManifestError(
            f"pool_succeeded={pool_succeeded!r} is not within 1..{draft_pool_size} — a pool "
            "cannot return more members than were requested, and a pool where none came back is "
            "a reportable failure rather than a selection"
        )
    if draft_rank > pool_succeeded:
        raise ManifestError(
            f"draft_rank={draft_rank!r} exceeds pool_succeeded={pool_succeeded!r} — the winner "
            "was ranked against members that never arrived, so the selection is not what it says"
        )
