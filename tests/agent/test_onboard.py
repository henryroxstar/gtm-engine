# tests/agent/test_onboard.py
"""Unit tests for agent.onboard — pure Python functions (no live SDK calls).

SDK-calling functions (extract, extract_product) are tested with monkeypatched
claude_agent_sdk.query. Staging/promote tests use the cfg_isolated fixture that
redirects both content_root and profiles_root to tmp_path so they never touch
the real profiles/ directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

onboard = pytest.importorskip("agent.onboard", reason="agent.onboard not yet implemented")


# ── config / MCP wiring ──────────────────────────────────────────────────────


def test_config_has_firecrawl_field():
    from agent.config import Config

    cfg = Config.from_env(repo_root=REPO_ROOT)
    assert hasattr(cfg, "firecrawl_api_key"), "Config missing firecrawl_api_key"


def test_config_has_onboarding_cap_field():
    from agent.config import Config

    cfg = Config.from_env(repo_root=REPO_ROOT)
    assert hasattr(cfg, "onboarding_cap_usd"), "Config missing onboarding_cap_usd"


def test_mcp_firecrawl_absent_when_no_key():
    import dataclasses

    from agent.config import Config
    from agent.mcp_config import build_mcp_servers

    cfg = dataclasses.replace(Config.from_env(repo_root=REPO_ROOT), firecrawl_api_key=None)
    assert "firecrawl" not in build_mcp_servers(cfg, "test")


def test_mcp_firecrawl_present_when_key_set():
    import dataclasses

    from agent.config import Config
    from agent.mcp_config import build_mcp_servers

    cfg = dataclasses.replace(Config.from_env(repo_root=REPO_ROOT), firecrawl_api_key="fc-test-key")
    servers = build_mcp_servers(cfg, "test")
    assert "firecrawl" in servers
    assert servers["firecrawl"]["env"]["FIRECRAWL_API_KEY"] == "fc-test-key"


# ── Capability registry ───────────────────────────────────────────────────────


def test_capability_registry_is_frozenset():
    from gtm_core.capability_registry import KNOWN_CAPABILITIES

    assert isinstance(KNOWN_CAPABILITIES, frozenset)


def test_capability_registry_contains_known_slugs():
    from gtm_core.capability_registry import KNOWN_CAPABILITIES

    for slug in ("content-creation", "prospect", "outreach", "pre-sales"):
        assert slug in KNOWN_CAPABILITIES, f"{slug!r} missing from KNOWN_CAPABILITIES"


def test_capability_registry_no_spaces():
    from gtm_core.capability_registry import KNOWN_CAPABILITIES

    for slug in KNOWN_CAPABILITIES:
        assert " " not in slug, f"slug {slug!r} contains a space"


# ── slugify ───────────────────────────────────────────────────────────────────


def test_slugify_basic():
    from agent.onboard import slugify

    assert slugify("Acme Corp") == "acme-corp"


def test_slugify_numeric_brand():
    from agent.onboard import slugify

    assert slugify("3M Company") == "3m-company"


def test_slugify_collapses_punctuation():
    from agent.onboard import slugify

    assert slugify("Foo & Bar, Inc.") == "foo-bar-inc"


def test_slugify_trims_dashes():
    from agent.onboard import slugify

    assert slugify("  --Leading-- ") == "leading"


def test_slugify_long_name_truncated():
    from agent.onboard import slugify

    result = slugify("a" * 50)
    assert len(result) <= 40


def test_slugify_word_boundary_truncation():
    from agent.onboard import slugify

    slug = slugify("verylongcompanyname-boundary-truncation-at-forty")
    assert len(slug) <= 40
    assert not slug.endswith("-"), f"slug should not end with dash: {slug!r}"


def test_slugify_reserved_staging():
    from agent.onboard import slugify

    with pytest.raises(ValueError, match="reserved"):
        slugify(".staging")


def test_slugify_reserved_system():
    from agent.onboard import slugify

    with pytest.raises(ValueError, match="reserved"):
        slugify("_system")


def test_slugify_empty_raises():
    from agent.onboard import slugify

    with pytest.raises(ValueError):
        slugify("")


def test_slugify_all_punctuation_raises():
    from agent.onboard import slugify

    with pytest.raises(ValueError):
        slugify("---")


# ── ingest ────────────────────────────────────────────────────────────────────


def test_ingest_text_returns_as_is(cfg):
    from agent.onboard import ingest

    assert ingest("Hello world", "text", cfg) == "Hello world"


def test_ingest_md_file(tmp_path, cfg):
    from agent.onboard import ingest

    md = tmp_path / "company.md"
    md.write_text("# Acme\n\nWe make things.")
    result = ingest(str(md), "file", cfg)
    assert "Acme" in result


def test_ingest_pdf_file_with_no_extractable_text_raises(tmp_path, cfg):
    """PDF ingest calls pypdf; an image-only/blank PDF extracts no text, which is now a
    friendly ValueError (Task 5b) instead of a silent empty string flowing downstream.
    """
    import pypdf

    from agent.onboard import ingest

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=595, height=842)
    pdf_path = tmp_path / "doc.pdf"
    with open(pdf_path, "wb") as f:
        writer.write(f)

    with pytest.raises(ValueError, match="no readable text"):
        ingest(str(pdf_path), "file", cfg)


def test_ingest_unsupported_file_type_raises(tmp_path, cfg):
    from agent.onboard import ingest

    f = tmp_path / "data.xlsx"
    f.write_bytes(b"fake xlsx")
    with pytest.raises(ValueError, match="unsupported file"):
        ingest(str(f), "file", cfg)


def test_ingest_invalid_source_type_raises(cfg):
    from agent.onboard import ingest

    with pytest.raises(ValueError, match="source_type"):
        ingest("something", "ftp", cfg)


def test_ingest_url_requires_firecrawl_key(cfg, monkeypatch):
    """URL ingest raises RuntimeError when no firecrawl key is configured."""
    import dataclasses

    from agent.onboard import ingest

    no_key_cfg = dataclasses.replace(cfg, firecrawl_api_key=None)
    with pytest.raises(RuntimeError, match="FIRECRAWL_API_KEY"):
        ingest("https://example.com", "url", no_key_cfg)


def test_ingest_url_raises_when_cap_exceeded(cfg, monkeypatch):
    """URL ingest raises RuntimeError when onboarding_cap_usd is exceeded."""
    import dataclasses

    from agent.onboard import ingest

    capped_cfg = dataclasses.replace(cfg, firecrawl_api_key="fc-key", onboarding_cap_usd=0.01)
    monkeypatch.setattr("gtm_core.ingest._onboarding_month_spend", lambda cfg: 1.00)
    with pytest.raises(RuntimeError, match="cap"):
        ingest("https://example.com", "url", capped_cfg)


# ── extract ───────────────────────────────────────────────────────────────────

import asyncio as _asyncio

# Minimal valid ProfileDraft for monkeypatching the SDK response
_VALID_DRAFT = {
    "source": {"type": "text", "value": "sample", "crawled_pages": 0},
    "confidence": "medium",
    "company": {
        "name": "Acme Corp",
        "slug": "acme-corp",
        "brand_name": "Acme",
        "description": "We make enterprise widgets.",
        "markets": ["United States"],
        "social_handle": "https://linkedin.com/company/acme",
    },
    "voice": {
        "tone": "Direct and technical.",
        "principles": ["Short sentences.", "Active voice.", "Data-first."],
        "ban_list": ["synergy"],
        "examples": [],
    },
    "icp": {
        "personas": [{"title": "VP Eng", "pain_points": ["slow builds"], "goals": ["fast CI"]}],
        "verticals": ["SaaS"],
        "company_size": "50-500",
    },
    "competitors": [{"name": "RivalCo", "differentiator": "We are faster."}],
    "pillars": ["DevOps", "Platform engineering"],
    "products": [
        {
            "name": "AcmeDeploy",
            "slug": "acme-deploy",
            "flagship": True,
            "description": "A CI/CD platform for fast-moving teams.",
            "technical_notes": "Kubernetes-native.",
            "capabilities": ["prospect"],
            "use_cases": ["Zero-downtime deploys"],
            "source_pages": [],
            "references": [
                {"url": "https://example.com", "title": "Acme Deploy", "summary": "Overview."}
            ],
        }
    ],
    "brand": {"palette": ["#000000", "#FFFFFF"], "assets_note": "Monochrome."},
    "gaps": [],
}


def test_extract_returns_validated_dict(cfg, monkeypatch):
    """async extract() calls the SDK, parses JSON, validates, returns dict."""
    import json

    async def _mock_query(*args, **kwargs):
        from claude_agent_sdk import AssistantMessage, TextBlock

        yield AssistantMessage(
            content=[TextBlock(text=json.dumps(_VALID_DRAFT))], model="claude-sonnet-4-6"
        )

    monkeypatch.setattr("claude_agent_sdk.query", _mock_query)

    from agent.onboard import extract

    result = _asyncio.run(extract("some source text about Acme Corp", cfg))
    assert result["company"]["slug"] == "acme-corp"
    assert result["confidence"] == "medium"


def test_extract_strips_markdown_fences(cfg, monkeypatch):
    """extract() handles JSON wrapped in ```json fences."""
    import json

    async def _mock_query(*args, **kwargs):
        from claude_agent_sdk import AssistantMessage, TextBlock

        text = f"```json\n{json.dumps(_VALID_DRAFT)}\n```"
        yield AssistantMessage(content=[TextBlock(text=text)], model="claude-sonnet-4-6")

    monkeypatch.setattr("claude_agent_sdk.query", _mock_query)

    from agent.onboard import extract

    result = _asyncio.run(extract("some text", cfg))
    assert result["company"]["name"] == "Acme Corp"


def test_extract_rejects_invalid_json(cfg, monkeypatch):
    async def _mock_query(*args, **kwargs):
        from claude_agent_sdk import AssistantMessage, TextBlock

        yield AssistantMessage(
            content=[TextBlock(text="not json at all")], model="claude-sonnet-4-6"
        )

    monkeypatch.setattr("claude_agent_sdk.query", _mock_query)

    from agent.onboard import extract

    with pytest.raises(ValueError, match="JSON"):
        _asyncio.run(extract("some text", cfg))


def test_extract_rejects_missing_required_field(cfg, monkeypatch):
    """extract() raises ValueError when a required field is absent."""
    import json

    bad_draft = {k: v for k, v in _VALID_DRAFT.items() if k != "products"}

    async def _mock_query(*args, **kwargs):
        from claude_agent_sdk import AssistantMessage, TextBlock

        yield AssistantMessage(
            content=[TextBlock(text=json.dumps(bad_draft))], model="claude-sonnet-4-6"
        )

    monkeypatch.setattr("claude_agent_sdk.query", _mock_query)

    from agent.onboard import extract

    with pytest.raises(ValueError, match="products"):
        _asyncio.run(extract("some text", cfg))


def test_extract_rejects_unknown_capabilities(cfg, monkeypatch):
    """extract() raises ValueError for capability slugs not in KNOWN_CAPABILITIES."""
    import json

    draft_with_bad_cap = {
        **_VALID_DRAFT,
        "products": [{**_VALID_DRAFT["products"][0], "capabilities": ["unknown-cap-xyz"]}],
    }

    async def _mock_query(*args, **kwargs):
        from claude_agent_sdk import AssistantMessage, TextBlock

        yield AssistantMessage(
            content=[TextBlock(text=json.dumps(draft_with_bad_cap))], model="claude-sonnet-4-6"
        )

    monkeypatch.setattr("claude_agent_sdk.query", _mock_query)

    from agent.onboard import extract

    with pytest.raises(ValueError, match="unknown-cap-xyz"):
        _asyncio.run(extract("some text", cfg))


# ── render ────────────────────────────────────────────────────────────────────


def test_render_returns_expected_files():
    from agent.onboard import render

    files = render(_VALID_DRAFT)
    expected_paths = {
        "PROFILE.md",
        "knowledge/voice.md",
        "knowledge/icp-personas.md",
        "knowledge/competitors.md",
        "knowledge/pillars.md",
        "knowledge/company.md",
        "knowledge/brand-notes.md",
        "knowledge/market-scan-config.md",
        "knowledge/product.md",
        "knowledge/voice-bans.txt",
        "knowledge/case-studies.md",
        "knowledge/audience-psychology.md",
        "products/acme-deploy/PRODUCT.md",
        "products/acme-deploy/knowledge/icp-personas.md",
        "products/acme-deploy/knowledge/market-scan-config.md",
    }
    assert set(files.keys()) == expected_paths, (
        f"Unexpected: {set(files.keys()) - expected_paths}\n"
        f"Missing: {expected_paths - set(files.keys())}"
    )


def test_render_profile_md_contains_company_block():
    from agent.onboard import render

    profile = render(_VALID_DRAFT)["PROFILE.md"]
    assert "Acme Corp" in profile
    assert "acme-corp" in profile
    assert "acme-deploy" in profile


def test_render_voice_md_contains_principles():
    from agent.onboard import render

    voice = render(_VALID_DRAFT)["knowledge/voice.md"]
    assert "Short sentences." in voice
    assert "Active voice." in voice
    assert "synergy" in voice


def test_render_icp_contains_persona():
    from agent.onboard import render

    icp = render(_VALID_DRAFT)["knowledge/icp-personas.md"]
    assert "VP Eng" in icp
    assert "slow builds" in icp


def test_render_brand_notes_contains_palette():
    from agent.onboard import render

    brand_notes = render(_VALID_DRAFT)["knowledge/brand-notes.md"]
    assert "#000000" in brand_notes


def test_render_per_product_files():
    from agent.onboard import render

    files = render(_VALID_DRAFT)
    product_md = files["products/acme-deploy/PRODUCT.md"]
    assert "AcmeDeploy" in product_md
    assert "Kubernetes-native" in product_md


def test_render_market_scan_config_contains_references():
    from agent.onboard import render

    scan = render(_VALID_DRAFT)["knowledge/market-scan-config.md"]
    assert "https://example.com" in scan


def test_render_stamps_valid_lifecycle_frontmatter(tmp_path):
    """Every managed knowledge topic render() produces must pass knowledge_meta_check with no
    manual seed — the onboarding→lifecycle wiring that used to be missing (a fresh profile failed
    the gate until `knowledge_meta seed` was run by hand)."""
    from datetime import date

    from agent.onboard import render
    from gtm_core import knowledge_meta

    files = render(_VALID_DRAFT, today=date(2026, 1, 15))
    prof = tmp_path / "acme-corp"
    for rel, content in files.items():
        dest = prof / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")

    problems = knowledge_meta.check(tmp_path, ["acme-corp"])
    assert problems == [], problems


def test_render_frontmatter_injected_date_and_cadence():
    """refreshed comes from the injected `today` (deterministic); review uses the shipped
    per-topic default (company → 90d, voice → evergreen)."""
    from datetime import date

    from agent.onboard import render
    from gtm_core.knowledge_meta import parse_frontmatter

    files = render(_VALID_DRAFT, today=date(2026, 1, 15))

    company_meta, _ = parse_frontmatter(files["knowledge/company.md"])
    assert company_meta["refreshed"] == "2026-01-15"
    assert company_meta["review"] == "90d"
    assert company_meta["source"] == "manual"  # _VALID_DRAFT.source.type == "text"

    voice_meta, _ = parse_frontmatter(files["knowledge/voice.md"])
    assert voice_meta["review"] == "evergreen"


def test_render_frontmatter_source_from_url_provenance():
    """A URL-sourced onboarding stamps the crawled URL as provenance, not a bare 'manual'."""
    from agent.onboard import render
    from gtm_core.knowledge_meta import parse_frontmatter

    draft = {**_VALID_DRAFT, "source": {"type": "url", "value": "https://acme.example/"}}
    meta, _ = parse_frontmatter(render(draft)["knowledge/company.md"])
    assert meta["source"] == "https://acme.example/"


def test_render_supplements_missing_template_topics_without_clobbering(tmp_path):
    """With a _template/knowledge dir, render() adds the starters it doesn't derive from the draft
    (so skills don't silently degrade) — but never overwrites a draft-derived file with the
    template version."""
    from agent.onboard import render

    tk = tmp_path / "knowledge"
    tk.mkdir(parents=True)
    (tk / "hook-matrix.md").write_text(
        "---\nsource: manual\nrefreshed: 2026-07-08\nreview: evergreen\n---\n# Hook Matrix\n"
    )
    (tk / "voice.md").write_text(
        "---\nsource: manual\nrefreshed: 2026-01-01\nreview: evergreen\n---\n# TEMPLATE VOICE\n"
    )

    files = render(_VALID_DRAFT, template_knowledge_dir=tk)

    # a starter the draft doesn't produce is pulled in
    assert "knowledge/hook-matrix.md" in files
    assert "# Hook Matrix" in files["knowledge/hook-matrix.md"]
    # a draft-derived file wins over the template version
    assert "TEMPLATE VOICE" not in files["knowledge/voice.md"]
    assert "Short sentences." in files["knowledge/voice.md"]


def test_render_without_template_dir_omits_supplement():
    """Backward-compatible: render(draft) with no template dir produces exactly the draft-derived
    set (the supplement only enriches a real profiles/ tree via the CLI)."""
    from agent.onboard import render

    assert "knowledge/hook-matrix.md" not in render(_VALID_DRAFT)


# ── stage / diff / promote / cancel ──────────────────────────────────────────

import json  # needed for json.loads() in staging/promote tests


def test_stage_creates_staging_dir(cfg_isolated):
    from agent.onboard import render, stage

    files = render(_VALID_DRAFT)
    draft_id, staged_root = stage("acme-corp", files, cfg_isolated)
    assert staged_root.exists()
    assert (staged_root / "PROFILE.md").exists()
    assert (staged_root / ".onboard-meta.json").exists()


def test_stage_meta_contains_draft_id(cfg_isolated):
    from agent.onboard import render, stage

    files = render(_VALID_DRAFT)
    draft_id, staged_root = stage("acme-corp", files, cfg_isolated)
    meta = json.loads((staged_root / ".onboard-meta.json").read_text())
    assert meta["draft_id"] == draft_id
    assert meta["status"] == "staged"


def test_diff_new_profile_shows_all_added(cfg_isolated):
    from agent.onboard import diff, render, stage

    files = render(_VALID_DRAFT)
    draft_id, staged_root = stage("acme-corp", files, cfg_isolated)
    diffs = diff("acme-corp", staged_root, cfg_isolated)
    assert all(entry["old"] is None for entry in diffs.values())
    assert "PROFILE.md" in diffs


def test_diff_existing_profile_shows_changes(cfg_isolated):
    from agent.onboard import diff, render, stage

    # Create a "live" profile first
    live_dir = cfg_isolated.profiles_root / "acme-corp"
    live_dir.mkdir(parents=True, exist_ok=True)
    (live_dir / "PROFILE.md").write_text("# Old Profile\n")

    files = render(_VALID_DRAFT)
    draft_id, staged_root = stage("acme-corp", files, cfg_isolated)
    diffs = diff("acme-corp", staged_root, cfg_isolated)
    assert diffs["PROFILE.md"]["old"] == "# Old Profile\n"
    assert "Acme Corp" in diffs["PROFILE.md"]["new"]


def test_promote_copies_to_profiles_root(cfg_isolated):
    from agent.onboard import promote, render, stage

    files = render(_VALID_DRAFT)
    draft_id, staged_root = stage("new-company", files, cfg_isolated)
    live_dir = promote("new-company", draft_id, staged_root, _VALID_DRAFT, cfg_isolated)
    assert live_dir.exists()
    assert (live_dir / "PROFILE.md").exists()
    assert not staged_root.exists()


def test_promote_refuses_if_live_profile_exists(cfg_isolated):
    """promote() raises ValueError rather than overwriting a live profile."""
    from agent.onboard import promote, render, stage

    # Create an existing live profile
    live_dir = cfg_isolated.profiles_root / "existing-co"
    live_dir.mkdir(parents=True, exist_ok=True)
    (live_dir / "PROFILE.md").write_text("# Existing\n")

    files = render(_VALID_DRAFT)
    draft_id, staged_root = stage("existing-co", files, cfg_isolated)

    with pytest.raises(ValueError, match="already exists"):
        promote("existing-co", draft_id, staged_root, _VALID_DRAFT, cfg_isolated)

    # Staging dir should still be there (not auto-removed on error)
    assert staged_root.exists()


def test_promote_logs_to_system_history(cfg_isolated):
    """promote() appends an audit record to content/_system/history.jsonl."""
    from agent.onboard import promote, render, stage

    files = render(_VALID_DRAFT)
    draft_id, staged_root = stage("audit-co", files, cfg_isolated)
    promote("audit-co", draft_id, staged_root, _VALID_DRAFT, cfg_isolated)

    history_path = cfg_isolated.content_root / "_system" / "history.jsonl"
    assert history_path.exists()
    records = [json.loads(line) for line in history_path.read_text().splitlines() if line.strip()]
    assert any(r.get("event") == "onboard.promote" and r.get("slug") == "audit-co" for r in records)


def test_cancel_removes_staging(cfg_isolated):
    from agent.onboard import cancel, render, stage

    files = render(_VALID_DRAFT)
    _, staged_root = stage("acme-corp", files, cfg_isolated)
    assert staged_root.exists()
    cancel(staged_root)
    assert not staged_root.exists()


def test_staged_root_for_draft_id_found(cfg_isolated):
    from agent.onboard import _staged_root_for_draft_id, render, stage

    files = render(_VALID_DRAFT)
    draft_id, staged_root = stage("acme-corp", files, cfg_isolated)
    found = _staged_root_for_draft_id(draft_id, cfg_isolated)
    assert found == staged_root


def test_staged_root_for_draft_id_not_found(cfg_isolated):
    from agent.onboard import _staged_root_for_draft_id

    with pytest.raises(FileNotFoundError):
        _staged_root_for_draft_id("nonexistent-uuid-1234", cfg_isolated)
