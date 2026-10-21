"""Which campaigns a page is about — resolved once, before anything is rendered.

``--campaign <slug>`` answered only one question ("this named campaign"), so the two
questions an operator actually asks — *what is running right now?* and *what does the
whole profile look like?* — were answered by hand, by remembering which slugs were live
and typing them into a comma list. A remembered slug list is a number nobody checked.

This module turns a scope into a :class:`Scope`: the slugs, the string
``model.scope_to_campaign`` already accepts, the output filename, and the phrase the
views use to describe themselves. It resolves **only slugs it read from the manifests**,
so ``scope_to_campaign`` and the unknown-slug refusal in ``render.render_dashboard`` are
unchanged and still the single validation point.

**Deliberately not here: aggregation.** Choosing a set of campaigns is not the same as
being able to add them up — see ``format._agree`` and the per-tile rules in
``views_status``. A scope that resolves cleanly can still contain a tile that must refuse.
"""

from __future__ import annotations

from dataclasses import dataclass

from gtm_core.prospect_lede import LIVE_STATUSES

#: Manifest ``status`` values that mean "this campaign is live work" — literally
#: ``prospect_lede.LIVE_STATUSES``, the same set ``campaigns_dashboard`` feeds through
#: ``go_live`` to decide ``state == "active"``. Imported, not re-typed, so there is only
#: one place to keep in sync. Anything else (``"list too small"``, ``"not set up"``,
#: blank) is out of ``open`` and is NAMED when excluded, never silently dropped.
OPEN_STATUSES = LIVE_STATUSES

MODES = ("campaign", "open", "all")


@dataclass(frozen=True)
class Scope:
    """What one page is about. ``slugs`` empty + ``stem`` None means the profile rollup."""

    mode: str
    slugs: tuple[str, ...]
    #: Comma string handed to ``scope_to_campaign``; also the value it stores as
    #: ``campaign_scope``, so ``render`` can compare the two for identity. Normalised
    #: here (no stray whitespace) so that comparison cannot fail on formatting alone.
    csv: str
    #: Output filename stem, or None for the profile-wide page. One slug keeps its own
    #: stable name so an existing bookmark or redirect stub still lands.
    stem: str | None
    #: How the views refer to this scope in prose ("this campaign", "these 2 campaigns").
    label: str
    #: Campaigns excluded from ``open`` and why — reported to the operator, never dropped.
    excluded: tuple[tuple[str, str], ...] = ()

    @property
    def is_scoped(self) -> bool:
        return bool(self.slugs)


def _label(mode: str, slugs: tuple[str, ...]) -> str:
    if len(slugs) == 1:
        return "this campaign"
    if mode == "open":
        return f"the {len(slugs)} open campaigns"
    return f"these {len(slugs)} campaigns"


def _stem(mode: str, slugs: tuple[str, ...]) -> str | None:
    if mode == "open":
        return "open"
    if len(slugs) == 1:
        return slugs[0]
    # Sorted, so re-rendering the same selection overwrites rather than accumulating pages.
    return "campaigns-" + "+".join(sorted(slugs))


def resolve(mode: str, campaign: str | None, campaigns: list[dict]) -> Scope:
    """Resolve a scope against the campaign manifests.

    ``campaigns`` is any list of dicts carrying ``slug`` and ``status`` — both
    ``campaigns_dashboard._load_manifests`` and the shaped ``build_campaigns`` output
    qualify, so a freshness check can resolve a scope without building the whole model.

    Raises ``SystemExit`` on an empty result. An empty page is the failure this whole
    module exists to prevent: it looks exactly like a campaign with nothing in it.
    """
    if mode not in MODES:  # pragma: no cover - argparse choices already refuse this
        raise SystemExit(f"unknown scope {mode!r} — one of {', '.join(MODES)}")

    if mode == "all":
        return Scope("all", (), "", None, "the whole profile")

    if mode == "campaign":
        slugs = tuple(s.strip() for s in str(campaign or "").split(",") if s.strip())
        if not slugs:
            raise SystemExit(
                "--scope campaign needs --campaign <slug>[,<slug>...]. To render every "
                "campaign at once use --scope all; for the ones marked active, --scope open."
            )
        # Unknown slugs are NOT filtered here. `scope_to_campaign` returns the model
        # unscoped when it cannot match one, and `render` turns that into a refusal —
        # one validation point, with the error message that already explains the fix.
        return Scope(
            "campaign", slugs, ",".join(slugs), _stem("campaign", slugs), _label("campaign", slugs)
        )

    known = [c for c in campaigns if c.get("slug")]
    live = tuple(
        c["slug"] for c in known if str(c.get("status", "")).strip().lower() in OPEN_STATUSES
    )
    excluded = tuple(
        (c["slug"], str(c.get("status", "")).strip() or "no status declared")
        for c in known
        if c["slug"] not in live
    )
    if not live:
        listed = "; ".join(f"{s} ({why})" for s, why in excluded) or "no manifests found"
        # Every option is a command the reader can run as written; editing the manifest is
        # the LAST one, because the person reading this is usually not the one who owns it.
        raise SystemExit(
            f"--scope open matched no campaign — {listed}. `open` shows only campaigns marked "
            f"as running: a manifest whose top-level `status` is one of "
            f"{', '.join(sorted(OPEN_STATUSES))}. To see everything now, re-run with "
            "--scope all. To show one campaign, use --scope campaign --campaign <slug>. To "
            'mark a campaign as running, set status = "active" in '
            "content/<profile>/plans/campaigns/<slug>.campaign.toml."
        )
    return Scope("open", live, ",".join(live), _stem("open", live), _label("open", live), excluded)
