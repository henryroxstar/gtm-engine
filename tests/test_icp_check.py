"""Tests for gtm_core.icp_check — a read-only critique of the ICP definition itself.

Every check reuses the implementation that already owns its question (prospects_backlog's
matcher, role_vocabulary's persona map, hook_coverage's matrix, prospects_backlog's scorer).
Writing a second matcher here would be the exact failure this package exists to prevent, so
several tests below assert the SHARED matcher agrees with itself, not that two independent
matchers happen to compute the same thing.

Fixtures follow tests/test_prospects_backlog.py's convention: obviously-fictional company
names on `.example`/`.com` (Acme Bank, Soft Co, ...), never real third-party identity (§R9).
"""

from __future__ import annotations

import csv

import pytest

from gtm_core import prospects_backlog as pb

# --- fixtures (mirrors tests/test_prospects_backlog.py's _setup / _IMPORT_HEADER) -------------

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


def _row(name, domain, country, naics, desc="", emp="", rev="", intent="", bid=None):
    return [name, domain, country, naics, desc, emp, rev, intent, bid or (domain or name)]


def _content(tmp_path):
    return tmp_path / "content"


def _write_imports(tmp_path, rows, *, profile="acme"):
    imp_dir = _content(tmp_path) / profile / "prospects" / "imports"
    imp_dir.mkdir(parents=True, exist_ok=True)
    with (imp_dir / "pull.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(_IMPORT_HEADER)
        w.writerows(rows)


def _write_import_bytes(tmp_path, data: bytes, *, profile="acme"):
    imp_dir = _content(tmp_path) / profile / "prospects" / "imports"
    imp_dir.mkdir(parents=True, exist_ok=True)
    (imp_dir / "pull.csv").write_bytes(data)


def _accounts(tmp_path, rows=None, *, profile="acme"):
    _write_imports(tmp_path, rows if rows is not None else _DEFAULT_ROWS, profile=profile)
    return pb.load_backlog_accounts(profile, content_root=_content(tmp_path))


_DEFAULT_ROWS = [
    _row(
        "Northwind Capital Co",
        "brightpath.example",
        "united states",
        "Business Support Services",
        desc="an ai workforce product for small teams",
    ),
    _row(
        "Vertex Platform Inc",
        "vertexplatform.example",
        "united states",
        "Software Publishers",
        desc="an intelligent automation platform for the enterprise",
    ),
]


# --- shared-matcher contract (Phase A done-criterion) ------------------------------------------


def test_keyword_hits_use_the_selectors_own_matcher(tmp_path):
    """The shared-matcher contract. One function, asserted equal on one fixture —
    a critique that disagrees with the selector is worse than no critique."""
    from gtm_core.icp_check.keyword import keyword_hits

    accounts = _accounts(tmp_path)
    ours = keyword_hits("ai workforce", accounts, field="description")

    theirs = pb._cohort_pattern({"description_keywords": ["ai workforce"]}, "description_keywords")
    expected = [a for a in accounts if theirs.search(str(a.get("description", "")).lower())]

    assert [h["company"] for h in ours.matches] == [a["company"] for a in expected]
    assert ours.count == len(expected) == 1


def test_keyword_names_the_rubric_key_the_phrase_would_go_into(tmp_path):
    """0.1(a): a description hit added to `keywords` matches nothing at selection time
    (score_account matches `keywords` against industry only). The tool must say which key."""
    from gtm_core.icp_check.keyword import keyword_hits

    accounts = _accounts(tmp_path)
    desc_hit = keyword_hits("ai workforce", accounts, field="description")
    assert desc_hit.rubric_key == "description_keywords"

    ind_hit = keyword_hits("software", accounts, field="industry")
    assert ind_hit.rubric_key == "keywords"


def test_a_short_stem_matches_word_initially_and_not_mid_word(tmp_path):
    """The `financ` shape: \\b<kw>\\w* must catch 'financial year' and miss 'refinanced'."""
    from gtm_core.icp_check.keyword import keyword_hits

    accounts = [
        {"company": "Has A Financial Year", "description": "our financial year ends in March"},
        {"company": "Already Refinanced", "description": "we refinanced last may"},
    ]
    hits = keyword_hits("financ", accounts, field="description")
    assert hits.count == 1
    assert hits.matches[0]["company"] == "Has A Financial Year"


def test_field_defaults_to_description(tmp_path):
    """0.1(a): the documented methodology tests description, not industry — every one of the
    four hand-run reproductions (88/38/1/1) is a description_keywords hit."""
    from gtm_core.icp_check.keyword import keyword_hits

    accounts = _accounts(tmp_path)
    hits = keyword_hits("ai workforce", accounts)  # no --field
    assert hits.field == "description"
    assert hits.rubric_key == "description_keywords"
    assert hits.count == 1


# --- refuse vs skip on a corrupt row (§4.4) -----------------------------------------------------


def test_an_undecodable_backlog_row_refuses_the_run(tmp_path):
    """§4.4 — skipping LOWERS a hit count, and a lower count reads as 'safe to add'.
    The failure inverts the verdict rather than shrinking it."""
    _write_import_bytes(
        tmp_path,
        b"business_id,business_name,business_business_description\n1,Acme,\xff\xfe bad\n",
    )
    with pytest.raises(UnicodeDecodeError):
        pb.load_backlog_accounts("acme", content_root=_content(tmp_path), strict=True)


def test_the_loaders_default_is_byte_identical_to_today(tmp_path):
    """NEGATIVE CONTROL for the strict flag. This loader feeds the enrichment queue; if
    strict=False ever starts raising, credits stop flowing for a silent reason. Prove the
    default (no `strict=` passed at all) still repairs, exactly as before this change."""
    _write_import_bytes(
        tmp_path,
        b"business_id,business_name,business_business_description\n1,Acme,\xff\xfe bad\n",
    )
    rows = pb.load_backlog_accounts("acme", content_root=_content(tmp_path))  # no strict=
    assert len(rows) == 1, "the default must still repair (errors='ignore'), never raise"


def test_strict_false_is_explicitly_equivalent_to_the_default(tmp_path):
    _write_import_bytes(
        tmp_path,
        b"business_id,business_name,business_business_description\n1,Acme,\xff\xfe bad\n",
    )
    default = pb.load_backlog_accounts("acme", content_root=_content(tmp_path))
    explicit = pb.load_backlog_accounts("acme", content_root=_content(tmp_path), strict=False)
    assert default == explicit


# --- §R18 negative control: a substring matcher would pass the wrong things --------------------


def test_a_substring_matcher_would_fail_the_short_stem_test(monkeypatch):
    """§R18: swap in a naive `kw in blob` matcher and the short-stem test must go RED —
    otherwise the test is only checking that SOME matcher exists, not that it is the shared one.
    Patched on prospects_backlog itself (not a local alias) so this proves keyword.py calls
    THROUGH to whatever the selector's own matcher currently is — the sharing property itself."""
    import gtm_core.icp_check.keyword as kwmod

    def _substring_shim(cohort, key):
        kws = cohort.get(key) or []
        if not kws:
            return None

        class _Shim:
            def search(self, blob):
                return next((kw for kw in kws if kw in blob), None)

        return _Shim()

    monkeypatch.setattr(pb, "_cohort_pattern", _substring_shim)

    accounts = [{"company": "Already Refinanced", "description": "we refinanced last may"}]
    # the substring shim WOULD match "financ" inside "refinanced" -- the defect this
    # test proves the real matcher does not have.
    hits = kwmod.keyword_hits("financ", accounts, field="description")
    assert hits.count == 1, "sanity: the shim itself must exhibit the defect"

    monkeypatch.undo()
    hits = kwmod.keyword_hits("financ", accounts, field="description")
    assert hits.count == 0, "the real, shared matcher must not mid-word match"


# =============================================================================================
# Phase B — `icp check`: the remaining two checks, plus criterion-unqueryable over a rubric.
# Each check must be shown BOTH to fire and to stay silent (§R18) — without the silent half,
# a bug that made every profile fail would look exactly like the check working.
# =============================================================================================

#: Hook CELL TEXT is deliberately meaningless here. These tests only ever assert on the
#: persona axis (does a label normalise onto the role vocabulary?), never on copy — so the
#: cells carry placeholder sentences rather than a tenant's real hooks. Pasting live hook copy
#: into a fixture puts the tenant's own messaging into a file that ships in the public carve;
#: it is not §R9 (that is third-party identity) but it is the same leak in the other direction.
_GRID_MATRIX = """---
source: manual
---
# Hook matrix — a tenant

## Enterprise

| Signal → / Persona ↓ | M&A / consolidation | Compliance event |
|---|---|---|
| **CISO** | "A first sentence." | "A second sentence." |
"""

_MATRIX_WITH_AN_UNMAPPABLE_PERSONA = """---
source: manual
---
# Hook matrix — a tenant

## Enterprise

| Signal → / Persona ↓ | M&A / consolidation | Compliance event |
|---|---|---|
| **Head of Vibes** | "A first sentence." | "A second sentence." |
"""


def _rubric_toml(cohorts: str) -> str:
    return f"""\
rubric_version = "test"

{cohorts}

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
"""


_GOOD_COHORTS = """\
[[cohort]]
name = "agent-factory"
weight = 30
description_keywords = ["ai workforce"]

[[cohort]]
name = "software-devtools"
weight = 14
keywords = ["software"]
"""

#: Every cohort the same weight and no keywords at all: nothing scores, so the queue is
#: arbitrary and still spends credits in that arbitrary order.
_FLAT_COHORTS = """\
[[cohort]]
name = "alpha"
weight = 10

[[cohort]]
name = "beta"
weight = 10
"""


def _profile_tree(
    tmp_path,
    *,
    cohorts: str = _GOOD_COHORTS,
    matrix: str | None = _GRID_MATRIX,
    vocabulary: str | None = None,
    rows=None,
    profile: str = "acme",
):
    """Build a throwaway profile + content tree; return (profiles_root, content_root)."""
    profiles_root = tmp_path / "profiles"
    knowledge = profiles_root / profile / "knowledge"
    knowledge.mkdir(parents=True, exist_ok=True)
    (knowledge / "icp-scoring.toml").write_text(_rubric_toml(cohorts), encoding="utf-8")
    if matrix is not None:
        (knowledge / "hook-matrix.md").write_text(matrix, encoding="utf-8")
    if vocabulary is not None:
        (knowledge / "role-vocabulary.toml").write_text(vocabulary, encoding="utf-8")
    (profiles_root / profile / "PROFILE.md").write_text(
        "target_markets: [United States]\n", encoding="utf-8"
    )
    _write_imports(tmp_path, rows if rows is not None else _DEFAULT_ROWS, profile=profile)
    return profiles_root, _content(tmp_path)


def _check(tmp_path, **kw):
    from gtm_core.icp_check.checks import run_checks

    max_hits = kw.pop("max_hits", 10)
    profiles_root, content_root = _profile_tree(tmp_path, **kw)
    return run_checks(
        "acme", profiles_root=profiles_root, content_root=content_root, max_hits=max_hits
    )


def _codes(findings, code):
    return [f for f in findings if f.startswith(f"{code}:")]


# --- criterion-unqueryable --------------------------------------------------------------------


def test_a_zero_hit_keyword_is_unqueryable(tmp_path):
    """One end of the range: a phrase that selects nothing at all."""
    cohorts = """\
[[cohort]]
name = "agent-factory"
weight = 30
description_keywords = ["quantum abacus"]
"""
    found = _codes(_check(tmp_path, cohorts=cohorts).findings, "criterion-unqueryable")
    assert found and "quantum abacus" in found[0]
    assert "select nothing" in found[0]


def test_a_keyword_over_the_ceiling_is_unqueryable(tmp_path):
    """The other end: a category label so broad it pulls in the whole market."""
    rows = [
        _row(
            f"Co {i}",
            f"co{i}.example",
            "united states",
            "Software Publishers",
            desc="an ai platform",
        )
        for i in range(6)
    ]
    cohorts = """\
[[cohort]]
name = "agent-factory"
weight = 30
description_keywords = ["ai platform"]
"""
    found = _codes(
        _check(tmp_path, cohorts=cohorts, rows=rows, max_hits=2).findings, "criterion-unqueryable"
    )
    assert found and "ai platform" in found[0]
    assert "ceiling of 2" in found[0]


def test_a_precise_keyword_is_silent(tmp_path):
    """NEGATIVE CONTROL. The `voice agent` shape: one hit, well inside the ceiling."""
    assert not _codes(_check(tmp_path).findings, "criterion-unqueryable")


def test_raising_the_ceiling_above_the_hit_count_silences_the_check(tmp_path):
    """§R18 in-process negative control — the ceiling must be what decides, not chance."""
    rows = [
        _row(
            f"Co {i}",
            f"co{i}.example",
            "united states",
            "Software Publishers",
            desc="an ai platform",
        )
        for i in range(6)
    ]
    cohorts = """\
[[cohort]]
name = "agent-factory"
weight = 30
description_keywords = ["ai platform"]
"""
    assert _codes(
        _check(tmp_path, cohorts=cohorts, rows=rows, max_hits=2).findings, "criterion-unqueryable"
    )
    assert not _codes(
        _check(tmp_path, cohorts=cohorts, rows=rows, max_hits=999).findings,
        "criterion-unqueryable",
    )


# --- persona-unmapped -------------------------------------------------------------------------


def test_a_matrix_persona_that_maps_to_no_seat_is_flagged(tmp_path):
    """A persona the tenant wrote an argument FOR that no recipient can be attributed TO.
    Note the label must genuinely fail to normalise: "Field CTO" would resolve to `cto`,
    which is the resolver working, not a defect."""
    found = _codes(
        _check(tmp_path, matrix=_MATRIX_WITH_AN_UNMAPPABLE_PERSONA).findings, "persona-unmapped"
    )
    assert found and "Head of Vibes" in found[0]


def test_every_mapped_persona_is_silent(tmp_path):
    """NEGATIVE CONTROL. Without this, a bug flagging every persona looks like the check working."""
    assert not _codes(_check(tmp_path).findings, "persona-unmapped")


def test_a_missing_matrix_is_reported_not_silently_passed(tmp_path):
    """Absence is never the permissive branch: no matrix means the question is unanswerable,
    which is a finding, not a pass."""
    found = _codes(_check(tmp_path, matrix=None).findings, "persona-unmapped")
    assert found, "a profile with no hook matrix must not read as 'every persona is mapped'"


# --- rubric-undiscriminating ------------------------------------------------------------------


def test_a_rubric_that_orders_nothing_is_flagged(tmp_path):
    """Every cohort the same weight and no keywords — the queue is arbitrary and still
    spends credits in that arbitrary order."""
    found = _codes(_check(tmp_path, cohorts=_FLAT_COHORTS).findings, "rubric-undiscriminating")
    assert found


def test_a_rubric_with_real_spread_is_silent(tmp_path):
    """NEGATIVE CONTROL. Weights 30/14, keywords that actually fire on the fixture."""
    rows = [
        _row(
            "Northwind Capital Co",
            "brightpath.example",
            "united states",
            "Business Support Services",
            desc="an ai workforce product for small teams",
        ),
        _row("Soft Co", "softco.example", "united states", "Software Publishers", desc="tooling"),
    ]
    assert not _codes(_check(tmp_path, rows=rows).findings, "rubric-undiscriminating")


def test_the_rubric_check_says_it_is_on_the_enrichment_scale(tmp_path):
    """PRD 3.A.1: score_account is an additive SPEND ranking, not the 18-point Tier-A
    qualification rubric in icp-personas.md. Conflating the two is a recorded defect, so
    this finding names its own scale in its own output."""
    found = _codes(_check(tmp_path, cohorts=_FLAT_COHORTS).findings, "rubric-undiscriminating")
    assert "enrichment" in found[0].lower()


# --- shape, aggregation and the honest-limits line ---------------------------------------------


def test_every_finding_uses_the_code_subject_detail_shape(tmp_path):
    """Matches hook_coverage/audit.py's convention so an operator needs no new vocabulary."""
    for finding in _check(tmp_path, cohorts=_FLAT_COHORTS, matrix=None).findings:
        assert ": " in finding and " — " in finding, finding


def test_findings_aggregate_into_counts_by_code(tmp_path):
    result = _check(tmp_path, cohorts=_FLAT_COHORTS, matrix=None)
    assert result.counts == {code: len(_codes(result.findings, code)) for code in result.counts}
    assert sum(result.counts.values()) == len(result.findings)


def test_the_finding_code_vocabulary_is_a_closed_list():
    """§4.2: the granting values are a closed list, so an unanticipated word blocks."""
    from gtm_core.icp_check.checks import FINDING_CODES

    assert FINDING_CODES
    assert "" not in FINDING_CODES
    assert set(FINDING_CODES) == {
        "criterion-unqueryable",
        "persona-unmapped",
        "rubric-undiscriminating",
    }


def test_a_vocabulary_whose_seat_names_an_unproducible_persona_fails_loud(tmp_path):
    """Why this check does NOT re-ask the vocabulary's own question: role_vocabulary.load
    already refuses such a file outright, before icp_check can see it. Re-checking it here
    would be a control that can never fire (§R18). Pinned so nobody adds that check later."""
    from gtm_core.role_vocabulary import VocabularyError

    vocabulary = """\
[[persona]]
name = "ciso"
cues = ["ciso", "chief information security officer"]

[[seat]]
name = "security"
personas = ["ciso", "ghost-persona"]
stakes = ["audit"]
"""
    with pytest.raises(VocabularyError, match="ghost-persona"):
        _check(tmp_path, vocabulary=vocabulary)


def test_a_deliberately_seatless_persona_does_not_fire(tmp_path):
    """NEGATIVE CONTROL, asserted on the OUTPUT rather than on a string the function cannot emit.

    An earlier version filtered findings for "role-vocabulary.toml" — which `persona_unmapped`
    never writes — so the filter was empty for every input and the assertion passed
    unconditionally. It also could not fail, because the function does not read `vocab` at all.
    This version builds a matrix whose personas ARE the default vocabulary's seatless ones and
    asserts silence: if a seatless check were ever re-introduced, this goes red.
    """
    from gtm_core import role_vocabulary as rv
    from gtm_core.hook_coverage.matrix import persona_key_of_label
    from gtm_core.icp_check.checks import persona_unmapped

    vocab = rv.DEFAULT_VOCABULARY
    seatless = sorted(p for p in vocab.personas if p not in vocab.persona_to_seat)
    assert seatless, "fixture assumption: the defaults DO carry resolver-only personas"

    # Narrow to the ones that ALSO normalise as a matrix label — that is the case under test:
    # seatless BUT mappable must be silent. A seatless persona whose bare name is not a title
    # cue (e.g. `partnership`) legitimately does not normalise, and flagging it is correct.
    subjects = [p for p in seatless if persona_key_of_label(p, None)]
    assert subjects, (
        "fixture assumption: at least one seatless persona normalises as a matrix label — "
        "without one this test would pass on an empty matrix and prove nothing"
    )
    rows = "\n".join(f'| **{p}** | "a hook." |' for p in subjects)
    matrix_text = (
        "---\nsource: manual\n---\n# Hook matrix — a tenant\n\n## Enterprise\n\n"
        "| Signal → / Persona ↓ | Compliance event |\n|---|---|\n" + rows + "\n"
    )
    profiles_root, _ = _profile_tree(tmp_path, matrix=matrix_text)
    from gtm_core.hook_coverage.matrix import parse_matrix

    matrix = parse_matrix(profiles_root / "acme" / "knowledge" / "hook-matrix.md", profile="acme")
    assert persona_unmapped(vocab, matrix) == [], (
        "a persona that is deliberately seatless but perfectly mappable must not be a finding"
    )


# =============================================================================================
# Phase B — the CLI: exit codes, the one-block summary, the ledger contract, untrusted text.
# =============================================================================================


def _run_cli(tmp_path, *argv, **kw):
    from gtm_core.icp_check.cli import main

    profiles_root, content_root = _profile_tree(tmp_path, **kw)
    return main(
        [
            *argv,
            "--profile",
            "acme",
            "--content-root",
            str(content_root),
            "--profiles-root",
            str(profiles_root),
        ]
    )


def _history(tmp_path):
    import json as _json

    path = _content(tmp_path) / "acme" / "history.jsonl"
    return [_json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def test_check_exits_one_on_findings_and_zero_under_warn_only(tmp_path):
    """Matches hook_coverage/cli.py exactly: `0 if (warn_only or not failed) else 1`."""
    assert _run_cli(tmp_path, "check", cohorts=_FLAT_COHORTS, matrix=None) == 1
    assert _run_cli(tmp_path, "check", "--warn-only", cohorts=_FLAT_COHORTS, matrix=None) == 0


def test_check_exits_zero_on_a_clean_profile(tmp_path):
    """NEGATIVE CONTROL for the exit code: a gate that always fails is not a gate."""
    assert _run_cli(tmp_path, "check") == 0


def test_check_emits_one_summary_block_not_a_per_criterion_stream(tmp_path, capsys):
    """2026-08-19 is binding: 'Acknowledging 388 findings is not an action a human performs.'"""
    _run_cli(tmp_path, "check", "--warn-only", cohorts=_FLAT_COHORTS, matrix=None)
    out = capsys.readouterr().out
    assert out.count("FAIL —") == 1, "more than one summary block"


def test_a_clean_check_still_states_what_it_cannot_tell_you(tmp_path, capsys):
    """§6: an operator reading a clean result as 'our ICP is validated' has read it wrong,
    and the command must say so in its own output, not only in the PRD."""
    _run_cli(tmp_path, "check")
    out = capsys.readouterr().out
    assert "PASS" in out
    assert "cannot tell you" in out and "needs replies" in out


def test_the_history_event_carries_counts_and_never_finding_text(tmp_path):
    """§R9 — the sharpest risk in this feature. Findings name real third-party companies;
    the ledger must carry only counts by code."""
    import json as _json

    cohorts = (
        '[[cohort]]\nname = "agent-factory"\nweight = 30\n'
        'description_keywords = ["quantum abacus"]\n'
    )
    _run_cli(tmp_path, "check", "--warn-only", cohorts=cohorts)
    event = next(r for r in _history(tmp_path) if r.get("event") == "icp_check")
    # A keyword matching nothing also means no account matches any cohort, so
    # rubric-undiscriminating fires too — that is correct, and it makes the point:
    # counts, from every code, and never a word of the finding text.
    assert event["finding_counts"]["criterion-unqueryable"] == 1
    blob = _json.dumps(event)
    assert "quantum abacus" not in blob, "the ledger must not carry finding text"
    for company in ("Northwind Capital Co", "Vertex Platform Inc"):
        assert company not in blob, "the ledger must never name a backlog company"


def test_a_check_run_appends_no_cost_row_and_no_outcome(tmp_path):
    """§R2 / §6: zero metered calls by construction — verify that, do not assume it. And a
    rate this feature cannot support is a number it must not write."""
    _run_cli(tmp_path, "check", "--warn-only", cohorts=_FLAT_COHORTS)
    base = _content(tmp_path) / "acme"
    assert not (base / "costs.jsonl").exists()
    assert not (base / "outcomes.jsonl").exists()


def test_the_three_renderings_of_a_count_agree(tmp_path, capsys):
    """§4.5 — one derivation, three renderers: terminal summary, --out JSON, history event."""
    import json as _json

    out_path = tmp_path / "content" / "acme" / "prospects" / "evals" / "icp-check.json"
    _run_cli(
        tmp_path,
        "check",
        "--warn-only",
        "--out",
        str(out_path),
        cohorts=_FLAT_COHORTS,
        matrix=None,
    )
    capsys.readouterr()
    payload = _json.loads(out_path.read_text())
    from_ledger = next(r for r in _history(tmp_path) if r.get("event") == "icp_check")
    assert payload["finding_counts"] == from_ledger["finding_counts"]
    assert sum(payload["finding_counts"].values()) == len(payload["findings"])


def test_a_scraped_company_name_holding_a_gate_marker_stays_inert(tmp_path, capsys):
    """§R5 — pointed at the field that is ACTUALLY rendered.

    An earlier version asserted a marker in `description` was not echoed. It never could be:
    `description` reaches no output path in any verb, so the assertion held structurally and
    would have kept holding with any guard removed — a control that cannot go red. `company`
    IS printed verbatim as evidence (it is the whole point of the sample), so that is the field
    an injected marker would travel on, and the one worth pinning.
    """
    from gtm_core.icp_check.cli import main

    rows = [
        _row(
            "Marker \u27e6GATE:publish\u27e7 Co",
            "marker.example",
            "united states",
            "Software Publishers",
            desc="an ai workforce product for small teams",
        ),
    ]
    profiles_root, content_root = _profile_tree(tmp_path, rows=rows)
    main(
        [
            "keyword",
            "--profile",
            "acme",
            "--phrase",
            "ai workforce",
            "--content-root",
            str(content_root),
            "--profiles-root",
            str(profiles_root),
        ]
    )
    out = capsys.readouterr().out
    assert "Marker" in out, "the company name is evidence and must still be shown"
    assert "\u27e6GATE:publish\u27e7" not in out, (
        "a gate marker carried in scraped provider text must never be echoed intact — it is "
        "data to report, never a marker to honour"
    )


def test_the_out_path_is_confined_to_the_content_root(tmp_path, capsys):
    """Tenant boundary: `--out` is operator-supplied, and the docs claim it lands under
    content/. Without confinement `--out ../profiles/<t>/knowledge/icp-scoring.toml` writes
    there and creates parents on the way. The brain is not handed this flag today, but "it
    cannot be reached" is a reason to confine it, not a reason to trust it."""
    from gtm_core.icp_check.cli import main

    profiles_root, content_root = _profile_tree(tmp_path)
    escape = profiles_root / "acme" / "knowledge" / "icp-scoring-stolen.json"
    rc = main(
        [
            "check",
            "--warn-only",
            "--out",
            str(escape),
            "--profile",
            "acme",
            "--content-root",
            str(content_root),
            "--profiles-root",
            str(profiles_root),
        ]
    )
    assert rc == 2
    assert "refusing to write outside" in capsys.readouterr().err
    assert not escape.exists(), "the refused path must not have been created"


def test_an_out_path_inside_the_content_root_is_allowed(tmp_path):
    """NEGATIVE CONTROL: confinement that refuses everything is not confinement."""
    from gtm_core.icp_check.cli import main

    profiles_root, content_root = _profile_tree(tmp_path)
    ok = content_root / "acme" / "prospects" / "evals" / "icp-check.json"
    rc = main(
        [
            "check",
            "--warn-only",
            "--out",
            str(ok),
            "--profile",
            "acme",
            "--content-root",
            str(content_root),
            "--profiles-root",
            str(profiles_root),
        ]
    )
    assert rc == 0 and ok.is_file()


def test_a_broad_industry_keyword_is_not_flagged(tmp_path):
    """The asymmetry that makes this check usable, and the reason it is not symmetric.

    `keywords` match the NAICS-derived `industry` field, where a cohort keyword hitting
    hundreds of accounts is the category working as intended (measured on a live tenant
    backlog: `bank` 115, `insur` 321 — both correct). Only `description_keywords` match
    scraped free text, where breadth IS the failure mode. Applying the description ceiling
    to industry keywords flags the healthy state of every industry cohort, and a check that
    fires on correct design is one an operator learns to ignore.
    """
    cohorts = '[[cohort]]\nname = "fs"\nweight = 30\nkeywords = ["software"]\n'
    rows = [
        _row(f"Co {i}", f"co{i}.example", "united states", "Software Publishers", desc="x")
        for i in range(30)
    ]
    found = _codes(
        _check(tmp_path, cohorts=cohorts, rows=rows, max_hits=2).findings, "criterion-unqueryable"
    )
    assert not found, f"a broad INDUSTRY keyword must not trip the description ceiling: {found}"


def test_the_same_breadth_on_a_description_keyword_does_fire(tmp_path):
    """§R18 discrimination control for the asymmetry above: identical hit count, other key,
    opposite verdict. Without this pair, 'the ceiling is scoped' and 'the ceiling is broken'
    look the same."""
    cohorts = '[[cohort]]\nname = "fs"\nweight = 30\ndescription_keywords = ["ai platform"]\n'
    rows = [
        _row(
            f"Co {i}",
            f"co{i}.example",
            "united states",
            "Software Publishers",
            desc="an ai platform",
        )
        for i in range(30)
    ]
    found = _codes(
        _check(tmp_path, cohorts=cohorts, rows=rows, max_hits=2).findings,
        "criterion-unqueryable",
    )
    assert found and "ceiling of 2" in found[0]


def test_zero_hit_phrases_aggregate_into_one_finding_per_cohort(tmp_path):
    """A real rubric has dozens of phrases. One line each is the 2026-08-19 '388 unreadable
    warnings' shape; one line per cohort per key is readable."""
    cohorts = (
        '[[cohort]]\nname = "fs"\nweight = 30\n'
        'description_keywords = ["no hit one", "no hit two", "no hit three"]\n'
    )
    found = _codes(_check(tmp_path, cohorts=cohorts).findings, "criterion-unqueryable")
    assert len(found) == 1, f"expected one aggregated finding, got {len(found)}"
    assert "3 of 3 phrase(s)" in found[0]


# =============================================================================================
# Phase C — `icp propose`: add / amend / retire, in the `lanes suggest-rules` shape.
# =============================================================================================

_FIXED_FINDINGS = [
    "criterion-unqueryable: icp-scoring.toml · cohort 'fs' · keywords — 2 of 5 phrase(s) "
    "select nothing on a 100-account backlog: 'alpha', 'beta'; they cannot identify this cohort",
    "persona-unmapped: hook-matrix.md — 1 matrix persona(s) do not normalise onto the role "
    "vocabulary (Head of Vibes); recipients can never be attributed to them",
    "rubric-undiscriminating: icp-scoring.toml — 90 of 100 accounts (90%) match no cohort at "
    "all, so this rubric orders almost nothing on the enrichment scale; the enrichment queue "
    "would spend in near-arbitrary order",
]


def test_propose_output_is_byte_stable_for_a_fixed_input():
    """An operator diffs this week over week; it must not reorder between runs."""
    from gtm_core.icp_check.propose import propose

    assert propose(_FIXED_FINDINGS) == propose(_FIXED_FINDINGS)


def test_every_proposal_line_names_a_file_an_edit_and_its_evidence(tmp_path):
    from gtm_core.icp_check.propose import propose

    out = propose(_FIXED_FINDINGS)
    for section in ("RETIRE", "AMEND", "ADD"):
        assert section in out, f"{section} section missing"
    # every section line names a file the operator must open
    for line in out.splitlines():
        if line[:8].strip() in {"RETIRE", "AMEND", "ADD"}:
            assert (".toml" in line) or (".md" in line), line


def test_a_retire_is_an_instruction_not_a_pasteable_block():
    """`lanes suggest-rules` emits paste-ready TOML because it only ever proposes ADDITIONS.
    A deletion has no such form, and output that looks like a patch and is not one is worse
    than prose."""
    from gtm_core.icp_check.propose import propose

    out = propose(_FIXED_FINDINGS)
    assert "RETIRE" in out
    assert "[[cohort]]" not in out, "a retire must not look like a pasteable TOML block"


def test_propose_never_rests_on_a_reply_rate():
    """§3.A.1: retire only from a measured STRUCTURAL property — hit count, mappability,
    spread. At n=1 outcomes a reply-rate retire is noise wearing a decision's clothes."""
    from gtm_core.icp_check.propose import propose

    low = propose(_FIXED_FINDINGS).lower()
    for word in ("reply rate", "replies", "open rate", "click"):
        assert word not in low, f"propose must not rest on {word!r}"


def test_the_footer_says_the_tool_never_writes():
    from gtm_core.icp_check.propose import propose

    assert "never writes" in propose(_FIXED_FINDINGS)


def test_a_clean_finding_set_proposes_nothing_and_says_why():
    """NEGATIVE CONTROL: a proposer that always proposes something is not a proposer."""
    from gtm_core.icp_check.propose import propose

    out = propose([])
    assert "no proposal" in out
    assert "RETIRE" not in out and "ADD" not in out
    assert "not a verdict" in out, "it must still refuse to read as validation"


def test_a_finding_it_cannot_act_on_produces_no_line():
    """Deliberately partial: a vague suggestion is the 2026-08-19 'unreadable warnings' shape.
    An unknown code yields nothing at all rather than a line nobody can act on."""
    from gtm_core.icp_check.propose import propose

    assert "no proposal" in propose(["some-future-code: whatever — details"])


# --- §5.A error clarity: name the file and the next action, never a stack trace ----------------


def test_an_absent_rubric_reports_one_line_not_a_traceback(tmp_path, capsys):
    """The message already names the file and the fix; a traceback buries that under frames
    the operator cannot act on. Still exits non-zero, so nothing reads it as success."""
    from gtm_core.icp_check.cli import main

    profiles_root = tmp_path / "profiles"
    (profiles_root / "acme" / "knowledge").mkdir(parents=True)
    rc = main(
        [
            "check",
            "--profile",
            "acme",
            "--content-root",
            str(tmp_path / "content"),
            "--profiles-root",
            str(profiles_root),
        ]
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert err.startswith("[icp-check] ")
    assert "icp-scoring.toml" in err, "the message must name the file"
    assert "Traceback" not in err and '  File "' not in err


def test_a_malformed_vocabulary_reports_one_line_too(tmp_path, capsys):
    from gtm_core.icp_check.cli import main

    profiles_root, content_root = _profile_tree(
        tmp_path,
        vocabulary='[[persona]]\nname = "ciso"\ncues = ["ciso"]\n',  # no default_persona
    )
    rc = main(
        [
            "check",
            "--profile",
            "acme",
            "--content-root",
            str(content_root),
            "--profiles-root",
            str(profiles_root),
        ]
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "default_persona" in err and "Traceback" not in err


def test_a_real_bug_keeps_its_traceback(tmp_path, monkeypatch):
    """NEGATIVE CONTROL, and the reason the caught set is a closed tuple: swallowing every
    exception would turn a programming error in this package into a tidy operator message,
    which is how a bug gets mistaken for a bad profile."""
    import gtm_core.icp_check.cli as climod

    def _boom(*a, **kw):
        raise RuntimeError("a bug in icp_check itself")

    monkeypatch.setattr(climod, "run_checks", _boom)
    profiles_root, content_root = _profile_tree(tmp_path)
    with pytest.raises(RuntimeError, match="a bug in icp_check itself"):
        climod.main(
            [
                "check",
                "--profile",
                "acme",
                "--content-root",
                str(content_root),
                "--profiles-root",
                str(profiles_root),
            ]
        )
