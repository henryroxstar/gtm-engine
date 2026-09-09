from __future__ import annotations

from pathlib import Path

from ..prospects_consolidate import _prospects_dir

TABS = (
    # Worklist first on purpose: it answers "where does each account stand", which is what
    # an operator opens this page to do. The four panels after it describe the campaign.
    ("worklist", "The worklist"),
    ("status", "Where things stand"),
    ("who", "Who we're emailing"),
    ("what", "What we're saying"),
    ("learn", "What we'll learn"),
    ("ops", "Operator notes"),
)

PAGE_NAME = "email_campaign_status.html"

#: The tenant's persona axis, from ``knowledge/voice.md``. Reproduced here as the page's
#: reference copy so a reader can see the rule the copy is written against without opening
#: the knowledge pack. Seven seats are defined; the automated resolver recognises three
#: (see :data:`SEAT_COVERAGE`) — that gap is the reason so many recipients read "unknown".
PERSONA_AXIS = (
    (
        "CEO / Founder",
        "the trust gap stalling an enterprise or partner deal",
        "close the logo your agents keep getting stuck on",
    ),
    (
        "CTO / Head of Platform",
        "identity and policy rebuilt per framework, burning 2–4 engineers",
        "one identity layer across your frameworks — ship, don't build",
    ),
    (
        "CISO / Head of Security",
        "cannot prove what agents did or who authorised them",
        "verifiable identity, tamper-evident audit, policy per action",
    ),
    (
        "Head of AI / Applied AI",
        "can build agents, cannot safely run them at scale",
        "reach production without the risk team stopping you",
    ),
    (
        "Chief Data Officer / DPO",
        "an agent on regulated data is a reportable breach",
        "delegation bound to the consenting human, auditor-ready",
    ),
    (
        "Head of Partnerships",
        "roadmap depends on partner agents you cannot verify",
        "trust that travels with the partner's agent",
    ),
    (
        "Compliance / Audit",
        "no provenance or attribution to show a regulator",
        "provenance from agent #1, not retrofitted at audit",
    ),
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
        "contacted, asked us not to write again, and — the largest group — outside the "
        "jurisdictions this profile is allowed to email at all.",
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
        "note": "the closest published comparator to this campaign's own audience",
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

#: The comparator the target is judged against — same ICP shape as this campaign.
PRIMARY_BENCHMARK = BENCHMARKS[0]


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
#: `tests/contracts/test_dashboard_freshness.py` asserts every path the model actually
#: opens matches one of these, so the two cannot drift quietly.
INPUT_GLOBS = (
    "plans/campaigns/*.campaign.toml",
    "history.jsonl",
    "prospects/latest.json",
    "prospects/sequences/cells.toml",
    "prospects/sequences/*.md",
    "prospects/sequences/*.csv",
    "prospects/sequences/.pool/master-list.csv",
    "prospects/sequences/.pool/sequence-stats.json",
    "prospects/sequences/.pool/sequence-state.json",
    "prospects/sequences/.pool/lint-*.json",
    "prospects/imports/*.csv",
    "prospects/prospects-*-hubspot.csv",
    "accounts/*/prospects-*-outreach-*.md",
    "accounts/*/email-*.md",
)


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
