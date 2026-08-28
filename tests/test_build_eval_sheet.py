"""Tests for the real-sequence golden-set builder (P0's render step).

All fixtures invented (docs/RULES.md R9): a synthetic ``cells.toml`` + spec + CSV under
``tmp_path``, standing in for a profile's real staged sequences. This proves the pipeline
end to end — resolve sources, render, sample, inject, write — without touching real
prospect data; the real run against a real profile is a separate, explicit operator step.
"""

from __future__ import annotations

import json

import pytest

from gtm_core.build_eval_sheet import (
    INJECTION_RECIPES,
    all_live_rows,
    build_golden_set,
    build_injected_golden_rows,
    build_real_golden_rows,
    load_draft_sources,
    load_sources,
    write_golden_set,
)

SPEC_TEXT = """
**Step 1 — Day 1** · Subject: `identity in production`
> Hi {{First Name}},
>
> {{Why Now}}. Once agents at {{Company}} move from retrieving data to acting on it,
> identity becomes the question an auditor asks first. Tell me if you've got this
> covered: your logs capture which account touched a record but not which agent held
> the authority to act.
>
> Would the one-pager on how another team mapped that to {{Company}}'s agent path be
> useful?
>
> Henry

**Step 2 — Day 4** (same thread, no subject)
> Hi {{First Name}},
>
> My hunch: the open piece is portable proof of the who, not the what.
>
> Want their before-and-after?
>
> Henry
"""

CSV_HEADER = (
    "first,last,email,title,company,company_domain,segment,tier,signal_clause,suppression\n"
)


def _csv_row(i, *, title="CISO", suppressed=False):
    return (
        f"Chris{i},Renner{i},chris{i}@cascade{i}.example,{title},Cascade{i},"
        f"cascade{i}.example,enterprise,A,"
        f'"Truist{i} sits on the AARM agent-runtime-security working group",'
        f"{'yes' if suppressed else ''}\n"
    )


@pytest.fixture
def profile_fixture(tmp_path):
    content_root = tmp_path / "content"
    seq_dir = content_root / "demo" / "prospects" / "sequences"
    seq_dir.mkdir(parents=True)

    (seq_dir / "spec-demo-2026-08-20.md").write_text(SPEC_TEXT, encoding="utf-8")

    rows = CSV_HEADER + "".join(_csv_row(i, title="CISO" if i % 2 else "CEO") for i in range(40))
    rows += _csv_row(40, suppressed=True)  # one suppressed row, must be excluded
    (seq_dir / "clean-demo.csv").write_text(rows, encoding="utf-8")

    (seq_dir / "cells.toml").write_text(
        """
[[sequence]]
id = "seq1"
title = "Demo sequence"
csv = "clean-demo.csv"
spec = "spec-demo-2026-08-20.md"
campaign = "demo-campaign"
""",
        encoding="utf-8",
    )
    return content_root


# --------------------------------------------------------------------------- loading


def test_load_sources_finds_the_spec_and_parses_touches(profile_fixture):
    sources = load_sources("demo", profile_fixture)
    assert len(sources) == 1
    assert len(sources[0].touches) == 2


def test_all_live_rows_excludes_suppressed(profile_fixture):
    rows, touches_by_spec = all_live_rows("demo", profile_fixture)
    assert len(rows) == 40  # 41 in the CSV, 1 suppressed
    assert all(not (r.get("suppression") or "").strip() for r in rows)


def test_all_live_rows_excludes_rows_whose_verdict_is_not_send(tmp_path):
    """A researched list carries a per-row send/drop verdict. A dropped row renders
    ``{{Why Now}}`` blank, so labeling it spends a slot on an email that cannot be sent."""
    content_root = tmp_path / "content"
    seq_dir = content_root / "demo" / "prospects" / "sequences"
    seq_dir.mkdir(parents=True)
    (seq_dir / "spec-demo-2026-08-20.md").write_text(SPEC_TEXT, encoding="utf-8")
    header = CSV_HEADER.rstrip("\n") + ",verdict\n"
    body = ""
    for i in range(6):
        verdict = "send" if i % 2 else "drop"
        clause = "" if verdict == "drop" else f"Cascade{i} shipped an agent gateway"
        body += (
            f"Chris{i},Renner{i},chris{i}@cascade{i}.example,CISO,Cascade{i},"
            f'cascade{i}.example,enterprise,A,"{clause}",,{verdict}\n'
        )
    (seq_dir / "clean-demo.csv").write_text(header + body, encoding="utf-8")
    (seq_dir / "cells.toml").write_text(
        '[[sequence]]\nid = "seq1"\ntitle = "Demo"\ncsv = "clean-demo.csv"\n'
        'spec = "spec-demo-2026-08-20.md"\ncampaign = "demo-campaign"\n',
        encoding="utf-8",
    )
    rows, _ = all_live_rows("demo", content_root)
    assert len(rows) == 3
    assert {r["verdict"] for r in rows} == {"send"}


def test_all_live_rows_keeps_every_row_when_the_list_has_no_verdict_column(profile_fixture):
    """A pre-verdict list must not be emptied by the verdict filter."""
    rows, _ = all_live_rows("demo", profile_fixture)
    assert len(rows) == 40
    assert "verdict" not in CSV_HEADER


def test_load_sources_campaign_filter_scopes_to_one_generation(tmp_path):
    """Mid-recut a profile has both generations in cells.toml; a sheet that mixes them
    calibrates the judge partly against copy that is being retired."""
    content_root = tmp_path / "content"
    seq_dir = content_root / "demo" / "prospects" / "sequences"
    seq_dir.mkdir(parents=True)
    for name in ("spec-old-2026-08-18.md", "spec-new-2026-08-20.md"):
        (seq_dir / name).write_text(SPEC_TEXT, encoding="utf-8")
    rows = CSV_HEADER + "".join(_csv_row(i) for i in range(4))
    (seq_dir / "clean-old.csv").write_text(rows, encoding="utf-8")
    (seq_dir / "clean-new.csv").write_text(rows, encoding="utf-8")
    (seq_dir / "cells.toml").write_text(
        '[[sequence]]\nid = "old"\ntitle = "Old"\ncsv = "clean-old.csv"\n'
        'spec = "spec-old-2026-08-18.md"\ncampaign = "live"\n\n'
        '[[sequence]]\nid = "new"\ntitle = "New"\ncsv = "clean-new.csv"\n'
        'spec = "spec-new-2026-08-20.md"\ncampaign = "recut"\n',
        encoding="utf-8",
    )
    assert len(load_sources("demo", content_root)) == 2
    only_new = load_sources("demo", content_root, campaign="recut")
    assert len(only_new) == 1
    assert only_new[0].csv_path.endswith("clean-new.csv")
    assert load_sources("demo", content_root, campaign="nope") == []


def test_all_live_rows_derives_seat_not_from_csv_column(profile_fixture):
    rows, _ = all_live_rows("demo", profile_fixture)
    seats = {r["seat"] for r in rows}
    assert "security" in seats  # from CISO titles
    assert "ceo" in seats  # from CEO titles (the old catch-all "exec" split at H3)
    assert "seat" not in CSV_HEADER  # confirms it really was derived, not read


# --------------------------------------------------------------------------- row-level cell
#
# `enriched["cell"]` used to be constant per spec (the spec's own declared cell). These pin
# the 2026-08-23 change: a row's OWN recorded/derived cell wins, so `adjudication`'s `cell`
# stratum can finally vary WITHIN one list.

MINI_HOOK_MATRIX = """---
source: manual
---
# Hook matrix — Fixture Co

## Enterprise

| Signal → / Persona ↓ | Compliance event (audit, breach) | M&A / consolidation |
|---|---|---|
| **CISO** | "Evidence a regulator can read." | "One perimeter." |
"""


@pytest.fixture
def profile_with_matrix(tmp_path):
    """Same shape as ``profile_fixture`` but with a real hook-matrix.md the row-cell
    preference can resolve against, and rows carrying `hook_cell`/`signal_column`."""
    content_root = tmp_path / "content"
    seq_dir = content_root / "demo" / "prospects" / "sequences"
    seq_dir.mkdir(parents=True)
    (seq_dir / "spec-demo-2026-08-20.md").write_text(SPEC_TEXT, encoding="utf-8")

    header = (
        "first,last,email,title,company,company_domain,segment,tier,signal_clause,"
        "suppression,hook_cell,signal_column\n"
    )
    rows = header
    # Row A: hook_cell already recorded directly -- must win outright.
    rows += (
        'A,One,a@x.example,CISO,X,x.example,enterprise,A,"X sits on a working group",,'
        '"CISO × M&A / consolidation",\n'
    )
    # Row B: no hook_cell, but signal_column is recorded -- must derive to a DIFFERENT
    # cell than row A, proving the stratum now varies WITHIN this one list.
    rows += (
        'B,Two,b@x.example,CISO,X,x.example,enterprise,A,"X sits on a working group",,,'
        '"Compliance event (audit, breach)"\n'
    )
    # Row C: neither column populated -- must fall back to the spec's declared cell,
    # unchanged from before this feature existed.
    rows += 'C,Three,c@x.example,CISO,X,x.example,enterprise,A,"X sits on a working group",,,\n'
    (seq_dir / "clean-demo.csv").write_text(rows, encoding="utf-8")

    (seq_dir / "cells.toml").write_text(
        """
[[sequence]]
id = "seq1"
csv = "clean-demo.csv"
spec = "spec-demo-2026-08-20.md"
campaign = "demo-campaign"
""",
        encoding="utf-8",
    )

    profiles_root = tmp_path / "profiles"
    (profiles_root / "demo" / "knowledge").mkdir(parents=True)
    (profiles_root / "demo" / "knowledge" / "hook-matrix.md").write_text(
        MINI_HOOK_MATRIX, encoding="utf-8"
    )
    return content_root, profiles_root


def test_row_level_cell_beats_the_specs_cell(profile_with_matrix):
    content_root, profiles_root = profile_with_matrix
    rows, _ = all_live_rows("demo", content_root, profiles_root=profiles_root)
    by_email = {r["email"]: r for r in rows}
    assert by_email["a@x.example"]["cell"] == "CISO × M&A / consolidation"


def test_cell_stratum_varies_within_one_list_via_derived_signal(profile_with_matrix):
    """This is the whole point: two rows in the SAME list, SAME spec, now report two
    DIFFERENT cells because they recorded different signals -- before this change every
    row in a list collapsed onto the one cell the spec declared."""
    content_root, profiles_root = profile_with_matrix
    rows, _ = all_live_rows("demo", content_root, profiles_root=profiles_root)
    by_email = {r["email"]: r for r in rows}
    assert by_email["b@x.example"]["cell"] == "CISO × Compliance event (audit, breach)"
    assert by_email["a@x.example"]["cell"] != by_email["b@x.example"]["cell"]


def test_a_row_with_neither_column_falls_back_to_the_specs_declared_cell(profile_with_matrix):
    content_root, profiles_root = profile_with_matrix
    rows, _ = all_live_rows("demo", content_root, profiles_root=profiles_root)
    by_email = {r["email"]: r for r in rows}
    # No declared hook_cell in SPEC_TEXT's front block (it has none), so this falls all
    # the way through to the spec-path tier -- unchanged from before this feature existed.
    assert by_email["c@x.example"]["cell"].startswith("spec:")


def test_a_missing_hook_matrix_does_not_crash_and_preserves_old_behaviour(profile_fixture):
    """`profile_fixture` (used by every other test in this file) has no hook-matrix.md at
    all for its fixture profile. `_load_matrix_if_present` must fail quiet, not raise, and
    every row must fall back to the spec-declared-cell tier exactly as before this change."""
    rows, _ = all_live_rows("demo", profile_fixture, profiles_root=profile_fixture / "profiles")
    assert rows  # did not raise
    for r in rows:
        assert r["cell"].startswith("spec:")


# --------------------------------------------------------------------------- real rows


def test_build_real_golden_rows_renders_touch_one(profile_fixture):
    rows, touches_by_spec = all_live_rows("demo", profile_fixture)
    golden, chosen = build_real_golden_rows(rows, touches_by_spec, n_real=10)
    assert len(golden) == 10
    assert len(chosen) == 10
    g = golden[0]
    assert g.touch == 1
    assert "{{" not in g.subject  # merge tags actually substituted
    assert "{{" not in g.body
    assert not g.injected


def test_build_real_golden_rows_is_stable_across_runs(profile_fixture):
    rows, touches_by_spec = all_live_rows("demo", profile_fixture)
    a, _ = build_real_golden_rows(rows, touches_by_spec, n_real=10)
    b, _ = build_real_golden_rows(rows, touches_by_spec, n_real=10)
    assert [g.row_id for g in a] == [g.row_id for g in b]


# --------------------------------------------------------------------------- injected rows


def test_build_injected_golden_rows_uses_every_usable_recipe_before_repeating(profile_fixture):
    rows, touches_by_spec = all_live_rows("demo", profile_fixture)
    usable_count = len(INJECTION_RECIPES)
    injected, unusable = build_injected_golden_rows(
        rows,
        touches_by_spec,
        INJECTION_RECIPES,
        n_injected=usable_count - len(unusable_for(profile_fixture)),
    )
    rules_used = [g.injected_rule for g in injected]
    assert len(rules_used) == len(set(rules_used))  # distinct while usable recipes remain
    assert all(g.injected for g in injected)
    # Every rule used must be one that was NOT reported unusable.
    assert not (set(rules_used) & set(unusable))


def unusable_for(content_root):
    """The recipes that produce no visible change against this fixture's copy."""
    rows, touches_by_spec = all_live_rows("demo", content_root)
    _, unusable = build_injected_golden_rows(
        rows, touches_by_spec, INJECTION_RECIPES, n_injected=len(INJECTION_RECIPES)
    )
    return unusable


def test_unusable_recipes_are_reported_not_silently_dropped(profile_fixture):
    """The bug this guards: a mutation that changes nothing a labeler can see (field not
    in the copy, or withheld as PII) used to be recorded as a planted defect anyway. The
    operator then can't penalise it, and `rule_lifecycle_report` reads that as 'nobody
    cared' → delete-candidate, retiring a working rule on an artifact of the sheet."""
    rows, touches_by_spec = all_live_rows("demo", profile_fixture)
    injected, unusable = build_injected_golden_rows(
        rows, touches_by_spec, INJECTION_RECIPES, n_injected=len(INJECTION_RECIPES)
    )
    # last-name-symbols and email-domain-mismatch can never show up in a rendered body:
    # neither field is in the copy, and the sheet withholds the address as PII.
    assert "last-name-symbols" in unusable
    assert "email-domain-mismatch" in unusable
    # ...and none of them silently became a sheet row.
    assert not (set(unusable) & {g.injected_rule for g in injected})


def test_every_injected_row_differs_from_its_clean_render(profile_fixture):
    """The load-bearing invariant: an injected row must actually look different."""
    rows, touches_by_spec = all_live_rows("demo", profile_fixture)
    injected, _ = build_injected_golden_rows(
        rows, touches_by_spec, INJECTION_RECIPES, n_injected=10
    )
    clean, _ = build_real_golden_rows(rows, touches_by_spec, n_real=len(rows))
    clean_by_email = {g.email: (g.subject, g.body) for g in clean}
    for g in injected:
        assert (g.subject, g.body) != clean_by_email.get(g.email), (
            f"{g.injected_rule} produced a render identical to the clean one"
        )


def test_injected_row_actually_carries_the_mutation(profile_fixture):
    rows, touches_by_spec = all_live_rows("demo", profile_fixture)
    injected, _ = build_injected_golden_rows(
        rows, touches_by_spec, INJECTION_RECIPES, n_injected=len(INJECTION_RECIPES)
    )

    company_allcaps = next(g for g in injected if g.injected_rule == "company-allcaps")
    # The company token should appear uppercased somewhere in the rendered body.
    assert any(
        tok.isupper() and len(tok) > 3
        for tok in company_allcaps.body.split()
        if tok.startswith("CASCADE")
    )

    banned = next(g for g in injected if g.injected_rule == "banned-word")
    assert "reach out" in banned.body.lower()

    persona = next(g for g in injected if g.injected_rule == "persona-lead-mismatch")
    assert "auditor" in persona.body.lower()


def test_persona_lead_mismatch_only_applied_to_non_security_donors(profile_fixture):
    rows, touches_by_spec = all_live_rows("demo", profile_fixture)
    # Draw enough rows to force multiple cycles through the recipe list, so the
    # donor_ok filter is genuinely exercised against both CISO (security) and CEO
    # (exec) donors in the fixture rather than happening to skip the check entirely.
    injected, _ = build_injected_golden_rows(
        rows, touches_by_spec, INJECTION_RECIPES, n_injected=len(INJECTION_RECIPES) * 2
    )
    persona_rows = [g for g in injected if g.injected_rule == "persona-lead-mismatch"]
    assert persona_rows  # the filter didn't just starve the recipe out entirely
    for g in persona_rows:
        # Fixture titles are only "CISO" (-> security seat, excluded by donor_ok) or
        # "CEO" (-> exec seat, allowed) — so every surviving donor must be a CEO row.
        assert g.context.get("title") == "CEO"


# --------------------------------------------------------------------------- full pipeline


def test_build_golden_set_produces_requested_split(profile_fixture):
    golden, _ = build_golden_set("demo", n_real=15, n_injected=10, content_root=profile_fixture)
    real_n = sum(1 for g in golden if not g.injected)
    injected_n = sum(1 for g in golden if g.injected)
    assert real_n == 15
    assert injected_n == 10


def test_build_golden_set_real_and_injected_rows_never_share_a_recipient(profile_fixture):
    golden, _ = build_golden_set("demo", n_real=20, n_injected=15, content_root=profile_fixture)
    real_emails = {g.email for g in golden if not g.injected}
    injected_emails = {g.email for g in golden if g.injected}
    assert not (real_emails & injected_emails)


def test_build_golden_set_raises_on_empty_profile(tmp_path):
    with pytest.raises(ValueError):
        build_golden_set("nonexistent", content_root=tmp_path / "content")


# --------------------------------------------------------------------------- writing


def test_write_golden_set_writes_blind_sheet_and_internal_record(profile_fixture, tmp_path):
    golden, _ = build_golden_set("demo", n_real=10, n_injected=5, content_root=profile_fixture)
    result = write_golden_set("demo", golden, content_root=profile_fixture)

    sheet_text = open(result["sheet"], encoding="utf-8").read()
    assert "injected" not in sheet_text.lower()
    assert all(g.row_id in sheet_text for g in golden)

    internal_lines = open(result["internal"], encoding="utf-8").read().strip().splitlines()
    assert len(internal_lines) == 15
    records = [json.loads(line) for line in internal_lines]
    assert sum(1 for r in records if r["injected"]) == 5
    assert all(r["injected_rule"] for r in records if r["injected"])


# --------------------------------------------------------------------------- duplicates / blanks


def test_add_duplicates_repeats_rows_with_the_same_row_id(profile_fixture):
    from gtm_core.build_eval_sheet import add_duplicates

    golden, _ = build_golden_set("demo", n_real=20, n_injected=5, content_root=profile_fixture)
    with_dups = add_duplicates(golden, 5)
    assert len(with_dups) == len(golden) + 5
    from collections import Counter

    repeats = [rid for rid, n in Counter(g.row_id for g in with_dups).items() if n > 1]
    assert len(repeats) == 5  # a duplicate shares its row_id, which is what makes it recoverable


def test_add_duplicates_is_stable_across_runs(profile_fixture):
    from gtm_core.build_eval_sheet import add_duplicates

    golden, _ = build_golden_set("demo", n_real=20, n_injected=5, content_root=profile_fixture)
    a = [g.row_id for g in add_duplicates(golden, 4)]
    b = [g.row_id for g in add_duplicates(golden, 4)]
    assert a == b


def test_add_duplicates_noop_at_zero(profile_fixture):
    from gtm_core.build_eval_sheet import add_duplicates

    golden, _ = build_golden_set("demo", n_real=10, n_injected=3, content_root=profile_fixture)
    assert len(add_duplicates(golden, 0)) == len(golden)


def test_choose_blank_is_stratified_across_injected_and_real(profile_fixture):
    from gtm_core.build_eval_sheet import choose_blank

    golden, _ = build_golden_set("demo", n_real=30, n_injected=10, content_root=profile_fixture)
    blank = choose_blank(golden, 12)
    assert len(blank) == 12
    by_id = {g.row_id: g for g in golden}
    # The blank set must contain BOTH classes — a holdout with no planted defects would
    # have almost no negative class to measure TNR against.
    assert any(by_id[rid].injected for rid in blank)
    assert any(not by_id[rid].injected for rid in blank)


def test_prefilled_sheet_leaves_blank_rows_blank_and_marks_them(profile_fixture):
    from gtm_core.build_eval_sheet import choose_blank
    from gtm_core.eval_calibration import render_labeling_sheet

    golden, _ = build_golden_set("demo", n_real=20, n_injected=5, content_root=profile_fixture)
    blank = choose_blank(golden, 8)
    prefill = {
        g.row_id: {
            "send_it": True,
            "fact_creates_problem": True,
            "fact_supports_pitch": False,
            "frame_fits_seat": True,
            "right_person": True,
            "note": "looks fine",
        }
        for g in golden
        if g.row_id not in blank
    }
    sheet = render_labeling_sheet(golden, prefill=prefill)
    assert sheet.count("← label this one cold") == 8
    assert sheet.count("`send_it:` ___") == 8  # exactly the blank ones
    assert sheet.count("`send_it:` Y") == len(golden) - 8


def test_prefilled_sheet_roundtrips_through_the_parser(profile_fixture):
    """The whole point: a pre-filled sheet must parse straight back with no edits."""
    from gtm_core.build_eval_sheet import choose_blank
    from gtm_core.eval_calibration import parse_filled_sheet, render_labeling_sheet

    golden, _ = build_golden_set("demo", n_real=20, n_injected=5, content_root=profile_fixture)
    blank = choose_blank(golden, 8)
    prefill = {
        g.row_id: {
            "send_it": False,
            "fact_creates_problem": False,
            "fact_supports_pitch": True,
            "frame_fits_seat": None,
            "right_person": True,
            "note": "n",
        }
        for g in golden
        if g.row_id not in blank
    }
    sheet = render_labeling_sheet(golden, prefill=prefill)
    labels = parse_filled_sheet(sheet, prefilled_ids=list(prefill))
    assert len(labels) == len(prefill)  # blank rows correctly skipped as unlabeled
    assert all(label_.prefilled for label_ in labels)
    assert all(label_.send_it is False for label_ in labels)
    assert all(label_.frame_fits_seat is None for label_ in labels)


def test_duplicated_rows_are_always_blank_even_if_not_sampled_into_the_blank_set(profile_fixture):
    """A pre-filled duplicate would show the same suggestion twice; the labeler keeps it
    both times and intra_rater_agreement reports a meaningless 1.0. Duplicates must be
    blank in BOTH places for the self-consistency check to measure anything."""
    from gtm_core.build_eval_sheet import add_duplicates, choose_blank

    golden, _ = build_golden_set("demo", n_real=30, n_injected=8, content_root=profile_fixture)
    with_dups = add_duplicates(golden, 6)
    # Ask for ZERO stratified blanks — duplicates must still come back blank.
    blank = choose_blank(with_dups, 0)
    from collections import Counter

    dup_ids = {rid for rid, n in Counter(g.row_id for g in with_dups).items() if n > 1}
    assert dup_ids, "fixture should have produced duplicates"
    assert dup_ids <= blank, "every duplicated row_id must be forced blank"


def test_prefilled_sheet_never_prefills_a_duplicate(profile_fixture):
    from gtm_core.build_eval_sheet import add_duplicates, choose_blank
    from gtm_core.eval_calibration import render_labeling_sheet

    golden, _ = build_golden_set("demo", n_real=30, n_injected=8, content_root=profile_fixture)
    with_dups = add_duplicates(golden, 6)
    blank = choose_blank(with_dups, 10)
    prefill = {
        g.row_id: {
            "send_it": True,
            "fact_creates_problem": True,
            "fact_supports_pitch": False,
            "frame_fits_seat": True,
            "right_person": True,
            "note": "",
        }
        for g in with_dups
        if g.row_id not in blank
    }
    sheet = render_labeling_sheet(with_dups, prefill=prefill)
    from collections import Counter

    dup_ids = {rid for rid, n in Counter(g.row_id for g in with_dups).items() if n > 1}
    assert not (dup_ids & set(prefill))
    # ...and both copies of each duplicate really do render blank on the sheet.
    for rid in dup_ids:
        assert sheet.count(f"`{rid}`") == 2


# --------------------------------------------------------------------------- drafted cells


DRAFT_SPEC = """
```
hook_cell:   CISO × Compliance event (audit, breach)
premise:     cross-org-agents
```

**Step 1 — Day 1** · Subject: `the partner's agents`
> Hi {{First Name}},
>
> {{Why Now}}. You may well have this covered, but a partner's agent arriving at
> {{Company}} carries no identity your gateway can check.
>
> Would the one-pager on how another team verified that at the edge be useful?
>
> Henry
"""


@pytest.fixture
def draft_fixture(profile_fixture):
    """A drafted cell alongside the staged sequence — deliberately NOT in cells.toml."""
    cell = profile_fixture / "demo" / "prospects" / "evals" / "drafts" / "ciso-x-compliance-event"
    cell.mkdir(parents=True)
    (cell / "spec.md").write_text(DRAFT_SPEC, encoding="utf-8")
    (cell / "rows.csv").write_text(
        CSV_HEADER + "".join(_csv_row(100 + i) for i in range(4)), encoding="utf-8"
    )
    return profile_fixture


def test_load_draft_sources_reads_a_cell_absent_from_cells_toml(draft_fixture):
    # The whole point: cells.toml is the STAGED-sequence join, and a drafted cell is not
    # staged. If this ever required a cells.toml row, drafting would corrupt outcome
    # attribution to widen a sheet.
    assert load_sources("demo", draft_fixture) == [
        s for s in load_sources("demo", draft_fixture) if s.staged
    ]
    drafts = load_draft_sources("demo", draft_fixture)
    assert len(drafts) == 1
    assert drafts[0].cell == "CISO × Compliance event (audit, breach)"
    assert drafts[0].staged is False


def test_staged_sources_are_marked_staged(draft_fixture):
    assert all(s.staged for s in load_sources("demo", draft_fixture))


def test_drafts_are_excluded_by_default_and_included_on_request(draft_fixture):
    # Positive control on both sides: the default must actually withhold them, and the flag
    # must actually admit them. A test that only asserts the default would pass on a loader
    # that never worked.
    without, _ = all_live_rows("demo", draft_fixture, include_drafts=False)
    with_, _ = all_live_rows("demo", draft_fixture, include_drafts=True)
    assert len(with_) - len(without) == 4
    assert all(r["__staged"] for r in without)
    assert sum(1 for r in with_ if not r["__staged"]) == 4


def test_drafted_rows_carry_their_declared_cell_as_the_messaging_axis(draft_fixture):
    rows, _ = all_live_rows("demo", draft_fixture, include_drafts=True)
    cells = {r["cell"] for r in rows if not r["__staged"]}
    assert cells == {"CISO × Compliance event (audit, breach)"}


def test_a_draft_dir_missing_either_file_is_skipped_not_fatal(draft_fixture):
    root = draft_fixture / "demo" / "prospects" / "evals" / "drafts"
    (root / "half-written").mkdir()
    (root / "half-written" / "spec.md").write_text(DRAFT_SPEC, encoding="utf-8")  # no rows.csv
    assert len(load_draft_sources("demo", draft_fixture)) == 1


def test_no_drafts_dir_is_not_an_error(profile_fixture):
    assert load_draft_sources("demo", profile_fixture) == []


def test_golden_set_can_draw_from_drafted_cells(draft_fixture):
    rows, _ = build_golden_set(
        "demo", n_real=12, n_injected=0, content_root=draft_fixture, include_drafts=True
    )
    specs = {r.spec for r in rows}
    assert any("drafts" in s for s in specs), "sampler never reached the drafted cell"
