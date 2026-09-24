"""The rule fleet scored as a classifier — the comparison that makes the judge falsifiable.

Fictional fixtures only (§R9).

The bias in these tests is toward catching a baseline that scores the fleet as WEAKER than
it is. That direction matters more than the other: an understated baseline makes the judge
look good by comparison, and "the judge beats the rules" is exactly the conclusion this
module exists to make hard to reach by accident.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from gtm_core.eval_calibration import _row_id
from gtm_core.rule_baseline import _FILE_LEVEL_LABELS, baseline_predictions

# The real spec grammar — a blockquoted body under a `**Step N — Day D**` header. Written
# out rather than approximated because the first draft of this fixture did NOT parse, and a
# spec that fails to parse produces exactly ONE file-level finding and zero per-row ones:
# every test below then passed while measuring nothing. A fixture that silently disables the
# thing under test is the same vacuous pass this whole module exists to make impossible.
SPEC = """# Sequence spec — test fixture

```
Campaign:    Fixture
Profile:     example         Product: Example Product
Provider:    saleshandy      Status: DRAFT — not staged
Sign-off:    Henry
hook_cell:   CISO × Partner / third-party agents entering the estate
argument_id: cross-org-agent-admission
Rules-Version: 2026-08-19
```

## 1. Variant

**Step 1 — Day 1** · Subject: `partner agents`
> Hi {{First Name}},
>
> Once agentic AI calls across an org boundary, mTLS and API keys prove the call came from a
> known company. They do not say which agent is calling, on whose authority, or what that
> company delegated to it. So {{Company}} takes the partner's word or blocks the integration,
> and neither survives the second partner.
>
> Tell me if this is already handled.
>
> Would the write-up on how another regulated institution admitted a partner's agents be
> useful?
>
> Henry
"""


def _parses(spec_path: Path) -> bool:
    """Guard against the fixture regressing to something the linter cannot read."""
    import sys

    sys.path.insert(0, "tests/linter")
    from outreach import parse_spec

    return bool(parse_spec(spec_path.read_text(encoding="utf-8")))


def _write(tmp_path: Path, rows: list[dict]) -> tuple[Path, Path]:
    spec = tmp_path / "spec.md"
    spec.write_text(SPEC, encoding="utf-8")
    csv_path = tmp_path / "list.csv"
    fields = sorted({k for r in rows for k in r})
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return spec, csv_path


def test_the_fixture_spec_actually_parses(tmp_path):
    """The control on every other test in this file.

    A spec the linter cannot parse yields one file-level `parse` ERROR and no per-row
    findings at all — so every row scores 'sendable' and every assertion below passes
    while exercising nothing. This is not hypothetical: the first version of this fixture
    did exactly that.
    """
    spec, _csv = _write(tmp_path, [{"email": "a@acme.example"}])
    assert _parses(spec), "the fixture spec no longer parses — every test here is vacuous"


def test_a_clean_row_is_predicted_sendable(tmp_path):
    """Positive control. A baseline that rejects everything scores a perfect TPR and is
    worthless — it would 'beat' any judge on defect-catching while flagging the whole list."""
    spec, csv_path = _write(
        tmp_path,
        [{"email": "chief@acme.example", "first": "Ada", "company": "Acme Systems"}],
    )
    preds = baseline_predictions(spec, csv_path, signoff="Henry")
    assert preds, "no predictions produced at all"
    assert all(preds.values()), f"a clean row was predicted unsendable: {preds}"


def test_a_row_with_a_blank_merge_field_is_predicted_unsendable(tmp_path):
    """The fleet must actually discriminate, or the comparison is between two constants."""
    spec, csv_path = _write(
        tmp_path,
        [
            {"email": "chief@acme.example", "first": "Ada", "company": "Acme Systems"},
            {"email": "broken@beta.example", "first": "", "company": "Beta Works"},
        ],
    )
    preds = baseline_predictions(spec, csv_path, signoff="Henry")
    by_email = {
        _row_id(str(spec), str(csv_path), "chief@acme.example", 1): "clean",
        _row_id(str(spec), str(csv_path), "broken@beta.example", 1): "broken",
    }
    labelled = {by_email[k]: v for k, v in preds.items() if k in by_email}
    assert labelled.get("clean") is True
    assert labelled.get("broken") is False, (
        "a row rendering a blank merge tag was predicted sendable — the fleet is being "
        "scored as blind to a defect it actually catches"
    )


def test_predictions_key_on_the_same_row_id_the_holdout_uses(tmp_path):
    """The join is the whole point. A different key silently yields an empty confusion
    matrix, which `confusion` reports as n=0 rather than as an error."""
    spec, csv_path = _write(
        tmp_path, [{"email": "chief@acme.example", "first": "Ada", "company": "Acme"}]
    )
    preds = baseline_predictions(spec, csv_path, signoff="Henry")
    expected = _row_id(str(spec), str(csv_path), "chief@acme.example", 1)
    assert expected in preds, f"row_id mismatch — holdout join would be empty: {list(preds)}"


def test_a_file_level_finding_does_not_condemn_every_row(tmp_path):
    """A SPEC- or PACK-level ERROR is a statement about the list, not about a recipient.

    Attributing one would mark the entire list unsendable and score the fleet as a
    reject-everything classifier. The filter originally named only PACK; a live run then
    produced exactly one ERROR and it was SPEC-level.
    """
    from gtm_core.rule_baseline import _is_recipient

    assert "SPEC" in _FILE_LEVEL_LABELS and "PACK" in _FILE_LEVEL_LABELS
    # The three non-recipient forms the linter actually emits. The third — a template-wide
    # aggregate — is why the filter is a positive test rather than a denylist.
    assert not _is_recipient("SPEC")
    assert not _is_recipient("PACK")
    assert not _is_recipient("touch1 (all 2 renders)")
    assert _is_recipient("chief@acme.example"), "the filter now rejects real recipients too"

    spec, csv_path = _write(
        tmp_path,
        [{"email": "chief@acme.example", "first": "Ada", "company": "Acme Systems"}],
    )
    # The premise check fires SPEC-level when the list cannot attest the declared premise.
    preds = baseline_predictions(
        spec,
        csv_path,
        signoff="Henry",
        profile="",  # premise vocab off; the point is the attribution rule, not the rule itself
    )
    assert all(preds.values()), "a file-level finding was attributed to a row"


def test_a_row_without_an_email_is_skipped_not_keyed_to_an_empty_string(tmp_path):
    """An empty-string row_id would collide across every unkeyed row."""
    spec, csv_path = _write(
        tmp_path,
        [
            {"email": "", "first": "Ada", "company": "Acme Systems"},
            {"email": "chief@beta.example", "first": "Bo", "company": "Beta Works"},
        ],
    )
    preds = baseline_predictions(spec, csv_path, signoff="Henry")
    assert len(preds) == 1, f"an unkeyed row produced a prediction: {preds}"


@pytest.mark.parametrize("touch", [1, 2])
def test_the_touch_argument_changes_the_key_not_the_verdict(tmp_path, touch):
    """`row_id` is per-touch; scoring touch 1 copy under touch 2's key would join to the
    wrong label."""
    spec, csv_path = _write(
        tmp_path, [{"email": "chief@acme.example", "first": "Ada", "company": "Acme"}]
    )
    preds = baseline_predictions(spec, csv_path, signoff="Henry", touch=touch)
    assert list(preds) == [_row_id(str(spec), str(csv_path), "chief@acme.example", touch)]
