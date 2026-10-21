"""MM13: the 402 the origin raises carries the same payload as the one the edge Worker returns.

A developer whose credits run out is refused by one of two places. The Cloudflare Worker
(``deploy/mcp-gateway/src/index.ts``) answers from its cached entitlement:
``{error: "payment_required", message, upgrade_url}``. The origin (``mcp_server``) answers when the Worker's
cache was stale or absent — and used to say only "please top up your balance", with no way to do it.

``gtm_core/payment_required.py`` is where the origin builds the same payload. This test reads the Worker's
SOURCE (not a copy of it that could go stale) and pins the two together: the same error code, the same two
messages, and each message paired with the same setting for its link (credits → ``BILLING_URL``, no
subscription → ``PRICING_URL``). If someone edits one side, this fails until the other is edited too.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from gtm_core import payment_required as pr

REPO = Path(__file__).resolve().parents[2]

if not (REPO / "deploy" / "mcp-gateway" / "src" / "index.ts").exists():
    # deploy/ is excluded from the OSS carve by design — this pins the origin to the edge Worker's source,
    # which only the private tree has.
    pytest.skip("deploy/ not present (private deployment surface)", allow_module_level=True)

WORKER = (REPO / "deploy" / "mcp-gateway" / "src" / "index.ts").read_text()

#: Every 402 body the Worker builds: ``error: "…", message: "…", upgrade_url: env.<SETTING>``.
_BODY = re.compile(
    r'error:\s*"(?P<error>[^"]+)",\s*message:\s*"(?P<message>[^"]+)",\s*upgrade_url:\s*env\.(?P<env>[A-Z_]+)',
)


def _worker_bodies() -> list[tuple[str, str, str]]:
    return [(m["error"], m["message"], m["env"]) for m in _BODY.finditer(WORKER)]


def test_the_worker_builds_exactly_two_payment_required_bodies():
    """If it grows a third, the origin has to learn it — this is the tripwire."""
    assert len(_worker_bodies()) == 2, _worker_bodies()


def test_the_origin_payload_has_the_workers_error_code_messages_and_link_settings():
    assert sorted(_worker_bodies()) == sorted(
        [
            (pr.ERROR_CODE, pr.MESSAGES["credits"], pr.URL_SETTINGS["credits"]),
            (pr.ERROR_CODE, pr.MESSAGES["subscription"], pr.URL_SETTINGS["subscription"]),
        ]
    )


def test_the_worker_returns_http_402_for_both():
    assert len(re.findall(r"status:\s*402", WORKER)) == 2


def test_the_settings_are_the_workers_own_names():
    assert (pr.URL_SETTINGS["credits"], pr.URL_SETTINGS["subscription"]) == (
        "BILLING_URL",
        "PRICING_URL",
    )
    assert re.search(r"^\s*BILLING_URL\?:\s*string;", WORKER, re.M)
    assert re.search(r"^\s*PRICING_URL\?:\s*string;", WORKER, re.M)


def test_the_payload_has_the_workers_keys_and_omits_the_link_when_there_is_none(monkeypatch):
    """The Worker's ``upgrade_url: env.X`` is dropped from the JSON when X is undefined; so is ours."""
    monkeypatch.setenv("BILLING_URL", "https://billing.example.test/top-up")
    assert set(pr.payload("credits")) == {"error", "message", "upgrade_url"}
    monkeypatch.delenv("BILLING_URL")
    assert set(pr.payload("credits")) == {"error", "message"}
