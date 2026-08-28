"""Guards for the LinkedIn + Syften capture importer.

The importer turns read passages that already live in the content tree into verified
customer-voice evidence records. The key risks are: (1) assigning a passage to the wrong demand
claim and inflating breadth, (2) importing our own reply framing as customer voice, and
(3) duplicating records on re-run.
"""

from __future__ import annotations

from pathlib import Path

from gtm_core.voc import capture as cap
from gtm_core.voc import evidence as ev

# --- claim mapper ------------------------------------------------------------------- #


def test_claim_mapper_assigns_risk_keywords_to_access_control_claim():
    assert (
        cap.claim_for_text(
            "Governing an AI agent well isn't about avoiding a fine; it's being able to prove customers can trust it."
        )
        == "agent-access-control-is-a-disclosed-risk"
    )


def test_claim_mapper_assigns_identity_keywords_to_identity_claim():
    assert (
        cap.claim_for_text(
            "When Agent A tells Agent B to process a refund, who proves the instruction came from Agent A?"
        )
        == "agent-identity-frameworks-are-nascent"
    )


def test_claim_mapper_falls_back_when_no_claim_scores_high():
    # Generic market noise should not be forced onto a demand claim.
    assert cap.claim_for_text("Interesting week for AI platforms in general.") == cap.FALLBACK_CLAIM


# --- LinkedIn parser ---------------------------------------------------------------- #


LINKEDIN_SAMPLE = """# LinkedIn reply — Alex Chen, "Authorisation is the hard part"

## Voice capture

<!-- voc:customer-voice -->
**Customer voice — what the poster said**
- Who: Alex Chen · CISO · Example Bank
- Source: screenshot + pasted comments · 2026-07-24
- Core claim (their words, one line): every autonomous agent needs deterministic authorisation before it can act.
- Verbatim: "The winners will be the ones making agents accountable, not smarter."
- Topic / pillar: agent authorization / AI Trust

<!-- voc:bd-focus -->
**BD focus — what we said back**
- Stance: agree-and-extend
- Core contribution (one line): accountability requires per-agent identity.
"""


def test_linkedin_parser_extracts_customer_voice_block_only():
    path = Path("/tmp/linkedin-reply-alex-chen-20260724.md")
    path.write_text(LINKEDIN_SAMPLE, encoding="utf-8")
    try:
        recs = cap.parse_linkedin(path)
    finally:
        path.unlink()

    assert len(recs) == 1
    rec = recs[0]
    assert rec.entity == "Alex Chen"
    assert rec.verbatim == "The winners will be the ones making agents accountable, not smarter."
    assert rec.speaker == "customer-voice"
    assert rec.verified is True
    assert rec.source_id == cap.SOURCE_LINKEDIN
    assert rec.claim_id == "agent-access-control-is-a-disclosed-risk"
    assert rec.date == "2026-07-24"
    assert rec.url == path.resolve().as_uri()


def test_linkedin_parser_ignores_files_without_customer_voice_block():
    path = Path("/tmp/linkedin-reply-no-block.md")
    path.write_text("# Reply\n\nJust our framing.\n", encoding="utf-8")
    try:
        assert cap.parse_linkedin(path) == []
    finally:
        path.unlink()


# --- Syften digest parser ----------------------------------------------------------- #


SYFTEN_SAMPLE = """# Syften digest — Week 2026-31

**Generated:** 2026-07-28

## What the market is talking about

Some prose.

## Signals worth acting on

### Signal 1 — H — Identity and delegation are now a published problem

`agentwatch` published a series on [agentwatch.example](https://agentwatch.example/):

> "when one agent hands a task to another, nothing in the handoff proves who actually asked for it"

**For us:** directly quotable.

### Signal 2 — M — Regulatory clock keeps ticking

`policywonk` notes that [EU AI Act Art. 50](https://example.com/ai-act) applies soon.

No blockquote here.

## ⚠ Name-collision traps in this window

Ignore this section.
"""


def test_syften_parser_extracts_blockquote_records_only():
    path = Path("/tmp/syften-digest-2026-07-28.md")
    path.write_text(SYFTEN_SAMPLE, encoding="utf-8")
    try:
        recs = cap.parse_syften_digest(path)
    finally:
        path.unlink()

    assert len(recs) == 1
    rec = recs[0]
    assert rec.entity == "agentwatch"
    assert rec.url == "https://agentwatch.example/"
    assert rec.date == "2026-07-28"
    assert "nothing in the handoff proves" in rec.verbatim
    assert rec.speaker == "customer-voice"
    assert rec.verified is True
    assert rec.source_id == cap.SOURCE_SYFTEN
    assert rec.claim_id == "agent-identity-frameworks-are-nascent"


def test_syften_parser_returns_empty_when_no_signals_section():
    path = Path("/tmp/syften-empty.md")
    path.write_text("# No signals\n\nNothing to see.\n", encoding="utf-8")
    try:
        assert cap.parse_syften_digest(path) == []
    finally:
        path.unlink()


# --- dedup / idempotency ------------------------------------------------------------ #


def test_new_records_excludes_already_captured_passages():
    existing = [
        ev.EvidenceRecord(
            claim_id="agent-access-control-is-a-disclosed-risk",
            verbatim="The winners will be the ones making agents accountable, not smarter.",
            url="file:///tmp/linkedin-reply-alex-chen-20260724.md",
            date="2026-07-24",
            entity="Alex Chen",
            speaker="customer-voice",
            source_id=cap.SOURCE_LINKEDIN,
            verified=True,
        )
    ]
    parsed = [
        ev.EvidenceRecord(
            claim_id="agent-access-control-is-a-disclosed-risk",
            verbatim="The winners will be the ones making agents accountable, not smarter.",
            url="file:///tmp/linkedin-reply-alex-chen-20260724.md",
            date="2026-07-24",
            entity="Alex Chen",
            speaker="customer-voice",
            source_id=cap.SOURCE_LINKEDIN,
            verified=True,
        ),
        ev.EvidenceRecord(
            claim_id="agent-identity-frameworks-are-nascent",
            verbatim="who proves the instruction came from Agent A?",
            url="https://agentwatch.example/",
            date="2026-07-28",
            entity="agentwatch",
            speaker="customer-voice",
            source_id=cap.SOURCE_SYFTEN,
            verified=True,
        ),
    ]
    fresh = cap.new_records(parsed, existing)
    assert len(fresh) == 1
    assert fresh[0].entity == "agentwatch"


# --- CLI ---------------------------------------------------------------------------- #


def test_cli_dry_run_reports_counts_without_writing(tmp_path: Path):
    profile_dir = tmp_path / "content" / "acme"
    linkedin_dir = profile_dir / "linkedin"
    linkedin_dir.mkdir(parents=True)
    linkedin_dir.joinpath("linkedin-reply-alex-chen-20260724.md").write_text(
        LINKEDIN_SAMPLE, encoding="utf-8"
    )

    store = profile_dir / "plans" / "market-intelligence" / "evidence.jsonl"
    store.parent.mkdir(parents=True)
    store.write_text("", encoding="utf-8")

    # Dry run must report counts without writing anything.
    assert cap.main(["--profile", "acme", "--repo-root", str(tmp_path)]) == 0
    assert store.read_text(encoding="utf-8") == ""


def test_cli_write_appends_records(tmp_path: Path):
    profile_dir = tmp_path / "content" / "acme"
    linkedin_dir = profile_dir / "linkedin"
    linkedin_dir.mkdir(parents=True)
    linkedin_dir.joinpath("linkedin-reply-alex-chen-20260724.md").write_text(
        LINKEDIN_SAMPLE, encoding="utf-8"
    )

    store = profile_dir / "plans" / "market-intelligence" / "evidence.jsonl"
    store.parent.mkdir(parents=True)

    assert cap.main(["--profile", "acme", "--repo-root", str(tmp_path), "--write"]) == 0
    records = ev.load(store)
    assert len(records) == 1
    assert records[0].entity == "Alex Chen"

    # Re-running must be idempotent.
    assert cap.main(["--profile", "acme", "--repo-root", str(tmp_path), "--write"]) == 0
    records = ev.load(store)
    assert len(records) == 1
