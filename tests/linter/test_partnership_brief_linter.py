"""Regression net for the partnership-brief linter.

Every `test_catches_*` below is a verbatim mistake that reached a partner-facing draft. If one of
these stops failing the lint, the linter has regressed to decoration.

The `test_accepts_*` cases matter just as much: a linter that cries wolf gets ignored, and the
first cut of this one produced 29 errors on a document that had already been verified line by
line. Precision is the feature.
"""

from __future__ import annotations

from tests.linter.partnership_brief_linter import lint_html, lint_markdown

SECTIONS = "\n".join(
    f"## {h}\n\nbody text.\n"
    for h in (
        "Executive summary",
        "1. The opportunity",
        "5. What each side brings",
        "6. Competitive landscape",
        "7. Why now",
        "9. Commercial shape",
        "10. Risks and open questions",
        "Sources",
    )
)


def _rules(md: str) -> set[str]:
    return {f.rule for f in lint_markdown(md)}


def _errors(md: str) -> set[str]:
    return {f.rule for f in lint_markdown(md) if f.level == "ERROR"}


# ── the mistakes that shipped ────────────────────────────────────────────────


def test_catches_standards_body_borrowing():
    """`did:tdw` is a DIF specification. Attaching W3C's name to it borrows authority from a
    body that has not standardised it — the error a spec-literate reviewer notices first."""
    assert "standards-attribution" in _errors("A verifiable W3C DID (did:tdw) per agent.")


def test_catches_wrong_body_for_openid_specs():
    assert "standards-attribution" in _errors("Conformance to the W3C OID4VCI profile.")


def test_catches_unsourced_statistic():
    """~65% of agent failures traced back to a vendor asserting it with no study behind it."""
    md = SECTIONS + "\n## Claim\n\nRoughly 65% of enterprise agent failures trace to drift.\n"
    assert "unsourced-statistic" in _errors(md)


def test_catches_absolute_claim_about_competitors():
    """'None of them publish a provenance model' was false — one competitor had a blog post
    titled almost exactly that."""
    md = SECTIONS + "\n## Landscape\n\nNone of them publish a provenance or trust model.\n"
    assert "absolute-claim" in _errors(md)


def test_catches_framework_enumeration():
    """ASI06 lists nine differently-named guidelines, not five 'defence layers'."""
    assert "framework-enumeration" in _rules("ASI06 prescribes five defence layers.")


def test_catches_internal_voice():
    md = "We tried to source it and found a vendor's uncited assertion."
    assert "internal-voice" in _errors(md)


def test_catches_internal_path_leak():
    assert "internal-leak" in _errors("See profiles/acme/knowledge/product.md for detail.")


def test_catches_untagged_capability_row():
    md = "| # | Integration point | What it does | Status |\n|---|---|---|---|\n| 1 | Identity | issues a DID | |\n"
    assert "untagged-capability" in _errors(md)


def test_catches_placeholder_link():
    assert "placeholder-link" in _errors("See [the docs](#) for detail.")


def test_catches_missing_required_section():
    assert "missing-section" in _errors("## Executive summary\n\nhi\n")


def test_catches_thin_opportunity():
    """A market section that asserts a market without naming who buys it."""
    md = SECTIONS + "\n## 1. The opportunity\n\nThe category is growing quickly and matters.\n"
    assert "thin-opportunity" in _rules(md)


def test_catches_missing_glossary():
    md = SECTIONS + (
        "\n## Tech\n\nThe DID and verifiable credential ride over MCP with a hash chain "
        "evaluated by OPA and bitemporal attestation.\n"
    )
    assert "missing-glossary" in _rules(md)


def test_catches_mermaid_without_accessible_title():
    assert "diagram-a11y" in _rules(SECTIONS + "\n```mermaid\nflowchart LR\n A --> B\n```\n")


# ── precision: these must NOT fire ───────────────────────────────────────────


def test_accepts_correct_registry_attribution():
    """`did:tdw` IS registered in a W3C registry — saying so is accurate, not borrowing."""
    md = "The did:tdw method is registered in the W3C DID method registry, but is not itself a W3C Recommendation."
    assert "standards-attribution" not in _errors(md)


def test_accepts_correct_owner_attribution():
    assert "standards-attribution" not in _errors("The W3C Verifiable Credential data model.")


def test_ordinary_never_is_not_an_absolute_claim():
    """'never on the hot path' is prose. Firing on it buries the real findings."""
    md = SECTIONS + "\n## Design\n\nThe network is never needed for a read or a write.\n"
    assert "absolute-claim" not in _errors(md)


def test_scoped_absolute_is_accepted():
    md = SECTIONS + "\n## Landscape\n\nOf the vendors reviewed, none of them ships signing.\n"
    assert "absolute-claim" not in _errors(md)


def test_cited_statistic_is_accepted():
    md = (
        SECTIONS
        + "\n## Why now\n\nGartner expects [over 40%](https://example.com/x) to be cancelled.\n"
    )
    assert "unsourced-statistic" not in _errors(md)


def test_statistic_elsewhere_in_a_cited_section_is_accepted():
    """Tables summarise sources cited in the same section; demanding a link per line forces
    citation spam and trains people to ignore the rule."""
    md = SECTIONS + (
        "\n## Landscape\n\nSee the [benchmark](https://example.com/b).\n\n"
        "| Vendor | Score |\n|---|---|\n| Acme | 74.0% |\n"
    )
    assert "unsourced-statistic" not in _errors(md)


def test_third_party_version_is_advisory_not_error():
    """A competitor's published version is a legitimate fact; only our own unreleased build is a
    leak, and code cannot tell them apart — so it warns rather than blocks."""
    findings = lint_markdown(SECTIONS + "\n## X\n\nLangMem is pre-1.0 (v0.0.30).\n")
    assert not [f for f in findings if f.rule == "version-string" and f.level == "ERROR"]
    assert "version-string" in {f.rule for f in findings}


def test_sources_section_statistics_are_not_flagged():
    md = SECTIONS.replace(
        "## Sources\n\nbody text.",
        "## Sources\n\nMINJA reports 98.2% injection success (arXiv 2503.03704).",
    )
    assert "unsourced-statistic" not in _errors(md)


# ── rendered-HTML structural accessibility ───────────────────────────────────


def test_html_catches_heading_skip():
    assert "heading-skip" in {f.rule for f in lint_html("<h2>a</h2><h5>b</h5>")}


def test_html_catches_missing_th_scope():
    assert "th-scope" in {f.rule for f in lint_html("<table><th>a</th></table>")}


def test_html_catches_img_without_alt():
    assert "img-alt" in {f.rule for f in lint_html('<img src="x.png">')}


def test_html_accepts_scoped_headers_and_alt_text():
    html = '<h2>a</h2><h3>b</h3><table><th scope="col">a</th></table><img src="x.png" alt="y">'
    assert not [f for f in lint_html(html) if f.level == "ERROR"]
