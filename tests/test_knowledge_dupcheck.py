"""Unit tests for the advisory duplicate-fact report (gtm_core.knowledge_dupcheck).

House self-test pattern (mirrors tests/contracts/test_layering.py): prove the checker actually
fires on a synthetic copy-paste, AND prove it does NOT fire on the real false-positive shape found
during exploration — the same term ("Sentinel Gate") re-worded across files for legitimately
different purposes (product-home definition vs. standards-crosswalk subject vs. persona anchor vs.
a deliberate `X ≠ competitor-X` collision guardrail). A keyword-presence check would flag all of
those; the block-similarity check here must not.
"""

from __future__ import annotations

from gtm_core.knowledge_dupcheck import find_candidates, render


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


_LONG_PARAGRAPH = (
    "Acme Sentinel Gate is the flagship product that lets enterprises govern which agents can "
    "call which tools, enforcing policy at the identity layer rather than trusting a bearer token "
    "that any process on the host could replay against a downstream API without detection at all."
)


def test_exact_copy_paste_across_two_files_is_flagged(tmp_path):
    profiles_root = tmp_path / "profiles"
    _write(profiles_root / "acme" / "knowledge" / "company.md", _LONG_PARAGRAPH)
    _write(profiles_root / "acme" / "knowledge" / "hook-matrix.md", _LONG_PARAGRAPH)

    pairs = find_candidates(profiles_root, "acme")
    assert len(pairs) == 1
    assert pairs[0].ratio >= 0.85
    relpaths = {pairs[0].a.relpath, pairs[0].b.relpath}
    assert relpaths == {"knowledge/company.md", "knowledge/hook-matrix.md"}


def test_repeat_within_the_same_file_is_not_flagged(tmp_path):
    profiles_root = tmp_path / "profiles"
    _write(
        profiles_root / "acme" / "knowledge" / "company.md",
        _LONG_PARAGRAPH + "\n\n" + _LONG_PARAGRAPH,
    )
    assert find_candidates(profiles_root, "acme") == []


def test_short_blocks_are_ignored(tmp_path):
    """A short repeated line (a header, a single bullet) is not copy-paste drift."""
    profiles_root = tmp_path / "profiles"
    _write(profiles_root / "acme" / "knowledge" / "a.md", "- ISO 27001 certified")
    _write(profiles_root / "acme" / "knowledge" / "b.md", "- ISO 27001 certified")
    assert find_candidates(profiles_root, "acme") == []


def test_legitimate_re_mentions_of_the_same_term_are_not_flagged(tmp_path):
    """The real case exploration surfaced: "Sentinel Gate" appears in >20 files doing four
    different jobs. None of these re-worded, purpose-specific mentions should cross the
    near-duplicate threshold even though they share the product name."""
    profiles_root = tmp_path / "profiles"
    kdir = profiles_root / "acme" / "knowledge"

    _write(
        kdir / "company.md",
        "Acme's flagship product is Sentinel Gate, formerly known internally as SG. It lets "
        "a platform team govern which autonomous agents may call which downstream tools at all.",
    )
    _write(
        kdir / "guidance" / "nist-gateway-alignment.md",
        "This crosswalk maps Acme Sentinel Gate controls against the NIST AI risk management "
        "framework for a CISO reviewing whether the deployed agent fleet satisfies each control.",
    )
    _write(
        kdir / "icp-personas.md",
        "When talking to a platform engineering buyer, lead the conversation with Sentinel Gate "
        "before introducing Sentinel Watch, since governance is what unblocks their rollout budget.",
    )
    _write(
        kdir / "industry" / "regtech-insurance.md",
        "Careful: an unrelated open-source project ships under a similarly spelled name, which is "
        "not the same product as Acme's Sentinel Gate and must never be conflated in a deck.",
    )

    pairs = find_candidates(profiles_root, "acme")
    assert pairs == [], f"expected no false-positive dup flags, got: {pairs}"


def test_quoted_verbatim_span_is_not_compared(tmp_path):
    """content_linter.py exempts double-quoted verbatim spans from ban-list rules for the same
    reason: quoting a source exactly is not drift, even if two files cite the same quote."""
    profiles_root = tmp_path / "profiles"
    quote = '"' + _LONG_PARAGRAPH + '"'
    _write(profiles_root / "acme" / "knowledge" / "a.md", f"The source states: {quote}")
    _write(profiles_root / "acme" / "knowledge" / "b.md", f"As cited elsewhere: {quote}")
    assert find_candidates(profiles_root, "acme") == []


def test_allowlist_suppresses_a_flagged_pair(tmp_path):
    profiles_root = tmp_path / "profiles"
    _write(profiles_root / "acme" / "knowledge" / "company.md", _LONG_PARAGRAPH)
    _write(profiles_root / "acme" / "knowledge" / "hook-matrix.md", _LONG_PARAGRAPH)

    allowlist = tmp_path / "allow.txt"
    allowlist.write_text("Acme Sentinel Gate is the flagship product\n", encoding="utf-8")

    assert find_candidates(profiles_root, "acme", allowlist_path=allowlist) == []


def test_render_is_advisory_and_never_a_gate_by_construction():
    """render() only ever produces markdown text — there is no exit-code / pass-fail path here at
    all, which is the structural guarantee that this check can never become blocking."""
    md = render([], profile="acme")
    assert "ADVISORY" in md
    assert "None found" in md
