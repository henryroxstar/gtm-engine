"""Unit tests for the deterministic preflight (PRD 2026-08-27 §3.4, W2 / T11+T12).

§R9 — every fixture here is fictional. The companies, domains and people below are
invented; ``acme.example`` and ``example.test`` are reserved names that can never
resolve to a real organisation.

Two things are under test, and they are different in kind:

* **T11** — the report's shape and its exit codes, including the rule that carries the
  whole phase: *a missing artifact is a skip, not a failure*. A daily unit that goes
  red every morning because nothing is staged is the unreadable-gate failure the PRD
  opens by quoting, arriving by a new route.
* **T12** — that the preflight stays free. It is a *precondition* only because running
  it always costs nothing; the moment it can reach a model or a network it stops being
  one, and the daily cadence in §3.7 stops being defensible.
"""

from __future__ import annotations

import ast
import csv
import json
from pathlib import Path

import pytest

from gtm_core import preflight_report as pr

REPO = Path(__file__).resolve().parent.parent


# --- fixtures --------------------------------------------------------------

#: The columns ``signal_record`` requires. A header missing these produces one
#: file-level finding and no per-row checks, which is its own tested behaviour.
#: ``signal_clause`` is load-bearing: without it a row is a *generic arc* that makes no
#: dated claim, so every provenance check short-circuits and the fixture would prove
#: nothing.
_HEADER = [
    "first",
    "last",
    "email",
    "title",
    "company",
    "company_domain",
    "country",
    "tier",
    "why_now",
    "signal_clause",
    "case_study",
    "src",
    "signal_source_url",
    "signal_observed",
    "signal_evidence",
    "signal_subject",
    "signal_agent_kind",
    "category_relation",
    "verdict",
    "verdict_reason",
]


def _row(**over) -> dict:
    base = {
        "first": "Rani",
        "last": "Okonkwo",
        "email": "rani@acme.example",
        "title": "Head of Platform",
        "company": "Acme Logistics",
        "company_domain": "acme.example",
        "country": "Singapore",
        "tier": "A",
        "why_now": "Shipped an agent gateway in March.",
        "signal_clause": "Shipped an agent gateway in March.",
        "case_study": "syndicated-lending",
        "src": "fixture",
        "signal_source_url": "https://acme.example/newsroom/agent-gateway",
        "signal_observed": "2026-08-01",
        "signal_evidence": "Acme Logistics shipped an agent gateway in March.",
        "signal_subject": "Acme Logistics",
        "signal_agent_kind": "ai",
        "category_relation": "prospect",
        "verdict": "send",
        "verdict_reason": "in market, fresh signal",
    }
    base.update(over)
    return base


def _staged(tmp_path: Path, profile: str, rows: list[dict]) -> Path:
    """Write a ready-to-load CSV where ``prospect_paths`` expects to find it."""
    seq = tmp_path / profile / "prospects" / "sequences"
    seq.mkdir(parents=True, exist_ok=True)
    out = seq / "ready-to-load.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=_HEADER)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in _HEADER})
    return out


def _dossier(tmp_path: Path, profile: str, slug: str = "acme-logistics") -> Path:
    """Give an account the research `no-dossier` (an ERROR) demands it have.

    Matches ``DOSSIER_GLOB_FULL`` in ``gtm_core.prospects_consolidate``.
    """
    folder = tmp_path / profile / "accounts" / slug
    folder.mkdir(parents=True, exist_ok=True)
    doc = folder / "account-dossier-acme-logistics-2026-08-20.md"
    doc.write_text("# Acme Logistics\n\nFictional fixture account.\n", encoding="utf-8")
    return doc


# --- T11: shape, exit codes, and skip-not-fail -----------------------------


class TestNothingStagedIsNotAFailure:
    """The rule the daily cadence depends on."""

    def test_an_empty_profile_skips_every_check_and_exits_zero(self, tmp_path):
        rep = pr.run_preflight("template", content_root=tmp_path, profiles_root=tmp_path)
        assert rep.checks, "the roster must be non-vacuous"
        assert all(c.status == pr.SKIP for c in rep.checks)
        assert not rep.failed
        assert rep.exit_code == 0

    def test_every_skip_states_a_reason(self, tmp_path):
        rep = pr.run_preflight("template", content_root=tmp_path, profiles_root=tmp_path)
        for c in rep.checks:
            assert c.detail.strip(), f"{c.name} skipped without saying why"

    def test_a_skip_is_not_a_pass(self, tmp_path):
        """Distinguishable in the report — otherwise 'all green' means 'ran nothing'."""
        rep = pr.run_preflight("template", content_root=tmp_path, profiles_root=tmp_path)
        assert rep.counts()[pr.OK] == 0
        assert rep.counts()[pr.SKIP] == len(rep.checks)


class TestTheRosterIsNonVacuous:
    def test_the_roster_names_every_module_the_prd_declares(self):
        assert {c.name for c in pr.ROSTER} == {
            "account_integrity",
            "list_fit",
            "merge_hygiene",
            "suppression",
            "hook_coverage",
            "email_compliance",
            "groundedness",
            "signal_record",
        }

    def test_the_roster_is_ordered_and_unique(self):
        names = [c.name for c in pr.ROSTER]
        assert len(names) == len(set(names))

    def test_signal_record_precedes_account_integrity(self):
        """Order is load-bearing, not cosmetic.

        ``account_integrity.audit_rows`` calls ``audit_records`` internally, so the
        record tier is subtracted from it using what ``signal_record`` already reported.
        Reversing these two silently doubles every record finding.
        """
        names = [c.name for c in pr.ROSTER]
        assert names.index("signal_record") < names.index("account_integrity")


class TestTheRecordTierIsNotCountedTwice:
    """The second correction the first real run forced.

    On a live profile, `verdict-missing` reported **972** times over **508** rows —
    once from ``signal_record`` and once from ``account_integrity``, which computes the
    same tier inside itself.
    """

    def test_a_record_finding_is_reported_once(self, tmp_path):
        _staged(tmp_path, "template", [_row(verdict="", verdict_reason="")])
        _dossier(tmp_path, "template")
        rep = pr.run_preflight(
            "template", content_root=tmp_path, profiles_root=tmp_path, as_of=_AS_OF
        )
        missing = [e for e in rep.errors if e.startswith("verdict-missing:")]
        assert len(missing) == 1, missing

    def test_no_finding_ever_outnumbers_the_rows(self, tmp_path):
        rows = [_row(email=f"p{i}@acme.example", verdict="") for i in range(10)]
        _staged(tmp_path, "template", rows)
        _dossier(tmp_path, "template")
        rep = pr.run_preflight(
            "template", content_root=tmp_path, profiles_root=tmp_path, as_of=_AS_OF
        )
        for rule, details in _by_rule(rep.errors).items():
            assert len(details) <= len(rows), f"{rule} fired {len(details)}x over {len(rows)} rows"

    def test_a_genuinely_duplicated_row_still_counts_twice(self, tmp_path):
        """Multiset subtraction, not set subtraction — a CSV carrying the same person
        twice is a real finding, and deduping it away would hide a real defect."""
        dup = _row(verdict="", verdict_reason="")
        _staged(tmp_path, "template", [dup, dict(dup)])
        _dossier(tmp_path, "template")
        rep = pr.run_preflight(
            "template", content_root=tmp_path, profiles_root=tmp_path, as_of=_AS_OF
        )
        missing = [e for e in rep.errors if e.startswith("verdict-missing:")]
        assert len(missing) == 2, missing


def _by_rule(findings: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for f in findings:
        out.setdefault(f.partition(": ")[0], []).append(f)
    return out


class TestACleanListPasses:
    def test_a_well_formed_row_produces_no_error(self, tmp_path):
        _staged(tmp_path, "template", [_row()])
        _dossier(tmp_path, "template")
        rep = pr.run_preflight(
            "template", content_root=tmp_path, profiles_root=tmp_path, as_of=_AS_OF
        )
        assert not rep.errors, rep.errors
        assert rep.exit_code == 0

    def test_an_account_with_no_research_is_an_error_not_a_warning(self, tmp_path):
        """`no-dossier` is the check that exists because contacts were emailed with
        zero account research behind their opener. It must never be budget-graded."""
        _staged(tmp_path, "template", [_row()])  # deliberately no dossier
        rep = pr.run_preflight(
            "template", content_root=tmp_path, profiles_root=tmp_path, as_of=_AS_OF
        )
        assert any(e.startswith("no-dossier:") for e in rep.errors)
        assert rep.exit_code == 1


class TestABrokenRowFails:
    def test_a_row_with_no_research_record_blocks(self, tmp_path):
        """A blank signal record is the case the whole record contract exists for."""
        _staged(
            tmp_path,
            "template",
            [_row(signal_source_url="", signal_observed="", signal_evidence="")],
        )
        rep = pr.run_preflight(
            "template", content_root=tmp_path, profiles_root=tmp_path, as_of=_AS_OF
        )
        assert rep.failed
        assert rep.exit_code == 1


class TestTheReportIsWritten:
    def test_it_lands_under_the_content_root_and_nowhere_else(self, tmp_path):
        _staged(tmp_path, "template", [_row()])
        rep = pr.run_preflight(
            "template", content_root=tmp_path, profiles_root=tmp_path, as_of=_AS_OF
        )
        path = pr.write_report(rep, content_root=tmp_path)
        assert path.is_relative_to(tmp_path / "template")
        latest = tmp_path / "template" / "preflight" / "latest.json"
        assert latest.is_file()
        payload = json.loads(latest.read_text(encoding="utf-8"))
        assert payload["profile"] == "template"
        assert {c["name"] for c in payload["checks"]} == {c.name for c in pr.ROSTER}

    def test_the_report_is_machine_readable(self, tmp_path):
        rep = pr.run_preflight("template", content_root=tmp_path, profiles_root=tmp_path)
        json.dumps(rep.to_dict())  # must not raise


class TestTheWarnTierRendersThroughTheBudget:
    def test_render_names_the_budget(self, tmp_path):
        _staged(tmp_path, "template", [_row()])
        rep = pr.run_preflight(
            "template", content_root=tmp_path, profiles_root=tmp_path, as_of=_AS_OF
        )
        text = pr.render(rep)
        assert "preflight" in text.lower()

    def test_an_acked_class_stops_counting_against_the_budget(self, tmp_path):
        """Acknowledgment is per class — the ``--ack`` contract, not a blanket pass.

        ``relation-adjacent`` is a WARN by design (a neighbouring vendor is worth
        naming, not blocking), which is what makes it the right class to grade.
        """
        rows = [_row(email=f"p{i}@acme.example", category_relation="adjacent") for i in range(40)]
        _staged(tmp_path, "template", rows)
        _dossier(tmp_path, "template")
        noisy = pr.run_preflight(
            "template", content_root=tmp_path, profiles_root=tmp_path, as_of=_AS_OF
        )
        classes = {c.rule for c in noisy.verdict.classes}
        assert "relation-adjacent" in classes, f"expected a WARN class, got {classes}"
        assert noisy.verdict.blocked, "40 unacked warnings must exceed the budget"
        calmed = pr.run_preflight(
            "template",
            content_root=tmp_path,
            profiles_root=tmp_path,
            as_of=_AS_OF,
            acked=tuple(classes),
        )
        assert calmed.verdict.unacked == 0
        assert not calmed.verdict.blocked


class TestTheErrorTierStaysReadable:
    """The correction the first real run forced.

    Against one live profile's 508 staged rows this module emitted **2283** errors and printed
    every one — `finding_budget`'s 388-warning collapse, reproduced one tier up by a gate
    written to prevent it. Errors still block at any count; only the printing changes.
    """

    def test_a_short_error_list_is_enumerated(self):
        text = pr.render_errors([f"rule-a: row {i}" for i in range(5)])
        assert "row 4" in text
        assert "Enumeration suppressed" not in text

    def test_a_long_error_list_reports_classes_and_exemplars(self):
        errors = [f"verdict-missing: row {i}" for i in range(500)]
        errors += [f"relation-unresolved: row {i}" for i in range(300)]
        text = pr.render_errors(errors)
        assert "Enumeration suppressed" in text
        assert "verdict-missing: 500" in text
        assert "relation-unresolved: 300" in text
        assert len(text.splitlines()) < 30, "the summary must fit on a screen"

    def test_suppressing_enumeration_never_softens_the_verdict(self, tmp_path):
        """Readability is not leniency — the exit code must not move."""
        rows = [_row(email=f"p{i}@acme.example", verdict="") for i in range(60)]
        _staged(tmp_path, "template", rows)
        _dossier(tmp_path, "template")
        rep = pr.run_preflight(
            "template", content_root=tmp_path, profiles_root=tmp_path, as_of=_AS_OF
        )
        assert len(rep.errors) > pr.ERROR_ENUMERATION_LIMIT
        assert rep.failed and rep.exit_code == 1

    def test_the_json_report_carries_error_classes(self, tmp_path):
        rows = [_row(email=f"p{i}@acme.example", verdict="") for i in range(60)]
        _staged(tmp_path, "template", rows)
        _dossier(tmp_path, "template")
        rep = pr.run_preflight(
            "template", content_root=tmp_path, profiles_root=tmp_path, as_of=_AS_OF
        )
        payload = rep.to_dict()
        assert payload["error_total"] == len(rep.errors)
        assert payload["error_classes"], "a 2000-error report needs a class summary"
        assert payload["error_classes"] == sorted(
            payload["error_classes"], key=lambda c: (-c["count"], c["rule"])
        )


class TestTheCli:
    def test_it_exits_zero_on_an_empty_profile(self, tmp_path, capsys):
        rc = pr.main(
            [
                "--profile",
                "template",
                "--content-root",
                str(tmp_path),
                "--profiles-root",
                str(tmp_path),
            ]
        )
        assert rc == 0

    def test_json_mode_emits_only_json_on_stdout(self, tmp_path, capsys):
        rc = pr.main(
            [
                "--profile",
                "template",
                "--content-root",
                str(tmp_path),
                "--profiles-root",
                str(tmp_path),
                "--json",
            ]
        )
        assert rc == 0
        json.loads(capsys.readouterr().out)


# --- T12: the preflight is free, and must stay free ------------------------

#: Importing any of these means the preflight can spend money or reach the network,
#: which is what disqualifies it as a precondition. Checked against the import graph,
#: not against a comment.
_FORBIDDEN_IMPORTS = {
    "httpx",
    "requests",
    "urllib.request",
    "urllib3",
    "socket",
    "gtm_core.models",
    "gtm_core.ledgers",
    "agent.ledgers",
}

_AS_OF = __import__("datetime").date(2026, 8, 28)


def _imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out.update(f"{node.module}.{a.name}" for a in node.names)
            if node.level:  # relative: `from .models import ...` inside gtm_core
                out.add(f"gtm_core.{node.module}")
    return out


def test_the_preflight_makes_no_network_call_and_resolves_no_model_role():
    """T12. The roster's own modules are checked too — a paid call one hop away is
    still a paid call, and the daily cadence rests on this being zero-cost."""
    src = REPO / "gtm_core" / "preflight_report.py"
    names = _imported_names(src)
    assert not (names & _FORBIDDEN_IMPORTS), f"preflight imports {names & _FORBIDDEN_IMPORTS}"
    assert "resolve_model" not in src.read_text(encoding="utf-8")


def test_a_preflight_run_appends_nothing_to_costs_jsonl(tmp_path):
    """DoD W2#4, measured rather than argued."""
    profile = "template"
    costs = tmp_path / profile / "costs.jsonl"
    costs.parent.mkdir(parents=True, exist_ok=True)
    costs.write_text("", encoding="utf-8")
    _staged(tmp_path, profile, [_row()])
    pr.run_preflight(profile, content_root=tmp_path, profiles_root=tmp_path, as_of=_AS_OF)
    assert costs.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("mod", sorted({c.module for c in pr.ROSTER}))
def test_no_roster_module_reaches_a_model_or_the_network(mod):
    """The transitive half of T12, one module per case so a failure names the culprit."""
    path = REPO / Path(*mod.split(".")).with_suffix(".py")
    if not path.is_file():
        pytest.skip(f"{mod} is not a single-file module")
    assert not (_imported_names(path) & _FORBIDDEN_IMPORTS)
