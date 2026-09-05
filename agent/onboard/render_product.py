from __future__ import annotations


def _render_product_md(products: list) -> str:
    sections: list[str] = []
    for p in products:
        use_cases = "\n".join(f"- {u}" for u in p.get("use_cases", []))
        sections.append(
            f"## {p['name']}\n\n"
            f"{p.get('description', '')}\n\n"
            f"**Technical notes:** {p.get('technical_notes', '_Not captured._')}\n\n"
            f"### Use cases\n{use_cases}"
        )
    return "# Product Overview\n\n" + "\n\n---\n\n".join(sections) + "\n"


def _render_per_product_md(product: dict) -> str:
    use_cases = "\n".join(f"- {u}" for u in product.get("use_cases", []))
    caps = ", ".join(product.get("capabilities", [])) or "_none_"
    source_pages = "\n".join(f"- {p}" for p in product.get("source_pages", [])) or "_none_"

    return f"""# {product["name"]} — Product File

## Description
{product.get("description", "")}

## Technical notes
{product.get("technical_notes", "_Not captured from source._")}

## Capabilities
{caps}

## Use cases
{use_cases}

## Source pages crawled
{source_pages}
"""


def _render_voice_bans_txt(voice: dict) -> str:
    """The machine-readable ban list, consumed by the prose linter (see the ban-file
    convention in profiles/*/knowledge/voice-bans.txt). Purely re-formats voice.ban_list,
    which is already-extracted evidence from the source text — no fabrication risk.
    """
    header = (
        "# Profile-specific banned words and phrases, seeded from the voice interview or "
        "extraction.\n"
        "# One entry per line. Case-insensitive, matched as a whole word or phrase.\n"
        "# Loaded by the prose linter via its --ban-file flag, pointed at this file.\n"
        "# These are ADDED to the linter's built-in AI-tells list (delve, seamless, em dashes, "
        "etc.) -\n"
        "# put here only the words and phrases this company must never say. Lines starting with "
        "# are comments.\n"
        "#\n"
        "# Reconcile with knowledge/voice.md: that file explains the voice in prose and links "
        "here for\n"
        "# the machine-checkable list, so the list lives in exactly one place (no drift).\n"
        "#\n"
        "# Add or remove entries any time - this list is a starting point, not a final one.\n"
    )
    ban_list = voice.get("ban_list", [])
    return header + "\n".join(ban_list) + "\n"


def _render_case_studies_stub(company: dict) -> str:
    """Case studies are experience, not evidence — the onboarding brain has no real
    customer story to draw from. Rendering a plausible-sounding example here would mean
    inventing a fake customer, so this ships as an honest fill-in-the-blank template
    instead of fabricated content.
    """
    brand_name = company["brand_name"]
    return (
        f"# Case Studies — {brand_name}\n\n"
        "> These prove your claims in every draft. Only you have the real ones, so I left\n"
        "> a template instead of inventing customer stories.\n\n"
        "## Case study 1\n"
        "- **Customer:** _Add one customer win (company + role)._\n"
        "- **Problem:** _What was broken before you._\n"
        "- **What you did:** _The specific thing._\n"
        "- **Result:** _A number if you have one._\n\n"
        '_Add more below, or say "learn from my material" and point me at a doc._\n'
    )


def _render_audience_psych_stub(icp: dict) -> str:
    """Audience psychology sits between evidence and experience: the pain_points/goals on
    each persona ARE evidence already in the draft, so those get derived in full. Anything
    beyond that — emotional stakes, beliefs, a contrarian thesis — is interpretation the
    brain has no basis for, so it stays a skeleton the founder fills in, never invented.
    """
    method_ref = "docs/audience-psychology-method.md"
    persona_sections = []
    for p in icp.get("personas", []):
        pains = ", ".join(p.get("pain_points", [])) or "_none captured_"
        goals = ", ".join(p.get("goals", [])) or "_none captured_"
        persona_sections.append(
            f"### {p['title']}\n"
            f"- **Pain points (from icp-personas.md):** {pains}\n"
            f"- **Goals (from icp-personas.md):** {goals}\n"
            "- **Emotional stakes:** _What does this pain feel like day-to-day? Add your own._\n"
            "- **Believed but never said:** _What do they think but won't say out loud? Add your own._\n"
            "- **Contrarian thesis:** _The belief you're reframing. Add your own._\n"
            "- **Proof as belief-shift:** _Link the case-studies.md entry that earns this._\n"
        )
    personas_text = (
        "\n".join(persona_sections) if persona_sections else "_No personas captured yet._"
    )

    return (
        f"# Audience Psychology\n\n"
        f"> Skeleton built from `icp-personas.md` — pain points and goals are real, "
        f"drawn from your draft. Everything else here is a prompt for you, not a guess.\n"
        f"> Method: [`{method_ref}`](../../../{method_ref})\n\n"
        f"{personas_text}\n"
    )
