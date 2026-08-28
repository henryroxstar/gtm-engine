"""Contract: the email-quality loop stays CONNECTED to something.

Modelled on `test_brief_lint_wired.py`, for the same reason and against the same failure.
Every function in this loop is unit-tested elsewhere; none of those tests notices if the
component stops being invoked. This repo has shipped that exact defect four times —
`--json` never passed, five unreachable catalogue rules, `cta-unstaged-artifact` dead
since the day it shipped, and `[roles.judge]` itself, which sat in the model registry for
a day with no consumer while the surrounding harness was reported done. `models.toml`
carries the warning in its own comment: *a component named in a design doc and absent from
the registry looks exactly like one that works.*

So these tests assert wiring, not behaviour. They are deliberately shallow and deliberately
annoying to delete.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SEQUENCE_BODY = REPO / "plugin" / "skills" / "email-sequence" / "body_template.md"
QUALITY_BODY = REPO / "plugin" / "skills" / "email-quality" / "body_template.md"


# ── R1: the components are actually invoked ───────────────────────────────────


def test_the_skill_tells_the_run_to_persist_a_qa_record():
    """Without `--json` on the gate, the rule lifecycle report has nothing to read.

    This is the single highest-leverage line in the whole loop: `rule_lifecycle_report`
    was written, tested, and returned an empty report forever, because zero QA records
    existed on disk. Delete this flag and every rule reverts to `no-control` — which the
    report will honestly say, and which nobody will notice.
    """
    text = SEQUENCE_BODY.read_text(encoding="utf-8")
    assert "--json content/" in text, (
        "the merge-render gate command no longer persists a QA record; "
        "gtm_core.eval_calibration rules is inert again"
    )
    assert "--sequence-id" in text, (
        "QA records without a sequence-id cannot be scoped, and a directory holding two "
        "campaigns aggregates into one confident, wrong fire rate"
    )


def test_the_skill_tells_the_run_to_invoke_the_judge():
    """The reading pass must route to `email-quality`, not just describe reading."""
    text = SEQUENCE_BODY.read_text(encoding="utf-8")
    assert "email-quality judge" in text, (
        "email-sequence no longer points its reading pass at the judge — the judge is "
        "wired, tested, and never called"
    )


def test_roles_judge_has_a_consumer():
    """`[roles.judge]` must resolve somewhere in the tree.

    Kills the exact failure class `gtm_core/models.toml` warns about in its own comment.
    A registry role with no consumer is indistinguishable from a working one by reading
    the registry.
    """
    from gtm_core.models import resolve_model

    spec = resolve_model("judge")
    assert spec.provider == "anthropic", (
        "the judge role must resolve to a Claude model — judged rows carry prospect PII "
        "and CLAUDE.md's model discipline binds this role"
    )

    consumers = [
        p
        for p in (REPO / "agent").rglob("*.py")
        if 'resolve_model("judge")' in p.read_text(encoding="utf-8")
    ]
    assert consumers, (
        "nothing in agent/ resolves the `judge` role. The role is registered and inert — "
        "the precise defect models.toml's own comment was added to prevent."
    )


def test_the_judge_mcp_server_is_registered_with_the_sdk():
    """A server that exists but is never spawned is the same as one that does not exist."""
    import dataclasses

    from agent.config import Config
    from agent.mcp_config import build_mcp_servers

    base = Config.from_env(repo_root=REPO)
    cfg = dataclasses.replace(base, anthropic_api_key="sk-test")
    servers = build_mcp_servers(cfg, "example")
    assert "judge" in servers, "the judge MCP server is not wired into mcp_config"
    assert servers["judge"]["args"] == ["-m", "agent.mcp.judge", "--transport", "stdio"]
    assert servers["judge"]["env"]["GTM_PROFILE"] == "example", (
        "without GTM_PROFILE the worker cannot scope its cost ledger or read a budget cap"
    )


def test_the_judge_server_is_a_registered_egress_allowlist_entry():
    """The judge makes HTTP calls. §R6 requires that to be a registered, reviewed entry.

    Positive control on the rule itself: the allowlist must ALSO still name vision, so a
    test that passes because the allowlist was emptied is impossible.
    """
    rules = (REPO / ".semgrep" / "gtm-invariants.yml").read_text(encoding="utf-8")
    assert "/agent/mcp/judge/**" in rules, (
        "the judge performs egress and is not on the §R6 allowlist — either register it "
        "or route its calls through an existing tool"
    )
    assert "/agent/mcp/vision/**" in rules, "allowlist looks emptied; this test is vacuous"


# ── R11: a partial batch must not read as a complete one ──────────────────────


def test_judge_record_count_equals_row_count():
    """`check-complete` fails on a short batch and passes on a full one.

    The positive control is the second half: a check that always fails is as useless as
    one that always passes, and 'every mistake looks like a deny' is this repo's own
    recurring lesson.
    """
    from gtm_core.adjudication import Adjudication, completeness

    rows = [{"email": "a@acme.example"}, {"email": "b@acme.example"}]
    full = [
        Adjudication(email="a@acme.example", verdict="send", score=4),
        Adjudication(email="b@acme.example", verdict="drop", score=1),
    ]
    short = full[:1]

    assert completeness(rows, full).complete, "a complete batch must report complete"
    result = completeness(rows, short)
    assert not result.complete
    assert result.missing == ["b@acme.example"]


def test_write_verdicts_refuses_a_partially_judged_list(tmp_path):
    """A half-judged list written as a judged one reports confidence it does not have."""
    from gtm_core.adjudication import Adjudication, main, write_records

    csv_path = tmp_path / "list.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["email", "company"])
        w.writeheader()
        w.writerow({"email": "a@acme.example", "company": "Acme"})
        w.writerow({"email": "b@acme.example", "company": "Acme"})

    records = tmp_path / "rec.jsonl"
    write_records([Adjudication(email="a@acme.example", verdict="send", score=4)], records)
    out = tmp_path / "out.csv"
    rc = main(
        ["write-verdicts", "--csv", str(csv_path), "--records", str(records), "--out", str(out)]
    )
    assert rc == 1, "write-verdicts accepted a partial batch"
    assert not out.exists(), "a refused write must not leave a half-written file"

    # Positive control: the same command succeeds once every row has a record.
    write_records(
        [
            Adjudication(email="a@acme.example", verdict="send", score=4),
            Adjudication(email="b@acme.example", verdict="drop", score=1, evidence="no fit"),
        ],
        records,
    )
    assert (
        main(
            ["write-verdicts", "--csv", str(csv_path), "--records", str(records), "--out", str(out)]
        )
        == 0
    )
    written = list(csv.DictReader(out.open(encoding="utf-8")))
    # The judge writes its OWN columns. Before 2026-08-27 it wrote `verdict`, which is the
    # researcher's, so a judge `send` silently erased a research `re-angle` or `drop` and
    # the enrollment gate then admitted the row.
    assert [r["judge_verdict"] for r in written] == ["send", "drop"]
    assert written[1]["judge_verdict_reason"] == "no fit", (
        "a non-send verdict with no reason is blocked by signal_record — the reason is "
        "what makes the verdict re-checkable"
    )


def test_write_verdicts_never_touches_the_research_verdict(tmp_path):
    """`verdict` has one writer: the researcher. This is what makes 'the judge ranks and
    never blocks' true of the artifact rather than only of the documentation."""
    from gtm_core.adjudication import Adjudication, main, write_records

    csv_path = tmp_path / "list.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["email", "verdict", "verdict_reason"])
        w.writeheader()
        w.writerow({"email": "a@acme.example", "verdict": "drop", "verdict_reason": "competitor"})

    records = tmp_path / "rec.jsonl"
    write_records([Adjudication(email="a@acme.example", verdict="send", score=5)], records)
    out = tmp_path / "out.csv"
    assert (
        main(
            ["write-verdicts", "--csv", str(csv_path), "--records", str(records), "--out", str(out)]
        )
        == 0
    )
    row = next(iter(csv.DictReader(out.open(encoding="utf-8"))))
    assert row["verdict"] == "drop", "the judge overwrote the researcher's verdict"
    assert row["verdict_reason"] == "competitor"
    assert row["judge_verdict"] == "send"


def test_write_verdicts_collapses_touches_worst_first(tmp_path):
    """One address carries one record per touch; keeping the last made the row-level
    verdict depend on iteration order."""
    from gtm_core.adjudication import Adjudication, main, write_records

    csv_path = tmp_path / "list.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["email"])
        w.writeheader()
        w.writerow({"email": "a@acme.example"})

    records = tmp_path / "rec.jsonl"
    write_records(
        [
            Adjudication(
                email="a@acme.example", verdict="drop", score=1, touch=1, evidence="wrong seat"
            ),
            Adjudication(email="a@acme.example", verdict="send", score=4, touch=2),
        ],
        records,
    )
    out = tmp_path / "out.csv"
    assert (
        main(
            ["write-verdicts", "--csv", str(csv_path), "--records", str(records), "--out", str(out)]
        )
        == 0
    )
    row = next(iter(csv.DictReader(out.open(encoding="utf-8"))))
    assert row["judge_verdict"] == "drop", "a later clean touch retracted an earlier defect"
    assert row["judge_verdict_reason"] == "wrong seat"


def test_judge_calibrated_is_blank_when_nobody_asked(tmp_path):
    """Tri-state on disk: '' (never checked) is not 'false' (checked, and it failed)."""
    from gtm_core.adjudication import Adjudication, main, write_records

    csv_path = tmp_path / "list.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["email"])
        w.writeheader()
        w.writerow({"email": "a@acme.example"})
    records = tmp_path / "rec.jsonl"
    write_records([Adjudication(email="a@acme.example", verdict="send", score=4)], records)
    out = tmp_path / "out.csv"
    main(["write-verdicts", "--csv", str(csv_path), "--records", str(records), "--out", str(out)])
    assert next(iter(csv.DictReader(out.open(encoding="utf-8"))))["judge_calibrated"] == ""


def test_judge_calibrated_is_false_when_the_profile_has_no_sealed_holdout(tmp_path, monkeypatch):
    from gtm_core.adjudication import Adjudication, main, write_records

    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    csv_path = tmp_path / "list.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["email"])
        w.writeheader()
        w.writerow({"email": "a@acme.example"})
    records = tmp_path / "rec.jsonl"
    write_records([Adjudication(email="a@acme.example", verdict="send", score=4)], records)
    out = tmp_path / "out.csv"
    main(
        [
            "write-verdicts",
            "--csv",
            str(csv_path),
            "--records",
            str(records),
            "--out",
            str(out),
            "--profile",
            "acme",
        ]
    )
    assert next(iter(csv.DictReader(out.open(encoding="utf-8"))))["judge_calibrated"] == "false"


# ── R7: a repaired body cannot skip the deterministic tier ────────────────────


def test_a_repaired_body_is_re_linted_before_it_can_be_enrolled():
    """Repair runs AFTER the copy gates, so a re-composed body can reintroduce a defect."""
    text = QUALITY_BODY.read_text(encoding="utf-8")
    assert "merge_render_linter.py <spec> --csv <repaired.csv>" in text, (
        "the repair loop no longer re-runs the deterministic gates on the repaired body; "
        "a re-composed email can now ship carrying a violation a rule already knows about"
    )


def test_the_skill_forbids_prefill_on_the_validating_round():
    """Prefill anchoring (R5) is a prompt-level decision, so the prompt has to carry it."""
    text = QUALITY_BODY.read_text(encoding="utf-8")
    assert "Do not pass `--prefill`" in text, (
        "the blind-first-round instruction is gone — the judge can now anchor the very "
        "labels meant to validate it"
    )


# ── R4: the disqualification has to survive into the next send list ───────────


def test_eval_disqualified_is_not_a_provider_dnc_reason():
    """A fit judgment must never reach the provider's permanent, unremovable DNC list."""
    from gtm_core import suppression

    assert suppression.EVAL_DISQUALIFIED not in suppression.PROVIDER_DNC_REASONS
    assert suppression.EVAL_WRONG_PERSON not in suppression.PROVIDER_DNC_REASONS
    # Positive control: the reason that SHOULD be there still is.
    assert "dnc-optout" in suppression.PROVIDER_DNC_REASONS


def test_a_regenerated_send_csv_still_excludes_an_eval_disqualified_row(tmp_path):
    """The R4 end-to-end: rebuild the CSV and prove the row is still excluded.

    This is the only risk in the plan with no recovery path — the send has happened. So
    the test rebuilds a build output from scratch (simulating a pool rebuild, which drops
    every in-file annotation) and asserts the ledger still catches the row.
    """
    from gtm_core.suppression import EVAL_DISQUALIFIED, Suppression, append, load, verify

    ledger = tmp_path / "suppression.csv"
    append(
        ledger,
        [Suppression(email="wrong@acme.example", reason=EVAL_DISQUALIFIED, date="2026-08-22")],
    )

    # A freshly rebuilt pool: no `suppression` column at all, and the row is back.
    rebuilt = tmp_path / "ready-to-load.csv"
    with rebuilt.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["email", "company"])
        w.writeheader()
        w.writerow({"email": "wrong@acme.example", "company": "Acme"})
        w.writerow({"email": "fine@beta.example", "company": "Beta"})

    findings = verify(rebuilt, load(ledger))
    assert findings, "a rebuild dropped the suppression and nothing caught it"
    assert "suppression" in findings[0]

    # Positive control: once the rebuild honours the ledger, verify passes. Without this
    # half, a `verify` that always returns findings would pass the test above too.
    with rebuilt.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["email", "company", "suppression"])
        w.writeheader()
        w.writerow(
            {"email": "wrong@acme.example", "company": "Acme", "suppression": EVAL_DISQUALIFIED}
        )
        w.writerow({"email": "fine@beta.example", "company": "Beta", "suppression": ""})
    assert verify(rebuilt, load(ledger)) == []


# ── R14: docs cannot claim a phase is built while its wiring test is absent ───


def test_the_judge_is_documented_where_the_model_discipline_lives():
    """A new live PII-bearing model role is a CLAUDE.md fact, not an implementation detail."""
    claude_md = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
    # Deliberately specific. A bare `"judge" in text` passes on the word "judgement",
    # which is already in this file — caught while writing this test, and exactly the
    # weak-assertion class that makes a green suite meaningless.
    assert "`judge`" in claude_md, (
        "CLAUDE.md's model discipline does not name the `judge` role — a live, "
        "PII-bearing role that must resolve to Claude belongs in the invariant doc"
    )
    assert "role `judge`" in claude_md or "roles.judge" in claude_md, (
        "the judge is mentioned but not as a model role bound by the discipline section"
    )


@pytest.mark.parametrize(
    "verb",
    ["score", "rules", "reconcile"],
)
def test_every_p1_p2_p3_function_has_a_cli_entry_point(verb):
    """The three phases were 'built' for days with no way to run them."""
    from gtm_core import eval_calibration

    with pytest.raises(SystemExit):
        eval_calibration.main([verb, "--help"])


def test_the_judge_writes_only_inside_the_content_root():
    """Tenant boundary: a judge that can write anywhere is a hole, not a worker."""
    src = (REPO / "agent" / "mcp" / "judge" / "scoring.py").read_text(encoding="utf-8")
    assert "resolve_content_root" in src and "relative_to(" in src, (
        "the judge no longer confines out_path to the resolved content root"
    )
    server = (REPO / "agent" / "mcp" / "judge" / "server.py").read_text(encoding="utf-8")
    assert "confine_to_content_root(out_path)" in server, (
        "the confinement helper exists but score_emails no longer calls it"
    )


def test_the_judge_scores_every_row_not_a_sample():
    """Per-row coverage is the point; a sampling judge silently un-covers the list."""
    server = (REPO / "agent" / "mcp" / "judge" / "server.py").read_text(encoding="utf-8")
    scoring = (REPO / "agent" / "mcp" / "judge" / "scoring.py").read_text(encoding="utf-8")
    # BOTH transports must walk the whole list — a fallback that samples would silently
    # under-cover exactly when the primary path is unavailable.
    assert "for index, row in enumerate(rows)" in server, "the api path stopped iterating every row"
    assert "range(0, len(rows), SDK_BATCH_ROWS)" in server, "the sdk path stopped covering the list"
    assert "unscored=True" in scoring, (
        "a row the judge cannot read must be recorded unscored, never dropped"
    )


def test_the_internal_eval_record_is_never_shown_to_a_labeler():
    """The sheet renderer must not print the fields that reveal a planted row."""
    from gtm_core.eval_calibration import GoldenRow, render_labeling_sheet

    row = GoldenRow(
        row_id="a" * 16,
        spec="spec.md",
        csv="list.csv",
        touch=1,
        email="someone@acme.example",
        subject="A subject",
        body="A body.",
        context={"title": "Head of Ops", "company": "Acme"},
        injected=True,
        injected_rule="company-allcaps",
    )
    sheet = render_labeling_sheet([row])
    assert "company-allcaps" not in sheet, "the sheet reveals which rows are planted"
    assert "someone@acme.example" not in sheet, "the sheet leaks the recipient address"
    # Positive control: it does render the thing a labeler is supposed to read.
    assert "A body." in sheet


def test_the_qa_record_carries_what_the_rule_report_reads(tmp_path):
    """The two ends of the P2 join must agree on field names.

    `rule_fire_rates` reads `renders`, `by_rule` and `checks_run`. If the linter renames
    one, the report silently returns zero fires for every rule and reads as a clean fleet.
    """
    from gtm_core.eval_calibration import rule_fire_rates

    record = {
        "renders": 10,
        "by_rule": {"specificity": {"ERROR": 3}},
        "checks_run": {"specificity": {}, "hedge-missing": {}},
    }
    rates = rule_fire_rates([record])
    assert rates["specificity"] == (3, 10)
    assert rates["hedge-missing"] == (0, 10), (
        "a rule that ran and found nothing must report 0/N, not vanish — a missing rule "
        "and a clean rule are different findings"
    )
    del tmp_path


# ── dual auth: key-first, OAuth fallback ──────────────────────────────────────
# The judge must run whether or not an API key exists. Production (docker-compose)
# provides one; a local Claude Code session does not and uses the host's OAuth via the
# Agent SDK instead. Both paths must exist, and which one ran must be recorded.


def test_backend_is_api_when_a_key_is_present(monkeypatch):
    from agent.mcp.judge import scoring

    # ModelSpec is frozen and `api_key()` reads os.getenv(api_key_env) at call time, so
    # the env var IS the seam — patching the object raises FrozenInstanceError.
    monkeypatch.setenv(scoring._SPEC.api_key_env, "sk-test")
    backend, reason = scoring.select_backend()
    assert backend == "api"
    assert "one row per request" in reason


def test_backend_falls_back_to_sdk_when_no_key(monkeypatch):
    """The whole point of the fallback: no key must not mean no judge."""
    from agent.mcp.judge import scoring

    monkeypatch.delenv(scoring._SPEC.api_key_env, raising=False)
    backend, reason = scoring.select_backend()
    assert backend == "sdk"
    assert "host's own auth" in reason


def test_the_judge_server_is_registered_even_without_a_key():
    """Gating registration on the key would delete the very fallback that exists for the
    no-key case — the judge would be absent exactly when the fallback should run."""
    import dataclasses

    from agent.config import Config
    from agent.mcp_config import build_mcp_servers

    base = Config.from_env(repo_root=REPO)
    keyless = build_mcp_servers(dataclasses.replace(base, anthropic_api_key=None), "example")
    assert "judge" in keyless, "no judge server without a key — the OAuth fallback is unreachable"
    assert "ANTHROPIC_API_KEY" not in keyless["judge"]["env"], (
        "an empty key was passed through; the worker would try the API path and fail"
    )

    withkey = build_mcp_servers(dataclasses.replace(base, anthropic_api_key="sk-test"), "example")
    assert withkey["judge"]["env"]["ANTHROPIC_API_KEY"] == "sk-test"
    # Positive control: a worker that IS key-gated stays key-gated.
    assert "vision" not in keyless and "vision" in withkey


def test_every_record_says_which_backend_produced_it():
    """A holdout scored across both transports is a confound. It must be a visible one.

    The SDK path batches to amortise subprocess cost, which trades away some of the
    per-row independence the confusion matrix and Cohen's kappa assume. `backend` and
    `judge_batch` are what make that trade measurable rather than invisible.
    """
    from gtm_core.adjudication import Adjudication

    rec = Adjudication(email="a@x.example", verdict="send", score=4, backend="sdk", judge_batch=5)
    assert rec.to_dict()["backend"] == "sdk"
    assert rec.to_dict()["judge_batch"] == 5
    # Default is the independent case, so a hand-written record is never mistaken for a
    # batched one.
    plain = Adjudication(email="b@x.example", verdict="send", score=4)
    assert plain.judge_batch == 1


def test_a_misaligned_batch_reply_scores_nobody_rather_than_the_wrong_body():
    """The batched path's sharpest edge.

    If the model returns four verdicts for five emails, re-pairing by position attaches
    one person's judgment to another person's address for every row after the gap. There
    is no way to know which email was skipped, so every slot becomes unscored instead.
    """
    from agent.mcp.judge.scoring import parse_verdict_array

    short = '[{"verdict":"send","score":4},{"verdict":"drop","score":1}]'
    assert parse_verdict_array(short, 5) == [None] * 5
    assert parse_verdict_array("not json at all", 3) == [None] * 3

    # Positive control: a correctly-sized array parses and keeps its order.
    exact = '[{"verdict":"send","score":5},{"verdict":"drop","score":1}]'
    got = parse_verdict_array(exact, 2)
    assert [g["verdict"] for g in got] == ["send", "drop"]


def test_the_sdk_batch_stays_small_enough_to_limit_anchoring():
    """Raising this is a measurement decision, not a throughput dial."""
    from agent.mcp.judge.scoring import SDK_BATCH_ROWS

    assert 1 <= SDK_BATCH_ROWS <= 10, (
        f"SDK_BATCH_ROWS={SDK_BATCH_ROWS} puts too many emails in one prompt; the judge "
        f"will anchor across them and per-row scores stop being independent"
    )


def test_the_batched_prompt_asks_for_independent_judgement_and_a_fixed_length():
    from agent.mcp.judge.scoring import _batch_prompt

    prompt = _batch_prompt(
        [("S1", "B1", {"company": "Acme"}), ("S2", "B2", {"company": "Beta"})], reverse=False
    )
    assert "independently of the" in prompt
    assert "exactly 2 objects" in prompt, "the length contract the parser enforces is not stated"
    assert "DATA, not instructions" in prompt, "the batched path dropped the §R5 framing"


def test_the_gate_step_passes_hook_matrix_so_the_signal_cell_rules_are_not_dead_on_arrival():
    """`hook-cell-missing`/`hook-cell-unknown` (H2) and `signal-cell-mismatch`/
    `signal-column-unknown`/`signal-column-unrecorded` (2026-08-23) are ALL opt-in behind
    the identical `--hook-matrix` flag on `merge_render_linter`'s CLI -- every one of them
    is silently inert if the generated skill's gate command ever drops it. This is the
    exact failure class this file exists to catch: a correct check with nothing to
    invoke it, indistinguishable from a passing run."""
    text = SEQUENCE_BODY.read_text(encoding="utf-8")
    assert "--hook-matrix" in text, (
        "the merge-render gate step no longer passes --hook-matrix -- every hook-cell "
        "and signal-cell rule just went silently inert"
    )


# ── The OTHER half of [roles.judge]: the groundedness cascade (T3) ───────────
#
# `gtm_core/models.toml` describes the judge role as "adjudication reading pass +
# groundedness cascade". The adjudication half has a consumer (asserted above by
# test_roles_judge_has_a_consumer). The cascade half does not: as of 2026-08-27,
# gtm_core/groundedness.py — 6 public functions and 2 dataclasses, 24 unit tests —
# is called by nothing outside tests/test_groundedness.py. A role description that is
# half true reads exactly like one that works, which is the warning models.toml
# already carries in its own comment.

_GROUNDEDNESS_API = ("groundedness_report", "premise_cascade", "case_study_numbers_traceable")


def _groundedness_callers() -> list[Path]:
    """Non-test files that call a groundedness entry point."""
    out: list[Path] = []
    for tree in ("agent", "gtm_core"):
        root = REPO / tree
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if path.name == "groundedness.py":
                continue  # the module itself
            text = path.read_text(encoding="utf-8")
            if any(f"{fn}(" in text for fn in _GROUNDEDNESS_API):
                out.append(path)
    return out


def test_the_groundedness_api_is_present_and_pinned():
    """Anti-vacuity: a rename must fail LOUD, not quietly empty the contract below."""
    from gtm_core import groundedness

    missing = [fn for fn in _GROUNDEDNESS_API if not hasattr(groundedness, fn)]
    assert not missing, f"gtm_core.groundedness lost {missing} — T3 now asserts nothing"


def test_the_groundedness_half_of_the_judge_role_has_a_consumer():
    """`[roles.judge]` promises a groundedness cascade; something must actually run it.

    Twin of test_roles_judge_has_a_consumer above, for the other half of the same role.
    Fact-checking that nothing invokes is indistinguishable, from the registry, from
    fact-checking that works.
    """
    callers = _groundedness_callers()
    assert callers, (
        "nothing under agent/ or gtm_core/ calls gtm_core.groundedness. models.toml's "
        "[roles.judge] describes an 'adjudication reading pass + groundedness cascade' and "
        "only the first half exists. See PRD §3.5 (W1a)."
    )


def test_the_email_quality_body_template_invokes_the_groundedness_gate():
    """Wired in Python but absent from the skill body is still inert.

    `content/` is gitignored, so CI never sees a runtime artifact — the body template is
    the only committed surface that proves the gate is actually invoked at run time. Same
    mechanism as the --hook-matrix assertion above.
    """
    text = QUALITY_BODY.read_text(encoding="utf-8")
    assert "groundedness" in text.lower(), (
        "email-quality's body_template.md never mentions the groundedness gate, so the "
        "judge step will not run it however well it is wired in Python"
    )
