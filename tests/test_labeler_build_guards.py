"""Positive controls for the eval labeler's own provenance guards.

Why this file exists. The labeling harness produced three of its own defects inside two
days, and every one of them looked exactly like a clean run:

* `_row_id` fingerprints (spec, csv, email, touch) but NOT the planted defect, so carrying
  suggestions across a re-sample re-paired 13 of 15 injected notes with a DIFFERENT rule.
  The sheet parsed, the gate passed, the counts matched.
* `_row_id` also does not fingerprint the BODY, so after the four specs were rewritten at
  14:12 against rows built at 14:11, all 20 carried notes and all 12 operator reviews were
  about text that no longer existed. 100% blast radius.
* the first `cell-segment-fit` run shipped three bugs, one of which INVERTED the defect it
  existed to catch.

Both surviving guards were verified by hand at the time — by deliberately corrupting one
row and watching the refusal fire. That is the right method and the wrong medium: a
one-off manual check is not a control, it is a memory. These tests make the corruption
automatic, so a guard that stops working fails a suite instead of going quiet.

The functions under test are pure (`body_hash`) or are re-implemented here as the exact
predicate `build.py` applies, because `build.py` is a script whose `main()` reads the live
content tree. What is pinned is the RULE, not the script's plumbing.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
from pathlib import Path

import pytest


def _find_build() -> Path:
    """Locate the labeler build script under whichever tenant content tree is checked out
    locally, without hardcoding a tenant name (this file must stay company-agnostic)."""
    content_root = Path(__file__).resolve().parents[1] / "content"
    matches = sorted(content_root.glob("*/prospects/evals/labeler-src/build.py"))
    if matches:
        return matches[0]
    return content_root / "_no_tenant_checked_out" / "labeler-src" / "build.py"


BUILD = _find_build()


def _load():
    if not BUILD.is_file():  # pragma: no cover - profile-local artefact
        pytest.skip("labeler build script not present in this checkout")
    spec = importlib.util.spec_from_file_location("labeler_build", BUILD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_body_hash_changes_when_the_copy_changes():
    """The 100%-blast-radius bug, as a control."""
    m = _load()
    before = m.body_hash("Hi Sam,\n\nContext first: they shipped a thing.\n\nHenry")
    after = m.body_hash("Hi Sam,\n\nThey shipped a thing.\n\nHenry")
    assert before != after


def test_body_hash_is_stable_under_rewrapping():
    """A re-wrap is not a rewrite. If it were treated as one, every rebuild would discard
    every carried note and the guard would be abandoned as too noisy — which is how a
    correct check gets switched off."""
    m = _load()
    a = m.body_hash("Hi Sam,\n\nOne two three four five.\n\nHenry")
    b = m.body_hash("Hi Sam,\n\nOne two three\nfour five.\n\nHenry")
    assert a == b


def test_body_hash_is_not_trivially_constant():
    """Negative control on the control: a hash that ignored its input would pass the
    stability test above and silently disable the staleness guard."""
    m = _load()
    assert len({m.body_hash(f"body {i}") for i in range(5)}) == 5


def test_the_injected_rule_provenance_predicate_refuses_a_repaired_note():
    """`build.py` refuses a carried note whose recorded rule is not the rule the current
    draw assigns. Pinned as the predicate, corrupted deliberately."""
    carried = {"injected_rule": "signal-contradicts-pitch"}
    drawn = "signal-agent-homonym"
    refused = carried.get("injected_rule") not in (None, drawn)
    assert refused, "a note written for one rule must not be re-paired with another"


def test_the_provenance_predicate_allows_a_matching_note():
    carried = {"injected_rule": "signal-agent-homonym"}
    assert carried.get("injected_rule") in (None, "signal-agent-homonym")


def test_a_pre_provenance_note_is_allowed_through():
    """Notes written before the guard existed carry no `injected_rule`. Refusing those
    would make the guard's introduction a migration event for no safety gain — the
    same reasoning `signal_record` applies to lists predating the record columns."""
    assert {}.get("injected_rule") in (None, "anything")


def test_every_injected_note_is_keyed_by_a_real_recipe(tmp_path):
    """The notes file and the recipe list must not drift apart.

    An injected row's note comes from `injected-notes.json` keyed by rule, so a recipe with
    no note crashes the build (loudly, which is right) and a note with no recipe is dead
    weight that will silently never be shown.
    """
    import json

    from gtm_core.build_eval_sheet import INJECTION_RECIPES

    notes_path = BUILD.parent / "injected-notes.json"
    if not notes_path.is_file():  # pragma: no cover
        pytest.skip("profile-local notes file not present")
    notes = json.loads(notes_path.read_text())
    recipes = {r["rule"] for r in INJECTION_RECIPES}
    missing = sorted(recipes - set(notes))
    assert not missing, f"recipes with no operator-facing note: {missing}"


def test_every_note_tag_is_explainable_by_the_legend():
    """The build already enforces this; pinning it means the enforcement cannot be removed
    without a test going red. A tag with no legend entry is a note the labeler cannot read."""
    import json

    notes_path = BUILD.parent / "injected-notes.json"
    tags_path = BUILD.parent / "tags.json"
    if not (notes_path.is_file() and tags_path.is_file()):  # pragma: no cover
        pytest.skip("profile-local labeler sources not present")
    notes = json.loads(notes_path.read_text())
    tags = json.loads(tags_path.read_text())
    known = {k for g in tags["groups"] for k, _ in g["tags"]}
    used = set()
    for note in notes.values():
        used |= set(re.findall(r"(?:^|; )([a-z][a-z0-9-]{3,}):", note))
    assert not sorted(used - known)


def test_the_aim_context_never_carries_the_conclusion():
    """The panel supplies evidence; the labeler supplies the conclusion.

    `category_relation` and `verdict` are the answers to two of the four aim injections and
    to the judgement being measured on every real row. Showing either would make the sheet
    grade itself — the same failure as showing the linter's verdict, which the design already
    forbids.
    """
    import json

    rows_path = BUILD.parent / "rows.json"
    if not rows_path.is_file():  # pragma: no cover
        pytest.skip("profile-local rows payload not present")
    for row in json.loads(rows_path.read_text()):
        ctx = row.get("ctx") or {}
        assert "category_relation" not in ctx
        assert "verdict" not in ctx


def test_the_payload_never_carries_the_injection_flag():
    """`internal-*.jsonl` is the only ground truth. A flag in the page source would make
    the blind sheet sighted for anyone who opens devtools."""

    rows_path = BUILD.parent / "rows.json"
    if not rows_path.is_file():  # pragma: no cover
        pytest.skip("profile-local rows payload not present")
    blob = rows_path.read_text()
    assert '"leaky"' not in blob
    assert '"injected"' not in blob
    assert '"injected_rule"' not in blob


def test_the_hash_helper_matches_a_plain_sha256_of_normalised_text():
    """Pins the algorithm so a future refactor cannot silently change what a stored
    `body_hash` means — every previously-recorded hash would then compare unequal and
    every carried note would be discarded as stale, quietly, on one commit."""
    m = _load()
    text = "Hi   Sam,\n\n  spaced   out.  "
    expected = hashlib.sha256(re.sub(r"\s+", " ", text).strip().encode()).hexdigest()[:16]
    assert m.body_hash(text) == expected


# --- storage namespace (2026-08-21) ------------------------------------------
#
# The one failure mode the build-time guards structurally could not reach: the operator's
# own browser. `body_hash` and `injected_rule` refuse a stale NOTE at build time, but the
# sheet's localStorage was namespaced by a fixed string, so a re-cut inherited the previous
# draw's saved answers — keyed by row ids that no longer existed. `rowState()` dereferences
# `state[r.id]`, threw on the first row, and `render()` never populated the list. The header
# is set BEFORE render(), so the page reported "30 rows" over an empty sheet: it looked
# loaded and empty rather than broken, which is the worst way for this to fail.


def test_the_storage_key_is_derived_from_the_draw_not_hardcoded():
    """A fixed key is what let one draw's state reach the next one."""
    tpl = BUILD.parent / "template.html"
    if not tpl.is_file():  # pragma: no cover
        pytest.skip("profile-local template not present")
    body = tpl.read_text()
    assert 'const KEY = "eval-labeler-" + DRAW;' in body
    assert "/*__DRAW__*/" in body, "the draw fingerprint placeholder must exist"
    assert 'const KEY = "eval-labeler-2026' not in body, "the key must not be a fixed literal"


def test_the_built_sheet_carries_a_substituted_draw_fingerprint():
    """The placeholder must actually be replaced; an unsubstituted default would put every
    draw back in one namespace under a different name."""
    sheets = sorted((BUILD.parents[1]).glob("labeler-*.html"))
    if not sheets:  # pragma: no cover
        pytest.skip("no built sheet present")
    latest = sheets[-1].read_text()
    m = re.search(r'const DRAW = "([0-9a-f]{12})"', latest)
    assert m, "built sheet carries no substituted draw fingerprint"
    assert m.group(1) != "dev"


def test_load_merges_saved_state_per_row_rather_than_wholesale():
    """Defence in depth behind the key. A stored state missing a row may lose that row's
    answers; it must never be able to blank the sheet."""
    tpl = BUILD.parent / "template.html"
    if not tpl.is_file():  # pragma: no cover
        pytest.skip("profile-local template not present")
    body = tpl.read_text()
    load_fn = body[body.index("function load() {") : body.index("function save() {")]
    assert "hasOwnProperty.call(saved, r.id)" in load_fn
    assert "if (raw) return JSON.parse(raw);" not in load_fn, (
        "returning stored state wholesale is the bug: rows absent from it get no seed"
    )


def test_two_different_draws_produce_two_different_keys():
    """The property that makes the fix structural rather than a rename."""
    import hashlib

    def draw(campaign, ids):
        return hashlib.sha256((campaign + "|" + ",".join(sorted(ids))).encode()).hexdigest()[:12]

    a = draw("ship30", ["r1", "r2", "r3"])
    b = draw("ship30", ["r1", "r2", "r4"])  # one row re-drawn
    c = draw("other", ["r1", "r2", "r3"])  # same rows, different campaign
    assert len({a, b, c}) == 3
    assert a == draw("ship30", ["r3", "r2", "r1"]), "order must not change the namespace"


# --- labeler interaction contract (2026-08-21) --------------------------------
#
# Two behaviours the operator asked for while labeling. Both are one-line changes and both
# were destructive-by-default beforehand, which is why they get pinned rather than trusted.


def _template() -> str:
    tpl = BUILD.parent / "template.html"
    if not tpl.is_file():  # pragma: no cover
        pytest.skip("profile-local template not present")
    return tpl.read_text()


def test_a_chip_never_replaces_the_pre_generated_note():
    """Applying a tag must APPEND, not overwrite.

    The first chip on an untouched row used to replace the model's suggestion outright. That
    is destructive and irreversible: the suggestion carries the reasoning and the evidence
    phrase behind it, and the changed-vs-kept diff — the actual measurement — needs the
    model's original read and the operator's correction side by side.
    """
    body = _template()
    apply_fn = body[body.index("function applyChip(") : body.index("let chipQ")]
    assert 'box.value = !box.value.trim() ? frag : box.value.trim() + "; " + frag;' in apply_fn
    # Assert on the CODE, not the prose: the word "untouched" still appears in the comment
    # explaining why the branch was removed, and that comment is worth keeping.
    assert "const untouched" not in apply_fn, "the replace-on-untouched branch must stay gone"


def test_scroll_resets_only_when_the_row_changes():
    """`setField()` re-renders in place on every Y/N button. An unconditional scroll reset
    threw the operator back to the top of the email four times per row."""
    body = _template()
    assert "if (_lastRenderedId !== r.id) {" in body
    # The reset must live INSIDE that guard, not anywhere else in render().
    render_fn = body[body.index("function render() {") : body.index("let _lastRenderedId")]
    resets = render_fn.count('document.querySelector("main").scrollTop = 0;')
    assert resets == 1, f"expected exactly one guarded scroll reset, found {resets}"
    guarded = render_fn[render_fn.index("if (_lastRenderedId !== r.id) {") :]
    assert 'document.querySelector("main").scrollTop = 0;' in guarded


# --- template_cold.html / build_from_sheet.py (the LIVE blind-sheet path) -----


def _find_build_from_sheet() -> Path:
    content_root = Path(__file__).resolve().parents[1] / "content"
    matches = sorted(content_root.glob("*/prospects/evals/labeler-src/build_from_sheet.py"))
    if matches:
        return matches[0]
    return content_root / "_no_tenant_checked_out" / "labeler-src" / "build_from_sheet.py"


def _load_build_from_sheet():
    path = _find_build_from_sheet()
    if not path.is_file():  # pragma: no cover - profile-local artefact
        pytest.skip("build_from_sheet.py not present in this checkout")
    spec = importlib.util.spec_from_file_location("build_from_sheet", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_export_handler_carries_every_field_the_page_asks_about():
    """Regression for 2026-09-01: `render()` builds its question list from `FIELDS`
    (`build_from_sheet.py`'s single field list), but the export button in
    `template_cold.html` has its OWN, separately hardcoded key list. Adding
    `bridge_depends_on_fact` to `FIELDS` made the page ask the question and save the
    answer into `state`/localStorage — and the export button silently dropped it anyway,
    because nobody had told it about the new key. The sheet looked complete; the exported
    file was missing a column, and nothing said so.

    Pinning the RULE (every FIELDS key must appear in the export payload), not the current
    field list, so the next field added here fails loudly instead of repeating this."""
    mod = _load_build_from_sheet()
    tpl = mod.HERE / "template_cold.html"
    if not tpl.is_file():  # pragma: no cover
        pytest.skip("profile-local template not present")
    body = tpl.read_text()
    export_fn = body[
        body.index('document.getElementById("export").onclick') : body.index("if (!lines.length)")
    ]
    for name, _prompt, _allow_na in mod.FIELDS:
        if name == "send_it":
            continue  # handled specially: `send_it: s.send_it === "Y"`, not `tri(...)`
        assert f"{name}:" in export_fn, (
            f"FIELDS asks '{name}' on the page but the export handler never writes it — "
            f"the labeler's answer would be silently dropped from the downloaded file"
        )
    assert "send_it: s.send_it" in export_fn


def test_build_from_sheet_fields_match_label_fields():
    """`build_from_sheet.FIELDS` and `gtm_core.eval_calibration.LABEL_FIELDS` are two
    independent lists that must name the same sub-checks in the same order, or the HTML
    labeler asks a question the markdown sheet / Label dataclass doesn't recognise (or
    vice versa). Closes the loop this file's other guard leaves open: LABEL_FIELDS ->
    FIELDS -> the page -> the export payload.
    """
    from gtm_core.eval_calibration import HOLISTIC_FIELD, LABEL_FIELDS

    mod = _load_build_from_sheet()
    assert [name for name, _prompt, _allow_na in mod.FIELDS] == [HOLISTIC_FIELD, *LABEL_FIELDS]


def test_build_from_sheet_parses_a_freshly_rendered_grouped_sheet():
    """The round trip that never existed until B1: render a synthetic spec-major sheet
    with `gtm_core.eval_calibration.render_labeling_sheet`, feed the markdown to
    `build_from_sheet.parse_sheet`, and assert every row_id, subject, and RECONSTRUCTED
    FULL BODY comes back byte-identical to what was rendered. This is what turns "the
    sheet format changed; fix the parser" from a runtime message into a red test — the
    two files (`gtm_core/eval_calibration.py` and this tenant's `build_from_sheet.py`)
    share a markdown grammar with no schema enforcing agreement between them.
    """
    from gtm_core.build_eval_sheet import build_golden_row
    from gtm_core.eval_calibration import _dewrap, render_labeling_sheet

    mod = _load_build_from_sheet()

    rows = [
        build_golden_row(
            spec="spec-a.md",
            csv="csv-a.csv",
            touch=1,
            email="a@x.example",
            subject="Shared subject",
            body="Open para.\n\nMid A.\n\nClose para.",
            context={"title": "CTO", "company": "Acme"},
        ),
        build_golden_row(
            spec="spec-a.md",
            csv="csv-a.csv",
            touch=1,
            email="b@x.example",
            subject="Shared subject",
            body="Open para.\n\nMid B.\n\nClose para.",
            context={"title": "CISO", "company": "Beta"},
        ),
        build_golden_row(
            spec="spec-b.md",
            csv="csv-b.csv",
            touch=1,
            email="c@x.example",
            subject="Solo subject",
            body="One paragraph only.",
            context={"title": "CEO", "company": "Gamma"},
        ),
    ]
    sheet = render_labeling_sheet(rows)
    parsed_rows, parsed_groups = mod.parse_sheet(sheet)

    assert {r["row_id"] for r in parsed_rows} == {r.row_id for r in rows}
    assert len(parsed_groups) == 2  # spec-a's 2-row group, spec-b's singleton

    by_id = {r.row_id: r for r in rows}
    for pr in parsed_rows:
        original = by_id[pr["row_id"]]
        assert pr["subject"] == original.subject
        assert pr["body"] == _dewrap(original.body), (
            f"row {pr['row_id']}: reconstructed body does not match the original — "
            f"got {pr['body']!r}, expected {_dewrap(original.body)!r}"
        )
