from __future__ import annotations

from datetime import date
from pathlib import Path

from .knowledge import _ensure_knowledge_frontmatter, _supplement_from_template
from .render_icp import (
    _render_buyer_journey_md,
    _render_competitors_md,
    _render_icp_md,
    _render_pillars_md,
)
from .render_product import (
    _render_audience_psych_stub,
    _render_case_studies_stub,
    _render_per_product_md,
    _render_product_md,
    _render_voice_bans_txt,
)
from .render_profile import (
    _render_brand_notes_md,
    _render_company_md,
    _render_market_scan_config_md,
    _render_profile_md,
    _render_voice_md,
)


def render(
    draft: dict,
    *,
    today: date | None = None,
    template_knowledge_dir: Path | None = None,
) -> dict[str, str]:
    """Convert a validated ProfileDraft into a dict of relative path → file content.

    Does NOT write to disk. Returns a flat mapping for stage() and diff() to consume.
    File layout mirrors the live profiles/<slug>/ structure.

    Every managed knowledge topic is stamped with lifecycle frontmatter (source/refreshed/review)
    so the profile passes ``knowledge_meta_check`` without a manual ``knowledge_meta seed``.
    ``today`` is injectable for deterministic tests (defaults to today's date).

    When ``template_knowledge_dir`` is given (the CLI passes the live ``_template/knowledge``), the
    ``_template`` starter topics render() doesn't derive from the draft are added too, so skills
    that read them don't silently degrade on a new profile.
    """
    files: dict[str, str] = {}

    company = draft["company"]
    voice = draft["voice"]
    icp = draft["icp"]
    competitors = draft.get("competitors", [])
    pillars = draft.get("pillars", [])
    products = draft.get("products", [])
    brand = draft.get("brand", {})

    files["PROFILE.md"] = _render_profile_md(
        company, voice, icp, pillars, products, brand, draft.get("settings")
    )
    files["knowledge/voice.md"] = _render_voice_md(voice)
    files["knowledge/icp-personas.md"] = _render_icp_md(icp, draft.get("settings"))
    files["knowledge/competitors.md"] = _render_competitors_md(competitors)
    files["knowledge/buyer-journey.md"] = _render_buyer_journey_md(icp, draft.get("buyer_journey"))
    files["knowledge/pillars.md"] = _render_pillars_md(pillars)
    files["knowledge/company.md"] = _render_company_md(company, draft)
    files["knowledge/brand-notes.md"] = _render_brand_notes_md(brand)
    files["knowledge/market-scan-config.md"] = _render_market_scan_config_md(products)
    files["knowledge/product.md"] = _render_product_md(products)
    files["knowledge/voice-bans.txt"] = _render_voice_bans_txt(voice)
    files["knowledge/case-studies.md"] = _render_case_studies_stub(company)
    files["knowledge/audience-psychology.md"] = _render_audience_psych_stub(icp)

    for product in products:
        slug = product["slug"]
        files[f"products/{slug}/PRODUCT.md"] = _render_per_product_md(product)
        files[f"products/{slug}/knowledge/icp-personas.md"] = _render_icp_md(
            icp, draft.get("settings")
        )
        files[f"products/{slug}/knowledge/market-scan-config.md"] = _render_market_scan_config_md(
            [product]
        )

    # Bring in the _template starters this profile would otherwise be missing (skills-degrade gap),
    # then stamp lifecycle frontmatter on every managed topic still lacking it (gate-fail gap). Order
    # matters: supplement first (its files arrive pre-stamped), then ensure only fills the draft-
    # derived files, never clobbering a starter's own freshness date.
    if template_knowledge_dir is not None:
        _supplement_from_template(files, template_knowledge_dir)
    _ensure_knowledge_frontmatter(files, draft, today or date.today())

    return files
