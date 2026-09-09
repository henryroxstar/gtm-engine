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


# The digest is hand-written and its findings heading has drifted three times. Between
# 2026-08-11 and 2026-08-31 every digest used a heading the parser did not know, so capture
# silently imported ZERO Syften evidence for five weeks while each pull reported success and
# the brief reported every practitioner demand as "breadth 1, weak". These guard the shapes
# that were actually produced, so the next rename fails a test instead of a quarter's briefs.

SYFTEN_BOLD_NUMBERED = """# Syften social listening — 2026-08-31

## What practitioners actually said

**1. Agents cannot self-provision credentials.** From
[r/somechain](https://forum.example/r/somechain/comments/1a2b3c/your_agent_cannot_sign_up/)
(2026-08-25):

> "That one sentence is the whole problem with how AI agents get onchain data."

Closing prose that is not a quote.

**2. Tool approval has no binding to what runs.** dev.to, 2026-08-25
([toolnotes.example](https://toolnotes.example/blog/the-mcp-server-you-approved/)):

> "nothing in the protocol binds the definition I approved to what executes"

## Lane outcome

`ok` — window covered.
"""


SYFTEN_HASH_NUMBERED = """# Syften digest — 2026-08-11

## Findings

### 1. Delegated authority failing in production

**r/someforum — `Quiet-Example-4471`, 2026-08-04** ·
[thread](https://forum.example/r/someforum/comments/1abc/):

> "the agent silently fell back to application identity and nobody noticed for a week"

## Lane outcome

`ok`.
"""


def test_syften_parser_reads_the_bold_numbered_findings_format():
    path = Path("/tmp/syften-digest-2026-08-31.md")
    path.write_text(SYFTEN_BOLD_NUMBERED, encoding="utf-8")
    try:
        recs = cap.parse_syften_digest(path)
    finally:
        path.unlink()

    assert len(recs) == 2
    # Distinct entities are the whole point: breadth counts (source_id, entity) pairs, and a
    # parser that read both quotes but labelled them identically would still report breadth 1.
    assert {r.entity for r in recs} == {"r/somechain", "toolnotes.example"}
    assert all(r.source_id == cap.SOURCE_SYFTEN for r in recs)
    assert all(r.speaker == "customer-voice" and r.verified for r in recs)


def test_syften_parser_reads_the_hash_numbered_findings_format():
    path = Path("/tmp/syften-digest-2026-08-11.md")
    path.write_text(SYFTEN_HASH_NUMBERED, encoding="utf-8")
    try:
        recs = cap.parse_syften_digest(path)
    finally:
        path.unlink()

    assert len(recs) == 1
    # Attributed as "`handle`, YYYY-MM-DD" — a byline, not an attribution verb.
    assert recs[0].entity == "Quiet-Example-4471"


SYFTEN_HOUSE_VOCAB = """# Syften digest — 2026-08-31

## What practitioners actually said

**1. Competitor chatter dominates.** Of 22 `competitor` items, a Stack Overflow question
reported `AccessDeniedException` on `CreateMemory`, and Northwind AI is a `direct` row in our own
registry — see [`enterprise-signals-2026-08-18.md`](https://example.invalid/enterprise.md):

> "the vendor's own launch cadence is echoing through community accounts"
"""


def test_syften_parser_never_treats_house_vocabulary_as_a_speaker():
    """Backticked jargon, code identifiers and in-repo filenames are not people.

    Inventing an entity is worse than finding none: entity is what makes two records count as
    two independent sources, so a fake one raises a demand's confidence band on no evidence.
    """
    path = Path("/tmp/syften-digest-2026-08-31-vocab.md")
    path.write_text(SYFTEN_HOUSE_VOCAB, encoding="utf-8")
    try:
        recs = cap.parse_syften_digest(path)
    finally:
        path.unlink()

    assert len(recs) == 1
    assert recs[0].entity not in {
        "competitor",
        "direct",
        "AccessDeniedException",
        "CreateMemory",
        "enterprise-signals-2026-08-18.md",
    }
    # Unidentifiable speakers collapse onto the shared fallback rather than inventing one.
    assert recs[0].entity == "Syften organic corpus"


def test_syften_records_always_carry_a_url_so_the_store_can_load_them():
    """An empty url is silently fatal: `evidence._coerce` rejects the record on load.

    Such a record is written, vanishes on read, and is therefore "new" on every subsequent
    run — the store grows without ever gaining evidence. A signal citing no external link
    still has provenance: the digest file it came from.
    """
    path = Path("/tmp/syften-digest-2026-08-31-nolink.md")
    path.write_text(
        "# Syften — 2026-08-31\n\n"
        "## What practitioners actually said\n\n"
        "**1. Identity plumbing is eating engineering time.** A practitioner, no link given:\n\n"
        '> "It\'s spent on the plumbing, not the core logic."\n',
        encoding="utf-8",
    )
    try:
        recs = cap.parse_syften_digest(path)
        assert len(recs) == 1
        assert recs[0].url, "a record with no url is dropped by the store on load"
        # Round-trips through the store's own loader rather than merely being non-empty.
        assert ev._coerce(recs[0].__dict__) is not None
    finally:
        path.unlink()


def test_dedup_key_survives_the_repo_moving_on_disk():
    """A local artifact's absolute path is not its identity.

    The repo was renamed gtm-engine -> gtm-engine; every `file://` URL changed with it, so the
    same four LinkedIn passages re-imported under new keys on every capture run and the store
    accumulated duplicates silently. Only the content-root-relative path is stable.
    """
    before = ev.EvidenceRecord(
        claim_id="c",
        verbatim="A fictional line standing in for a real captured passage.",
        url="file:///Users/x/Developer/gtm-engine/content/example/linkedin/reply-a.md",
        date="2026-07-19",
        entity="Jordan Vale",
        speaker="customer-voice",
        source_id=cap.SOURCE_LINKEDIN,
        verified=True,
        note="",
        tags=[],
    )
    after = ev.EvidenceRecord(**{**before.__dict__, "url": before.url.replace("infra", "server")})

    assert cap._dedup_key(before) == cap._dedup_key(after)
    assert cap.new_records([after], [before]) == []


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
