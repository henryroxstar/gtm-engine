"""Merge-field hygiene tests.

Every case in the "real defects" classes below is a value that actually appeared in
``content/acme/prospects/sequences/ready-to-load.csv`` on 2026-07-28 and passed the
outreach copy linter with zero errors. They are the regression floor: the copy gate
cannot see these, so this module has to.
"""

from __future__ import annotations

import datetime

import pytest

from gtm_core.merge_hygiene import (
    SIGNAL_MAX_AGE_DAYS,
    blocks,
    check_row,
    clean_company,
    clean_first_name,
    clean_last_name,
    clean_row,
    clean_title,
    ends_in_sibilant,
    signal_clause,
    signal_is_fresh,
    signal_latest_date,
)

# --- first name: real defects --------------------------------------------


@pytest.mark.parametrize(
    ("first", "last", "email", "expected"),
    [
        # The 🍦 row: first/last swapped, the emoji carries no letters, so the real
        # name is recovered from `last` rather than sent as "Hi 🍦,".
        ("\U0001f366", "Marcus Webb", "marcus.webb@summitline.example", "Marcus"),
        ("Dr.rani", "Sharma", "rani@brightpath.example", "Rani"),
        ("M.omar", "Haddad", "omar@forgeworks.example", "Omar"),
        ("A.j.", "Marchetti", "aj.marchetti@eastvale.example", "AJ"),
    ],
)
def test_real_first_name_defects_are_repaired(first, last, email, expected):
    assert clean_first_name(first, last, email) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Chris", "Chris"),  # already clean — idempotent
        ("chris", "Chris"),
        ("CHRIS", "Chris"),
        ("Mary Jane", "Mary"),  # multi-word -> given name
        ("Dr. Rani", "Rani"),  # space-separated honorific
        ("Prof Alan", "Alan"),
        ("Gregory, Mba, Pmp", "Gregory"),  # post-nominals
        ("O'Brien", "O'Brien"),  # interior punctuation preserved
        ("jean-luc", "Jean-Luc"),  # re-capitalized after the hyphen
        ("McAfee", "McAfee"),  # interior capital not flattened
    ],
)
def test_first_name_normalization(raw, expected):
    assert clean_first_name(raw) == expected


def test_first_name_is_never_derived_from_the_email_address():
    # "abrooke@" would give "Hi Abrooke," — an initial glued to a surname. Blocking the
    # row for a human beats guessing; the local-part is never consulted.
    assert clean_first_name("", "", "abrooke@medipath.example") == ""
    assert clean_first_name("\U0001f366", "", "dana.reyes@example.com") == ""


def test_first_name_never_invented_from_nothing():
    assert clean_first_name("", "", "") == ""
    assert clean_first_name("\U0001f366", "\U0001f366", "123@example.com") == ""


@pytest.mark.parametrize(
    "raw",
    [
        "Dr.rani",
        "M.omar",
        # Regression: "A.j." -> "AJ" -> "Aj". consolidate() re-cleans the master list on
        # every scheduled sweep, so a non-idempotent repair degrades the name over time.
        "A.j.",
        "CHRIS",
        "Mary Jane",
        "Gregory, Mba, Pmp",
    ],
)
def test_first_name_clean_is_idempotent(raw):
    once = clean_first_name(raw, "Sharma", "x@example.com")
    assert clean_first_name(once, "Sharma", "x@example.com") == once


def test_two_letter_initials_survive_re_cleaning():
    assert clean_first_name("A.j.", "Marchetti") == "AJ"
    assert clean_first_name("AJ", "Marchetti") == "AJ"
    # 3+ letters is a shouted name, not initials — still normalized.
    assert clean_first_name("CHRIS") == "Chris"


# --- last name (feeds {{Last Name}} and the swapped-field recovery) ------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Reece \U0001f947✨", "Reece"),  # emoji in the surname column
        ("Rothstein, Mba, Pmp", "Rothstein"),  # multiple post-nominals, one pass each
        ("Warrix, Cpa, Mba", "Warrix"),
        ("Kampsen, Msn, Aprn, Fnp-c", "Kampsen"),
        ("Mccaffrey , Shrm - Scp", "Mccaffrey"),
        ("Milewski Gamelin Rn", "Milewski Gamelin"),
        ("Renner", "Renner"),  # already clean
    ],
)
def test_last_name_defects_are_repaired(raw, expected):
    assert clean_last_name(raw) == expected


def test_accented_surnames_are_never_mangled():
    # "Pérez Trufero" is a correct name, not an encoding defect. Only emoji/symbol
    # categories are stripped; letters with diacritics are letters.
    assert clean_last_name("Pérez Trufero") == "Pérez Trufero"
    assert clean_last_name("Müller-Schmidt") == "Müller-Schmidt"


@pytest.mark.parametrize("raw", ["Reece \U0001f947✨", "Warrix, Cpa, Mba", "Pérez Trufero"])
def test_last_name_clean_is_idempotent(raw):
    once = clean_last_name(raw)
    assert clean_last_name(once) == once


# --- title ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Pipe segments are real information — joined, never truncated away.
        ("Vice President | Compliance", "Vice President, Compliance"),
        (
            "Founder | chief executive officer | head of ai innovations",
            "Founder, chief executive officer, head of ai innovations",
        ),
        ("Chief Technology Officer", "Chief Technology Officer"),
    ],
)
def test_title_headlines_are_normalized(raw, expected):
    assert clean_title(raw) == expected


def test_title_case_is_left_alone():
    # Titles arrive in wildly mixed case; re-casing is a copy decision, not hygiene.
    assert clean_title("head of emea private assets") == "head of emea private assets"


def test_title_clean_is_idempotent():
    once = clean_title("Vice President | Compliance")
    assert clean_title(once) == once


# --- possessive ----------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["Gears & Vectors", "Vantos", "k7 Systems", "Cedar Networks", "Zenix"]
)
def test_sibilant_endings_are_detected(name):
    assert ends_in_sibilant(name)


@pytest.mark.parametrize("name", ["Cascade", "Clay", "MediPath", "Scale AI", "Keppel"])
def test_non_sibilant_endings_are_not_flagged(name):
    assert not ends_in_sibilant(name)


# --- company: real defects -----------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Scraped LinkedIn headlines leaking into `company`.
        ("Canopus GBS | SAP Consulting | AI & Automation |", "Canopus GBS"),
        ("Technosoft Automotive | Yana Automotive Solution", "Technosoft Automotive"),
        ("DevTrial | We Build Tests", "DevTrial"),
        # Glyphs and sentence punctuation that read wrong mid-sentence.
        ("Westcor Land Title Insurance Company®", "Westcor Land Title Insurance"),
        # The "!" is brand styling, not a sentence end — dropped, never truncated at.
        (
            "All About You! Collaborative Health Care Services LLC.",
            "All About You Collaborative Health Care Services",
        ),
        # Legal suffixes — the mail-merge tell.
        ("MediPath, Inc.", "MediPath"),
        ("Teladoc Health, Inc.", "Teladoc Health"),
        ("S&P Global Inc.", "S&P Global"),
        ("Keppel Ltd.", "Keppel"),
        ("Elastic N.V.", "Elastic"),
        ("Carlson Capital, L.P.", "Carlson Capital"),
        ("k7 Systems, Inc.", "k7 Systems"),
        ("C3.ai, Inc.", "C3.ai"),
        ("B. Riley Principal 150 Merger Corp.", "B. Riley Principal 150 Merger"),
        ("AIN - Associates In Nephrology, S.C.", "AIN - Associates In Nephrology"),
        ("Planet Labs PBC", "Planet Labs"),
        ("Sandy Spring Bancorp, Inc.", "Sandy Spring"),
        ("McNeil & Co.", "McNeil"),  # dangling "&" also removed
        # Trailing parenthetical qualifiers.
        ("Berkley Risk (a Berkley Company)", "Berkley Risk"),
        ("Aziro (formerly MSys Technologies)", "Aziro"),
        ("Euronet Merchant Services APAC (f.k.a Pure Commerce)", "Euronet Merchant Services APAC"),
    ],
)
def test_real_company_defects_are_repaired(raw, expected):
    assert clean_company(raw) == expected


@pytest.mark.parametrize(
    "name",
    [
        "Cascade",
        "Clay",
        "Scale AI",
        "PYMNTS",  # genuinely capitalized brand — left alone
        "Gears & Vectors",
        "Get Well",
        "Signzy",
        "Johnson & Johnson MedTech",
        "Edge",
    ],
)
def test_clean_company_leaves_good_names_untouched(name):
    assert clean_company(name) == name


def test_company_clean_is_idempotent():
    once = clean_company("Canopus GBS | SAP Consulting | AI & Automation |")
    assert clean_company(once) == once


def test_company_stripping_never_empties_a_name():
    # A company legitimately *named* like a suffix survives — the transform reverts
    # rather than producing "" or a 1-char stub.
    for name in ("Co", "Inc", "Ltd", "SA"):
        assert clean_company(name) == name


# --- signal clause -------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Fits whole — never split a value that needed no splitting.
        ("Agent Control Layer launch (2026-06-09)", "Agent Control Layer launch (2026-06-09)"),
        # Too long: take the lead fact, splitting only OUTSIDE parentheses so the investor
        # list inside the bracket is not cut in half.
        (
            '$243M Series C @ $1.25B (2025-07-29, a16z + Oak HC/FT) + move into "Chart Awareness" '
            "(multi-agent clinical suite across 40+ health systems)",
            "$243M Series C @ $1.25B (2025-07-29, a16z + Oak HC/FT)",
        ),
        # A short lead is a bare product name; the news is the next segment.
        (
            "FinThrive Fusion — agentic AI-powered RCM platform unveiled at HIMSS (2026-03-09); "
            "autonomous agents that code claims and act on reimbursement risk",
            "agentic AI-powered RCM platform unveiled at HIMSS (2026-03-09)",
        ),
    ],
)
def test_signal_clause_reduces_research_to_a_sendable_span(raw, expected):
    assert signal_clause(raw) == expected


@pytest.mark.parametrize(
    ("raw", "why"),
    [
        # An intent-topic score is a targeting input, not an event to quote back.
        ("machine learning & artificial intelligence (intent score 81)", "intent label"),
        ("emerging tech (intent score 84)", "intent label"),
        ("security (intent score 81)", "intent label"),
        # Research recording the ABSENCE of a signal. Sending this opens a cold email by
        # telling the prospect we found nothing about them.
        (
            "No dated funding round or launch event confirmed in research, StitchFin still "
            "appears bootstrapped",
            "no signal",
        ),
        ("No dated funding/launch signal verifiable for this company", "no signal"),
        # Explicitly unverified.
        (
            "AI-powered clinical Care Pathways coordinating agents across hospital networks in "
            "real time. *(Dated launch/funding signal to confirm at outreach.)*",
            "unverified",
        ),
        ("", "empty"),
        ("short", "too short"),
    ],
)
def test_signal_clause_fails_closed(raw, why):
    assert signal_clause(raw) == "", why


def test_a_verbose_lead_fact_never_falls_through_to_a_later_fragment():
    # Regression: taking any segment that merely *fits* yielded mid-sentence fragments
    # ("scaling LuLu", "an autonomous agent holding real payment authorit").
    zest = (
        "oversubscribed customer-led round (2025-11-05, SchoolsFirst / Members 1st / ORNL / "
        "Truliant / Citi Ventures) earmarked for full-borrower-journey automation + scaling LuLu"
    )
    assert signal_clause(zest) == ""


def test_signal_clause_is_a_verbatim_span_never_a_paraphrase():
    raw = "Acquired Aster for agentic AI (2026-06-02) + hiring a platform team"
    out = signal_clause(raw)
    assert out and out in raw


def test_signal_clause_never_returns_a_banned_em_dash():
    assert "—" not in signal_clause("Launch of the thing — with detail (2026-01-01)")


def test_signal_clause_is_idempotent():
    once = signal_clause("Agent Control Layer launch (2026-06-09) + more detail here")
    assert signal_clause(once) == once


# --- row-level judgement -------------------------------------------------


def _row(**kw) -> dict:
    base = {
        "first": "Chris",
        "last": "Renner",
        "email": "chris@cascade.example",
        "company": "Cascade",
        "company_domain": "cascade.example",
    }
    base.update(kw)
    return base


def test_clean_row_passes_a_good_row_through_unchanged():
    row = _row()
    assert clean_row(row) == row
    assert check_row(row) == []
    assert not blocks(row)


@pytest.mark.parametrize(
    ("kw", "rule"),
    [
        ({"first": ""}, "first-name-empty"),
        ({"first": "there"}, "first-name-placeholder"),
        ({"first": "\U0001f366"}, "first-name-unrenderable"),
        ({"company": ""}, "company-empty"),
        ({"company": "DevTrial | We Build Tests"}, "company-headline"),
        ({"company": "Westcor Land Title Insurance Company®"}, "company-trademark-glyph"),
        ({"company": "All About You! Collaborative"}, "company-sentence-punct"),
        ({"company": "Acme Corp."}, "company-trailing-period"),
        ({"email": "not-an-email"}, "email-malformed"),
        ({"email": "info@cascade.example"}, "email-role-address"),
    ],
)
def test_blocking_findings(kw, rule):
    found = check_row(_row(**kw))
    assert rule in {f.rule for f in found}
    assert any(f.level == "block" for f in found)
    assert blocks(_row(**kw))


@pytest.mark.parametrize(
    ("kw", "rule"),
    [
        # No trailing period — a period mid-sentence is a block, tested above.
        ({"company": "Sandy Spring Bancorp, Inc"}, "company-legal-suffix"),
        ({"company": "Berkley Risk (a Berkley Company)"}, "company-parenthetical"),
        ({"company": "Universal Property & Casualty Insurance Company"}, "company-long"),
        ({"email": "chris@gmail.com", "company_domain": ""}, "email-freemail"),
        ({"company_domain": "other.com"}, "email-domain-mismatch"),
        ({"company_domain": "geo-blocked-site.azurewebsites.net"}, "company-domain-junk"),
    ],
)
def test_advisory_findings_never_block(kw, rule):
    found = check_row(_row(**kw))
    assert rule in {f.rule for f in found}
    assert all(f.level == "warn" for f in found if f.rule == rule)
    assert not blocks(_row(**kw))


def test_subdomain_email_is_not_a_domain_mismatch():
    assert not any(
        f.rule == "email-domain-mismatch"
        for f in check_row(
            _row(email="chris@mail.cascade.example", company_domain="cascade.example")
        )
    )


@pytest.mark.parametrize(
    ("email", "domain"),
    [
        # Regression: lstrip("www.") strips a character SET, so "wavelet.example" became
        # "andb.ai" and every w-leading domain reported a phantom mismatch.
        ("dev@wavelet.example", "wavelet.example"),
        ("a@wideloop.example", "wideloop.example"),
        ("a@cascade.example", "www.cascade.example"),
    ],
)
def test_www_prefix_stripping_does_not_eat_leading_domain_characters(email, domain):
    assert not any(
        f.rule == "email-domain-mismatch"
        for f in check_row(_row(email=email, company_domain=domain))
    )


@pytest.mark.parametrize(
    ("kw", "rule"),
    [
        ({"last": "Reece \U0001f947✨"}, "last-name-symbols"),
        ({"last": "Warrix, Cpa, Mba"}, "last-name-credentials"),
        ({"title": "Vice President | Compliance"}, "title-headline"),
    ],
)
def test_last_and_title_defects_are_advisory_at_row_level(kw, rule):
    # Advisory here because today's copy renders neither field. The merge-render linter
    # escalates them to errors when a template actually uses {{Last Name}}/{{Job Title}}.
    found = check_row(_row(**kw))
    assert rule in {f.rule for f in found}
    assert not blocks(_row(**kw))


def test_clean_row_only_touches_fields_the_caller_carries():
    # Inventing a key would change the row's shape and break fixed-schema CSV writers.
    assert clean_row({"first": "chris"}) == {"first": "Chris"}


def test_clean_row_repairs_every_merge_field():
    dirty = {
        "first": "Dr.rani",
        "last": "Warrix, Cpa, Mba",
        "email": "a@x.com",
        "company": "DevTrial | We Build Tests",
        "title": "Vice President | Compliance",
    }
    assert clean_row(dirty) == {
        "first": "Rani",
        "last": "Warrix",
        "email": "a@x.com",
        "company": "DevTrial",
        "title": "Vice President, Compliance",
    }


def test_clean_row_derives_first_before_cleaning_last():
    # The swapped-field recovery reads the RAW last column; cleaning last first would
    # still work here, but the ordering is load-bearing when last carries credentials.
    out = clean_row(
        {"first": "\U0001f366", "last": "Marcus Webb, Cpa", "email": "c@x.com", "company": "X"}
    )
    assert out["first"] == "Marcus"
    assert out["last"] == "Marcus Webb"


def test_clean_row_repairs_then_check_row_clears():
    dirty = _row(
        first="\U0001f366",
        last="Marcus Webb",
        email="marcus.webb@summitline.example",
        company="DevTrial | We Build Tests",
        company_domain="summitline.example",
    )
    assert blocks(dirty)
    cleaned = clean_row(dirty)
    assert cleaned["first"] == "Marcus"
    assert cleaned["company"] == "DevTrial"
    assert not blocks(cleaned)


# --- signal freshness ----------------------------------------------------
#
# Every clause below is a real value from ready-to-load-signal.csv on 2026-07-29.

AS_OF = datetime.date(2026, 7, 29)


@pytest.mark.parametrize(
    "clause,expected",
    [
        ("Agent Control Layer launch (2026-06-09)", datetime.date(2026, 6, 9)),
        ("Ask Atlas launched May 2026", datetime.date(2026, 5, 1)),
        (
            'Sept 2025 "Agentic AI Breakthrough" (MCP client + Sales/Knowledge/HR agents)',
            datetime.date(2025, 9, 1),
        ),
        ("CoreWeave completed its $1.4B acquisition of W&B, 2025-05-05", datetime.date(2025, 5, 5)),
        # newest of several wins — recency is what the opener claims
        (
            "#312 on the 2025 Deloitte Technology Fast 500, 244% revenue growth over "
            "FY2021-FY2024 (2025-11-20)",
            datetime.date(2025, 11, 20),
        ),
        ("Launched CardioOne Connect", None),
        ("Agentic CKYC platform (AI-native, agent-orchestrated CERSAI/KYC pipeline)", None),
    ],
)
def test_signal_latest_date(clause, expected):
    assert signal_latest_date(clause) == expected


def test_latest_date_ignores_impossible_dates():
    """A date-shaped string that is not a date must not raise or be believed."""
    assert signal_latest_date("filed 2026-13-45") is None


@pytest.mark.parametrize(
    "clause",
    [
        "Agent Control Layer launch (2026-06-09)",  # 7 weeks
        "Acquired Aster for agentic AI (2026-06-02)",  # 8 weeks
        "$20M (EUR 17M) Series A led by Cathay Innovation and K Fund, 2026-01-08",  # 6.7 mo
    ],
)
def test_fresh_signals_pass(clause):
    assert signal_is_fresh(clause, as_of=AS_OF)


@pytest.mark.parametrize(
    "clause",
    [
        # 15 months — the case that made this gate exist
        "CoreWeave completed its $1.4B acquisition of W&B, 2025-05-05",
        "AI-Powered Continuous Monitoring launch (2025-01-13)",  # 18 months
        "$300M growth investment from Spectrum Equity, 2025-07-22",  # 12 months
        "Launched CardioOne Connect",  # undated
        "DeepAgent's agentic push (heavy Nov 2025 review coverage)",  # 9 months
    ],
)
def test_stale_or_undated_signals_fail(clause):
    assert not signal_is_fresh(clause, as_of=AS_OF)


def test_future_date_is_not_fresh():
    """A date ahead of today is bad research, not breaking news."""
    assert not signal_is_fresh("launch (2027-01-01)", as_of=AS_OF)


def test_freshness_is_independent_of_shape():
    """The two gates are separate: this clause is well-formed AND stale."""
    stale = "CoreWeave completed its $1.4B acquisition of W&B, 2025-05-05"
    assert signal_clause(stale) == stale, "shape gate passes it"
    assert not signal_is_fresh(stale, as_of=AS_OF), "freshness gate rejects it"


def test_boundary_is_inclusive():
    edge = (AS_OF - datetime.timedelta(days=SIGNAL_MAX_AGE_DAYS)).isoformat()
    assert signal_is_fresh(f"launch ({edge})", as_of=AS_OF)
    older = (AS_OF - datetime.timedelta(days=SIGNAL_MAX_AGE_DAYS + 1)).isoformat()
    assert not signal_is_fresh(f"launch ({older})", as_of=AS_OF)
