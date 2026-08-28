"""Guards for the SEC-roster importer.

The importer exists to make one distinction structural rather than rhetorical: a full-text
search hit is a worklist item, not evidence. These tests pin that it can never produce a
citable record, and that re-running it does not inflate the backlog.
"""

from __future__ import annotations

from pathlib import Path

from gtm_core.voc import evidence as ev
from gtm_core.voc import roster

SAMPLE = """# US Enterprise Signals — 2026-07-29

## Verified passages (context actually read — citable)

**Lumen Cloud, Inc.** — 10-K, filed 2026-03-05 · `vendor-voice`
> "quoted text here"

## Roster — enterprise / buyer-side (unverified context)

### Financial services & payments — 2
| Company | Filed | Ticker |
|---|---|---|
| NORTHBRIDGE BANK CORP | 2026-02-20 | NBC |
| MERIDIAN PAYMENTS INC. | 2025-11-06 | MRP |

### Travel, retail & consumer — 1
| Company | Filed | Ticker |
|---|---|---|
| Harborview Travel Holdings Inc. | 2026-02-18 | HRBV |

**Buyer-side subtotal: 3.**

---

## Roster — vendor / platform-side (competitive & partner intel)

Lumen Cloud · Forge Systems · Anchorpoint

**Vendor-side subtotal: 3.**
"""


def test_no_imported_record_is_ever_citable():
    """The whole point of the importer. A parsed table row must be structurally incapable of
    backing a demand claim, however the brief later refers to it."""
    parsed = roster.parse(SAMPLE)
    assert parsed["records"]
    for rec in parsed["records"]:
        assert rec.verified is False
        assert rec.is_citable() is False
        assert rec.verbatim == roster.UNREAD_PLACEHOLDER
    assert ev.breadth(parsed["records"], roster.ROSTER_CLAIM) == 0


def test_header_and_separator_rows_are_not_imported_as_companies():
    """A lookahead placed after `\\s*` is defeated by backtracking, which silently admitted
    the literal header 'Company' as a filer. Pinned so the fix cannot regress."""
    entities = {r.entity for r in roster.parse(SAMPLE)["records"]}
    assert "Company" not in entities
    assert not any(set(e) <= {"-", ":"} for e in entities)


def test_counts_match_the_document_subtotals():
    parsed = roster.parse(SAMPLE)
    buyer = [r for r in parsed["records"] if "buyer-side" in r.tags]
    vendor = [r for r in parsed["records"] if "vendor-side" in r.tags]
    assert len(buyer) == 3
    # Lumen Cloud appears in the vendor paragraph AND in the verified section — it must not
    # be imported as backlog, or one company becomes two entities.
    assert len(vendor) == 2
    assert parsed["dropped_as_verified"] == 1


def test_legal_suffixes_do_not_split_one_company_into_two():
    assert roster._norm_entity("Lumen Cloud, Inc.") == roster._norm_entity("Lumen Cloud")
    assert roster._norm_entity("Forge.ai, Inc.") == roster._norm_entity("Forge.ai")
    # ...but genuinely different filers stay different.
    assert roster._norm_entity("Ethos Technologies Inc.") != roster._norm_entity("Ethos Corp")


def test_buyer_and_vendor_rows_get_different_speakers():
    by_entity = {r.entity: r for r in roster.parse(SAMPLE)["records"]}
    assert by_entity["NORTHBRIDGE BANK CORP"].speaker == "customer-voice"
    assert by_entity["Forge Systems"].speaker == "vendor-voice"


def test_vendor_rows_survive_a_store_round_trip(tmp_path: Path):
    """Vendor names carry no filing date; an empty required field would be dropped on reload,
    so the record would write successfully and then vanish."""
    path = tmp_path / "evidence.jsonl"
    records = roster.parse(SAMPLE)["records"]
    ev.append(path, records)
    assert len(ev.load(path)) == len(records)


def test_reimporting_is_idempotent():
    """The store is append-only, so a second run must add nothing rather than double the backlog."""
    records = roster.parse(SAMPLE)["records"]
    assert len(roster.new_records(records, [])) == len(records)
    assert roster.new_records(records, records) == []


def test_a_read_filer_never_reappears_in_the_backlog_under_a_different_claim():
    """The backlog's entire meaning is "not yet read". A filer whose passage was read under a
    *demand* claim must not import under ROSTER_CLAIM just because the claim ids differ — that
    listed already-read companies as unread."""
    records = roster.parse(SAMPLE)["records"]
    already_read = [
        ev.EvidenceRecord(
            claim_id="agents-act-autonomously-in-production",  # NOT the roster claim
            verbatim="a real passage that was actually read",
            url="https://sec.gov/x",
            date="2025-11-06",
            entity="MERIDIAN PAYMENTS INC.",
            speaker="customer-voice",
            source_id=roster.SOURCE_ID,
            verified=True,
        )
    ]
    kept = {r.entity for r in roster.new_records(records, already_read)}
    assert "MERIDIAN PAYMENTS INC." not in kept
    assert "NORTHBRIDGE BANK CORP" in kept  # unread filers are untouched


def test_an_unverified_record_does_not_block_the_backlog_row():
    """Only a *verified* record means the filer was read. An unverified one under another claim
    must not silently remove it from the worklist."""
    records = roster.parse(SAMPLE)["records"]
    not_read = [
        ev.EvidenceRecord(
            claim_id="some-other-claim",
            verbatim="placeholder",
            url="https://sec.gov/x",
            date="2025-11-06",
            entity="MERIDIAN PAYMENTS INC.",
            speaker="customer-voice",
            source_id=roster.SOURCE_ID,
            verified=False,
        )
    ]
    assert "MERIDIAN PAYMENTS INC." in {r.entity for r in roster.new_records(records, not_read)}


def test_a_verified_record_for_the_same_filer_blocks_the_backlog_row():
    records = roster.parse(SAMPLE)["records"]
    existing = [
        ev.EvidenceRecord(
            claim_id=roster.ROSTER_CLAIM,
            verbatim="a real passage",
            url="https://sec.gov/x",
            date="2026-02-20",
            entity="Northbridge Bank, Corp.",  # same filer, different legal-form spelling
            speaker="customer-voice",
            source_id=roster.SOURCE_ID,
            verified=True,
        )
    ]
    kept = {r.entity for r in roster.new_records(records, existing)}
    assert "NORTHBRIDGE BANK CORP" not in kept
