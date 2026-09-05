"""The judge reads a 1:1 outreach pack, not only a sequence spec + CSV.

Until 2026-09-04 a pack handed to ``score_emails`` found no ``**Step N — Day D**`` headers,
produced zero touches, and the batch **reported success having scored nothing**. That made the
1:1 lane — one named person, the highest stakes per email — the only lane with no path into the
loop that is supposed to improve the next batch, since the judge is the only surface that emits
an adjudication record.

Every fixture here is fictional (§R9): real recipients live in ``content/`` and ``profiles/``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.mcp.judge.scoring import build_record, load_rows

PACK = """# Outreach Pack: Borea Systems (2026-09-04)

Rules-Version: 2026-09-04

**Tier:** A 🔥 | **Score:** 9/12 | **Segment:** Builder | **Market:** Singapore
**Primary persona:** Dana Rivera, Co-Founder (the technical founder: champion and buyer)
**Contact:** Dana Rivera — dana@borea.example
**Why-now (🔥):** Shipped a scheduling agent that writes into a customer's own ticket queue.
[borea.example | 2026-09-04]
**Capability:** identity

---
## LinkedIn DM (≤ 280 chars)
> Short DM that is not the email.

---
## Email (touch 1)
**Subject:** the ticket queue
> Hi Dana,
>
> Borea's scheduling agent writes into a customer's own ticket queue.
>
> My hunch: the queue records the write, not which agent held the authority to make it.
>
> Want me to sketch what per-agent identity looks like on that install?
>
> Alex

**Word count:** 44 / 100.
"""

DRAFT = """# Cold email — Cirrus Labs — 2026-09-04

Rules-Version: 2026-09-04

- **To:** Sam Okonjo, CTO — sam@cirrus.example
- **Why-now:** Published an agent that files expense reports into a client's finance system.

---
**Subject:** the finance system
Hi Sam,

Cirrus files expense reports into a client's finance system.

My hunch: the client sees one service account, not one agent per action.

Want me to sketch what that separation looks like?

Alex

---
**Word count:** 38
"""


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_a_pack_yields_one_row_and_one_touch(tmp_path):
    rows, touches, problems = load_rows(str(_write(tmp_path, "pack.md", PACK)), "", "1")
    assert problems == []
    assert len(rows) == 1 and len(touches) == 1
    assert rows[0]["email"] == "dana@borea.example"
    assert rows[0]["company"] == "Borea Systems"
    assert rows[0]["segment"] == "Builder"
    assert rows[0]["title"] == "Co-Founder"
    assert "scheduling agent" in rows[0]["signal_clause"]
    assert touches[0].number == 1
    assert touches[0].subject == "the ticket queue"


def test_the_body_is_the_email_verbatim_not_the_dm_or_the_meta(tmp_path):
    """A pack is already rendered — the judge must score the bytes the person receives."""
    _, touches, _ = load_rows(str(_write(tmp_path, "pack.md", PACK)), "", "1")
    body = touches[0].body
    assert body.startswith("Hi Dana,")
    assert body.rstrip().endswith("Alex")
    assert "Short DM" not in body, "the LinkedIn DM is a different channel"
    assert "Word count" not in body, "meta lines are not sent"
    assert "{{" not in body, "a pack carries no merge fields"


def test_render_on_a_pack_body_is_identity(tmp_path):
    """The passthrough property the whole adapter rests on: no CSV, nothing to substitute."""
    from agent.mcp.judge.render import render

    _, touches, _ = load_rows(str(_write(tmp_path, "pack.md", PACK)), "", "1")
    assert render(touches[0].body, {}) == touches[0].body


def test_the_csv_path_is_unused_by_a_pack(tmp_path):
    """A pack names its own recipient, so a missing CSV is not a problem for it."""
    p = str(_write(tmp_path, "pack.md", PACK))
    assert load_rows(p, "/nonexistent.csv", "1") == load_rows(p, "", "1")


def test_a_pack_with_no_contact_is_refused_not_silently_empty(tmp_path):
    """The failure this whole change exists to stop: a batch that scores nothing and says ok."""
    stripped = "\n".join(ln for ln in PACK.splitlines() if not ln.startswith("**Contact:**"))
    rows, touches, problems = load_rows(str(_write(tmp_path, "pack.md", stripped)), "", "1")
    assert rows == [] and touches == []
    assert problems and "Contact" in problems[0]


@pytest.mark.parametrize(
    "heading",
    [
        "# Outreach Pack: Borea Systems (2026-09-04)",
        "# Outreach Pack — Borea Systems — 2026-09-04",
    ],
)
def test_both_authored_heading_shapes_carry_the_company(tmp_path, heading):
    """A parser that accepts one shape does not fail — it yields an empty company."""
    text = PACK.replace("# Outreach Pack: Borea Systems (2026-09-04)", heading)
    rows, _, problems = load_rows(str(_write(tmp_path, "pack.md", text)), "", "1")
    assert problems == []
    assert rows[0]["company"] == "Borea Systems"


def test_the_draft_outreach_shape_adapts_too(tmp_path):
    rows, touches, problems = load_rows(str(_write(tmp_path, "email-x.md", DRAFT)), "", "1")
    assert problems == []
    assert rows[0]["email"] == "sam@cirrus.example"
    assert rows[0]["company"] == "Cirrus Labs"
    assert touches[0].subject == "the finance system"
    assert touches[0].body.startswith("Hi Sam,")


def test_a_pack_row_builds_a_record_hashed_on_the_sent_bytes(tmp_path):
    import hashlib

    spec = _write(tmp_path, "pack.md", PACK)
    rows, touches, _ = load_rows(str(spec), "", "1")
    rec = build_record(
        rows[0],
        spec_path=str(spec),
        csv_path="",
        subject=touches[0].subject,
        body=touches[0].body,
        touch_n=1,
        verdict=None,
        backend="api",
        judge_batch=1,
        repair_attempt=0,
        repaired=False,
    )
    assert rec.email == "dana@borea.example"
    assert rec.unscored is True, "no verdict must become unscored, never a vanished row"
    assert rec.body_hash == hashlib.sha256(touches[0].body.encode("utf-8")).hexdigest()[:16]


def test_two_packs_do_not_collide_on_row_id(tmp_path):
    """`csv_path` is "" for every pack, so the spec path must carry the identity."""
    a = _write(tmp_path, "a.md", PACK)
    b = _write(tmp_path, "b.md", PACK)
    ids = set()
    for spec in (a, b):
        rows, touches, _ = load_rows(str(spec), "", "1")
        ids.add(
            build_record(
                rows[0],
                spec_path=str(spec),
                csv_path="",
                subject=touches[0].subject,
                body=touches[0].body,
                touch_n=1,
                verdict=None,
                backend="api",
                judge_batch=1,
                repair_attempt=0,
                repaired=False,
            ).row_id
        )
    assert len(ids) == 2


def test_a_sequence_spec_still_needs_its_csv(tmp_path):
    """No regression: the template path is unchanged and still joins against a CSV."""
    spec = _write(
        tmp_path,
        "spec.md",
        "# Spec\n\n**Step 1 — Day 0** Subject: `hello`\n\n> Hi {{First Name}},\n>\n> Alex\n",
    )
    rows, touches, problems = load_rows(str(spec), "/nonexistent.csv", "1")
    assert rows == [] and touches == [] and problems and "no csv" in problems[0]


def test_a_file_that_is_neither_says_so(tmp_path):
    """`detect_format` answers 'draft-outreach' by elimination, so the touch count is the
    positive test — otherwise a malformed spec routes into the pack branch and scores zero."""
    rows, touches, problems = load_rows(str(_write(tmp_path, "junk.md", "# nothing\n")), "", "1")
    assert rows == [] and touches == []
    assert "neither a sequence spec" in problems[0]


def test_only_verbatim_citation_fields_are_copied(tmp_path):
    """The pack's `[source | date]` bracket fills what it records and nothing more.

    `signal_source_url` stays empty on purpose: a pack cites a bare host, and the record asks
    for an https URL naming one source. Upgrading a host to a URL manufactures a citation the
    pack never made — a source finding that fires honestly is worth more than a clean column.
    """
    rows, _, _ = load_rows(str(_write(tmp_path, "pack.md", PACK)), "", "1")
    assert rows[0]["signal_observed"] == "2026-09-04"
    assert rows[0]["signal_subject"] == "Borea Systems"
    assert not rows[0].get("signal_source_url")
