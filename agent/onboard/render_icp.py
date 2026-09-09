from __future__ import annotations


def _derive_icp_signals(icp: dict) -> list[str]:
    """Pull candidate buying signals from persona pain_points + goals.

    Dedupes (case-insensitive) while preserving first-seen order, capped at 5 —
    enough for a Gate A "hit N of 5" rubric line without overwhelming it.
    """
    signals: list[str] = []
    seen: set[str] = set()
    for persona in icp.get("personas", []):
        for signal in [*persona.get("pain_points", []), *persona.get("goals", [])]:
            signal = signal.strip()
            key = signal.lower()
            if signal and key not in seen:
                seen.add(key)
                signals.append(signal)
    return signals[:5]


def _render_icp_md(icp: dict, settings: dict | None = None) -> str:
    settings = settings or {}
    persona_sections = []
    for p in icp.get("personas", []):
        pains = "\n".join(f"  - {x}" for x in p.get("pain_points", []))
        goals = "\n".join(f"  - {x}" for x in p.get("goals", []))
        persona_sections.append(
            f"### {p['title']}\n\n**Pain points:**\n{pains}\n\n**Goals:**\n{goals}"
        )
    personas_text = "\n\n".join(persona_sections)
    verticals = ", ".join(icp.get("verticals", [])) or "_Not industry-specific_"
    size = icp.get("company_size", "Unknown")

    segments = settings.get("segment_mix", "your primary segment")

    derived_signals = _derive_icp_signals(icp)
    if derived_signals:
        total = len(derived_signals)
        threshold = max(
            1, round(total * 0.6)
        )  # ~60% of surfaced signals, matching the 3-of-5 default
        gate_a = (
            f"**Gate A — ICP qualification (hit ≥ {threshold} of {total}):** "
            f"{', '.join(derived_signals)}."
        )
    else:
        gate_a = (
            "**Gate A — ICP qualification:** buying signals every genuine fit shows — "
            "refine once personas are filled in."
        )

    return f"""# ICP & Persona Cards

## Target company profile
- **Verticals:** {verticals}
- **Company size:** {size}

## Personas

{personas_text}

## Scoring & gates  *(the `prospect` skill reads this)*

> Starting rubric below — tuned defaults derived from your ICP that you can adjust. The generic
> scoring **machinery** (order of ops, heat axis, Tier-A ordering, per-run distribution, default
> thresholds) lives in `plugin/skills/prospect/references/gates-and-scoring.md`; the **criteria
> below are yours** and override any example shown there. If the rubric grows large, move it to
> its own `knowledge/buyer-intent-signals.md` and link it here.

**Segments:** {segments}. **Publish ≥ 6; Tier-A ≥ 12 of 18.**

{gate_a}

**Gate B — Disqualifiers (fail ALL to survive):** wrong stage or setting, no reachable champion,
a hard constraint you cannot meet.

**Gate C — Firmographic floor:** company size {size} · HQ or material ops in a PROFILE `target_market`.

**Rubric (out of 18):** fit-to-persona (6) · buying-signal strength (6) · reachability (3) ·
recency of trigger (3) — adjust the weights once you know which signals actually predict a buy.
The "why now" trigger list that feeds the recency axis lives in [`buyer-journey.md`](buyer-journey.md) §A.

**Heat (topic-intent, after the rubric):** intent topics live in `market-scan-config.md`; +2 either
feed / +1 both, capped at 18. No feed connected → heat = 0; Tier-A is fit + 🆕 new-in-role + 🔥 recency.
"""


def _render_competitors_md(competitors: list) -> str:
    if not competitors:
        return "# Competitive Landscape\n\n_No competitors identified from source._\n"

    # Group by bucket (the tiering) while preserving first-seen bucket order. Competitors with no
    # bucket fall into a trailing "Other" group so nothing is dropped.
    buckets: dict[str, list] = {}
    for c in competitors:
        bucket = (c.get("bucket") or "").strip() or "Other"
        buckets.setdefault(bucket, []).append(c)

    # A single flat table (the old shape) when nothing is bucketed — no empty "Other" heading noise.
    if list(buckets.keys()) == ["Other"]:
        rows = "\n".join(f"| {c['name']} | {c.get('differentiator', '')} |" for c in competitors)
        return f"""# Competitive Landscape

| Competitor | How we differ |
|---|---|
{rows}
"""

    sections: list[str] = []
    for bucket, members in buckets.items():
        rows = "\n".join(f"| {c['name']} | {c.get('differentiator', '')} |" for c in members)
        sections.append(f"## {bucket}\n\n| Competitor | How we differ |\n|---|---|\n{rows}")
    body = "\n\n".join(sections)
    return f"# Competitive Landscape\n\n{body}\n"


def _render_pillars_md(pillars: list) -> str:
    items = "\n".join(f"- {p}" for p in pillars)
    return f"# Content Pillars\n\n{items}\n"


# Things only the seller can confirm — the same list whether the brain inferred a journey (added
# to whatever it flagged) or left a skeleton for the operator to fill.
_DEFAULT_OPERATOR_CONFIRM = [
    "Real sales-cycle length per segment (weeks vs. quarters?).",
    "Which trigger has historically preceded a won deal — re-rank the list from real pipeline.",
    "Who actually signs vs. who champions — does it escalate above the primary buyer?",
    "The objections actually heard in the room — replace the inferred ones with your real top-3.",
]


def _render_buyer_journey_md(icp: dict, buyer_journey: dict | None) -> str:
    """Render knowledge/buyer-journey.md — the 'why now / where in the journey / what moves them'
    layer. When the draft carries a derived ``buyer_journey`` it renders the full trigger table,
    journey map, and per-persona deltas; otherwise it ships a persona-derived skeleton the operator
    fills in. Either way the inferred/unknown items are surfaced as ⚠️ confirm-with-your-team prompts
    (never a raw <...> placeholder — see test_no_angle_bracket_placeholders_ship)."""
    if buyer_journey and (buyer_journey.get("triggers") or buyer_journey.get("stages")):
        return _render_buyer_journey_full(icp, buyer_journey)
    return _render_buyer_journey_skeleton(icp)


def _render_buyer_journey_full(icp: dict, bj: dict) -> str:
    parts: list[str] = [
        "# Buyer Journey, Triggers & Objections\n",
        "> Derived by profile-onboard from your personas and the industry context in your source.\n"
        "> This is **inferred** reasoning, not site-stated fact — review the ⚠️ items in section D\n"
        "> before you rely on it. Persona pains/goals live in "
        "[`icp-personas.md`](icp-personas.md); this file is the connective tissue between them.\n",
    ]

    triggers = bj.get("triggers", [])
    if triggers:
        rows = "\n".join(
            f"| {i} | {t.get('event', '')} | {t.get('activates', '')} | "
            f"{t.get('predicts', '')} | {t.get('detect_via', '')} |"
            for i, t in enumerate(triggers, 1)
        )
        parts.append(
            '## A. Trigger events ("why now") — ranked by how strongly each predicts a buy\n\n'
            "| # | Trigger | Most activates | Predicts | Detect via |\n"
            "|---|---|---|---|---|\n"
            f"{rows}\n\n"
            "> **Guardrail:** a trigger times the touch and picks the angle — the outreach cites a\n"
            "> **public** fact (a funding round, a job posting, a product launch), never "
            '"our data shows\n> you\'re in-market." Never build a cold touch around a specific '
            "misfortune (an accident,\n> outage, layoff, or audit finding)."
        )

    stages = bj.get("stages", [])
    if stages:
        primary = bj.get("primary_persona", "the primary buyer")
        rows = "\n".join(
            f"| {s.get('stage', '')} | {s.get('buyer_question', '')} | {s.get('angle', '')} | "
            f"{s.get('proof', '')} | {s.get('objection', '')} | {s.get('pillar', '')} |"
            for s in stages
        )
        parts.append(
            f"## B. Buyer journey — {primary}\n\n"
            "| Stage | Question in their head | Angle that moves them | Proof they need | "
            "Objection to pre-empt | Pillar / asset |\n"
            "|---|---|---|---|---|---|\n"
            f"{rows}\n\n"
            "**Content-stage rule:** early stages are **1-to-many** (posts, articles, carousels — "
            "category education); later stages are **1-to-few / 1-to-1** (decks, ROI models, "
            "tailored outreach). Don't send a late-stage asset to an early-stage audience."
        )

    deltas = bj.get("persona_deltas", [])
    if deltas:
        blocks = "\n\n".join(
            f"### {d.get('persona', '')}\n{d.get('notes', '_Add where this persona differs._')}"
            for d in deltas
        )
        parts.append("## C. Other personas — where their journey differs\n\n" + blocks)

    confirm = list(bj.get("operator_confirm", [])) or list(_DEFAULT_OPERATOR_CONFIRM)
    confirm_items = "\n".join(f"- ⚠️ {c}" for c in confirm)
    parts.append(
        '## D. Confirm with your sales team (the "ask", not the "research")\n\n'
        "These are inferred above — only your team truly knows them. Correct them on first review:\n\n"
        f"{confirm_items}"
    )

    return "\n\n".join(parts) + "\n"


def _render_buyer_journey_skeleton(icp: dict) -> str:
    persona_blocks = []
    for p in icp.get("personas", []):
        pains = ", ".join(p.get("pain_points", [])) or "_none captured_"
        goals = ", ".join(p.get("goals", [])) or "_none captured_"
        persona_blocks.append(
            f"### {p['title']}\n"
            f"- **Pains (from icp-personas.md):** {pains}\n"
            f"- **Goals (from icp-personas.md):** {goals}\n"
            "- **Why-now triggers:** _What events make this persona buy now? "
            "(funding, hiring wave, new-in-role, regulation, peer adoption, an RFP.)_\n"
            "- **Journey stages & objections:** _The questions they ask and the objections they "
            "raise as they move from unaware to decision — add your own._\n"
        )
    personas_text = "\n".join(persona_blocks) if persona_blocks else "_No personas captured yet._"
    confirm_items = "\n".join(f"- ⚠️ {c}" for c in _DEFAULT_OPERATOR_CONFIRM)

    return (
        "# Buyer Journey, Triggers & Objections\n\n"
        "> Skeleton built from your personas. The pains and goals below are real (from your draft),\n"
        "> but the triggers, journey stages, and objections are things only your sales team knows —\n"
        "> so this is a fill-in template, not a guess. Persona cards: "
        "[`icp-personas.md`](icp-personas.md).\n\n"
        "## A. Per-persona — why-now, journey, objections\n\n"
        f"{personas_text}\n"
        '## B. Confirm with your sales team (the "ask", not the "research")\n\n'
        f"{confirm_items}\n"
    )
