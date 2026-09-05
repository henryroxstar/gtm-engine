"""Merge-field hygiene tests.

Every case in the "real defects" classes below keeps the SHAPE of a value that appeared
in ``content/<profile>/prospects/sequences/ready-to-load.csv`` on 2026-07-28 and passed the
outreach copy linter with zero errors — names, figures and investors are fictional (the
shape is what broke the parser; the identity is not needed to keep it fixed). They are the
regression floor: the copy gate cannot see these, so this module has to.
"""

from __future__ import annotations

import datetime

import pytest

from gtm_core.merge_hygiene import (
    SIGNAL_MAX_AGE_DAYS,
    bare_host,
    blocks,
    check_row,
    clean_company,
    clean_first_name,
    clean_last_name,
    clean_row,
    clean_segment,
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


@pytest.mark.parametrize("name", ["Cascade", "Clay", "MediPath", "Scale AI", "Eastvale"])
def test_non_sibilant_endings_are_not_flagged(name):
    assert not ends_in_sibilant(name)


# --- company: real defects -----------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Scraped LinkedIn headlines leaking into `company`.
        ("Canopy GBS | SAP Consulting | AI & Automation |", "Canopy GBS"),
        ("Technosync Automotive | Yara Automotive Solution", "Technosync Automotive"),
        ("DevTrial | We Build Tests", "DevTrial"),
        # Glyphs and sentence punctuation that read wrong mid-sentence.
        ("Westvale Land Title Insurance Company®", "Westvale Land Title Insurance"),
        # The "!" is brand styling, not a sentence end — dropped, never truncated at.
        (
            "All For You! Collaborative Health Care Services LLC.",
            "All For You Collaborative Health Care Services",
        ),
        # Legal suffixes — the mail-merge tell.
        ("MediPath, Inc.", "MediPath"),
        ("Telavista Health, Inc.", "Telavista Health"),
        ("R&D Global Inc.", "R&D Global"),
        ("Kestrel Ltd.", "Kestrel"),
        ("Elastique N.V.", "Elastique"),
        ("Carver Capital, L.P.", "Carver Capital"),
        ("k9 Systems, Inc.", "k9 Systems"),
        ("Q3.ai, Inc.", "Q3.ai"),
        ("B. Rowan Principal 150 Merger Corp.", "B. Rowan Principal 150 Merger"),
        ("AIC - Associates In Cardiology, S.C.", "AIC - Associates In Cardiology"),
        ("Orbit Labs PBC", "Orbit Labs"),
        ("Stony Brook Bancorp, Inc.", "Stony Brook"),
        ("McNair & Co.", "McNair"),  # dangling "&" also removed
        # Trailing parenthetical qualifiers.
        ("Halyard Risk (a Halyard Company)", "Halyard Risk"),
        ("Azura (formerly MSyn Technologies)", "Azura"),
        (
            "Euromint Merchant Services APAC (f.k.a Plain Commerce)",
            "Euromint Merchant Services APAC",
        ),
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
        "Good Form",
        "Wideloop",
        "Gears & Vectors MedTech",
        "Edge",
    ],
)
def test_clean_company_leaves_good_names_untouched(name):
    assert clean_company(name) == name


def test_company_clean_is_idempotent():
    once = clean_company("Canopy GBS | SAP Consulting | AI & Automation |")
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
        # A trailing date stamp is dropped: freshness is `signal_observed`'s column and the
        # opener states what happened, not when. The linter's `signal-stray-digit` fails a
        # clause that keeps it, so returning it was producing output our own gate refused.
        ("Agent Control Layer launch (2026-06-09)", "Agent Control Layer launch"),
        # Too long: take the lead fact, splitting only OUTSIDE parentheses so the investor
        # list inside the bracket is not cut in half.
        (
            '$210M Series C @ $1.1B (2025-07-29, Halyard + Cirrus HC/FT) + move into "Ward Awareness" '
            "(multi-agent clinical suite across 40+ health systems)",
            "$210M Series C @ $1.1B (2025-07-29, Halyard + Cirrus HC/FT)",
        ),
        # A short lead is a bare product name; the news is the next segment.
        (
            "Medipath Fusion — agentic AI-powered RCM platform unveiled at HIMSS (2026-03-09); "
            "autonomous agents that code claims and act on reimbursement risk",
            "agentic AI-powered RCM platform unveiled at HIMSS",
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
            "No dated funding round or launch event confirmed in research, Summitline still "
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
    # ("scaling Lila", "an autonomous agent holding real payment authorit").
    zest = (
        "oversubscribed customer-led round (2025-11-05, Riverbend FCU / Members First / "
        "Summitline / Halyard Ventures) earmarked for full-borrower-journey automation + scaling Lila"
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
        ({"company": "Westvale Land Title Insurance Company®"}, "company-trademark-glyph"),
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
        ({"company": "Stony Brook Bancorp, Inc"}, "company-legal-suffix"),
        ({"company": "Halyard Risk (a Halyard Company)"}, "company-parenthetical"),
        ({"company": "Universal Homestead & Casualty Insurance Company"}, "company-long"),
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
        # "avelet.example" and every w-leading domain reported a phantom mismatch.
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
    ("raw", "host"),
    [
        ("cascade.example", "cascade.example"),
        ("www.cascade.example", "cascade.example"),
        ("https://www.cascade.example", "cascade.example"),
        ("cascade.example/sg", "cascade.example"),
        ("cascade.example/our-reach/singapore", "cascade.example"),
        ("https://cascade.example/en?lang=en#top", "cascade.example"),
        ("cascade.example:443", "cascade.example"),
        ("cascade.example.", "cascade.example"),
        ("sub.cascade.example/a/b", "sub.cascade.example"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_bare_host_reduces_a_domain_field_to_the_host_it_names(raw, host):
    assert bare_host(raw) == host


def test_a_landing_page_url_in_the_domain_column_is_not_a_domain_mismatch():
    """Providers park a landing-page URL in the domain column ("acme.example/sg"). The host
    matches; only the field is dirty. Measured 2026-08-20 on the live lists: every
    non-bare-host value produced a false mismatch — 10 of 52 mismatch findings."""
    findings = check_row(_row(email="chris@cascade.example", company_domain="cascade.example/en"))
    assert not [f for f in findings if f.rule == "email-domain-mismatch"]


def test_but_the_dirty_domain_field_is_still_reported_as_junk():
    """Normalising for comparison must not hide the defect: right finding, right name."""
    findings = check_row(_row(email="chris@cascade.example", company_domain="cascade.example/en"))
    junk = [f for f in findings if f.rule == "company-domain-junk"]
    assert junk and "cascade.example/en" in junk[0].detail


def test_a_genuine_mismatch_still_fires_through_the_normaliser():
    """The negative control — normalising must not make the rule unable to fire."""
    assert [
        f
        for f in check_row(
            _row(email="chris@summitline.example", company_domain="cascade.example/en")
        )
        if f.rule == "email-domain-mismatch"
    ]


@pytest.mark.parametrize(
    ("kw", "rule"),
    [
        ({"last": "Reece \U0001f947✨"}, "last-name-symbols"),
        ({"last": "Warrix, Cpa, Mba"}, "last-name-credentials"),
        ({"last": "K"}, "last-name-initial"),
        ({"title": "Vice President | Compliance"}, "title-headline"),
        (
            {"title": "Deputy director, treasury, 20 years in institutional portfolio management"},
            "title-bio-fragment",
        ),
    ],
)
def test_last_and_title_defects_are_advisory_at_row_level(kw, rule):
    # Advisory here because today's copy renders neither field. The merge-render linter
    # escalates them to errors when a template actually uses {{Last Name}}/{{Job Title}}.
    found = check_row(_row(**kw))
    assert rule in {f.rule for f in found}
    assert not blocks(_row(**kw))


@pytest.mark.parametrize("last", ["Yu", "Ma", "Ng", "Li", "Oh", "Ha"])
def test_two_letter_surnames_are_not_mistaken_for_initials(last):
    """The rule keys on a SINGLE letter, not on shortness — these are real surnames, and
    flagging them would train the operator to ignore the rule."""
    assert not any(f.rule == "last-name-initial" for f in check_row(_row(last=last)))


@pytest.mark.parametrize(
    "company",
    [
        "B. Rowan Principal 150 Merger",  # the shape of the row found in the 2026-08-11 spot check
        "Churchyard Capital Acquisition Corp",
        "Verity Holdco",
    ],
)
def test_deal_vehicle_captured_as_the_company_blocks(company):
    """A SPAC/shell name renders mid-sentence as the employer and reads plainly wrong."""
    found = check_row(_row(company=company))
    assert "company-transaction-entity" in {f.rule for f in found}
    assert blocks(_row(company=company))


@pytest.mark.parametrize(
    "company", ["Mergermarket", "Merger Market Insights", "Acquisition.example"]
)
def test_transaction_rule_does_not_swallow_real_brands(company):
    assert not any(f.rule == "company-transaction-entity" for f in check_row(_row(company=company)))


@pytest.mark.parametrize(
    "title",
    [
        "Chief Information Security Officer & Vice President of Information Security",
        "Senior Vice President, Director of Regulatory Management - Chief Compliance Officer",
    ],
)
def test_long_but_genuine_titles_are_not_flagged_as_bios(title):
    """Length is not the signal — these are real 74- and 82-character titles."""
    assert not any(f.rule == "title-bio-fragment" for f in check_row(_row(title=title)))


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
        ("Ask Meridian launched May 2026", datetime.date(2026, 5, 1)),
        (
            'Sept 2025 "Agentic AI Breakthrough" (MCP client + Sales/Knowledge/HR agents)',
            datetime.date(2025, 9, 1),
        ),
        (
            "Nimbus Compute completed its $1.4B acquisition of R&L, 2025-05-05",
            datetime.date(2025, 5, 5),
        ),
        # newest of several wins — recency is what the opener claims
        (
            "#412 on the 2025 Technology Fast 500 list, 244% revenue growth over "
            "FY2021-FY2024 (2025-11-20)",
            datetime.date(2025, 11, 20),
        ),
        ("Launched PulseOne Connect", None),
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
        "Acquired Astral for agentic AI (2026-06-02)",  # 8 weeks
        "$20M (EUR 17M) Series A led by Meridian Ventures and Q Fund, 2026-01-08",  # 6.7 mo
    ],
)
def test_fresh_signals_pass(clause):
    assert signal_is_fresh(clause, as_of=AS_OF)


@pytest.mark.parametrize(
    "clause",
    [
        # 15 months — the case that made this gate exist
        "Nimbus Compute completed its $1.4B acquisition of R&L, 2025-05-05",
        "AI-Powered Continuous Monitoring launch (2025-01-13)",  # 18 months
        "$300M growth investment from Halyard Equity, 2025-07-22",  # 12 months
        "Launched PulseOne Connect",  # undated
        "DeepRelay's agentic push (heavy Nov 2025 review coverage)",  # 9 months
    ],
)
def test_stale_or_undated_signals_fail(clause):
    assert not signal_is_fresh(clause, as_of=AS_OF)


def test_future_date_is_not_fresh():
    """A date ahead of today is bad research, not breaking news."""
    assert not signal_is_fresh("launch (2027-01-01)", as_of=AS_OF)


def test_freshness_is_independent_of_shape():
    """The two gates are separate: this clause is well-formed AND stale.

    Freshness reads the research value, which keeps its date; the clause drops the
    stamp. Both must be read from the source column, never from each other's output.
    """
    stale = "Nimbus Compute completed its $1.4B acquisition of R&L, 2025-05-05"
    assert signal_clause(stale) == "Nimbus Compute completed its $1.4B acquisition of R&L", (
        "shape gate passes it, minus the stamp"
    )
    assert not signal_is_fresh(stale, as_of=AS_OF), "freshness gate rejects it"


def test_boundary_is_inclusive():
    edge = (AS_OF - datetime.timedelta(days=SIGNAL_MAX_AGE_DAYS)).isoformat()
    assert signal_is_fresh(f"launch ({edge})", as_of=AS_OF)
    older = (AS_OF - datetime.timedelta(days=SIGNAL_MAX_AGE_DAYS + 1)).isoformat()
    assert not signal_is_fresh(f"launch ({older})", as_of=AS_OF)


# --- segment -------------------------------------------------------------
#
# Measured 2026-08-21 across 87 prospect CSVs / 36,210 rows: 'Enterprise' 17,318,
# 'Startup' 9,204, 'startup' 5,488, 'enterprise' 3,941, 'unspecified' 259. A 2:1 split
# with no majority convention, so any comparison against the stored string has to
# normalise first. `prospects_import` already writes lowercase and already capitalises
# on the way out to the HubSpot column, which is what makes lowercase the canonical form.


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Enterprise", "enterprise"),
        ("Startup", "startup"),
        ("enterprise", "enterprise"),
        ("startup", "startup"),
        ("unspecified", "unspecified"),
        ("  Enterprise  ", "enterprise"),
        ("ENTERPRISE", "enterprise"),
    ],
)
def test_clean_segment_canonicalises_a_known_segment(raw, expected):
    assert clean_segment(raw) == expected


@pytest.mark.parametrize("raw", ["SMB", "Mid-Market", "Series B", ""])
def test_clean_segment_leaves_an_unknown_value_alone(raw):
    """Case repair only. Mapping "Mid-Market" onto a known segment would be inventing one."""
    assert clean_segment(raw) == raw.strip()


def test_clean_segment_is_idempotent():
    for raw in ("Enterprise", "startup", "SMB", ""):
        assert clean_segment(clean_segment(raw)) == clean_segment(raw)


def test_check_row_warns_on_a_noncanonical_segment():
    findings = check_row(
        {
            "first": "Ada",
            "company": "Halden Systems",
            "email": "ada@halden.example",
            "segment": "Enterprise",
        }
    )
    rules = [f.rule for f in findings]
    assert "segment-noncanonical" in rules
    assert all(f.level == "warn" for f in findings if f.field == "segment")


def test_check_row_warns_on_an_unrecognised_segment():
    findings = check_row(
        {
            "first": "Ada",
            "company": "Halden Systems",
            "email": "ada@halden.example",
            "segment": "Mid-Market",
        }
    )
    assert "segment-unknown" in [f.rule for f in findings]


def test_check_row_says_nothing_about_a_segment_column_the_row_does_not_carry():
    """Most merge CSVs have no segment column; an absent optional field is not a defect."""
    findings = check_row(
        {"first": "Ada", "company": "Halden Systems", "email": "ada@halden.example"}
    )
    assert not [f for f in findings if f.field == "segment"]


def test_clean_row_normalises_segment_only_when_the_row_carries_one():
    assert clean_row({"first": "Ada", "company": "Acme", "segment": "Enterprise"})["segment"] == (
        "enterprise"
    )
    assert "segment" not in clean_row({"first": "Ada", "company": "Acme"})


def test_a_noncanonical_segment_never_blocks_a_row():
    """Spelling is advisory: it must not stop a well-formed row from being mailed."""
    assert not blocks(
        {
            "first": "Ada",
            "company": "Halden Systems",
            "email": "ada@halden.example",
            "segment": "Enterprise",
        }
    )


# --- qualification score ---------------------------------------------------
#
# Regression for the 2026-07-24 / 2026-08-11 bulk runs: the enrichment queue's weighted SPEND
# ranking (0-53, from icp-scoring.toml) was written into GTM_Score, the per-account
# QUALIFICATION verdict (a 0-12 rubric from icp-personas.md). 749 published rows carry the wrong
# scale, and one run invented a "Tier C" to describe the bottom of a distribution the A/B split
# was never shaped for. Nothing failed at the time: the CSV schema declares GTM_Score as
# "numeric, no denominator", so a 53 was as acceptable as a 9.
#
# The collision is fixed at source (the queue column is now `icp_backlog_score`). This is the
# second line, for the next scorer that reaches for the same column.


@pytest.mark.parametrize("raw", ["53", "13", "40", "-1"])
def test_check_row_warns_on_a_score_that_cannot_be_a_qualification_verdict(raw):
    findings = check_row({"first": "Ada", "company": "Halden Systems", "score": raw})
    assert [f.rule for f in findings if f.field == "score"] == ["score-out-of-range"]


@pytest.mark.parametrize("raw", ["0", "5", "9", "10", "12", "8.0"])
def test_check_row_accepts_every_plausible_rubric_verdict(raw):
    """Includes the ceilings this machinery documents (10 and 12) and the bound itself."""
    findings = check_row({"first": "Ada", "company": "Halden Systems", "score": raw})
    assert not [f for f in findings if f.field == "score"]


def test_check_row_warns_on_a_non_numeric_score():
    findings = check_row({"first": "Ada", "company": "Halden Systems", "score": "A"})
    assert [f.rule for f in findings if f.field == "score"] == ["score-not-numeric"]


@pytest.mark.parametrize("row", [{}, {"score": ""}, {"score": None}])
def test_check_row_says_nothing_about_a_score_the_row_does_not_carry(row):
    """Same arrangement as segment: an absent or blank optional column is not a defect."""
    findings = check_row({"first": "Ada", "company": "Halden Systems", **row})
    assert not [f for f in findings if f.field == "score"]


def test_the_real_july_bulk_scores_would_have_been_caught():
    """The actual values that shipped: the vibebulk run's range was 4-53.

    A 4 is not caught and cannot be — it is indistinguishable from a real low verdict. That is the
    documented limit of a magnitude check, and the reason the queue column was renamed at source.
    """
    caught = [
        v
        for v in (4, 13, 18, 19, 24, 34, 43, 53)
        if any(f.rule == "score-out-of-range" for f in check_row({"score": str(v)}))
    ]
    assert caught == [13, 18, 19, 24, 34, 43, 53], caught


def test_a_two_token_given_name_is_a_name():
    """Until 2026-09-04 the pattern had no space in it, so every romanised Chinese, Malay or
    Indian given name written as two tokens was blocked as "not a name". On a Singapore list
    that rejects the market the campaign is aimed at."""
    from gtm_core.merge_hygiene.names import _NAME_CHARS_RE

    for name in ("Hui Jie", "Wei Ming", "Siti Nurhaliza", "Mary Anne", "Jean Luc"):
        assert _NAME_CHARS_RE.match(name), name


def test_junk_is_still_not_a_name():
    from gtm_core.merge_hygiene.names import _NAME_CHARS_RE

    for junk in ("J0hn", "a b c d", "", "Hui  Jie", "\U0001f366", " Leading"):
        assert not _NAME_CHARS_RE.match(junk), junk
