"""``signature_source``: who closes the email, the body or the mailbox.

The default (``body``) must be byte-identical to the rule as it was; ``mailbox`` inverts the
``sign-off`` rule so an email is not signed twice (body, then the mailbox's own signature).
Fictional data only (§R9).
"""

from __future__ import annotations

import pytest
from outreach import (
    ALL_RULE_IDS,
    RULE_CATALOGUE,
    EmailBlock,
    lint_email,
    lint_merge_render,
    parse_spec,
)

from gtm_core.signature_source import read_signature_source

SIGNOFF = "Dana"
CLOSED = (
    "Hi Sam,\n\n"
    "Agents at Cascade are moving from retrieving data to acting on it, and the audit trail has "
    "not caught up with that shift across most regulated estates this year, which is why the "
    "question of who held the authority comes first.\n\n"
    "Would the one-pager on how another team mapped that be useful?\n\n"
    f"{SIGNOFF}"
)
OPEN = CLOSED.rsplit("\n\n", 1)[0]  # the same email without the closing name


def block(body: str) -> EmailBlock:
    return EmailBlock(
        1, "Sam Ortega · CISO, Cascade", "Sam", "Cascade", "sam@cascade.example", "a note", body
    )


def signoff_hits(body: str, **kw) -> list:
    return [v for v in lint_email(block(body), signoff=SIGNOFF, **kw) if v.rule == "sign-off"]


# ── the default is unchanged ──────────────────────────────────────────────────────────────────


def test_default_is_body_and_demands_the_bare_sign_off():
    assert signoff_hits(CLOSED) == []
    (hit,) = signoff_hits(OPEN)
    assert (
        hit.level == "ERROR" and hit.detail == "last line must be bare 'Dana'"
    )  # the original message


@pytest.mark.parametrize(
    "body", [CLOSED, OPEN, CLOSED + "\nP.S. one more thing", f"Regards,\n{SIGNOFF}"]
)
def test_naming_body_is_byte_identical_to_not_naming_anything(body):
    assert lint_email(block(body), signoff=SIGNOFF, signature_source="body") == lint_email(
        block(body), signoff=SIGNOFF
    )


# ── mailbox inverts it ────────────────────────────────────────────────────────────────────────


def test_mailbox_flags_a_body_that_ends_with_the_sign_off_name():
    (hit,) = signoff_hits(CLOSED, signature_source="mailbox")
    assert hit.level == "ERROR" and "double sign-off" in hit.detail and "'Dana'" in hit.detail


def test_mailbox_flags_a_valediction_block_and_a_bare_valediction():
    for tail in ("Regards,\nDana", "Best,\nDana", "Regards,", "thanks"):
        body = OPEN + "\n\n" + tail
        (hit,) = signoff_hits(body, signature_source="mailbox")
        assert "double sign-off" in hit.detail, tail


def test_mailbox_is_clean_for_a_body_that_ends_on_its_last_sentence():
    assert signoff_hits(OPEN, signature_source="mailbox") == []


def test_mailbox_does_not_flag_a_closing_question_that_merely_contains_a_valediction_word():
    body = OPEN.rsplit("\n\n", 1)[0] + "\n\nCould you thank the team for the intro?"
    assert signoff_hits(body, signature_source="mailbox") == []


def test_mailbox_changes_only_the_sign_off_rule():
    """Everything else the linter says about the body is identical in both modes."""

    def rest(src):
        return [
            v
            for v in lint_email(block(OPEN), signoff=SIGNOFF, signature_source=src)
            if v.rule != "sign-off"
        ]

    assert rest("body") == rest("mailbox")


def test_an_unknown_value_is_refused_not_read_as_the_default():
    with pytest.raises(ValueError, match="signature_source"):
        lint_email(block(CLOSED), signoff=SIGNOFF, signature_source="Mailbox")


# ── the rule id and catalogue ─────────────────────────────────────────────────────────────────


def test_the_rule_id_is_unchanged_and_its_description_names_both_readings():
    assert "sign-off" in RULE_CATALOGUE and "signature-source" not in ALL_RULE_IDS
    description = RULE_CATALOGUE["sign-off"][1]
    assert "missing or not the expected name" in description  # the original reading survives
    assert "signature_source: mailbox" in description and "double sign-off" in description


# ── the profile drives it ─────────────────────────────────────────────────────────────────────

SPEC = f"""
**Step 1 — Day 1** · Subject: `identity in production`
> Hi {{{{First Name}}}},
>
> Agents at {{{{Company}}}} are moving from retrieving data to acting on it, and the audit trail
> has not caught up with that shift across most regulated estates this year.
>
> Would the one-pager on how another team mapped that be useful?
>
> {SIGNOFF}
"""
ROW = {
    "first": "Sam",
    "last": "Ortega",
    "email": "sam@cascade.example",
    "company": "Cascade",
    "company_domain": "cascade.example",
    "title": "CISO",
    "signal_clause": "Cascade sits on the BRIGHTPATH agent-runtime-security working group",
}


def render_hits(profile=None, **kw) -> list:
    violations, _ = lint_merge_render(
        parse_spec(SPEC), [ROW], signoff=SIGNOFF, profile=profile, **kw
    )
    return [v for v in violations if v.rule == "sign-off"]


@pytest.fixture
def profiles(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path))

    def write(text: str | None, name: str = "democo"):
        (tmp_path / name).mkdir(exist_ok=True)
        if text is not None:
            (tmp_path / name / "PROFILE.md").write_text(text)

    return write


def test_a_profile_that_does_not_say_is_body(profiles):
    assert read_signature_source("democo") == "body"  # no folder at all
    profiles(None)
    assert read_signature_source("democo") == "body"  # a folder, no PROFILE.md
    profiles("name: Dana\nemail_signature: Dana\n")
    assert read_signature_source("democo") == "body"  # a PROFILE.md that does not set it
    assert render_hits("democo") == []  # so the closed spec passes exactly as before


def test_a_mailbox_profile_inverts_the_rule_through_lint_merge_render(profiles):
    profiles("name: Dana\nsignature_source: mailbox   # the sequencer appends the signature\n")
    assert read_signature_source("democo") == "mailbox"
    (hit,) = render_hits("democo")
    assert "double sign-off" in hit.detail
    # An explicit argument beats the profile; no profile means body.
    assert render_hits("democo", signature_source="body") == []
    assert render_hits(None) == []


@pytest.mark.parametrize("written", ["mailbox", "Mailbox", '"mailbox"', "'mailbox'", "MAILBOX   "])
def test_the_value_is_read_forgivingly_but_only_two_values_exist(profiles, written):
    profiles(f"signature_source: {written}\n")
    assert read_signature_source("democo") == "mailbox"


def test_an_unknown_profile_value_is_refused(profiles):
    profiles("signature_source: mailbxo\n")
    with pytest.raises(ValueError, match="not one of body | mailbox"):
        read_signature_source("democo")
    with pytest.raises(ValueError):
        render_hits("democo")


def test_only_an_assignment_line_counts(profiles):
    profiles("# signature_source: mailbox\nThe signature_source: mailbox setting is optional.\n")
    assert read_signature_source("democo") == "body"


def test_the_first_assignment_wins(profiles):
    profiles("signature_source: body\nsignature_source: mailbox\n")
    assert read_signature_source("democo") == "body"


def test_an_unsafe_profile_name_is_refused(profiles):
    with pytest.raises(ValueError):
        read_signature_source("../democo")


def test_the_template_documents_the_setting_without_turning_it_on():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / "profiles/_template/PROFILE.md").read_text()
    lines = [ln for ln in text.splitlines() if "signature_source" in ln]
    assert lines and all(ln.startswith("# ") for ln in lines)  # commented out: inert until set
    assert "body" in lines[0] and "mailbox" in lines[0]
