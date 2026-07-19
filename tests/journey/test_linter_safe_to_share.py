# tests/journey/test_linter_safe_to_share.py
"""Tests for lint_safe_to_share — the journey-pipeline safe-to-publish gate.

Denylist-dependent cases use a SYNTHETIC denylist injected via
GTM_SAFE_SHARE_DENYLIST_FILE, so this test carries no real tenant tokens and
passes identically in the private repo and the public cut (where the real
denylist file is excluded). The secret-pattern cases are denylist-independent.
"""

import importlib

import pytest

import tests.linter.content_linter as cl

_SYNTHETIC_DENYLIST = """\
token: acmecorp
token: widgetco
host: internal\\.example\\.test
"""


@pytest.fixture(autouse=True)
def synthetic_denylist(tmp_path, monkeypatch):
    """Point the linter at a fake denylist and reload it, so no real tenant token
    is needed. Restore + reload afterward so other test modules see the default."""
    f = tmp_path / "denylist.txt"
    f.write_text(_SYNTHETIC_DENYLIST, encoding="utf-8")
    monkeypatch.setenv("GTM_SAFE_SHARE_DENYLIST_FILE", str(f))
    importlib.reload(cl)
    yield
    monkeypatch.delenv("GTM_SAFE_SHARE_DENYLIST_FILE", raising=False)
    importlib.reload(cl)


# ── tenant token blocking (synthetic) ─────────────────────────────────────────


def test_blocks_tenant_name():
    v = cl.lint_safe_to_share("We used Acmecorp's platform in our pipeline.")
    errors = [x for x in v if x.severity == "error"]
    assert any("acmecorp" in x.message.lower() for x in errors)


def test_blocks_second_tenant_token():
    v = cl.lint_safe_to_share("The demo used WidgetCo for document parsing.")
    errors = [x for x in v if x.severity == "error"]
    assert any("widgetco" in x.message.lower() for x in errors)


def test_blocks_tenant_name_case_insensitive():
    v = cl.lint_safe_to_share("ACMECORP was the first customer.")
    assert any(x.severity == "error" for x in v)


def test_allows_clean_neutral_text():
    text = "I built a content engine that reads its own git log to narrate the build."
    assert cl.passes_safe_to_share(text)


# ── internal hostname blocking (synthetic) ────────────────────────────────────


def test_blocks_internal_hostname():
    v = cl.lint_safe_to_share("Point the webhook at https://api.internal.example.test/v1/hook.")
    assert any(x.severity == "error" and x.rule == "safe.hostname" for x in v)


def test_allows_placeholder_host():
    # A generic placeholder host must NOT trip the internal-hostname rule.
    assert cl.passes_safe_to_share("Point the webhook at https://n8n.example.com/webhook/publish.")


# ── credential / secret pattern blocking (denylist-independent) ───────────────


def test_blocks_token_pattern():
    v = cl.lint_safe_to_share("Set BACKEND_JWT_SECRET before deploying.")
    assert any(x.severity == "error" and x.rule == "safe.credential" for x in v)


def test_blocks_api_key_pattern():
    v = cl.lint_safe_to_share("The FIRECRAWL_API_KEY goes in your .env file.")
    assert any(x.severity == "error" for x in v)


def test_blocks_dotenv_reference():
    v = cl.lint_safe_to_share("Copy the values from .env.example into your local .env.")
    assert any(x.severity == "error" for x in v)


def test_allows_text_mentioning_env_vars_abstractly():
    text = "The app reads its config from environment variables, not hardcoded values."
    assert cl.passes_safe_to_share(text)


# ── passes_safe_to_share helper ───────────────────────────────────────────────


def test_passes_safe_to_share_true_for_clean():
    assert cl.passes_safe_to_share("We shipped the two-gate approval model on day 2.") is True


def test_passes_safe_to_share_false_for_tenant():
    assert cl.passes_safe_to_share("acmecorp was the pilot customer") is False


def test_returns_list_of_violations():
    v = cl.lint_safe_to_share("acmecorp and BACKEND_TOKEN both appear here.")
    assert isinstance(v, list)
    assert all(isinstance(x, cl.Violation) for x in v)
    assert len(v) >= 2
