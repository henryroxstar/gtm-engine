"""A tenant that never configured a disclosure line must still fail closed.

EU AI Act Article 50 (applicable 2026-08-02) is the reason, and `CLAUDE.md` states the
property in one sentence: *"a tenant that never configured one has not opted out of the duty,
it fails closed."* `agent/publish.py:validate_disclosure` implements it as two different
refusals, and the difference between them is the whole point:

* **no line configured at all** → *"no disclosure line is configured (BRAND.toml
  [disclosure].line)"* — an instruction to go and write one;
* **a line configured but absent from the post** → *"the post does not carry a configured
  disclosure line"* — an instruction to paste the line you already wrote.

`profiles/_template/knowledge/BRAND.toml` ships `line = "<your disclosure line>"`, a
placeholder. It is a non-empty string, so it satisfies `validate_disclosure`'s truthiness
check. A tenant that inherited it would move from the **first** refusal to the **second**
without anyone deciding to: the operator would be told to paste a line, the obvious thing to
paste is the placeholder, and a synthetic-media post would then ship carrying the literal text
`<your disclosure line>` as its Article 50 disclosure.

That is exactly what widening `_supplement_from_template`'s suffix filter to `.toml` did on
2026-09-24 — the change was aimed at the fact registry's three tables and pulled `BRAND.toml`
along with them, because the filter is by suffix and a brand kit is also TOML. Before it, a
fresh tenant had **no** `knowledge/BRAND.toml` at all (nothing under `agent/onboard/` renders
one), so the hard branch fired.

The exclusion is therefore not a style preference. This test is the negative control that
would have caught the widening, and it is written to fail on the mechanism (the file is
carried) rather than on the symptom (a post validates), because the symptom is three modules
away from the change that causes it.
"""

from __future__ import annotations

from pathlib import Path

from agent.onboard.knowledge import _supplement_from_template
from agent.publish import validate_disclosure

_TEMPLATE = Path(__file__).resolve().parents[2] / "profiles" / "_template" / "knowledge"


def test_the_template_ships_a_truthy_placeholder_disclosure():
    """The premise. If this ever becomes empty, the test below stops meaning anything."""
    text = (_TEMPLATE / "BRAND.toml").read_text(encoding="utf-8")
    assert 'line            = "<your disclosure line>"' in text or "<your disclosure line>" in text


def test_a_placeholder_line_would_satisfy_the_gate():
    """Why the placeholder must not be inherited, stated as behaviour rather than assertion.

    This is the mechanism the exclusion protects against, proven directly against
    `validate_disclosure` — not inferred from reading it.
    """
    placeholder = "<your disclosure line>"
    post = f"Some copy about the thing. {placeholder}"
    assert validate_disclosure(post, "soul", [placeholder]) is None


def test_no_configured_line_is_the_hard_refusal():
    """The branch the exclusion keeps reachable, and its wording."""
    problem = validate_disclosure("Some copy about the thing.", "soul", [])
    assert problem is not None
    assert "no disclosure line is configured" in problem


def test_brand_toml_is_not_inherited_by_an_onboarded_tenant():
    """The guard itself.

    A tenant with no `knowledge/BRAND.toml` has no configured disclosure line, so
    `validate_disclosure` returns the hard refusal and the duty cannot be discharged by
    accident.
    """
    files: dict[str, str] = {}
    _supplement_from_template(files, _TEMPLATE)
    assert files, "_supplement_from_template carried nothing — this assertion would be vacuous"
    assert "knowledge/BRAND.toml" not in files, (
        "the template's BRAND.toml was carried to a new tenant; its placeholder "
        "[disclosure].line is truthy, which downgrades validate_disclosure's "
        "'no disclosure line is configured' refusal to 'the post does not carry' — "
        "EU AI Act Art. 50 must fail closed for a tenant that configured nothing"
    )


def test_the_registry_tables_are_still_inherited():
    """The negative control for the exclusion.

    Excluding one filename is a blunt instrument; this pins that it stayed one filename wide
    and did not take the fact registry out with it.
    """
    files: dict[str, str] = {}
    _supplement_from_template(files, _TEMPLATE)
    for name in ("claims.toml", "proof.toml", "angles.toml", "role-vocabulary.toml"):
        assert f"knowledge/{name}" in files
