"""agent.wizard — profile onboarding wizard: interview → knowledge files + PROFILE.md patch.

The wizard collects 6 answers from the operator (via Telegram) and writes the
profile's knowledge tree:

    profiles/<slug>/knowledge/
        voice.md         — brand voice, tone, dos/don'ts
        icp-personas.md  — ICP: industry, title, pain, trigger
        competitors.md   — top 3 competitors + one-liner differentiation each
        pillars.md       — 3–5 content pillars with rationale

It also patches two fields in PROFILE.md when wizard anchor markers are present:
    <!-- <<WIZARD:icp>> -->
    <!-- <<WIZARD:content_pillars>> -->

Hand-crafted profiles that lack these markers are NOT patched; callers should
surface a note to the operator to update those fields manually.

This module is pure logic — no Telegram or web imports. It is driven by
cockpit/bot.py (Telegram wizard).
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Question sequence
# ---------------------------------------------------------------------------

QUESTIONS: list[dict] = [
    {
        "id": "tagline",
        "prompt": (
            "Let's set up this profile. What's the *company name* and a one-line tagline?\n\n"
            "Example: _Acme — close the books in a day, not a fortnight._"
        ),
    },
    {
        "id": "voice",
        "prompt": (
            "Describe your *brand voice* in 3 adjectives, the overall tone, and one thing "
            "the writing should *never* sound like.\n\n"
            "Example: _Bold, precise, irreverent. Tone: sharp practitioner talk, not consultant-speak. "
            "Never: jargon-heavy, passive, or overly formal._"
        ),
    },
    {
        "id": "wedge",
        "prompt": (
            "What's your *wedge* — the one thing you do that no competitor does as well?\n\n"
            "Example: _We're the only ledger that reconciles sub-ledgers continuously — "
            "no month-end freeze required._"
        ),
    },
    {
        "id": "icp",
        "prompt": (
            "Describe your *Ideal Customer Profile*: industry, job title, top pain, "
            "and the trigger event that makes them act now.\n\n"
            "Example: _Mid-market retail, Financial Controller, pain: month-end close takes "
            "two weeks of manual reconciliation, trigger: an audit finding or a funding round._"
        ),
    },
    {
        "id": "competitors",
        "prompt": (
            # Fictional placeholder companies on purpose — this file ships publicly, so the
            # examples must never carry a real vendor's name or a competitive claim about them.
            "Name your *top 3 competitors* and one sentence on what makes you different from each.\n\n"
            "Example:\n"
            "1. Contoso — we reconcile continuously; Contoso batches at period close.\n"
            "2. Northwind — we're API-first; Northwind is spreadsheet-export-first.\n"
            "3. Globex — we self-serve in a day; Globex needs a six-week implementation."
        ),
    },
    {
        "id": "pillars",
        "prompt": (
            "List *3–5 content pillars* — the topics you want to own in your market.\n\n"
            "Example:\n"
            "1. Continuous close & real-time reconciliation\n"
            "2. Audit-readiness for finance teams\n"
            "3. Finance-engineering collaboration\n"
            "4. Controls without slowing the business"
        ),
    },
]

# Answer IDs in order — used for sequential navigation.
QUESTION_IDS = [q["id"] for q in QUESTIONS]


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@dataclass
class WizardState:
    profile: str
    answers: dict[str, str] = field(default_factory=dict)
    step: int = 0  # 0–5 = answering questions; 6 = at confirmation prompt

    @property
    def is_complete(self) -> bool:
        return self.step >= len(QUESTIONS)

    def current_question(self) -> dict | None:
        if self.step < len(QUESTIONS):
            return QUESTIONS[self.step]
        return None

    def record_answer(self, text: str) -> None:
        if self.step < len(QUESTIONS):
            self.answers[QUESTION_IDS[self.step]] = text.strip()
            self.step += 1


# ---------------------------------------------------------------------------
# Confirmation summary
# ---------------------------------------------------------------------------


def build_summary(state: WizardState) -> str:
    """Return a formatted preview of what will be written."""
    lines = [f"*Profile:* {state.profile}\n"]
    labels = {
        "tagline": "Company / tagline",
        "voice": "Brand voice",
        "wedge": "Wedge",
        "icp": "ICP",
        "competitors": "Competitors",
        "pillars": "Content pillars",
    }
    for i, qid in enumerate(QUESTION_IDS, 1):
        answer = state.answers.get(qid, "(no answer)")
        label = labels.get(qid, qid)
        lines.append(f"*{i}. {label}:*\n{answer}\n")
    lines.append("Reply *yes* to save, or *edit N* (e.g. _edit 3_) to revise a specific answer.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# File writers
# ---------------------------------------------------------------------------


def _dedent(text: str) -> str:
    return textwrap.dedent(text).strip()


def _scaffold_product_knowledge(
    profiles_root: Path, state: WizardState, products: list[dict]
) -> None:
    """Write per-product icp-personas.md + market-scan-config.md stubs.

    Called only when the profile declares ≥2 products — meaning the products
    have genuinely different ICPs or scan topics and should override the shared
    profile-level knowledge pack. Stubs are seeded from the wizard's general ICP
    and competitors answers and marked for customization.

    Skips any product file that already exists (protects hand-crafted content).
    """
    icp = state.answers.get("icp", "")
    competitors = state.answers.get("competitors", "")

    for product in products:
        slug = product.get("slug", "")
        name = product.get("name", slug)
        if not slug:
            continue

        product_dir = profiles_root / state.profile / "products" / slug
        product_dir.mkdir(parents=True, exist_ok=True)

        icp_path = product_dir / "icp-personas.md"
        if not icp_path.exists():
            icp_path.write_text(
                _dedent(f"""
                    # {name} — ICP & Personas

                    > Wizard-generated stub — customize for {name} specifically.
                    > This file overrides `knowledge/icp-personas.md` for any {name}-bound skill.
                    > The shared buyer archetype lives at the profile level.

                    ## Primary ICP (starting point — refine for {name})
                    {icp}

                    ## TODO
                    - Add {name}-specific pain points, triggers, and success metrics.
                    - Add persona cards with scoring criteria for prospect prioritization.
                    - Add competitor context specific to {name}'s market.
                """)
                + "\n",
                encoding="utf-8",
            )

        scan_path = product_dir / "market-scan-config.md"
        if not scan_path.exists():
            scan_path.write_text(
                _dedent(f"""
                    # {name} — Market Scan Config

                    > Wizard-generated stub — customize for {name} specifically.
                    > This file overrides `knowledge/market-scan-config.md` for any {name}-bound skill.
                    > Global topics (cross-product) live at the profile level.

                    ## Core topics (fill in for {name})
                    - (add topics specific to {name}'s market and use cases)

                    ## Competitors to monitor
                    {competitors}

                    ## Sources
                    - (add {name}-specific sources: blogs, communities, Product Hunt categories)

                    ## TODO
                    - Replace with {name}-specific topics, competitors, and sources.
                    - See `knowledge/market-scan-config.md` for global topics to inherit from.
                """)
                + "\n",
                encoding="utf-8",
            )


def write_knowledge_files(profiles_root: Path, state: WizardState) -> None:
    """Write voice.md, icp-personas.md, competitors.md, pillars.md.

    Overwrites existing profile-level knowledge files. Never touches files
    outside ``profiles/<slug>/knowledge/`` or ``profiles/<slug>/products/<slug>/``.

    When the profile declares ≥2 products, also scaffolds per-product
    icp-personas.md and market-scan-config.md stubs — skipping any that already
    exist (protects hand-crafted content). This wires in the product→profile
    fallback that ``gtm_core.resolve_knowledge_file`` implements at runtime.
    """
    knowledge_dir = profiles_root / state.profile / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    voice = state.answers.get("voice", "")
    wedge = state.answers.get("wedge", "")
    icp = state.answers.get("icp", "")
    competitors = state.answers.get("competitors", "")
    pillars = state.answers.get("pillars", "")

    # voice.md
    (knowledge_dir / "voice.md").write_text(
        _dedent(f"""
            # Brand Voice

            > Wizard-generated — edit freely.

            ## Summary
            {voice}

            ## Wedge / differentiation
            {wedge}

            ## Dos and don'ts
            - Write like a practitioner, not a vendor.
            - Lead with insight, not product features.
            - Never use jargon that obscures meaning.
        """)
        + "\n",
        encoding="utf-8",
    )

    # icp-personas.md
    (knowledge_dir / "icp-personas.md").write_text(
        _dedent(f"""
            # ICP Personas

            > Wizard-generated — edit freely.

            ## Primary persona
            {icp}

            ## Notes
            - Use these details for radar scoring (pain/trigger weighting).
            - Studio copy should speak directly to the top pain and trigger.
        """)
        + "\n",
        encoding="utf-8",
    )

    # competitors.md
    (knowledge_dir / "competitors.md").write_text(
        _dedent(f"""
            # Competitors

            > Wizard-generated — edit freely.

            ## Overview
            {competitors}

            ## Notes
            - Studio copy should reference at least one differentiation angle per draft.
            - Keep this file current as the competitive landscape shifts.
        """)
        + "\n",
        encoding="utf-8",
    )

    # pillars.md
    (knowledge_dir / "pillars.md").write_text(
        _dedent(f"""
            # Content Pillars

            > Wizard-generated — edit freely.

            ## Pillars
            {pillars}

            ## Notes
            - Radar scoring should weight items that map to these pillars.
            - Each week's plan should cover at least 2–3 distinct pillars.
        """)
        + "\n",
        encoding="utf-8",
    )

    # When the profile declares ≥2 products, scaffold per-product stubs so the
    # resolve_knowledge_file() product→profile fallback has something to override.
    from agent.profiles import load_products  # local import avoids circular risk

    products = load_products(profiles_root, state.profile)
    if len(products) >= 2:
        _scaffold_product_knowledge(profiles_root, state, products)


# ---------------------------------------------------------------------------
# PROFILE.md patcher
# ---------------------------------------------------------------------------

_ICP_MARKER = "<!-- <<WIZARD:icp>> -->"
_PILLARS_MARKER = "<!-- <<WIZARD:content_pillars>> -->"

# Matches the block immediately following a marker up to the next blank line.
_AFTER_MARKER_RE = re.compile(r"(?<=\n)((?:(?!\n\n).)+)", re.DOTALL)


def _replace_after_marker(text: str, marker: str, replacement: str) -> tuple[str, bool]:
    """Replace the content block immediately after ``marker`` with ``replacement``.

    Returns (new_text, patched). If ``marker`` is not found, returns (text, False).

    The block ends at the next blank line (``\\n\\n``) or EOF. We search for the
    blank line starting from the marker itself (not from after the first newline)
    so that a blank line immediately following the marker is correctly detected.
    """
    idx = text.find(marker)
    if idx == -1:
        return text, False

    after_marker = idx + len(marker)
    # Skip the newline immediately after the marker.
    if after_marker < len(text) and text[after_marker] == "\n":
        after_marker += 1

    # Search for the next blank line starting from the marker position so we
    # don't overshoot a \n\n that begins at the marker's own trailing newline.
    end = text.find("\n\n", idx + len(marker))
    if end == -1:
        end = len(text)

    new_text = text[:after_marker] + replacement + "\n" + text[end:]
    return new_text, True


def patch_profile_md(profiles_root: Path, state: WizardState) -> bool:
    """Patch ``icp:`` and ``content_pillars:`` in PROFILE.md via anchor markers.

    Returns True if at least one marker was found and patched. Returns False
    (and logs a note) when neither marker is present — callers should surface
    this to the operator for hand-crafted profiles that predate the wizard.
    """
    profile_path = profiles_root / state.profile / "PROFILE.md"
    if not profile_path.exists():
        return False

    text = profile_path.read_text(encoding="utf-8")

    icp = state.answers.get("icp", "")
    pillars_raw = state.answers.get("pillars", "")

    # Build a compact YAML-ish icp block.
    icp_block = f"icp_summary: |\n  {icp}"

    # Build a pillars list — normalise leading numbers/bullets.
    pillar_lines = [
        re.sub(r"^\s*[\d\-\*\.]+\s*", "", ln).strip()
        for ln in pillars_raw.splitlines()
        if ln.strip()
    ]
    if pillar_lines:
        pillars_block = "content_pillars:\n" + "\n".join(f"  - {p}" for p in pillar_lines)
    else:
        pillars_block = "content_pillars: []  # fill in manually"

    text, patched_icp = _replace_after_marker(text, _ICP_MARKER, icp_block)
    text, patched_pillars = _replace_after_marker(text, _PILLARS_MARKER, pillars_block)

    if patched_icp or patched_pillars:
        profile_path.write_text(text, encoding="utf-8")
        return True
    return False


# ---------------------------------------------------------------------------
# Knowledge status (used by web.py /knowledge-status)
# ---------------------------------------------------------------------------

_KNOWLEDGE_FILES = ("voice.md", "icp-personas.md", "competitors.md", "pillars.md")


def knowledge_status(profiles_root: Path, profile: str) -> dict:
    """Return a dict showing which knowledge files exist.

    Response shape:
        {
            "voice": bool, "icp": bool, "competitors": bool, "pillars": bool,
            "complete": bool,  # True when all four profile-level files present
            "per_product": {   # populated when the profile declares products
                "<slug>": {"icp": bool, "market_scan": bool},
                ...
            }
        }

    ``complete`` is based on profile-level files only — per-product files are
    optional overrides and do not gate completeness.
    """
    knowledge_dir = profiles_root / profile / "knowledge"
    presence: dict = {
        "voice": (knowledge_dir / "voice.md").is_file(),
        "icp": (knowledge_dir / "icp-personas.md").is_file(),
        "competitors": (knowledge_dir / "competitors.md").is_file(),
        "pillars": (knowledge_dir / "pillars.md").is_file(),
    }
    presence["complete"] = all(v for k, v in presence.items() if k != "complete")

    from agent.profiles import load_products  # local import avoids circular risk

    products = load_products(profiles_root, profile)
    per_product: dict[str, dict] = {}
    for p in products:
        slug = p.get("slug", "")
        if not slug:
            continue
        product_dir = profiles_root / profile / "products" / slug
        per_product[slug] = {
            "icp": (product_dir / "icp-personas.md").is_file(),
            "market_scan": (product_dir / "market-scan-config.md").is_file(),
        }
    presence["per_product"] = per_product
    return presence
