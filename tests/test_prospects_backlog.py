"""Tests for gtm_core.prospects_backlog — deterministic backlog → enrichment-queue selection."""

from __future__ import annotations

import csv

import pytest

from gtm_core import prospects_backlog as pb

# --- fixtures ---------------------------------------------------------------

_RUBRIC = """\
rubric_version = "test"

[[cohort]]
name = "fs"
weight = 30
keywords = ["bank", "financ"]

[[cohort]]
name = "software"
weight = 14
keywords = ["software"]

[segment]
enterprise_floor = 1000
enterprise_bonus = 10
midmarket_floor = 200
midmarket_bonus = 5

[intent]
high_score = 75
high_bonus = 8
elevated_score = 60
elevated_bonus = 3

[geo_bonus]
"united states" = 5
singapore = 4
"""

_IMPORT_HEADER = [
    "business_name",
    "business_domain",
    "business_country_name",
    "business_naics_description",
    "business_business_description",
    "business_number_of_employees_range",
    "business_yearly_revenue_range",
    "business_business_intent_topics",
    "business_id",
]


def _setup(tmp_path, *, imports, master=None, markets="[United States, Singapore]"):
    """Build a throwaway profile + content tree; return (content_root, profiles_root)."""
    content_root = tmp_path / "content"
    profiles_root = tmp_path / "profiles"
    profile = "acme"

    (profiles_root / profile / "knowledge").mkdir(parents=True)
    (profiles_root / profile / "knowledge" / "icp-scoring.toml").write_text(
        _RUBRIC, encoding="utf-8"
    )
    (profiles_root / profile / "PROFILE.md").write_text(
        f"target_markets: {markets}\n", encoding="utf-8"
    )

    imp_dir = content_root / profile / "prospects" / "imports"
    imp_dir.mkdir(parents=True)
    with (imp_dir / "pull.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(_IMPORT_HEADER)
        w.writerows(imports)

    if master is not None:
        pool = content_root / profile / "prospects" / "sequences" / ".pool"
        pool.mkdir(parents=True)
        with (pool / "master-list.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["company", "company_domain"])
            w.writerows(master)

    return content_root, profiles_root


def _row(name, domain, country, naics, emp="", rev="", intent="", bid=None):
    return [name, domain, country, naics, "", emp, rev, intent, bid or (domain or name)]


# --- rubric -----------------------------------------------------------------


def test_load_rubric_missing_is_hard_error(tmp_path):
    (tmp_path / "profiles" / "acme" / "knowledge").mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        pb.load_rubric("acme", tmp_path / "profiles")


def test_load_rubric_lowercases_keywords_and_geo(tmp_path):
    _, profiles_root = _setup(tmp_path, imports=[])
    rubric = pb.load_rubric("acme", profiles_root)
    assert rubric["cohort"][0]["keywords"] == ["bank", "financ"]
    assert rubric["geo_bonus"]["united states"] == 5  # normalized key


# --- scoring ----------------------------------------------------------------


def test_score_account_sums_cohort_segment_intent_geo():
    rubric = {
        "cohort": [{"name": "fs", "weight": 30, "keywords": ["bank"]}],
        "segment": {
            "enterprise_floor": 1000,
            "enterprise_bonus": 10,
            "midmarket_floor": 200,
            "midmarket_bonus": 5,
        },
        "intent": {"high_score": 75, "high_bonus": 8, "elevated_score": 60, "elevated_bonus": 3},
        "geo_bonus": {"united states": 5},
    }
    rec = {
        "industry": "Commercial Banking",
        "description": "",
        "employees_range": "5001-10000",
        "top_intent_score": 80,
        "country": "United States",
    }
    score, cohort = pb.score_account(rec, rubric)
    assert cohort == "fs"
    assert score == 30 + 10 + 8 + 5


def test_score_account_midmarket_and_elevated_intent():
    rubric = {
        "cohort": [{"name": "fs", "weight": 30, "keywords": ["bank"]}],
        "segment": {
            "enterprise_floor": 1000,
            "enterprise_bonus": 10,
            "midmarket_floor": 200,
            "midmarket_bonus": 5,
        },
        "intent": {"high_score": 75, "high_bonus": 8, "elevated_score": 60, "elevated_bonus": 3},
        "geo_bonus": {"singapore": 4},
    }
    rec = {
        "industry": "bank",
        "description": "",
        "employees_range": "500-999",
        "top_intent_score": 65,
        "country": "Singapore",
    }
    score, _ = pb.score_account(rec, rubric)
    assert score == 30 + 5 + 3 + 4


def test_score_account_no_cohort_scores_zero_axis():
    rubric = {
        "cohort": [{"name": "fs", "weight": 30, "keywords": ["bank"]}],
        "segment": {},
        "intent": {},
        "geo_bonus": {},
    }
    score, cohort = pb.score_account(
        {
            "industry": "Widget Manufacturing",
            "description": "",
            "employees_range": "",
            "top_intent_score": 0,
            "country": "",
        },
        rubric,
    )
    assert cohort == ""
    assert score == 0


# --- selection --------------------------------------------------------------


def test_select_excludes_resolved_and_out_of_market(tmp_path):
    imports = [
        _row("Acme Bank", "acmebank.com", "united states", "Commercial Banking"),
        _row("Beta Health", "betahealth.com", "united states", "Software Publishers"),
        _row("Gamma Ltd", "gamma.co.uk", "united kingdom", "Commercial Banking"),  # out of market
        _row(
            "Delta Bank", "deltabank.com", "united states", "Commercial Banking"
        ),  # already resolved
    ]
    master = [["Delta Bank", "deltabank.com"]]
    content_root, profiles_root = _setup(tmp_path, imports=imports, master=master)

    res = pb.select_backlog("acme", content_root=content_root, profiles_root=profiles_root)

    assert res["already_resolved_skipped"] == 1  # Delta
    assert res["out_of_market_skipped"] == 1  # Gamma (UK)
    assert res["queued"] == 2
    companies = {r["company"] for r in res["top_preview"]}
    assert companies == {"Acme Bank", "Beta Health"}


def test_select_ranks_high_cohort_first_and_writes_queue(tmp_path):
    imports = [
        _row("Soft Co", "softco.com", "united states", "Software Publishers"),  # weight 14
        _row(
            "Big Bank", "bigbank.com", "united states", "Commercial Banking", emp="5001-10000"
        ),  # 30+10
    ]
    content_root, profiles_root = _setup(tmp_path, imports=imports)
    res = pb.select_backlog("acme", content_root=content_root, profiles_root=profiles_root)

    with pb.enrichment_queue_path("acme", content_root).open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [r["company"] for r in rows] == ["Big Bank", "Soft Co"]  # higher score first
    assert int(rows[0]["score"]) > int(rows[1]["score"])
    assert res["queued"] == 2


def test_unknown_country_dropped_by_default_included_with_flag(tmp_path):
    imports = [
        _row("Acme Bank", "acmebank.com", "united states", "Commercial Banking"),
        _row("Noloc Bank", "noloc.com", "", "Commercial Banking"),  # blank country
    ]
    content_root, profiles_root = _setup(tmp_path, imports=imports)

    default = pb.select_backlog("acme", content_root=content_root, profiles_root=profiles_root)
    assert default["queued"] == 1
    assert default["unknown_country"] == 1

    lenient = pb.select_backlog(
        "acme", include_unknown_country=True, content_root=content_root, profiles_root=profiles_root
    )
    assert lenient["queued"] == 2


def test_backlog_dedup_by_business_id_and_org_token(tmp_path):
    imports = [
        # same business_id twice, second carries stronger intent -> keep the stronger
        _row(
            "Acme Bank",
            "acmebank.com",
            "united states",
            "Commercial Banking",
            intent='[{"topic":"x","score":40}]',
            bid="B1",
        ),
        _row(
            "Acme Bank",
            "acmebank.com",
            "united states",
            "Commercial Banking",
            intent='[{"topic":"x","score":90}]',
            bid="B1",
        ),
        # different id, same root domain (sub-brand) -> org-token collapse
        _row("Acme Bank Cards", "acmebank.com", "united states", "Commercial Banking", bid="B2"),
    ]
    content_root, profiles_root = _setup(tmp_path, imports=imports)
    accounts = pb.load_backlog_accounts("acme", content_root)
    assert len(accounts) == 1
    assert accounts[0]["top_intent_score"] == 90


# --- batches ----------------------------------------------------------------


def test_batches_chunks_queue_ids(tmp_path):
    imports = [
        _row(f"Bank {i}", f"bank{i}.com", "united states", "Commercial Banking") for i in range(7)
    ]
    content_root, profiles_root = _setup(tmp_path, imports=imports)
    pb.select_backlog("acme", content_root=content_root, profiles_root=profiles_root)

    chunks = pb.batches("acme", batch_size=3, content_root=content_root)
    assert [len(c) for c in chunks] == [3, 3, 1]
    assert sum(len(c) for c in chunks) == 7

    capped = pb.batches("acme", batch_size=3, limit=4, content_root=content_root)
    assert [len(c) for c in capped] == [3, 1]


def test_batches_without_queue_raises(tmp_path):
    content_root, _ = _setup(tmp_path, imports=[])
    with pytest.raises(FileNotFoundError):
        pb.batches("acme", content_root=content_root)
