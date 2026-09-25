"""Lint gate keeping founder-facing documentation honest and non-drifting.

Ensures that:
- PROSPECTING.md's status table contains every label in gtm_core.prospect_status.LABELS
- docs/onboarding/SALES-FAQ.md links to the status table and does not restate the labels
"""

from __future__ import annotations

import re
from pathlib import Path

from gtm_core.prospect_status import LABELS

REPO = Path(__file__).resolve().parent.parent.parent
PROSPECTING_MD = REPO / "PROSPECTING.md"
SALES_FAQ_MD = REPO / "docs" / "onboarding" / "SALES-FAQ.md"


def test_prospecting_status_table_lists_every_label() -> None:
    text = PROSPECTING_MD.read_text(encoding="utf-8")
    status_header = re.search(r"### The status word[^\n]*\n", text)
    assert status_header, "PROSPECTING.md missing '### The status word' section"
    section = text[status_header.end() :]
    next_header = re.search(r"\n###? ", section)
    table_text = section[: next_header.start()] if next_header else section

    missing: list[str] = []
    for key, label in LABELS.items():
        if f"**{label}**" not in table_text:
            missing.append(f"{key}: **{label}**")
    assert not missing, (
        "PROSPECTING.md status table missing labels from gtm_core.prospect_status.LABELS:\n  "
        + "\n  ".join(missing)
    )


def test_sales_faq_links_to_status_table_and_does_not_restate_labels() -> None:
    text = SALES_FAQ_MD.read_text(encoding="utf-8")
    assert "What do the status words mean?" in text, (
        "docs/onboarding/SALES-FAQ.md must have a question 'What do the status words mean?'"
    )
    assert (
        "PROSPECTING.md#the-status-word--one-word-six-values-always-derived" in text
        or "PROSPECTING.md" in text
    ), "docs/onboarding/SALES-FAQ.md must link to the status table in PROSPECTING.md"

    restate: list[str] = []
    for label in LABELS.values():
        if f"**{label}**" in text:
            restate.append(f"**{label}**")
    assert not restate, (
        "docs/onboarding/SALES-FAQ.md must not restate status labels; it must link to PROSPECTING.md. Found:\n  "
        + "\n  ".join(restate)
    )


def test_one_gateless_action_named_in_guide_and_faq() -> None:
    try:
        import agent.optout_auto_add  # noqa: F401
    except ImportError:
        import pytest

        pytest.skip("agent.optout_auto_add does not exist")

    expected_sentence = (
        "The one thing it does without asking is honour an unsubscribe: "
        "when someone replies 'stop', they go on your do-not-contact list."
    )
    guide_text = (REPO / "END-USER-ONBOARDING.md").read_text(encoding="utf-8")
    faq_text = SALES_FAQ_MD.read_text(encoding="utf-8")
    assert expected_sentence in guide_text, (
        f"END-USER-ONBOARDING.md must contain the gateless action sentence: {expected_sentence!r}"
    )
    assert expected_sentence in faq_text, (
        f"SALES-FAQ.md must contain the gateless action sentence: {expected_sentence!r}"
    )


def test_safety_guarantee_phrasing_in_guide_and_readme() -> None:
    readme_text = (REPO / "README.md").read_text(encoding="utf-8")
    guide_text = (REPO / "END-USER-ONBOARDING.md").read_text(encoding="utf-8")

    assert "no raw HTTP or shell access" not in readme_text, (
        "README.md must not contain 'no raw HTTP or shell access' (superseded by per-path guarantee)"
    )
    assert "no raw HTTP or shell access" not in guide_text, (
        "END-USER-ONBOARDING.md must not contain 'no raw HTTP or shell access'"
    )

    assert "asks you first" in readme_text, "README.md must contain 'asks you first'"
    assert "asks you first" in guide_text, "END-USER-ONBOARDING.md must contain 'asks you first'"


def test_fear_words_absent_from_guide() -> None:
    guide_text = (REPO / "END-USER-ONBOARDING.md").read_text(encoding="utf-8")
    for phrase in ("already cost", "fake `python`", "silently"):
        assert phrase not in guide_text, f"Fear phrase {phrase!r} found in END-USER-ONBOARDING.md"
    assert not re.search(r"\btrap\b", guide_text, re.IGNORECASE), (
        "Fear word 'trap' found in END-USER-ONBOARDING.md"
    )


def test_no_ask_your_admin_in_founder_docs() -> None:
    guide_text = (REPO / "END-USER-ONBOARDING.md").read_text(encoding="utf-8")
    faq_text = SALES_FAQ_MD.read_text(encoding="utf-8")
    assert "your admin" not in guide_text.lower(), "Found 'your admin' in END-USER-ONBOARDING.md"
    assert "your admin" not in faq_text.lower(), "Found 'your admin' in SALES-FAQ.md"


def test_status_table_matches_the_code() -> None:
    doc = (REPO / "END-USER-ONBOARDING.md").read_text(encoding="utf-8")
    for label in LABELS.values():
        assert f"**{label}**" in doc, f"guide is missing the live label {label!r}"
    assert "**Ready to send**" not in doc
