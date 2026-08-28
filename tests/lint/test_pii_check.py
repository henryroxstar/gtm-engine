"""Guards for the PII checker — the control that keeps real people out of the repo.

A gate nobody tests is a gate that quietly stops working. These pin the two properties
that matter: it catches the leak classes that actually reached the public repo, and it
does not fire on the placeholder data the suite is full of (a noisy gate gets skipped).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("pii_check", ROOT / "tests/lint/pii_check.py")
pii_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pii_check)


def _scan(tmp_path: Path, body: str, name: str = "fixture.py") -> int:
    (tmp_path / name).write_text(body, encoding="utf-8")
    return pii_check.main([str(tmp_path / name)])


# --- the leak SHAPES that actually shipped ------------------------------------------- #
#
# These deliberately use synthetic, unlisted values rather than the real addresses that
# leaked. A test proving "we block real people's data" must not itself carry real people's
# data — that would republish the very thing v0.9.1 removed. The shape is what is under
# test: an unlisted mail domain, a plain E.164 number, a personal LinkedIn slug.


@pytest.mark.parametrize(
    "leak",
    [
        'EMAIL = "j.doe@unlisted-corp.com"',  # shape of the v0.8.0 merge-hygiene fixture
        'ROW = ["Sam", "Ortega", "sam@unlisted-fintech.io"]',  # v0.7.0 prospect fixture
        '"phone": "+81 90-4471-2288"',  # v0.9.1 Apollo company fixture
        'url = "https://linkedin.com/in/unlisted-person"',
    ],
)
def test_real_contact_shape_is_caught(tmp_path, leak):
    assert _scan(tmp_path, leak) == 1


def test_a_real_gmail_is_not_masked_by_the_freemail_fixture(tmp_path):
    """chris@gmail.com is allowlisted as the free-mail RULE INPUT. That pin must be exact —
    allowlisting gmail.com wholesale would hide every real prospect's personal address."""
    assert _scan(tmp_path, 'EMAIL = "chris@gmail.com"') == 0
    assert _scan(tmp_path, 'EMAIL = "someone.unlisted@gmail.com"') == 1


# --- the placeholders the suite legitimately uses ------------------------------------ #


@pytest.mark.parametrize(
    "ok",
    [
        'EMAIL = "someone@acme.example"',  # RFC-2606 reserved TLD
        'EMAIL = "dev@example.com"',  # RFC-2606 reserved domain
        'EMAIL = "pass@n8n.example.com"',  # subdomain of a reserved domain
        'EMAIL = "a@x.com"',  # single-letter placeholder host
        'EMAIL = "chris@cascade.example"',  # declared fictional domain
        'DSN = "https://public@example.ingest.sentry.io/1"',  # vendor host shape
        'url = "https://linkedin.com/in/jane-doe"',  # placeholder person
        'url = "https://linkedin.com/company/acme"',  # org page, not a person
        'PHONE = "+1 555-0142"',  # NANP reserved fictional range
    ],
)
def test_placeholder_data_does_not_fire(tmp_path, ok):
    assert _scan(tmp_path, ok) == 0


@pytest.mark.parametrize(
    "not_a_phone",
    [
        'RUN_ID = "20260614-0001"',
        'UUID = "11111111-1111-4111-8111-111111111111"',
        'SHA = "550e8400-e29b-41d4-a716-446655440000"',
    ],
)
def test_ids_are_not_mistaken_for_phone_numbers(tmp_path, not_a_phone):
    """The first draft matched UUIDs and run ids. A gate that cries wolf gets skipped."""
    assert _scan(tmp_path, not_a_phone) == 0


# --- fixture URL hosts (the 2026-08-11 leak class: a real blog as a parser fixture) --- #
#
# The rule fires only inside tests/ — docs/ and plugin/ cite real vendors and standards
# bodies by design. The scan helper below plants the fixture under a `tests/` dir so the
# path-scoped rule sees it; _scan (above) writes outside one, which doubles as the proof
# that non-test surfaces are exempt.


def _scan_in_tests(tmp_path: Path, body: str) -> int:
    d = tmp_path / "tests"
    d.mkdir()
    (d / "fixture.py").write_text(body, encoding="utf-8")
    return pii_check.main([str(d / "fixture.py")])


def test_a_real_looking_blog_domain_in_a_test_fixture_is_caught(tmp_path):
    """The shape that shipped: a practitioner's real blog (their words attributed to it)
    as sample input. Synthetic unlisted domain here, per the header note."""
    assert _scan_in_tests(tmp_path, 'SAMPLE = "see https://unlisted-blog.cloud/post"') == 1


@pytest.mark.parametrize(
    "ok",
    [
        'URL = "https://agentwatch.example/"',  # RFC-2606 — the required fixture form
        'URL = "https://share.explorium.ai.evil.example/x"',  # suffix-confusion negative test
        'API = "https://api.rocketreach.co/v2"',  # allowlisted vendor API
        'BUCKET = "https://some-bucket.s3.amazonaws.com/f.csv"',  # allowlisted infra suffix
        'RENDERER = "http://fake-renderer"',  # dotless internal stand-in
    ],
)
def test_legitimate_fixture_urls_do_not_fire(tmp_path, ok):
    assert _scan_in_tests(tmp_path, ok) == 0


def test_the_url_rule_is_scoped_to_tests_only(tmp_path):
    """A real domain in a doc/skill file is a citation, not a fixture — out of scope here
    (the identity read still covers it)."""
    assert _scan(tmp_path, 'SRC = "https://unlisted-blog.cloud/post"', name="guide.md") == 0


# --- the invariant itself ------------------------------------------------------------ #


def test_the_repo_source_surface_is_clean():
    """The standing invariant: no real contact details anywhere outside content/ + profiles/."""
    assert pii_check.main([]) == 0


def test_allowlist_is_shared_with_the_export():
    """One rule, two consumers. If the export stops calling this checker and re-implements
    the rule, the two can disagree — which is exactly how SHIPPING_DOCS drifted.

    Private-repo invariant only: the export script is deliberately not carved into the
    public cut, so this suite must still pass there."""
    script = ROOT / "scripts/oss-export.sh"
    if not script.exists():
        pytest.skip("export script is private and not part of the public cut")
    assert "tests/lint/pii_check.py" in script.read_text(encoding="utf-8")
