"""The account-integrity gate must say no where it used to say yes (2026-09-21 audit).

Three shapes, each a case where the gate's PASS was not true:

* a direct competitor's own listed name was never indexed once the entry also listed a
  domain, so the same company on a regional or mail subdomain passed in every lane;
* the generic lane waved through a research record that was WRONG (stale, future-dated,
  sourced from a search page), not merely absent;
* ``--lane`` alone checked no verdicts, and ``--require-verdict`` printed PASS over a file
  that still held every row it had just refused.

Every fixture is invented and lives under ``tmp_path``; domains are RFC 2606 ``.example``.
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

import pytest

from gtm_core import account_integrity as ai
from gtm_core.account_integrity import (
    _ROW_LEVEL_RULES,
    GENERIC_LANE_ADVISORY,
    GENERIC_LANE_STAYS_ERROR,
    audit_rows,
    competitor_match,
    load_competitors,
)
from gtm_core.competitor_index import domain_stem
from gtm_core.prospect_paths import evals_dir, suppression_ledger

PROFILE = "acme"
AS_OF = "2026-08-15"

_COMPETITORS = """
schema = 1

[[competitor]]
name = "Contoso Agent Broker"
tier = "direct"
aliases = ["Contoso Broker", "CAB Gateway"]
domains = ["contoso.example"]
note = "Overlaps the core wedge."

[[competitor]]
name = "Northwind Robotics"
tier = "adjacent"
aliases = []
domains = ["northwind.example"]
note = "Neighbouring product."
"""


def _profiles(tmp_path: Path, body: str = _COMPETITORS) -> Path:
    root = tmp_path / "profiles"
    (root / PROFILE / "knowledge").mkdir(parents=True, exist_ok=True)
    (root / PROFILE / "knowledge" / "competitors.toml").write_text(body, encoding="utf-8")
    return root


def _row(**kw) -> dict:
    base = {
        "first": "Jordan",
        "last": "Vance",
        "email": "jordan.vance@vertex.example",
        "title": "Chief Information Security Officer",
        "company": "Vertex Systems",
        "company_domain": "vertex.example",
        "signal_clause": "opened an AI governance program covering autonomous agents",
        "why_now": "Vertex Systems opened an AI governance program in 2026",
        "suppression": "",
        "signal_source_url": "https://vertexsystems.example/news/ai-governance",
        "signal_observed": "2026-08-01",
        "signal_evidence": (
            "Vertex Systems opened an AI governance program covering autonomous agents "
            "across its claims and underwriting workflows."
        ),
        "signal_subject": "Vertex Systems",
        "signal_agent_kind": "ai",
        "category_relation": "prospect",
        "verdict": "send",
        "verdict_reason": "",
        "judge_verdict": "",
        "judge_calibrated": "",
    }
    base.update(kw)
    return base


# --- PSK-031: every identity a competitor entry declares is indexed ------------------


@pytest.mark.parametrize(
    "domain",
    ["", "contoso-apac.example", "mail.contoso.example", "contoso.example"],
)
def test_a_direct_competitors_listed_name_hits_whatever_domain_the_row_carries(tmp_path, domain):
    competitors = load_competitors(PROFILE, _profiles(tmp_path))
    hit = competitor_match("Contoso Agent Broker", domain, competitors)
    assert hit is not None and hit.direct, f"listed name missed on company_domain={domain!r}"


@pytest.mark.parametrize(
    "labels",
    [
        ("contoso", "example"),
        ("mail", "contoso", "example"),
        ("contoso", "co", "uk"),  # two-part country suffix
        ("eu", "contoso", "io"),  # a TLD the explicit suffix list does not carry
        ("contoso", "tech"),
    ],
)
def test_domain_stem_is_the_label_before_the_public_suffix(labels):
    """Joined at run time: these are host SHAPES, not anybody's domain."""
    assert domain_stem(".".join(labels)) == "contoso"
    assert domain_stem("https://www." + ".".join(labels) + "/sg") == "contoso"


def test_a_mail_subdomain_hits_by_domain_stem_under_an_unlisted_display_name(tmp_path):
    """The first label of `mail.contoso.example` is `mail`; the company is `contoso`."""
    competitors = load_competitors(PROFILE, _profiles(tmp_path))
    hit = competitor_match("Regional Ops Pte", "mail.contoso.example", competitors)
    assert hit is not None and hit.direct


def test_an_alias_alone_hits(tmp_path):
    competitors = load_competitors(PROFILE, _profiles(tmp_path))
    hit = competitor_match("CAB Gateway", "", competitors)
    assert hit is not None and hit.direct
    assert competitor_match("Contoso Broker Ltd", "unrelated.example", competitors) is not None


def test_the_rows_email_domain_is_an_identity_too(tmp_path):
    competitors = load_competitors(PROFILE, _profiles(tmp_path))
    hit = competitor_match("Some Holding", "", competitors, email="avery@eu.contoso.example")
    assert hit is not None and hit.direct
    assert competitor_match("Some Holding", "", competitors) is None, "control: no email, no hit"


def test_an_entry_with_no_domains_still_hits_by_name(tmp_path):
    body = 'schema = 1\n\n[[competitor]]\nname = "Fabrikam Freight"\ntier = "direct"\n'
    competitors = load_competitors(PROFILE, _profiles(tmp_path, body))
    hit = competitor_match("Fabrikam Freight", "", competitors)
    assert hit is not None and hit.direct


def test_an_unrelated_company_sharing_one_word_is_not_flagged(tmp_path):
    """Exact token, never substring: one shared word of a multi-word name is not a match."""
    competitors = load_competitors(PROFILE, _profiles(tmp_path))
    assert competitor_match("Agent Freight Lines", "agentfreight.example", competitors) is None
    assert competitor_match("Broker Partners", "", competitors) is None
    assert competitor_match("Northwind Freight Lines", "nwfreight.example", competitors) is None


def test_a_freemail_domain_never_produces_a_domain_stem_hit(tmp_path):
    body = (
        'schema = 1\n\n[[competitor]]\nname = "Contoso Agent Broker"\ntier = "direct"\n'
        'domains = ["contoso.example", "gmail.com"]\n'
    )
    competitors = load_competitors(PROFILE, _profiles(tmp_path, body))
    assert "gmail" not in competitors, "a free-mail domain must never be indexed"
    assert competitor_match("Vertex Systems", "", competitors, email="chris@gmail.com") is None, (
        "a founder on free webmail is not the competitor"
    )
    # Even an index that somehow carries the stem must not convict a free-mail address.
    forged = {"gmail": next(iter(competitors.values()))}
    assert competitor_match("Vertex Systems", "gmail.com", forged, email="chris@gmail.com") is None


def test_direct_wins_when_a_row_matches_two_entries(tmp_path):
    competitors = load_competitors(PROFILE, _profiles(tmp_path))
    # Name says the adjacent entry, email says the direct one.
    hit = competitor_match(
        "Northwind Robotics", "northwind.example", competitors, email="a@contoso.example"
    )
    assert hit is not None and hit.direct
    # Control: without the direct identity the adjacent hit is what comes back.
    hit = competitor_match("Northwind Robotics", "northwind.example", competitors)
    assert hit is not None and not hit.direct


def test_a_malformed_entry_is_skipped_with_a_warning_never_a_traceback(tmp_path, caplog):
    body = (
        "schema = 1\n\n"
        '[[competitor]]\nname = "Contoso Agent Broker"\ntier = "direct"\n'
        'aliases = ["CAB Gateway", 7]\ndomains = "contoso.example"\n\n'
        '[[competitor]]\nname = 12\ntier = "direct"\n'
    )
    with caplog.at_level(logging.WARNING):
        competitors = load_competitors(PROFILE, _profiles(tmp_path, body))
    assert competitor_match("CAB Gateway", "", competitors) is not None
    assert competitor_match("Contoso Agent Broker", "", competitors) is not None
    text = caplog.text
    assert "aliases" in text and "domains" in text and "name" in text


def test_the_audit_blocks_a_direct_competitor_on_a_regional_domain_in_every_lane(tmp_path):
    root = _profiles(tmp_path)
    rows = [
        _row(
            email="avery@contoso-apac.example",
            company="Contoso Agent Broker",
            company_domain="contoso-apac.example",
            signal_subject="Contoso Agent Broker",
        )
    ]
    for lane in ("", "signal", "generic"):
        a = audit_rows(rows, PROFILE, content_root=tmp_path, profiles_root=root, lane=lane)
        assert any(e.startswith("competitor-direct:") for e in a.errors), f"lane={lane!r}"
        assert a.competitor_direct == 1


def test_the_audit_reads_the_email_of_a_later_contact_on_the_same_account(tmp_path):
    """Account-level dedupe must not hide the one contact whose address is the competitor's."""
    root = _profiles(tmp_path)
    rows = [
        _row(email="a@vertex.example"),
        _row(email="b@mail.contoso.example"),
    ]
    a = audit_rows(rows, PROFILE, content_root=tmp_path, profiles_root=root)
    assert a.competitor_direct == 1
    # And one account with two competitor contacts is still ONE finding.
    rows = [
        _row(email="a@contoso.example", company="Contoso Agent Broker", company_domain=""),
        _row(email="b@contoso.example", company="Contoso Agent Broker", company_domain=""),
    ]
    a = audit_rows(rows, PROFILE, content_root=tmp_path, profiles_root=root)
    assert a.competitor_direct == 1


# --- PSK-032: a WRONG record is an error in every lane -----------------------------------


def _rules(lines: list[str]) -> set[str]:
    return {line.split(":", 1)[0] for line in lines}


def test_every_record_rule_is_classified_absent_or_wrong():
    """A `signal-*`/`agent-kind-*` rule added later must be classified, not defaulted."""
    record_rules = {r for r in _ROW_LEVEL_RULES if r.startswith(("signal-", "agent-kind-"))}
    advisory = {r for r in GENERIC_LANE_ADVISORY if r.startswith(("signal-", "agent-kind-"))}
    assert not (advisory & GENERIC_LANE_STAYS_ERROR), "a rule cannot be in both classes"
    assert advisory | GENERIC_LANE_STAYS_ERROR == record_rules
    for rule in ("signal-stale", "signal-observed-future", "signal-source-is-search"):
        assert rule in GENERIC_LANE_STAYS_ERROR
    assert "verdict-inadmissible" in _ROW_LEVEL_RULES
    assert "verdict-inadmissible" not in GENERIC_LANE_ADVISORY


@pytest.mark.parametrize(
    ("change", "rule"),
    [
        ({"signal_observed": "2025-01-01"}, "signal-stale"),
        ({"signal_observed": "2027-01-01"}, "signal-observed-future"),
        (
            {"signal_source_url": "https://www.google.com/search?q=vertex+systems"},
            "signal-source-is-search",
        ),
    ],
)
def test_a_wrong_record_stays_an_error_in_the_generic_lane(tmp_path, change, rule):
    import datetime

    rows = [_row(verdict="re-angle", verdict_reason="argument does not fit the seat", **change)]
    a = audit_rows(
        rows, PROFILE, content_root=tmp_path, lane="generic", as_of=datetime.date(2026, 8, 15)
    )
    assert rule in _rules(a.errors)
    assert a.failed


def test_an_absent_record_is_still_one_advisory_line_in_the_generic_lane(tmp_path):
    import datetime

    rows = [
        _row(
            email=f"p{i}@vertex.example",
            verdict="",
            signal_source_url="",
            signal_observed="",
            signal_evidence="",
            signal_subject="",
            signal_agent_kind="",
        )
        for i in range(3)
    ]
    a = audit_rows(
        rows, PROFILE, content_root=tmp_path, lane="generic", as_of=datetime.date(2026, 8, 15)
    )
    assert a.errors == []
    assert sum(1 for w in a.warnings if w.startswith("signal-source-missing:")) == 1
    assert any(w.startswith("agent-kind-unresolved:") and "GENERIC" in w for w in a.warnings)


# --- CLI fixtures ---------------------------------------------------------------------


def _setup(tmp_path: Path, monkeypatch, rows: list[dict], *, lanes: str | None = "signal") -> Path:
    content_root = tmp_path / "content"
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content_root))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    folder = content_root / PROFILE / "accounts" / "vertex-systems"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "account-dossier-vertex-systems-2026-08-12.docx").write_text("x", encoding="utf-8")
    if lanes is not None:
        state = evals_dir(PROFILE, content_root)
        state.mkdir(parents=True, exist_ok=True)
        with (state / "lanes-state.jsonl").open("w", encoding="utf-8") as fh:
            for r in rows:
                rec = {"email": r["email"], "lane": lanes, "trigger": "t1", "stamp": "2026-08-14"}
                fh.write(json.dumps(rec) + "\n")
    p = tmp_path / "list.csv"
    cols = list(_row())
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return p


def _read(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --- PSK-032 at the CLI -----------------------------------------------------------------


def test_cli_generic_lane_fails_on_a_stale_signal(tmp_path, monkeypatch, capsys):
    p = _setup(tmp_path, monkeypatch, [_row(signal_observed="2025-01-01")], lanes=None)
    rc = ai.main(["--csv", str(p), "--profile", PROFILE, "--lane", "generic", "--as-of", AS_OF])
    out = capsys.readouterr().out
    assert rc == 1
    assert "signal-stale" in out
    assert out.rstrip().splitlines()[-1] == "FAIL"


def test_cli_generic_lane_passes_a_row_with_no_record_at_all(tmp_path, monkeypatch, capsys):
    blank = dict.fromkeys(
        (
            "signal_clause",
            "why_now",
            "signal_source_url",
            "signal_observed",
            "signal_evidence",
            "signal_subject",
            "signal_agent_kind",
            "category_relation",
            "verdict",
        ),
        "",
    )
    p = _setup(tmp_path, monkeypatch, [_row(**blank)], lanes=None)
    rc = ai.main(["--csv", str(p), "--profile", PROFILE, "--lane", "generic", "--as-of", AS_OF])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert out.rstrip().splitlines()[-1] == "PASS"
    advisory = [x for x in out.splitlines() if x.startswith("    - verdict-missing:")]
    assert len(advisory) == 1 and "advisory in the GENERIC lane" in advisory[0]


# --- PSK-023(a): --lane alone still checks verdicts -------------------------------------


def test_lane_without_require_verdict_refuses_an_inadmissible_verdict(
    tmp_path, monkeypatch, capsys
):
    rows = [
        _row(email="keep@vertex.example"),
        _row(email="gone@vertex.example", verdict="drop", verdict_reason="not a buyer"),
    ]
    p = _setup(tmp_path, monkeypatch, rows, lanes=None)
    rc = ai.main(["--csv", str(p), "--profile", PROFILE, "--lane", "generic", "--as-of", AS_OF])
    out = capsys.readouterr().out
    assert rc == 1
    line = next(x for x in out.splitlines() if "verdict-inadmissible:" in x)
    assert "gone@vertex.example" in line and "drop" in line
    assert "keep@vertex.example" not in line
    assert out.rstrip().splitlines()[-1] == "FAIL"


def test_verdict_inadmissible_is_lane_specific_and_silent_without_a_lane(tmp_path):
    import datetime

    rows = [_row(verdict="re-angle", verdict_reason="argument does not fit the seat")]
    kw = {"content_root": tmp_path, "as_of": datetime.date(2026, 8, 15)}
    assert "verdict-inadmissible" in _rules(audit_rows(rows, PROFILE, lane="signal", **kw).errors)
    assert "verdict-inadmissible" not in _rules(
        audit_rows(rows, PROFILE, lane="generic", **kw).errors
    )
    # A plain audit names no lane, so it asks no admissibility question (today's behaviour).
    assert "verdict-inadmissible" not in _rules(audit_rows(rows, PROFILE, **kw).errors)


# --- PSK-023(b): PASS must not read as "load this file" ---------------------------------


def _mixed_rows() -> list[dict]:
    return [
        _row(email="keep@vertex.example"),
        _row(email="angle@vertex.example", verdict="re-angle", verdict_reason="wrong seat"),
        _row(email="gone@vertex.example", verdict="drop", verdict_reason="not a buyer"),
        _row(email="judged@vertex.example", judge_verdict="drop", judge_calibrated="true"),
        _row(email="optout@vertex.example", suppression="unsubscribed"),
    ]


def _gate(p: Path, *extra: str) -> list[str]:
    return [
        "--csv",
        str(p),
        "--profile",
        PROFILE,
        "--require-verdict",
        "send",
        "--as-of",
        AS_OF,
        *extra,
    ]


def test_require_verdict_names_the_rows_it_refused(tmp_path, monkeypatch, capsys):
    p = _setup(tmp_path, monkeypatch, _mixed_rows())
    rc = ai.main(_gate(p))
    out = capsys.readouterr().out
    assert rc == 0, out
    refused = [x for x in out.splitlines() if x.startswith("  refused: ")]
    assert any("angle@vertex.example" in x and "re-angle" in x for x in refused)
    assert any("gone@vertex.example" in x and "drop" in x for x in refused)
    assert any("judged@vertex.example" in x and "judge" in x for x in refused)
    assert not any("keep@vertex.example" in x for x in refused)


def test_the_refused_listing_is_capped_at_twenty(tmp_path, monkeypatch, capsys):
    rows = [_row(email="keep@vertex.example")] + [
        _row(email=f"gone{i}@vertex.example", verdict="drop", verdict_reason="not a buyer")
        for i in range(25)
    ]
    p = _setup(tmp_path, monkeypatch, rows)
    ai.main(_gate(p))
    out = capsys.readouterr().out
    assert sum(1 for x in out.splitlines() if x.startswith("  refused: ")) == 20
    assert "+5 more" in out


def test_the_final_line_is_honest_without_write_kept(tmp_path, monkeypatch, capsys):
    p = _setup(tmp_path, monkeypatch, _mixed_rows())
    rc = ai.main(_gate(p))
    last = capsys.readouterr().out.rstrip().splitlines()[-1]
    assert rc == 0
    assert last == (
        "PASS (kept 1 of 5) — the input file still contains the 4 refused row(s); "
        "re-run with --write-kept <path> and load that file"
    )


def test_write_kept_holds_only_admissible_unsuppressed_rows(tmp_path, monkeypatch, capsys):
    p = _setup(tmp_path, monkeypatch, _mixed_rows())
    kept = p.parent / "kept.csv"  # beside the list it filters — see the confinement test below
    rc = ai.main(_gate(p, "--write-kept", str(kept)))
    out = capsys.readouterr().out
    assert rc == 0, out
    got = _read(kept)
    assert [r["email"] for r in got] == ["keep@vertex.example"]
    assert list(got[0]) == list(_row()), "same columns as the input"
    assert out.rstrip().splitlines()[-1] == (
        f"PASS (kept 1 of 5) — the input file still contains the 4 refused row(s); load {kept}"
    )
    assert not list(kept.parent.glob("*.tmp")), "the write is atomic and leaves no temp file"
    assert len(_read(p)) == 5, "the input is never rewritten"


def test_write_kept_refuses_to_overwrite_the_input(tmp_path, monkeypatch, capsys):
    p = _setup(tmp_path, monkeypatch, _mixed_rows())
    before = p.read_bytes()
    assert ai.main(_gate(p, "--write-kept", str(p))) == 2
    assert ai.main(_gate(p, "--write-kept", str(p.parent / "." / p.name))) == 2
    assert "REFUSED" in capsys.readouterr().err
    assert p.read_bytes() == before


def test_write_kept_is_not_written_when_the_gate_fails(tmp_path, monkeypatch, capsys):
    rows = [*_mixed_rows(), _row(email="stale@vertex.example", signal_observed="2025-01-01")]
    p = _setup(tmp_path, monkeypatch, rows)
    kept = tmp_path / "kept.csv"
    rc = ai.main(_gate(p, "--write-kept", str(kept)))
    out = capsys.readouterr().out
    assert rc == 1
    assert not kept.exists(), "a kept file beside a FAIL is a file someone will load"
    assert out.rstrip().splitlines()[-1] == "FAIL"


def test_a_list_with_nothing_filtered_still_ends_in_a_bare_pass(tmp_path, monkeypatch, capsys):
    p = _setup(tmp_path, monkeypatch, [_row(email="keep@vertex.example")])
    kept = tmp_path / "kept.csv"
    rc = ai.main(_gate(p, "--write-kept", str(kept)))
    out = capsys.readouterr().out
    assert rc == 0, out
    assert out.rstrip().splitlines()[-1] == "PASS"
    assert [r["email"] for r in _read(kept)] == ["keep@vertex.example"]


def test_a_ledger_suppressed_row_is_not_in_the_kept_file(tmp_path, monkeypatch, capsys):
    rows = [_row(email="keep@vertex.example"), _row(email="ledger@vertex.example")]
    p = _setup(tmp_path, monkeypatch, rows)
    ledger = suppression_ledger(PROFILE, tmp_path / "content")
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        "email,reason,date\nledger@vertex.example,unsubscribed,2026-08-01\n", encoding="utf-8"
    )
    kept = tmp_path / "kept.csv"
    rc = ai.main(_gate(p, "--write-kept", str(kept)))
    out = capsys.readouterr().out
    assert rc == 0, out
    assert [r["email"] for r in _read(kept)] == ["keep@vertex.example"]
    assert "kept 1 of 2" in out.rstrip().splitlines()[-1]


def test_a_traversing_profile_name_is_refused_not_resolved(tmp_path):
    import pytest as _pytest

    from gtm_core.competitor_index import load_competitors

    with _pytest.raises(ValueError):
        load_competitors("../outside", profiles_root=tmp_path)


# --- review M5 / L4 (2026-09-21) ----------------------------------------------------------


def test_write_kept_with_include_suppressed_is_refused(tmp_path, monkeypatch, capsys):
    """The kept file is "the file to load". With --include-suppressed the suppressed rows are
    never removed, so it would hand an opted-out person straight to the sending tool."""
    p = _setup(tmp_path, monkeypatch, _mixed_rows())
    kept = tmp_path / "kept.csv"
    assert ai.main(_gate(p, "--include-suppressed", "--write-kept", str(kept))) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and not kept.exists()
    (line,) = captured.err.strip().splitlines()
    assert line.startswith("REFUSED: ") and "--include-suppressed" in line


def test_each_of_those_flags_alone_still_works(tmp_path, monkeypatch, capsys):
    """Negative control: the refusal is about the COMBINATION."""
    p = _setup(tmp_path, monkeypatch, _mixed_rows())
    kept = tmp_path / "kept.csv"
    assert ai.main(_gate(p, "--write-kept", str(kept))) == 0
    assert [r["email"] for r in _read(kept)] == ["keep@vertex.example"]
    assert "suppressed: skipped 1 row(s)" in capsys.readouterr().out
    ai.main(_gate(p, "--include-suppressed"))
    assert "suppressed: skipped" not in capsys.readouterr().out


@pytest.mark.parametrize("extra", [[], ["--lane", "generic"]])
def test_an_empty_list_is_not_a_pass_at_enrollment(tmp_path, monkeypatch, capsys, extra):
    p = _setup(tmp_path, monkeypatch, [])
    kept = tmp_path / "kept.csv"
    assert ai.main(_gate(p, "--write-kept", str(kept), *extra)) == 1
    out = capsys.readouterr().out
    assert out.rstrip().splitlines()[-1] == "FAIL — the list is empty; nothing to enrol"
    assert "PASS" not in out and not kept.exists()


def test_an_empty_list_outside_enrollment_is_still_an_audit_with_nothing_to_find(
    tmp_path, monkeypatch, capsys
):
    """Without --require-verdict nothing is being enrolled, so this finding does not apply."""
    p = _setup(tmp_path, monkeypatch, [])
    assert ai.main(["--csv", str(p), "--profile", PROFILE, "--as-of", AS_OF]) == 0
    assert "the list is empty" not in capsys.readouterr().out


def test_the_kept_file_must_be_written_beside_the_list_it_filters(tmp_path):
    """The kept list is named people at named companies, and the path is chosen by whoever runs
    the gate. Beside the input is where it belongs — and one tenant's list written into another
    tenant's folder is the error the content root cannot catch, since both sit under it."""
    import argparse

    from gtm_core.verdict_refusals import flag_refusal

    tenant_a = tmp_path / "content" / "tenant-a" / "prospects" / "sequences"
    tenant_b = tmp_path / "content" / "tenant-b" / "prospects" / "sequences"
    for folder in (tenant_a, tenant_b):
        folder.mkdir(parents=True)
    listed = tenant_a / "ready-to-load-generic-2026-09-21.csv"
    listed.write_text("email\n", encoding="utf-8")

    def args(kept):
        return argparse.Namespace(
            write_kept=kept, include_suppressed=False, csv=listed, require_verdict="send"
        )

    assert flag_refusal(args(tenant_a / "kept.csv")) is None
    assert flag_refusal(args(tenant_a / "sub" / ".." / "kept.csv")) is None

    for elsewhere in (tenant_b / "kept.csv", tmp_path / "kept.csv"):
        refusal = flag_refusal(args(elsewhere))
        assert refusal and refusal.startswith("REFUSED: --write-kept")
        assert str(tenant_a) in refusal


# --- 2026-09-22 adversarial-regression-hunt: the foreign-lane refusal never ran ----------
#
# `hubspot-csv-map.md` documents the `lane` column as "read by the enrollment gate
# (`account_integrity --lane`), which refuses a list whose column disagrees", and
# `prospect/SKILL.md` gives that invocation with no `--require-verdict`. Coverage over the
# whole account-integrity scope shows the block implementing it (the `else` arm of
# `--require-verdict`) never executes, and scoped mutation confirmed three ways: deleting
# the refusal, deleting only its `return 2`, and dropping `""` from the set subtracted from
# the CSV's lane values all left the suite green.
#
# The middle one is the reason these are ERROR-level and not a nice-to-have: without the
# `return 2` the gate PRINTS "REFUSED: ..." and then goes on to PASS. An operator reading a
# run that says REFUSED and exits 0 is worse served than one with no check at all.


def _setup_with_lane(tmp_path, monkeypatch, rows: list[dict], *, lanes: str) -> Path:
    """`_setup` derives its header from a bare `_row()`, so a `lane=` kwarg reaches the dict
    and never the file. Rewrite the CSV with the row's own keys once the roots and the
    lane-state file are in place."""
    bare = [{k: v for k, v in r.items() if k != "lane"} for r in rows]
    p = _setup(tmp_path, monkeypatch, bare, lanes=lanes)
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return p


def test_cli_refuses_a_list_whose_lane_column_disagrees_with_the_flag(
    tmp_path, monkeypatch, capsys
):
    """`--lane generic` over rows the router stamped `personalised`. The lane-state file
    agrees with the CSV, so `_refuse_lane_state_mismatch` has nothing to say and this
    refusal is the only one that can fire — otherwise a passing test would prove nothing
    about which check produced the 2."""
    p = _setup_with_lane(tmp_path, monkeypatch, [_row(lane="personalised")], lanes="personalised")
    rc = ai.main(["--csv", str(p), "--profile", PROFILE, "--lane", "generic", "--as-of", AS_OF])
    err = capsys.readouterr().err
    assert "must not be enrolled into another" in err, err
    # Asserted together, deliberately: the message alone survives deleting `return 2`.
    assert rc == 2, f"printed REFUSED but exited {rc} — a refusal that does not refuse"


def test_a_blank_lane_cell_is_not_a_foreign_lane(tmp_path, monkeypatch, capsys):
    """An unstamped row is unrouted, not routed elsewhere. `""` is subtracted alongside the
    requested lane for exactly this reason, and dropping it turns every list carrying one
    blank cell into a refusal — a gate that cries wolf on its own routing gap.

    Asserts the absence of THIS message rather than a return code, so it stays honest about
    which check it is measuring even when another one legitimately fires."""
    p = _setup_with_lane(tmp_path, monkeypatch, [_row(lane="")], lanes="generic")
    ai.main(["--csv", str(p), "--profile", PROFILE, "--lane", "generic", "--as-of", AS_OF])
    err = capsys.readouterr().err
    assert "must not be enrolled into another" not in err, err
