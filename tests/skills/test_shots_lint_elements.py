"""C11 — the `elements` shot-list field and its lint rule.

The rule is narrow: it warns when a named slug has no element in the bound profile's library, and
it is INERT when no library was supplied. Both halves matter — a check that guesses a profile is
a check that reports one tenant's elements as missing from another's.
"""

from __future__ import annotations

import pytest

from gtm_core.shots_lint import lint_shotlist


def _doc(elements):
    return {
        "source_item": "item-1",
        "total_duration_s": 4.0,
        "style_scaffold": {"look": "clean studio", "provider_model": "wan2_7"},
        "shots": [
            {
                "camera": "locked-off wide",
                "visual": "a machined brass dial on a bench",
                "motion_prompt": "the dial turns a quarter clockwise",
                "duration_s": 4.0,
                "role": "broll",
                "elements": elements,
            }
        ],
    }


def _element_warnings(elements, known):
    _, warnings = lint_shotlist(_doc(elements), known_elements=known)
    return [w for w in warnings if ".elements names" in w]


def test_a_slug_the_library_does_not_hold_warns():
    """It would otherwise fail mid-run, after the script is written and the gate is passed."""
    hits = _element_warnings(["brass-dial", "ghost"], {"brass-dial"})
    assert len(hits) == 1 and "ghost" in hits[0]


def test_a_slug_the_library_holds_is_clean():
    """Positive control."""
    assert _element_warnings(["brass-dial"], {"brass-dial"}) == []


def test_the_rule_is_inert_when_no_library_was_supplied():
    """The CLI has no --profile. Guessing one is worse than saying nothing."""
    _, warnings = lint_shotlist(_doc(["anything-at-all"]))
    assert not [w for w in warnings if ".elements names" in w]


def test_an_empty_library_still_reports_the_slug():
    """An empty set is "the library was read and holds nothing", not "no library was supplied"."""
    hits = _element_warnings(["brass-dial"], set())
    assert len(hits) == 1 and "library is empty" in hits[0]


@pytest.mark.parametrize("bad", [None, "brass-dial", 7, {}])
def test_a_non_list_elements_field_is_ignored_by_this_rule(bad):
    """Shape is the schema's job; this rule does not double-report it as a missing element."""
    doc = _doc(["x"])
    doc["shots"][0]["elements"] = bad
    _, warnings = lint_shotlist(doc, known_elements={"brass-dial"})
    assert not [w for w in warnings if ".elements names" in w]


def test_the_finding_is_a_warning_and_never_an_error():
    """A slug typo is caught here early; refusing the whole list would over-claim, since the
    storyboard can still resolve every OTHER shot while the operator fixes one name."""
    errors, _ = lint_shotlist(_doc(["ghost"]), known_elements={"brass-dial"})
    assert not [e for e in errors if ".elements names" in e]


# ── the CLI actually runs the rule (review finding: it had no production caller) ──────────────


def _cli_tree(tmp_path, monkeypatch, *, elements):
    """A shot list where a real run puts one: content/<profile>/scripts/, under the root."""
    import json

    from gtm_core.elements import Element, Pose, write

    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    scripts = tmp_path / "probe" / "scripts"
    scripts.mkdir(parents=True)
    write(
        Element(
            slug="brass-dial",
            kind="object",
            name="Brass dial",
            poses=[Pose("wide", "01.png", use="u")],
        ),
        "probe",
        content_root=tmp_path,
    )
    path = scripts / "demo.shots.json"
    path.write_text(json.dumps(_doc(elements)))
    return path


def test_the_cli_derives_the_profile_from_the_path_and_warns_on_an_unknown_slug(
    tmp_path, monkeypatch, capsys
):
    import json

    from gtm_core.shots_lint.cli import main

    path = _cli_tree(tmp_path, monkeypatch, elements=["ghost"])
    main([str(path), "--no-schema"])
    out = json.loads(capsys.readouterr().out)
    assert [w for w in out["warnings"] if ".elements names 'ghost'" in w], (
        "the CLI did not run the element check — a declared contract nobody runs"
    )


def test_the_cli_is_clean_on_a_slug_the_library_holds(tmp_path, monkeypatch, capsys):
    import json

    from gtm_core.shots_lint.cli import main

    path = _cli_tree(tmp_path, monkeypatch, elements=["brass-dial"])
    main([str(path), "--no-schema"])
    assert not [
        w for w in json.loads(capsys.readouterr().out)["warnings"] if ".elements names" in w
    ]


def test_a_shot_list_outside_the_content_root_leaves_the_element_check_inert(tmp_path, capsys):
    """Fixtures and goldens live outside the root; judging them against a guessed tenant is worse
    than silence."""
    import json

    from gtm_core.shots_lint.cli import main

    path = tmp_path / "loose.shots.json"
    path.write_text(json.dumps(_doc(["anything"])))
    main([str(path), "--no-schema"])
    assert not [
        w for w in json.loads(capsys.readouterr().out)["warnings"] if ".elements names" in w
    ]


def test_a_failed_twin_write_still_reports_the_lint_findings(tmp_path, capsys):
    """Review finding: the error path printed only the confinement error and dropped the
    already-computed findings, so a retry without the flag could read the file as clean."""
    import json

    from gtm_core.shots_lint.cli import main

    path = tmp_path / "loose.shots.json"  # outside the content root -> the twin write is refused
    doc = _doc(["x"])
    doc["shots"][0]["motion_prompt"] = "he"  # a real finding that must not be lost
    path.write_text(json.dumps(doc))
    code = main([str(path), "--no-schema", "--render-shotlist"])
    out = json.loads(capsys.readouterr().out)
    assert code == 2
    assert out["shotlist_error"].startswith("--render-shotlist:")
    assert "warnings" in out and "errors" in out, "the findings were swallowed by the twin error"
