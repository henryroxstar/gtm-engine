from __future__ import annotations

from pathlib import Path

from ..prospects_consolidate import _prospects_dir

#: PS20 Phase 2 — the five tabs, by question, in reading order (PRD Phase 2). Overview first:
#: this page is read by a founder and a founding AE, so the worklist-first order is superseded.
TABS: tuple[tuple[str, str], ...] = (
    ("overview", "Overview"),
    ("accounts", "Accounts"),
    ("emails", "Emails"),
    ("results", "Results"),
    ("ops", "Operator notes"),
)
#: A tab's label by id — how an in-page sentence names a tab, so a rename cannot strand it.
TAB_LABELS: dict[str, str] = dict(TABS)


#: Operator notes: collapsed `<details>` groups in reading order, `(id, summary, block ids)`.
#: "Numbers that need a look" opens itself when it has something to show.
OPS_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "numbers",
        "Numbers that need a look",
        ("cross-check", "reconciliation", "shared-sequences", "unlinked", "list-vs-provider"),
    ),
    (
        "before-sending",
        "Before sending starts",
        (
            "sent",
            "re-push",
            "holding-up",
            "nothing-outstanding",
            "load-files",
            "ceiling-tile",
            "compliance",
        ),
    ),
    ("sending-setup", "Sending setup", ("setup-tiles", "sequence-table", "forecast")),
    (
        "list-quality",
        "List quality",
        ("pool", "roster-notes", "segment-mix", "needs-address", "finding-new-people"),
    ),
    (
        "email-quality",
        "Email quality",
        ("subjects", "opening-lines", "capability-spread", "judge-notes", "checks-detail"),
    ),
    (
        "experiment-design",
        "Experiment design",
        ("varies", "grid", "readable-difference", "benchmarks", "experiment-notes"),
    ),
    ("replies", "Replies and opt-outs", ("inbound-health",)),
    ("maintenance", "Maintenance", ("maintenance-lines",)),
)

#: Every `data-section` id a tab may render — a SUBSET rule: a declared section with nothing
#: to show is absent. A card with no declared home is how the page sprawled one finding at a
#: time (PRD root cause 1), so a new section is an edit here.
#: Enforced by tests/contracts/test_dashboard_ps20_structure.py.
SECTIONS: dict[str, frozenset[str]] = {
    "overview": frozenset({"lede", "campaign-lines", "accounts-funnel", "contacts-by-status"}),
    "accounts": frozenset({"filter", "account-tiles", "account-table"}),
    "emails": frozenset({"email-table", "hand-sent", "packs-list"}),
    "results": frozenset(
        {
            "results-figures",
            "campaign-results",
            "when-we-know",
            "small-numbers",
            "learnings",
            "can-answer",
        }
    ),
    "ops": frozenset(
        {g for g, _s, _b in OPS_GROUPS} | {b for _g, _s, bs in OPS_GROUPS for b in bs}
    ),
}

PAGE_NAME = "email_campaign_status.html"

#: PS20 (``health.page_warnings``) — sending figures older than this are flagged stale on
#: the page-wide warning strip, regardless of whether the reconciliation itself agrees.
FIGURES_MAX_AGE_DAYS = 2

#: PS20 P3.5 — a bounce rate STRICTLY above this percentage draws the ``bounce-rate`` risk
#: pill (``RISK_REASONS`` below). ``aggregate.bounce_rate`` returns a percentage (e.g. 3.1,
#: not 0.031), so this constant is a percentage too, and the comparison is a plain ``>``.
BOUNCE_RISK_PCT = 3

#: PS20 P1.5 — the only reasons a WARN colour may give (``data-warn``). Warn means one thing:
#: a number on this page cannot be trusted. Closed, so a new warning has to name itself here
#: before it can render (``tests/contracts/test_dashboard_colour_reasons.py``).
WARN_REASONS: tuple[str, ...] = (
    "checks-untrusted",  # the lede: the checks never ran, or their report is stale/unreadable
    "records-disagree",  # two records of one thing disagree: figures vs lists, list vs provider
    "figures-old",  # the sending figures are older than FIGURES_MAX_AGE_DAYS, or undated
    "unreadable",  # the sending figures could not be read at all
    "tripwire",  # the filter's row data disagrees with the figures it filters
    "unmapped",  # a routed row whose status this build cannot name
)

#: PS20 P1.5 — the only reasons a RISK colour may give (``data-risk``): a genuine risk to a
#: person or to the brand. States, design facts, verdicts and ages stay neutral.
#: ``out-of-market`` and ``competitor`` colour no element yet. Both are kept because a live
#: source reaches the page: ``prospect_status_receipt.fit_failure_reason`` feeds the lede's
#: "take them out of the sending tool" line — ``outside-market`` from PS15's
#: ``account_hold_reason``, ``competitor`` from the ledger's ``category_relation``. (The
#: ``competitor-adjacent`` hold trigger never does: it folds into "Waiting on you".)
RISK_REASONS: tuple[str, ...] = (
    "opted-out",
    "do-not-contact",
    "unread-reply",  # a reply this system could not read may be an opt-out
    "bounce-rate",
    "do-not-load",
    "re-push",  # starting now would send copy that was already replaced
    "blocking-check",
    "compliance",
    "out-of-market",
    "competitor",
)

#: Which persona-axis seats the automated resolver can actually detect today.
#: Widened 2026-08-20 (hook-coverage H3) from three buckets to six, sized to where
#: recipients actually are: the old "exec" bucket held 156 of 397 live rows — CEOs, CPOs,
#: data leaders and 28 CTOs that a bare "president" cue had swept in. `finops` and
#: `partnership` resolve as personas but have no seat and so are absent here: 0 and 1
#: recipients respectively across the whole pool.
SEAT_COVERAGE = {
    "security": "CISO / Risk / Data & Compliance",
    "cto": "CTO / Head of Platform",
    "ceo": "CEO / Founder",
    "architect": "Enterprise / Cloud Architect · CIO",
    "ai-platform": "Chief AI Officer / Head of AI Platform",
    "product": "CPO / Head of Product",
}


def resolve_seat_coverage(
    profile: str | None = None,
    profiles_root: Path | None = None,
    content_root: Path | None = None,
) -> dict[str, str]:
    """Derive seat coverage from the tenant's resolved role vocabulary.

    When the tenant defines custom seats, returns those seats and their display labels
    derived from the seat's personas. Falls back to SEAT_COVERAGE for default seats
    unless the tenant overrides their personas.
    """
    from .. import role_vocabulary
    from ..role_vocabulary.defaults import DEFAULT_SEAT_RULES

    p_root = profiles_root
    if p_root is None and content_root is not None:
        sibling = content_root.parent / "profiles"
        if sibling.is_dir():
            p_root = sibling

    try:
        vocab = role_vocabulary.load(profile=profile, profiles_root=p_root)
    except Exception:
        return dict(SEAT_COVERAGE)

    if getattr(vocab, "source", None) == "built-in default":
        return dict(SEAT_COVERAGE)

    default_personas = {s: p for s, p, _ in DEFAULT_SEAT_RULES}
    out: dict[str, str] = {}

    for seat, personas, _ in vocab.seat_rules:
        if seat in SEAT_COVERAGE and personas == default_personas.get(seat):
            out[seat] = SEAT_COVERAGE[seat]
        elif personas:
            out[seat] = " / ".join(p.replace("-", " ").title() for p in personas)
        else:
            out[seat] = seat.replace("-", " ").title()
    return out or dict(SEAT_COVERAGE)


#: Plain-English gloss for each pipeline bucket. The bucket names are internal; a reader
#: asked "what is awaiting verification — a qualified account, research, or compliance?"
#: and could not tell from the label, which is the whole problem.
FUNNEL_GLOSS = (
    (
        "ready",
        "Good to email now",
        "The address is verified deliverable, the person is inside a market we are allowed to "
        "email, they are not on the do-not-contact list, they have not been emailed before, and "
        "their row renders cleanly into the copy. Note what this does NOT mean: it is not a "
        "statement that the account was scored against the ICP, that buying-intent signals were "
        "found, that a dossier exists, or that an email has been drafted for them. Those happen "
        "later and are tracked per campaign.",
    ),
    (
        "needs_verification",
        "Address not confirmed",
        "We have the right person at the right company, but nobody has proven the inbox exists. "
        "A deliverability gap, not a research or qualification gap.",
    ),
    (
        "blocked",
        "Bad address",
        "The address failed verification outright. Emailing it would bounce and damage the "
        "reputation of the sending domain.",
    ),
    (
        "excluded_dnc_or_sent",
        "Off limits",
        "Everything we are holding but cannot mail. Three different reasons live here: already "
        "contacted, asked us not to write again, and outside the jurisdictions this profile is "
        "allowed to email at all.",
    ),
)


#: Published cold-email reply-rate reference points, researched 2026-08-19.
#:
#: Three warnings travel with these numbers and are rendered on the page, because a
#: benchmark quoted without them is worse than none.
#:
#: 1. **The denominator is not standardised.** Belkins divides replies by *emails sent*;
#:    most others divide by *people contacted*. A three-touch sequence makes those differ
#:    by roughly 3x, which is most of the gap between 0.45% and 3.4% — not a real
#:    difference in performance.
#: 2. **Every source is a cold-email vendor** reporting on its own platform's traffic.
#:    Self-selected populations, and an interest in the answer.
#: 3. **The widely-cited 8.5% (Backlinko/Pitchbox, 12M emails) is link-building and
#:    blogger outreach, not B2B sales** — a different population answering a different
#:    request. It is excluded here rather than shown, since quoting it as a sales
#:    benchmark is a category error.
#:
#: The retired "5-18%" band traced to Woodpecker's "up to 18% for advanced personalization"
#: — a single vendor's best case presented as a range ceiling. It was never a band.
BENCHMARKS = (
    {
        "label": "SaaS selling to enterprise",
        "low": 0.018,
        "high": 0.018,
        "basis": "per person contacted",
        "source": "Cleanlist meta-analysis, Feb 2026",
        "url": "https://www.cleanlist.ai/blog/2026-02-18-cold-email-response-rate-statistics",
        "note": "",
    },
    {
        "label": "SaaS selling to SaaS",
        "low": 0.024,
        "high": 0.024,
        "basis": "per person contacted",
        "source": "Cleanlist meta-analysis, Feb 2026",
        "url": "https://www.cleanlist.ai/blog/2026-02-18-cold-email-response-rate-statistics",
        "note": "",
    },
    {
        "label": "all industries, average",
        "low": 0.031,
        "high": 0.0343,
        "basis": "mixed / per email sent",
        "source": "Woodpecker 20M+ emails (Jun 2026) and Cleanlist (Feb 2026)",
        "url": "https://woodpecker.co/blog/cold-email-statistics/",
        "note": "Woodpecker reports 3.43%, down from 5.1% in 2024",
    },
    {
        "label": "all industries, per email sent",
        "low": 0.0045,
        "high": 0.0051,
        "basis": "per email sent",
        "source": "Belkins, 7.5M emails sent in 2025 (updated Jun 2026)",
        "url": "https://belkins.io/blog/cold-email-response-rates",
        "note": "0.45% global, 0.51% US — the strictest denominator of any source found",
    },
    {
        "label": "considered good / top decile",
        "low": 0.05,
        "high": 0.10,
        "basis": "per person contacted",
        "source": "Woodpecker (Jun 2026)",
        "url": "https://woodpecker.co/blog/cold-email-statistics/",
        "note": "10%+ described as excellent; 18% is one vendor's personalisation best case",
    },
)


def dashboard_path(profile: str, content_root: Path | None = None) -> Path:
    return _prospects_dir(profile, content_root).parent / PAGE_NAME


def page_title(m: dict) -> str:
    """ "Email Campaign Status — <product>". Falls back to the profile when no campaign
    declares a product, so a tenant that never set one still gets a usable title rather
    than a dangling dash."""
    scoped = m.get("campaign_scope")
    for c in m["campaigns"]["campaigns"]:
        if c.get("product"):
            base = f"Email Campaign Status — {c['product']}"
            # A scoped page and the profile-wide page are different claims about the same
            # numbers; if the title cannot tell them apart, a screenshot of one reads as the
            # other. That is how "0 of 990" was read as this campaign's figure.
            return f"{base} · {c.get('title') or scoped}" if scoped else base
    return f"Email Campaign Status — {m['profile']}"


#: Everything the model reads, as globs relative to ``<content_root>/<profile>/``.
#:
#: This list is the freshness check's subject: `page_inputs` digests what it resolves AND
#: records the globs, so a manifest or roster export that APPEARS after a render is caught
#: too. Keep it in step with `sources.py` / `model.py` — a file read but not listed here is
#: a file whose change can silently pass the check, which is worse than not checking.
#: `tests/contracts/test_dashboard_reads_are_inventoried.py` asserts every path the model
#: actually opens (or, for a few name/stat-only reads it lists directly, matches) one of
#: these, so the two cannot drift quietly.
INPUT_GLOBS = (
    "plans/campaigns/*.campaign.toml",
    "history.jsonl",
    # PS20 T1.9 — read by `outcomes.read_outcomes` (via `build_model`), at the profile's
    # top level like `history.jsonl`, not under `prospects/`.
    "outcomes.jsonl",
    # PS20 T1.9 — `prospect_readiness.load_readiness` (via `build_model`); also top-level,
    # not under `prospects/`.
    "preflight/latest.json",
    "prospects/latest.json",
    "prospects/sequences/cells.toml",
    "prospects/sequences/*.md",
    "prospects/sequences/*.csv",
    "prospects/sequences/.pool/master-list.csv",
    "prospects/sequences/.pool/sequence-stats.json",
    "prospects/sequences/.pool/sequence-state.json",
    "prospects/sequences/.pool/lint-*.json",
    # PS20 T1.9 — `prospects_dashboard.build_status` counts this hold queue (via
    # `build_model`). A dedicated entry, not a `*.csv` wildcard: `sequences/*.csv` cannot
    # cross into the hidden `.pool/` subdirectory (glob `*` never crosses `/`).
    "prospects/sequences/.pool/needs-verification.csv",
    # PS20 T1.9 review round 2 — `prospect_readiness.input_paths`/`fingerprints` (reached
    # through `load_readiness` via `build_model`) STATS this (mtime/size), never opens it —
    # `prospect_readiness.suppression_ledger`'s path. Flips the lede's readiness state, so a
    # change here changes the page with nothing ever calling `open()` on it — a `_confine`-
    # style gap the content-digest read of `suppression.csv` closes more strongly than the
    # stat-only check it complements.
    "prospects/sequences/.pool/suppression.csv",
    "prospects/evals/lanes-state.jsonl",
    # PS20 T1.9 — `roster.judge_queue` (via `model.roster_model`) reads the newest of these.
    "prospects/evals/retarget-queue-*.jsonl",
    # PS20 T1.9 — the eval-labeling round `health.eval_labeler` links to, and the markdown
    # sheet it counts pre-filled/blind rows from (read by the model since Task 9, so
    # `render_html` opens nothing). Date-shaped, to match `health._LABELER_RE`/the exact
    # ``sheet-{stamp}.md`` name `eval_labeler` builds — a bare `*` glob also matched an
    # unrelated `sheet-<date>-ship30-labeled.md` on a live tenant, which is read by nothing
    # here and so flagged false staleness on every edit to it.
    "prospects/evals/labeler-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-*.html",
    "prospects/evals/sheet-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md",
    # PS20 T1.9 — the lane hold sheet `health.review_sheet` finds for the lede's link (PS15).
    # Date-shaped, to match `health._SHEET_RE` exactly: a bare `hold-*.csv` also matched
    # `hold-decisions-<date>-filled.csv` on a live tenant (`gtm_core.lanes.decisions`'s own
    # answers file, read by nothing here) and flagged it as false staleness on every edit.
    "prospects/evals/hold-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].csv",
    "prospects/evals/hold-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].html",
    "prospects/imports/*.csv",
    "prospects/prospects-*-hubspot.csv",
    "accounts/*/prospects-*-outreach-*.md",
    "accounts/*/email-*.md",
)

#: Named (not globbed) files under ``resolve_profiles_root()/<profile>`` — a different root
#: than everything in :data:`INPUT_GLOBS`, which is why `page_inputs` tracks them separately
#: (its ``profile_files`` section). ``PROFILE.md`` feeds `email_compliance.read_target_markets`,
#: reached through `model.market_split` -> `prospects_consolidate.suppression._resolve_market_gate`.
PROFILE_FILES = ("PROFILE.md",)


def input_globs(profile: str, content_root: Path | None = None) -> tuple[Path, list[str]]:
    """``(root, globs)`` for :func:`gtm_core.page_inputs.write_inventory`.

    The manifests' own ``roster_globs`` are appended, because a campaign names its roster
    exports with whatever basename its run produced — ``prospects-*-hubspot.csv`` covers
    the convention and nothing guarantees a manifest follows it. Found by the freshness
    contract test on its first run: a roster CSV outside the convention was read by the
    page and absent from the inventory, so editing it would have passed ``--check-fresh``
    — a green result that is a false claim, which is worse than not checking at all.
    """
    from ..campaigns_dashboard import _load_manifests

    globs = list(INPUT_GLOBS)
    for m in _load_manifests(profile, content_root):
        globs += [f"prospects/{g}" for g in (m.get("roster_globs") or [])]
    return _prospects_dir(profile, content_root).parent, sorted(set(globs))
