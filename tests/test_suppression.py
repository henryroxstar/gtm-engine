"""Tests for the durable local suppression ledger.

Regression cover for 2026-08-11: 55 local exclusions were written into `ready-to-load.csv` and
`.pool/master-list.csv`, both of which are build outputs of `prospects_consolidate`. A rebuild an
hour later returned every suppressed person to the sendable pool, with no error anywhere.

All fixtures are invented (docs/RULES.md R9).
"""

from __future__ import annotations

import csv

import pytest

from gtm_core import suppression
from gtm_core.suppression import (
    PROVIDER_DNC_REASONS,
    Suppression,
    apply,
    load,
    verify,
)

LEDGER_ROWS = [
    {
        "email": "dana@northwind.example",
        "reason": "dnc-optout",
        "date": "2026-08-11",
        "note": "replied asking to be removed",
    },
    {
        "email": "sam@meridian.example",
        "reason": "contacted-step1",
        "date": "2026-08-11",
        "note": "sent 2026-08-11 via generic",
    },
    {
        "email": "kit@harbourline.example",
        "reason": "role-mismatch",
        "date": "2026-08-11",
        "note": "title: Director of catering",
    },
]

POOL_ROWS = [
    {"email": "dana@northwind.example", "company": "Northwind", "title": "CTO"},
    {"email": "sam@meridian.example", "company": "Meridian", "title": "CISO"},
    {"email": "kit@harbourline.example", "company": "Harbourline", "title": "Director of catering"},
    {"email": "ros@calder.example", "company": "Calder", "title": "Head of Engineering"},
]


@pytest.fixture
def ledger_path(tmp_path):
    p = tmp_path / "suppression.csv"
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["email", "reason", "date", "note"])
        w.writeheader()
        w.writerows(LEDGER_ROWS)
    return p


def _pool(tmp_path, rows=None, with_suppression=False):
    p = tmp_path / "ready-to-load.csv"
    rows = rows if rows is not None else POOL_ROWS
    cols = ["email", "company", "title"] + (
        ["suppression", "suppression_date"] if with_suppression else []
    )
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    return p


def test_load_reads_the_ledger(ledger_path):
    led = load(ledger_path)
    assert len(led) == 3
    assert led["dana@northwind.example"].reason == "dnc-optout"


def test_missing_ledger_is_empty_not_an_error(tmp_path):
    assert load(tmp_path / "nope.csv") == {}


def test_apply_marks_only_ledger_rows(tmp_path, ledger_path):
    pool = _pool(tmp_path)
    marked, total = apply(pool, load(ledger_path))
    assert (marked, total) == (3, 4)
    rows = {r["email"]: r for r in csv.DictReader(pool.open())}
    assert rows["dana@northwind.example"]["suppression"] == "dnc-optout"
    assert rows["kit@harbourline.example"]["suppression"] == "role-mismatch"
    assert rows["ros@calder.example"]["suppression"] == ""  # untouched, still sendable


def test_apply_is_idempotent(tmp_path, ledger_path):
    pool = _pool(tmp_path)
    led = load(ledger_path)
    apply(pool, led)
    first = pool.read_bytes()
    apply(pool, led)
    assert pool.read_bytes() == first


def test_apply_adds_the_columns_when_a_rebuild_dropped_them(tmp_path, ledger_path):
    """The actual recovery path: consolidate regenerates the file with its own 22 columns."""
    pool = _pool(tmp_path, with_suppression=False)
    assert "suppression" not in pool.read_text().splitlines()[0]
    apply(pool, load(ledger_path))
    assert "suppression" in pool.read_text().splitlines()[0]


def test_apply_does_not_clobber_a_hand_set_value_outside_the_ledger(tmp_path, ledger_path):
    rows = [dict(r) for r in POOL_ROWS]
    rows[3]["suppression"] = "manual-hold"
    pool = _pool(tmp_path, rows, with_suppression=True)
    apply(pool, load(ledger_path))
    out = {r["email"]: r for r in csv.DictReader(pool.open())}
    assert out["ros@calder.example"]["suppression"] == "manual-hold"


# --- the guard that would have caught the loss ---------------------------


def test_verify_passes_when_the_ledger_is_honoured(tmp_path, ledger_path):
    pool = _pool(tmp_path)
    apply(pool, load(ledger_path))
    assert verify(pool, load(ledger_path)) == []


def test_verify_catches_a_rebuild_that_dropped_the_whole_column(tmp_path, ledger_path):
    """The 2026-08-11 failure exactly: build output regenerated with no suppression column."""
    pool = _pool(tmp_path, with_suppression=False)
    findings = verify(pool, load(ledger_path))
    assert len(findings) == 1
    assert "no `suppression` column" in findings[0]
    assert "every local exclusion was dropped" in findings[0]


def test_verify_catches_one_person_silently_returning_to_the_pool(tmp_path, ledger_path):
    pool = _pool(tmp_path)
    apply(pool, load(ledger_path))
    rows = list(csv.DictReader(pool.open()))
    for r in rows:
        if r["email"] == "dana@northwind.example":
            r["suppression"] = ""  # someone re-imported her
    with pool.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    findings = verify(pool, load(ledger_path))
    assert len(findings) == 1
    assert "dana@northwind.example" in findings[0]
    assert "sendable in this file" in findings[0]


def test_verify_reports_a_missing_target(tmp_path, ledger_path):
    # Rule-prefixed since 2026-08-27 so `finding_budget.split_rule` can group these;
    # it partitions on the first ": ", which made the filename the rule name.
    assert verify(tmp_path / "gone.csv", load(ledger_path)) == ["target-missing: gone.csv"]


def test_only_real_optouts_belong_on_the_provider_dnc_list():
    """Local exclusions must never be pushed to the provider DNC list: it is global, permanent,
    and the connector exposes no removal tool."""
    assert PROVIDER_DNC_REASONS == {"dnc-optout"}
    assert "contacted-step1" not in PROVIDER_DNC_REASONS
    assert "role-mismatch" not in PROVIDER_DNC_REASONS


def test_suppression_is_frozen():
    s = Suppression(email="a@b.example", reason="dnc-optout")
    with pytest.raises(Exception):
        s.reason = "role-mismatch"  # type: ignore[misc]


# ── the eval-writeback reasons: local exclusions, never a provider DNC push ───


def test_eval_disqualified_is_not_a_provider_dnc_reason():
    """A fit judgment is not a legal do-not-contact.

    The provider's Global DNC list is global, permanent, and its MCP surface exposes no
    removal tool. Pushing "we decided they were the wrong buyer this quarter" onto it
    forfeits the company forever AND ships a private commercial judgment to a third party.
    """
    from gtm_core.suppression import EVAL_DISQUALIFIED, EVAL_WRONG_PERSON

    assert EVAL_DISQUALIFIED not in PROVIDER_DNC_REASONS
    assert EVAL_WRONG_PERSON not in PROVIDER_DNC_REASONS
    # Positive control: the reason that genuinely IS a DNC is still classified as one, so
    # this test cannot pass by the set having been emptied.
    assert "dnc-optout" in PROVIDER_DNC_REASONS


def test_append_adds_a_new_entry_and_skips_an_existing_one(tmp_path):
    from gtm_core.suppression import append

    ledger = tmp_path / "sup.csv"
    added, skipped = append(
        ledger,
        [Suppression(email="New@Acme.Example", reason="eval-disqualified", date="2026-08-22")],
    )
    assert (added, skipped) == (1, 0)
    assert "new@acme.example" in load(ledger), "the email was not normalised on write"

    added, skipped = append(
        ledger, [Suppression(email="new@acme.example", reason="role-mismatch", date="2026-08-23")]
    )
    assert (added, skipped) == (0, 1), "append overwrote an existing ledger entry"
    assert load(ledger)["new@acme.example"].reason == "eval-disqualified"


def test_append_to_a_fresh_ledger_writes_a_header(tmp_path):
    """A headerless CSV loads as zero suppressions — an empty ledger that looks like a file."""
    from gtm_core.suppression import append

    ledger = tmp_path / "sup.csv"
    append(ledger, [Suppression(email="a@acme.example", reason="eval-disqualified")])
    first_line = ledger.read_text(encoding="utf-8").splitlines()[0]
    # A fresh ledger gets the current column set, which since 2026-08-27 carries the
    # person key. An existing legacy ledger keeps its own header — see the test below.
    assert first_line == ",".join(suppression.COLUMNS), f"no header row: {first_line!r}"
    assert len(load(ledger)) == 1


def test_append_keeps_a_legacy_ledgers_own_header(tmp_path):
    """Widening the header would rewrite rows this call was not asked to touch."""
    from gtm_core.suppression import append

    ledger = tmp_path / "sup.csv"
    with ledger.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(suppression.LEGACY_COLUMNS))
        w.writeheader()
        w.writerow({"email": "old@acme.example", "reason": "role-mismatch"})
    append(ledger, [Suppression(email="new@acme.example", reason="eval-disqualified")])
    lines = ledger.read_text(encoding="utf-8").splitlines()
    assert lines[0] == ",".join(suppression.LEGACY_COLUMNS)
    assert len(load(ledger)) == 2


def test_append_of_nothing_creates_no_file(tmp_path):
    """An empty writeback must not leave an empty ledger that later reads as authoritative."""
    from gtm_core.suppression import append

    ledger = tmp_path / "sup.csv"
    assert append(ledger, []) == (0, 0)
    assert not ledger.exists()


# --- crash safety (B12) ------------------------------------------------------ #
# `apply` rewrites a build output in place. Opened for "w", a crash between the
# truncate and the last row leaves a half-written send list where a complete one
# used to be — on this path, the list of people about to be emailed.


def test_apply_leaves_the_target_intact_when_the_write_fails(tmp_path, monkeypatch, ledger_path):
    target = _pool(tmp_path, [{"email": "someone@acme.example"}], with_suppression=False)
    before = target.read_text(encoding="utf-8")
    ledger = suppression.load(ledger_path)

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(suppression.fsio.os, "replace", _boom)
    with pytest.raises(OSError):
        suppression.apply(target, ledger)
    assert target.read_text(encoding="utf-8") == before, "a failed rewrite truncated the send list"


def test_apply_stays_idempotent_through_the_atomic_writer(tmp_path, ledger_path):
    target = _pool(tmp_path, [{"email": "someone@acme.example"}], with_suppression=False)
    ledger = suppression.load(ledger_path)
    suppression.apply(target, ledger)
    once = target.read_text(encoding="utf-8")
    suppression.apply(target, ledger)
    assert target.read_text(encoding="utf-8") == once


# --- person-level keys (PRD 2026-08-19 §4) ----------------------------------- #
# An address-keyed exclusion is only as durable as the provider's guess at the
# address format. The incident §4 records: a contact on the DNC list as
# firstname.lastname@<domain> came back from a later re-sourcing as
# firstname@<domain> — the same person, a different key, so the exclusion did not
# survive and they were enrolled again.


def _ledger_with(tmp_path, rows, header=None):
    p = tmp_path / "suppression.csv"
    header = header or ["email", "name", "company_domain", "reason", "date", "note"]
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in header})
    return p


def test_person_key_is_stable_across_address_formats():
    a = suppression.person_key("Dana Vance", "northwind.example")
    b = suppression.person_key("dana  vance", "Northwind.Example")
    assert a == b and a


def test_person_key_needs_both_halves():
    assert suppression.person_key("", "northwind.example") == ""
    assert suppression.person_key("Dana Vance", "") == ""


def test_a_suppressed_person_stays_suppressed_at_a_new_address(tmp_path):
    """The §4 incident, as a regression."""
    p = _ledger_with(
        tmp_path,
        [
            {
                "email": "dana.vance@northwind.example",
                "name": "Dana Vance",
                "company_domain": "northwind.example",
                "reason": "dnc-optout",
                "date": "2026-08-11",
            }
        ],
    )
    index = suppression.load_index(p)
    resourced = {
        "email": "dana@northwind.example",
        "first": "Dana",
        "last": "Vance",
        "company_domain": "northwind.example",
    }
    hit = index.match(resourced)
    assert hit is not None, "the same person at a new address escaped the ledger"
    assert hit.reason == "dnc-optout"


def test_a_different_person_at_the_same_company_is_not_suppressed(tmp_path):
    p = _ledger_with(
        tmp_path,
        [
            {
                "email": "dana.vance@northwind.example",
                "name": "Dana Vance",
                "company_domain": "northwind.example",
                "reason": "dnc-optout",
            }
        ],
    )
    index = suppression.load_index(p)
    assert (
        index.match(
            {
                "email": "sam@northwind.example",
                "first": "Sam",
                "last": "Okafor",
                "company_domain": "northwind.example",
            }
        )
        is None
    )


def test_email_only_ledger_rows_still_match_by_address(tmp_path):
    """Backfill cannot resolve every row, so the address key must stay live."""
    p = _ledger_with(tmp_path, [{"email": "kit@harbourline.example", "reason": "role-mismatch"}])
    index = suppression.load_index(p)
    assert index.match({"email": "kit@harbourline.example"}) is not None


def test_the_old_four_column_ledger_still_loads(tmp_path):
    p = _ledger_with(
        tmp_path,
        [{"email": "kit@harbourline.example", "reason": "role-mismatch"}],
        header=["email", "reason", "date", "note"],
    )
    assert "kit@harbourline.example" in suppression.load(p)
    assert suppression.load_index(p).match({"email": "kit@harbourline.example"}) is not None


def test_append_does_not_duplicate_a_person_already_held_under_another_address(tmp_path):
    p = _ledger_with(
        tmp_path,
        [
            {
                "email": "dana.vance@northwind.example",
                "name": "Dana Vance",
                "company_domain": "northwind.example",
                "reason": "dnc-optout",
            }
        ],
    )
    added, skipped = suppression.append(
        p,
        [
            Suppression(
                email="dana@northwind.example",
                name="Dana Vance",
                company_domain="northwind.example",
                reason="eval-wrong-person",
            )
        ],
    )
    assert (added, skipped) == (0, 1), "the same person was appended twice under two addresses"


def test_apply_marks_a_row_matched_only_by_person_key(tmp_path):
    p = _ledger_with(
        tmp_path,
        [
            {
                "email": "dana.vance@northwind.example",
                "name": "Dana Vance",
                "company_domain": "northwind.example",
                "reason": "dnc-optout",
                "date": "2026-08-11",
            }
        ],
    )
    target = tmp_path / "pool.csv"
    with target.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["email", "first", "last", "company_domain"])
        w.writeheader()
        w.writerow(
            {
                "email": "dana@northwind.example",
                "first": "Dana",
                "last": "Vance",
                "company_domain": "northwind.example",
            }
        )
    marked, total = suppression.apply(target, suppression.load_index(p))
    assert (marked, total) == (1, 1)


def test_verify_catches_a_person_key_match_that_the_build_left_sendable(tmp_path):
    p = _ledger_with(
        tmp_path,
        [
            {
                "email": "dana.vance@northwind.example",
                "name": "Dana Vance",
                "company_domain": "northwind.example",
                "reason": "dnc-optout",
            }
        ],
    )
    target = tmp_path / "pool.csv"
    with target.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(
            fh, fieldnames=["email", "first", "last", "company_domain", "suppression"]
        )
        w.writeheader()
        w.writerow(
            {
                "email": "dana@northwind.example",
                "first": "Dana",
                "last": "Vance",
                "company_domain": "northwind.example",
                "suppression": "",
            }
        )
    findings = suppression.verify(target, suppression.load_index(p))
    assert findings and "suppressed" in findings[0]


def test_verify_findings_are_rule_prefixed_for_the_budget(tmp_path):
    """`finding_budget.split_rule` partitions on the first ': ' — findings must lead
    with a rule name, not a filename, or every file becomes its own class."""
    p = _ledger_with(tmp_path, [{"email": "kit@harbourline.example", "reason": "role-mismatch"}])
    target = tmp_path / "pool.csv"
    with target.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["email", "suppression"])
        w.writeheader()
        w.writerow({"email": "kit@harbourline.example", "suppression": ""})
    findings = suppression.verify(target, suppression.load_index(p))
    assert findings
    for f in findings:
        assert f.split(": ", 1)[0] in {
            "suppressed-row-sendable",
            "suppression-column-missing",
            "target-missing",
        }, f


# --- backfilling the person key onto a ledger that predates it --------------- #


def _source(tmp_path, rows):
    p = tmp_path / "master-list.csv"
    cols = ["email", "first", "last", "company_domain"]
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    return p


def test_migrate_backfills_the_person_key_from_the_pool(tmp_path):
    led = _ledger_with(
        tmp_path,
        [{"email": "dana.vance@northwind.example", "reason": "dnc-optout"}],
        header=list(suppression.LEGACY_COLUMNS),
    )
    src = _source(
        tmp_path,
        [
            {
                "email": "dana.vance@northwind.example",
                "first": "Dana",
                "last": "Vance",
                "company_domain": "northwind.example",
            }
        ],
    )
    matched, unmatched, total = suppression.migrate(led, src)
    assert (matched, unmatched, total) == (1, 0, 1)
    index = suppression.load_index(led)
    assert (
        index.match(
            {
                "email": "dana@northwind.example",
                "first": "Dana",
                "last": "Vance",
                "company_domain": "northwind.example",
            }
        )
        is not None
    )


def test_migrate_keeps_an_unmatched_row_on_its_address_key(tmp_path):
    """A migration must never reduce what the ledger suppresses."""
    led = _ledger_with(
        tmp_path,
        [{"email": "gone@calder.example", "reason": "role-mismatch"}],
        header=list(suppression.LEGACY_COLUMNS),
    )
    src = _source(tmp_path, [])
    matched, unmatched, total = suppression.migrate(led, src)
    assert (matched, unmatched, total) == (0, 1, 1)
    assert suppression.load_index(led).match({"email": "gone@calder.example"}) is not None


def test_migrate_is_idempotent(tmp_path):
    led = _ledger_with(
        tmp_path,
        [{"email": "dana.vance@northwind.example", "reason": "dnc-optout"}],
        header=list(suppression.LEGACY_COLUMNS),
    )
    src = _source(
        tmp_path,
        [
            {
                "email": "dana.vance@northwind.example",
                "first": "Dana",
                "last": "Vance",
                "company_domain": "northwind.example",
            }
        ],
    )
    suppression.migrate(led, src)
    once = led.read_bytes()
    suppression.migrate(led, src)
    assert led.read_bytes() == once


def test_migrate_snapshots_before_it_rewrites(tmp_path):
    led = _ledger_with(
        tmp_path,
        [{"email": "dana.vance@northwind.example", "reason": "dnc-optout"}],
        header=list(suppression.LEGACY_COLUMNS),
    )
    suppression.migrate(led, _source(tmp_path, []))
    snaps = list((led.parent / ".snapshots").glob("suppression-*.csv"))
    assert snaps, "the ledger was rewritten with no recoverable copy"


def test_migrate_cli_reports_counts(tmp_path, capsys):
    led = _ledger_with(
        tmp_path,
        [{"email": "dana.vance@northwind.example", "reason": "dnc-optout"}],
        header=list(suppression.LEGACY_COLUMNS),
    )
    src = _source(
        tmp_path,
        [
            {
                "email": "dana.vance@northwind.example",
                "first": "Dana",
                "last": "Vance",
                "company_domain": "northwind.example",
            }
        ],
    )
    rc = suppression.main(["migrate", "--ledger", str(led), "--source", str(src)])
    assert rc == 0
    assert "1/1 row(s) now carry a person key" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# reconcile_dnc — the provider read-back the module docstring promised and, until
# 2026-09-21, did not have. Three real opt-outs sat on a live provider's DNC list for
# six weeks with no `dnc-optout` row in the ledger; nothing could have reported it.
#
# Every refusal below carries a paired negative control: a check that fires on a clean
# input is not a check (docs/RULES.md §R18).
# ---------------------------------------------------------------------------

_DNC_LEDGER = [
    {"email": "dana@northwind.example", "reason": "dnc-optout", "date": "2026-09-01"},
    {"email": "kit@harbourline.example", "reason": "dnc-optout", "date": "2026-09-01"},
    # A local-only exclusion: NOT a provider-DNC reason, so its absence upstream is correct.
    {"email": "sam@meridian.example", "reason": "out-of-market", "date": "2026-09-01"},
]


def test_reconcile_dnc_passes_when_every_optout_is_on_the_provider(tmp_path):
    led = load(_ledger_with(tmp_path, _DNC_LEDGER))
    findings = suppression.reconcile_dnc(led, ["dana@northwind.example", "kit@harbourline.example"])
    assert findings == []


def test_reconcile_dnc_catches_an_optout_missing_upstream(tmp_path):
    """The negative control for the test above: drop one, it must be named."""
    led = load(_ledger_with(tmp_path, _DNC_LEDGER))
    findings = suppression.reconcile_dnc(led, ["dana@northwind.example"])
    assert len(findings) == 1
    assert "kit@harbourline.example" in findings[0]
    assert "dnc-optout-not-on-provider" in findings[0]


def test_reconcile_dnc_ignores_local_only_reasons(tmp_path):
    """`out-of-market` is OUR judgment, not theirs — it must never be expected upstream.

    Pushing it there would forfeit the account forever on a list with no removal tool.
    """
    led = load(_ledger_with(tmp_path, _DNC_LEDGER))
    findings = suppression.reconcile_dnc(led, ["dana@northwind.example", "kit@harbourline.example"])
    assert not any("sam@meridian.example" in f for f in findings)


def test_reconcile_dnc_accepts_a_domain_level_entry(tmp_path):
    """A whole-domain DNC covers its addresses; demanding the exact address would be noise."""
    led = load(_ledger_with(tmp_path, _DNC_LEDGER))
    findings = suppression.reconcile_dnc(led, ["dana@northwind.example"], ["harbourline.example"])
    assert findings == []


def test_reconcile_dnc_refuses_an_empty_payload_rather_than_reporting_clean(tmp_path):
    """An unparsed payload and a genuinely empty DNC list are indistinguishable here.

    Reading "no entries" as "nothing to check" would turn a broken pipe into a PASS —
    the shape of the 2026-08-11 unfed-sink failure, one layer up.
    """
    led = load(_ledger_with(tmp_path, _DNC_LEDGER))
    findings = suppression.reconcile_dnc(led, [])
    assert len(findings) == 1
    assert "provider-dnc-empty" in findings[0]


def test_reconcile_dnc_empty_payload_is_fine_when_nothing_is_claimed(tmp_path):
    """Negative control for the refusal above: no claims, nothing to prove."""
    led = load(_ledger_with(tmp_path, [_DNC_LEDGER[2]]))
    assert suppression.reconcile_dnc(led, []) == []


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(
            {
                "payload": {
                    "dncListDetails": [
                        {"value": "Dana@Northwind.Example", "type": "email"},
                        {"value": "kit@harbourline.example", "type": "email"},
                    ]
                }
            },
            id="get_dnc_items_by_id",
        ),
        pytest.param(
            {
                "emails": ["dana@northwind.example", "KIT@harbourline.example"],
                "domains": [],
                "fetched_at": "2026-09-21T00:00:00Z",
            },
            id="consolidate-cache",
        ),
        pytest.param(
            ["dana@northwind.example", "kit@harbourline.example"],
            id="legacy-bare-list",
        ),
    ],
)
def test_normalize_dnc_payload_reads_every_real_shape(payload):
    """All three shapes exist in this repo; casing must not decide a suppression."""
    emails, _domains = suppression.normalize_dnc_payload(payload)
    assert emails == {"dana@northwind.example", "kit@harbourline.example"}


def test_normalize_dnc_payload_separates_domains_from_emails():
    emails, domains = suppression.normalize_dnc_payload(
        {"payload": {"dncListDetails": [{"value": "@harbourline.example", "type": "domain"}]}}
    )
    assert emails == set()
    assert domains == {"harbourline.example"}


def test_normalize_dnc_payload_survives_an_unreadable_shape():
    assert suppression.normalize_dnc_payload("not a payload") == (set(), set())
    assert suppression.normalize_dnc_payload(None) == (set(), set())


def test_reconcile_dnc_cli_exits_nonzero_on_a_finding(tmp_path, monkeypatch, capsys):
    import io

    led = _ledger_with(tmp_path, _DNC_LEDGER)
    monkeypatch.setattr(
        "sys.stdin", io.StringIO('{"emails": ["dana@northwind.example"], "domains": []}')
    )
    rc = suppression.main(["reconcile-dnc", "--ledger", str(led)])
    assert rc == 1
    assert "kit@harbourline.example" in capsys.readouterr().out


def test_reconcile_dnc_cli_exits_zero_when_reconciled(tmp_path, monkeypatch, capsys):
    """Negative control for the CLI test above."""
    import io

    led = _ledger_with(tmp_path, _DNC_LEDGER)
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(
            '{"emails": ["dana@northwind.example", "kit@harbourline.example"], "domains": []}'
        ),
    )
    rc = suppression.main(["reconcile-dnc", "--ledger", str(led)])
    assert rc == 0
    assert "PASS" in capsys.readouterr().out


def test_reconcile_dnc_cli_refuses_an_empty_stdin(tmp_path, monkeypatch):
    """No payload must not read as a clean reconciliation."""
    import io

    led = _ledger_with(tmp_path, _DNC_LEDGER)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert suppression.main(["reconcile-dnc", "--ledger", str(led)]) == 2


def test_provider_dnc_reasons_is_the_single_source_of_truth():
    """reconcile_dnc must key off the constant, not a hardcoded 'dnc-optout'."""
    assert "dnc-optout" in PROVIDER_DNC_REASONS
    assert suppression.EVAL_DISQUALIFIED not in PROVIDER_DNC_REASONS
