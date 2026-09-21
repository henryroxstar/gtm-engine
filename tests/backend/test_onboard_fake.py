"""Scripted onboarding extraction behind ``GTM_FAKE_RUNS`` (dev-only).

A local stack has no model key, so ``POST /v1/onboard`` could not reach a draft there. Under
the existing dev-only flag the brain call is swapped for a deterministic script; everything
around it (ingest, slugify, render, stage, the persisted draft, promote) stays real.

The properties pinned here: the brain is never reached with the flag on in development, and
always reached otherwise (the flag alone is not enough — ``ENV`` must be ``development``);
the same holds for a URL source's crawl, which is scripted too; the scripted draft goes
through the real validator and is visibly marked; the scripted delay keeps a job observably
in flight; and the module imports nothing that can spend, egress, or publish.
"""

from __future__ import annotations

import time

import pytest

from agent.onboard import OnboardingExtractError, OnboardingInputError, render
from backend.services import onboard_fake
from tests.backend import test_onboard_http as _http
from tests.backend.test_fake_runs import (
    _FORBIDDEN_MODULES,
    REPO,
    _forbidden_uses,
    _scan_names,
)

# The HTTP harness (workspace-scoped cfg, fake DB scope, auth override) is test_onboard_http's.
# Bound as module attributes rather than imported by name: a fixture imported by name and then
# requested as a parameter is an F811 shadow (see tests/backend/conftest.py).
acting = _http.acting
cfg = _http.cfg
cfg_b = _http.cfg_b
client = _http.client
db_scope = _http.db_scope
settle = _http.settle
_EXTRACT = _http._EXTRACT
_REPO_ROOT = _http._REPO_ROOT

_TEXT = "Harrowgate Tidewater Supply\nWe stock marine hardware.\nFamily-run since 1994."
_SLUG = "harrowgate-tidewater-supply"


@pytest.fixture(autouse=True)
def _no_flag_from_the_shell(monkeypatch):
    monkeypatch.delenv("GTM_FAKE_RUNS", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv(onboard_fake.DELAY_ENV, raising=False)


@pytest.fixture
def fake_env(monkeypatch):
    monkeypatch.setenv("GTM_FAKE_RUNS", "1")
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv(onboard_fake.DELAY_ENV, "0")


@pytest.fixture
def brain_calls(monkeypatch):
    """The brain, stubbed to record the call and fail as an unusable draft does (502)."""
    calls: list[str] = []

    async def _brain(prompt, cfg):
        calls.append(prompt)
        raise OnboardingExtractError("brain reached")

    monkeypatch.setattr(_EXTRACT, "_run_brain_query", _brain)
    return calls


def _ingest(client, text: str = _TEXT, source_type: str = "text"):
    return settle(
        client, client.post("/onboard", json={"source_type": source_type, "source": text})
    )


def _re_extract(client, draft_id: str, product: str, text: str):
    return settle(
        client,
        client.post(
            f"/onboard/{draft_id}/product/{product}/extract",
            json={"source_type": "text", "source": text},
        ),
    )


# ── the flag on, in development: scripted ────────────────────────────────────


def test_ingest_is_scripted_and_never_reaches_the_brain(client, fake_env, brain_calls, caplog):
    with caplog.at_level("WARNING"):
        res = _ingest(client)

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["slug"] == _SLUG
    assert onboard_fake.SCRIPTED_GAP in body["gaps"]
    assert brain_calls == []
    assert any("scripted" in r.getMessage().lower() for r in caplog.records)


def test_a_scripted_draft_survives_a_restart_and_promotes(client, cfg, fake_env, brain_calls):
    from backend.routers import onboard

    draft_id = _ingest(client).json()["draft_id"]
    onboard._drafts.clear()

    assert client.get(f"/onboard/{draft_id}/diff").status_code == 200
    res = client.post(
        f"/onboard/{draft_id}/promote",
        json={"confirmed_company_name": "Harrowgate Tidewater Supply"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["slug"] == _SLUG
    assert (cfg.profiles_root / _SLUG / "PROFILE.md").is_file()
    assert brain_calls == []


def test_re_extract_is_scripted(client, fake_env, brain_calls):
    draft_id = _ingest(client).json()["draft_id"]
    product = onboard_fake.FAKE_PRODUCT_SLUG

    res = _re_extract(client, draft_id, product, "It now ships a tide-table widget.")

    assert res.status_code == 200, res.text
    assert res.json()["slug"] == _SLUG
    assert res.json()["draft_id"] != draft_id
    assert brain_calls == []


def test_re_extract_of_an_unknown_product_is_the_callers_error(client, fake_env, brain_calls):
    draft_id = _ingest(client).json()["draft_id"]

    res = _re_extract(client, draft_id, "no-such-product", "More text.")

    assert res.status_code == 422
    assert res.json()["error"]["code"] == "onboarding_input_invalid"
    assert brain_calls == []


def test_a_url_source_is_scripted_with_no_key_and_no_crawl(client, cfg, fake_env, brain_calls):
    """The client fixture makes any httpx.Client an AssertionError, and cfg has no key."""
    assert cfg.firecrawl_api_key is None

    res = _ingest(client, "https://www.harrowgate-supply.example/about", source_type="url")

    assert res.status_code == 200, res.text
    assert res.json()["slug"] == "harrowgate-supply"
    assert onboard_fake.SCRIPTED_GAP in res.json()["gaps"]
    assert brain_calls == []


def test_a_url_with_no_host_is_the_callers_error(client, fake_env, brain_calls):
    res = _ingest(client, "https://", source_type="url")

    assert res.status_code == 422
    assert res.json()["error"]["code"] == "onboarding_input_invalid"


def test_the_scripted_delay_keeps_the_job_in_flight(client, fake_env, monkeypatch):
    monkeypatch.setenv(onboard_fake.DELAY_ENV, "0.5")

    started = time.monotonic()
    submitted = client.post("/onboard", json={"source_type": "text", "source": _TEXT})
    assert submitted.status_code == 202
    assert submitted.json()["status"] == "pending"
    polled = client.get(f"/onboard/jobs/{submitted.json()['job_id']}").json()
    assert polled["status"] in ("pending", "running")

    res = settle(client, submitted)
    assert res.status_code == 200, res.text
    assert time.monotonic() - started >= 0.5


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, onboard_fake.DEFAULT_DELAY_S),
        ("", onboard_fake.DEFAULT_DELAY_S),
        ("2.5", 2.5),
        ("-3", 0.0),
        ("soon", onboard_fake.DEFAULT_DELAY_S),
    ],
)
def test_the_delay_setting_parses(monkeypatch, raw, expected):
    if raw is not None:
        monkeypatch.setenv(onboard_fake.DELAY_ENV, raw)
    assert onboard_fake.fake_delay_s() == expected


@pytest.mark.parametrize(
    ("url", "name"),
    [
        ("www.quillfield.example", "Quillfield"),
        ("https://www.quillfield-orchards.example/about?x=1", "Quillfield Orchards"),
        ("http://someone@shop.quillfield.example:8080/", "Shop"),
        ("QUILLFIELD.example", "Quillfield"),
    ],
)
def test_the_scripted_page_names_the_hosts_company(url, name):
    text = onboard_fake.fake_page_text(url)
    assert text.splitlines()[0] == name
    assert onboard_fake.fake_extract(text)["company"]["name"] == name


@pytest.mark.parametrize("url", ["", "   ", "https://", "https:///path", "www.", "https://-/"])
def test_a_url_without_a_usable_host_is_refused(url):
    with pytest.raises(OnboardingInputError):
        onboard_fake.fake_page_text(url)


def test_the_company_name_is_the_first_non_blank_line_stripped_and_capped():
    draft = onboard_fake.fake_extract("\n   \n  Quillfield Orchards  \nabout us\n")
    assert draft["company"]["name"] == "Quillfield Orchards"

    long = onboard_fake.fake_extract("Z" * 500)
    assert len(long["company"]["name"]) == onboard_fake.MAX_NAME_LEN


@pytest.mark.parametrize("text", ["", "   \n\t\n", "!!! ---\nsecond line"])
def test_a_text_with_no_usable_first_line_is_the_callers_error(text):
    with pytest.raises(OnboardingInputError):
        onboard_fake.fake_extract(text)


def test_the_scripted_draft_is_the_real_validators_shape_and_renders(tmp_path):
    draft = onboard_fake.fake_extract(_TEXT)
    assert draft["confidence"] in {"high", "medium", "low"}
    files = render(
        draft, template_knowledge_dir=_REPO_ROOT / "profiles" / "_template" / "knowledge"
    )
    assert "PROFILE.md" in files
    assert "Harrowgate Tidewater Supply" in files["PROFILE.md"]


def test_the_scripted_draft_goes_through_the_real_validator(monkeypatch):
    seen: list[str] = []
    real = onboard_fake._parse_and_validate_draft

    def _spy(raw):
        seen.append(raw)
        return real(raw)

    monkeypatch.setattr(onboard_fake, "_parse_and_validate_draft", _spy)
    draft = onboard_fake.fake_extract(_TEXT)
    onboard_fake.fake_extract_product(onboard_fake.FAKE_PRODUCT_SLUG, "more", draft)
    assert len(seen) == 2


# ── anywhere else: the real brain ────────────────────────────────────────────


@pytest.mark.parametrize("env", ["staging", "production", "test", None])
def test_the_flag_outside_development_still_reaches_the_brain(
    client, monkeypatch, brain_calls, env
):
    monkeypatch.setenv("GTM_FAKE_RUNS", "1")
    if env is not None:
        monkeypatch.setenv("ENV", env)

    res = _ingest(client)

    assert res.status_code == 502
    assert res.json()["error"]["code"] == "onboarding_extract_failed"
    assert len(brain_calls) == 1


@pytest.mark.parametrize("env", ["staging", "production", None])
def test_the_flag_outside_development_still_crawls_a_url(client, monkeypatch, brain_calls, env):
    """Real ingest runs: with no FIRECRAWL_API_KEY configured, that is the 503."""
    monkeypatch.setenv("GTM_FAKE_RUNS", "1")
    if env is not None:
        monkeypatch.setenv("ENV", env)

    res = _ingest(client, "https://www.harrowgate-supply.example", source_type="url")

    assert res.status_code == 503
    assert res.json()["error"]["code"] == "url_ingest_unavailable"
    assert brain_calls == []


def test_flag_off_reaches_the_brain(client, monkeypatch, brain_calls):
    monkeypatch.setenv("ENV", "development")

    res = _ingest(client)

    assert res.status_code == 502
    assert len(brain_calls) == 1


def test_flag_off_re_extract_reaches_the_brain(client, monkeypatch, brain_calls):
    monkeypatch.setenv("GTM_FAKE_RUNS", "1")
    monkeypatch.setenv("ENV", "development")
    draft_id = _ingest(client).json()["draft_id"]
    monkeypatch.delenv("GTM_FAKE_RUNS")

    res = _re_extract(client, draft_id, onboard_fake.FAKE_PRODUCT_SLUG, "More text.")

    assert res.status_code == 502
    assert len(brain_calls) == 1


# ── structure ────────────────────────────────────────────────────────────────

_ONBOARD_FAKE_FORBIDDEN_MODULES = (*_FORBIDDEN_MODULES, "agent.session", "gtm_core.ingest")
_ONBOARD_FAKE_FORBIDDEN_NAMES = frozenset(
    {"_run_brain_query", "extract", "extract_product", "ingest", "_ingest_url"}
)


def _onboard_fake_uses(source: str) -> set[str]:
    return _forbidden_uses(
        source,
        _scan_names() | _ONBOARD_FAKE_FORBIDDEN_NAMES,
        package="backend.services",
        modules=_ONBOARD_FAKE_FORBIDDEN_MODULES,
    )


def test_the_onboard_fake_scan_discriminates():
    """Negative control on synthetic source: the scan flags each thing it exists to catch."""
    synthetic = (
        "import claude_agent_sdk\n"
        "from agent.session import build_agent_options\n"
        "from agent.onboard.extract import _run_brain_query\n"
        "from agent.onboard import extract\n"
        "from gtm_core.ingest import _onboarding_month_spend\n"
        "import httpx\n"
    )
    assert _onboard_fake_uses(synthetic) == {
        "claude_agent_sdk",
        "agent.session.build_agent_options",
        "agent.onboard.extract._run_brain_query",
        "agent.onboard.extract",
        "gtm_core.ingest._onboarding_month_spend",
        "httpx",
    }


def test_onboard_fake_imports_nothing_that_can_spend_egress_or_publish():
    source = (REPO / "backend" / "services" / "onboard_fake.py").read_text(encoding="utf-8")
    assert _onboard_fake_uses(source) == set()
