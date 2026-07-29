"""Contract test — the prospect skill actually references the bulk-mode tools.

Bulk-mode discovery (docs/prds/2026-07-19-bulk-discovery-explorium.md) is spread across
several files: the skill's body_template.md / generated SKILL.md, its
discovery-and-budget.md reference, the gtm_core.prospects_import extractor, and the
cockpit CSV receiver. This test is the drift guard — if any of those loses its pointer
to the others (e.g. a future edit strips the bulk-mode section, or the ingest/finalize
CLI commands get renamed without updating the docs that tell the skill to call them),
this fails instead of the gap surfacing only at the next live bulk run.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO / "plugin" / "skills" / "prospect"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_body_template_references_bulk_mode_and_import_cli():
    body = _text(SKILL_DIR / "body_template.md")
    assert "Bulk run" in body
    assert "Bulk mode" in body
    assert "gtm_core.prospects_import ingest" in body
    assert "gtm_core.prospects_import finalize" in body
    assert "references/discovery-and-budget.md" in body


def test_skill_md_is_in_sync_with_bulk_mode_additions():
    # SKILL.md is codegen-derived from body_template.md (tests/lint/skill_codegen_sync.sh
    # is the byte-level sync gate); this just confirms the bulk-mode content actually made
    # it through codegen into what the running skill loads, not only the source template.
    skill_md = _text(SKILL_DIR / "SKILL.md")
    assert "Bulk run" in skill_md
    assert "gtm_core.prospects_import finalize" in skill_md


def test_discovery_and_budget_documents_the_bulk_funnel():
    ref = _text(SKILL_DIR / "references" / "discovery-and-budget.md")
    for tool in ("fetch-entities-statistics", "export-to-csv", "estimate-cost", "exclude_key"):
        assert tool in ref, f"bulk-mode reference doc lost its mention of {tool!r}"
    assert "prospects_import ingest" in ref
    assert "per_run_cap_usd" in ref  # the $ cap that gates the auto-scaled bulk export


def test_prospects_import_module_exposes_the_documented_cli_commands():
    # The docs promise `ingest` and `finalize` subcommands — confirm the module still has them,
    # so a refactor that renames/removes one fails here instead of at the next live bulk run.
    from gtm_core import prospects_import as pi

    assert callable(pi.ingest)
    assert callable(pi.finalize)


def test_cockpit_csv_receiver_is_wired():
    # The Phase 1.5 receiver: uploads helper + ingress handler + bot.py registration.
    from gtm_core import uploads

    assert callable(uploads.save_inbound_csv)
    assert callable(uploads.build_csv_prompt)

    ingress_src = _text(REPO / "cockpit" / "ingress.py")
    assert "async def on_document" in ingress_src
    assert "save_inbound_csv" in ingress_src

    bot_src = _text(REPO / "cockpit" / "bot.py")
    assert "self.on_document" in bot_src
    assert 'filters.Document.FileExtension("csv")' in bot_src
