"""MM1 — provider unit prices live in one committed registry, and an UNPRICED provider refuses.

Before this, ``ROCKETREACH_USD_PER_LOOKUP`` / ``APOLLO_USD_PER_CREDIT`` defaulted to ``0`` and
nothing in ``deploy/`` set them, so every lookup contributed ``$0.00`` to every cap. The rules
pinned here:

* the price comes from ``gtm_core/provider_costs.toml`` (never from env), and is exact;
* an absent / zero / negative / malformed price raises instead of billing ``0``;
* a worker on the HOUSE key (or the legacy single-tenant VPS, where the variable is unset)
  refuses an unpriced call BEFORE any HTTP and records the refusal in ``denials.jsonl``;
* a worker on the tenant's OWN key (``byok``) records the unit count at ``cost_usd = 0`` and
  needs no price — a wallet is never billed for lookups the tenant already pays the vendor for.

Network-free: the HTTP transport is a recorder; keys are fake; people are fictional.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import subprocess
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mcp", reason="mcp (FastMCP) not installed")
pytest.importorskip("httpx", reason="httpx not installed")

from agent.mcp.apollo import server as apollo  # noqa: E402
from agent.mcp.rocketreach import server as rocketreach  # noqa: E402
from gtm_core import provider_registry as reg  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMITTED = REPO_ROOT / "gtm_core" / "provider_costs.toml"

GOOD = """
[provider.rocketreach]
class = "data"
unit = "lookup"
usd_per_unit = 0.5
status = "estimate"
source = "test fixture"
"""


def _committed_raw() -> dict:
    return tomllib.loads(COMMITTED.read_text(encoding="utf-8"))["provider"]


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "provider_costs.toml"
    path.write_text(text, encoding="utf-8")
    return path


# ── (a) a priced provider bills the registry value, exactly ───────────────────────────────────


@pytest.mark.parametrize(
    ("provider", "unit"), [("rocketreach", "lookup"), ("apollo", "credit")], ids=str
)
def test_unit_price_equals_the_committed_registry_value_exactly(provider, unit):
    raw = _committed_raw()[provider]["usd_per_unit"]
    price = reg.unit_price(provider, unit)
    assert price == float(raw)
    assert price > 0, "a $0 price is the hazard this registry exists to remove"


def test_committed_registry_declares_both_providers_and_a_class_for_each():
    committed = _committed_raw()
    assert set(committed) == {"rocketreach", "apollo"}
    assert committed["rocketreach"]["unit"] == "lookup"
    assert committed["apollo"]["unit"] == "credit"
    for name, entry in committed.items():
        assert entry["status"] in ("estimate", "reconciled"), name
        assert str(entry["source"]).strip(), f"{name}: an unsourced price is a guess with a number"
        # `class` is a REQUIRED field of the committed file even though the loader defaults an
        # absent one to identity: the schema MM5 builds on must not depend on that default.
        assert "class" in entry, f"{name}: the committed registry must declare its class"
        assert entry["class"] == "data", name


# ── (b) absent / zero / negative / malformed refuses ──────────────────────────────────────────

# (label, replacement of the `usd_per_unit = 0.5` line). Each is a registry a TOML author or a
# bad merge could actually produce; none may be read as a price.
BAD_PRICES = [
    ("zero-int", "usd_per_unit = 0"),
    ("zero-float", "usd_per_unit = 0.0"),
    ("negative", "usd_per_unit = -0.5"),
    ("string", 'usd_per_unit = "0.5"'),
    ("garbage-string", 'usd_per_unit = "wharrgarbl"'),
    ("empty-string", 'usd_per_unit = ""'),
    ("bool", "usd_per_unit = true"),
    ("nan", "usd_per_unit = nan"),
    ("inf", "usd_per_unit = inf"),
    ("list", "usd_per_unit = [0.5]"),
    ("missing", ""),
]


@pytest.mark.parametrize(("label", "line"), BAD_PRICES, ids=[b[0] for b in BAD_PRICES])
def test_a_bad_price_never_loads_and_never_prices(tmp_path, label, line):
    path = _write(tmp_path, GOOD.replace("usd_per_unit = 0.5", line))
    with pytest.raises(reg.ProviderRegistryError):
        reg.load_provider_costs(path)
    with pytest.raises(reg.UnpricedProviderError):
        reg.unit_price("rocketreach", "lookup", registry_path=path)


BAD_STRUCTURE = [
    ("missing-unit", GOOD.replace('unit = "lookup"\n', "")),
    ("empty-unit", GOOD.replace('unit = "lookup"', 'unit = ""')),
    ("unknown-key", GOOD + "surprise = 1\n"),
    ("unknown-class", GOOD.replace('class = "data"', 'class = "pooled"')),
    ("unknown-status", GOOD.replace('"estimate"', '"guess"')),
    ("missing-status", GOOD.replace('status = "estimate"\n', "")),
    ("missing-source", GOOD.replace('source = "test fixture"\n', "")),
    ("blank-source", GOOD.replace('source = "test fixture"', 'source = "  "')),
    ("unknown-table", GOOD + '\n[surprise.rocketreach]\nunit = "x"\n'),
    ("provider-not-a-table", 'provider = "rocketreach"\n'),
    ("entry-not-a-table", "[provider]\nrocketreach = 0.5\n"),
    ("unparseable", "[provider.rocketreach"),
]


@pytest.mark.parametrize(("label", "text"), BAD_STRUCTURE, ids=[b[0] for b in BAD_STRUCTURE])
def test_a_malformed_registry_refuses_rather_than_pricing(tmp_path, label, text):
    path = _write(tmp_path, text)
    with pytest.raises(reg.ProviderRegistryError):
        reg.load_provider_costs(path)
    with pytest.raises(reg.UnpricedProviderError):
        reg.unit_price("rocketreach", "lookup", registry_path=path)


def test_a_missing_registry_file_refuses(tmp_path):
    with pytest.raises(reg.UnpricedProviderError):
        reg.unit_price("rocketreach", "lookup", registry_path=tmp_path / "nope.toml")


def test_a_provider_absent_from_the_registry_is_unpriced(tmp_path):
    path = _write(tmp_path, GOOD)
    with pytest.raises(reg.UnpricedProviderError, match="apollo"):
        reg.unit_price("apollo", "credit", registry_path=path)


def test_a_price_is_per_the_declared_unit_only(tmp_path):
    """A lookup price must not be read as a per-credit price: that is a unit-confusion bug that
    would bill every Apollo credit at the RocketReach rate."""
    path = _write(tmp_path, GOOD)
    with pytest.raises(reg.UnpricedProviderError, match="credit"):
        reg.unit_price("rocketreach", "credit", registry_path=path)


def test_the_instrument_check_a_well_formed_registry_does_price(tmp_path):
    """Every refusal above would pass vacuously if the loader refused everything."""
    path = _write(tmp_path, GOOD)
    assert reg.unit_price("rocketreach", "lookup", registry_path=path) == 0.5
    assert reg.load_provider_costs(path)["rocketreach"].provider_class == "data"


def test_an_absent_class_defaults_to_identity(tmp_path):
    """Identity is the BYOK-mandatory class (a shared sending identity shares a reputation), so
    an entry nobody classified must land on the restrictive side."""
    path = _write(tmp_path, GOOD.replace('class = "data"\n', ""))
    assert reg.load_provider_costs(path)["rocketreach"].provider_class == "identity"


def test_the_registry_module_has_no_network_import():
    """Workers import it and it sits under the §R6 egress scan: only the standard library and a
    sibling gtm_core module (the ledger) may be imported — and of the standard library, nothing
    that opens a socket, so no HTTP client can arrive here by the back door."""
    tree = ast.parse(Path(reg.__file__).read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    allowed = set(sys.stdlib_module_names) | {"gtm_core"}
    network = {"urllib", "http", "socket", "ssl", "ftplib", "smtplib", "imaplib", "poplib"}
    assert roots <= allowed, sorted(roots - allowed)
    assert not roots & network, sorted(roots & network)


# ── key source: the only granting value is the exact string "byok" ────────────────────────────


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_an_unset_key_source_is_the_legacy_priced_path(raw):
    assert reg.resolve_key_source(raw) is None


@pytest.mark.parametrize("raw", ["house", "byok"])
def test_the_two_key_sources_resolve_to_themselves(raw):
    assert reg.resolve_key_source(raw) == raw


@pytest.mark.parametrize("raw", ["wharrgarbl", "BYOK", " byok", "byok ", "true", "1", "tenant"])
def test_any_other_key_source_is_refused_never_read_as_byok(raw):
    with pytest.raises(reg.KeySourceError):
        reg.resolve_key_source(raw)


# ── the two workers, driven the same way ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Worker:
    id: str
    mod: Any
    provider: str
    unit: str
    units_key: str
    api_key_env: str
    price_env: str
    source_env: str
    meter_extra: tuple  # extra positional args `_meter` takes after the unit count
    billable: Callable[[], str]  # one billable tool call, end to end
    ok_payload: dict  # what the provider answers a billable call with


RR_PAYLOAD = {
    "id": 7,
    "status": "complete",
    "name": "Jane Roe",
    "emails": [{"email": "jane.roe@example.invalid", "type": "professional", "grade": "A"}],
}
AP_PAYLOAD = {
    "person": {
        "id": "p1",
        "name": "Jane Roe",
        "email": "jane.roe@example.invalid",
        "email_status": "verified",
    }
}

_CAP_ENV = {
    "rocketreach": "ROCKETREACH_MONTHLY_LOOKUP_CAP",
    "apollo": "APOLLO_MONTHLY_CREDIT_CAP",
}

WORKERS = [
    _Worker(
        id="rocketreach",
        mod=rocketreach,
        provider="rocketreach",
        unit="lookup",
        units_key="lookups",
        api_key_env="ROCKETREACH_API_KEY",
        price_env="ROCKETREACH_USD_PER_LOOKUP",
        source_env="ROCKETREACH_KEY_SOURCE",
        meter_extra=(),
        billable=lambda: asyncio.run(
            rocketreach._lookup({"name": "Jane Roe", "current_employer": "Example Co"})
        ),
        ok_payload=RR_PAYLOAD,
    ),
    _Worker(
        id="apollo",
        mod=apollo,
        provider="apollo",
        unit="credit",
        units_key="credits",
        api_key_env="APOLLO_API_KEY",
        price_env="APOLLO_USD_PER_CREDIT",
        source_env="APOLLO_KEY_SOURCE",
        meter_extra=("person_enrich",),
        billable=lambda: asyncio.run(
            apollo.apollo_person_enrich(name="Jane Roe", organization_name="Example Co")
        ),
        ok_payload=AP_PAYLOAD,
    ),
]


@pytest.fixture(params=WORKERS, ids=lambda w: w.id)
def worker(request) -> _Worker:
    return request.param


class _Ledger:
    def __init__(self, base: Path) -> None:
        self.base = base

    def _rows(self, name: str) -> list[dict]:
        path = self.base / name
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def costs(self) -> list[dict]:
        return self._rows("costs.jsonl")

    def denials(self) -> list[dict]:
        return self._rows("denials.jsonl")


@pytest.fixture
def ledger(tmp_path, monkeypatch) -> _Ledger:
    """A per-test content tree the worker's own ``Config.from_env()`` resolves to."""
    root = tmp_path / "content"
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(root))
    monkeypatch.setenv("GTM_PROFILE", "example")
    for var in (
        "ROCKETREACH_KEY_SOURCE",
        "APOLLO_KEY_SOURCE",
        "ROCKETREACH_USD_PER_LOOKUP",
        "APOLLO_USD_PER_CREDIT",
        "ROCKETREACH_MONTHLY_LOOKUP_CAP",
        "APOLLO_MONTHLY_CREDIT_CAP",
    ):
        monkeypatch.delenv(var, raising=False)
    return _Ledger(root / "example")


class _Resp:
    status_code = 200
    headers: dict = {}

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _Client:
    """Answers every request with one payload and records each one."""

    def __init__(self, calls: list, payload: dict) -> None:
        self._calls = calls
        self._payload = payload

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_exc) -> bool:
        return False

    async def request(self, method, url, **kw):
        self._calls.append((method, url))
        return _Resp(self._payload)

    async def get(self, url, **kw):
        self._calls.append(("GET", url))
        return _Resp(self._payload)

    async def post(self, url, **kw):
        self._calls.append(("POST", url))
        return _Resp(self._payload)


def _transport(monkeypatch, payload: dict) -> tuple[list, list]:
    """Replace httpx.AsyncClient; returns (constructed, requests) so a test can assert BOTH are
    empty — building a client is already an attempt to leave the process."""
    constructed: list = []
    calls: list = []

    def _factory(*a, **k):
        constructed.append((a, k))
        return _Client(calls, payload)

    monkeypatch.setattr(rocketreach.httpx, "AsyncClient", _factory)  # one shared httpx module
    return constructed, calls


def _use_registry(monkeypatch, tmp_path: Path, text: str) -> None:
    monkeypatch.setattr(reg, "_DEFAULT_REGISTRY", _write(tmp_path, text))


UNPRICED = GOOD.replace("[provider.rocketreach]", "[provider.some_other_vendor]")


# (c) the env overrides are gone ----------------------------------------------------------------


def test_the_env_price_overrides_have_no_effect(worker, tmp_path):
    """A SUBPROCESS, because that is how a stdio worker really gets its env: set at spawn, read
    at import. Re-reading the override anywhere (import time or call time) would bill $0 here."""
    root = tmp_path / "content"
    code = f"import {worker.mod.__name__} as m; m._meter(3, *{worker.meter_extra!r})"
    env = {
        **os.environ,
        worker.price_env: "0",
        "GTM_CONTENT_ROOT": str(root),
        "GTM_PROFILE": "example",
    }
    env.pop(worker.source_env, None)
    done = subprocess.run(  # noqa: S603 — fixed argv, no shell
        [sys.executable, "-c", code], env=env, cwd=REPO_ROOT, capture_output=True, text=True
    )
    assert done.returncode == 0, done.stderr
    rows = _Ledger(root / "example").costs()
    assert len(rows) == 1
    assert rows[0]["cost_usd"] == round(3 * reg.unit_price(worker.provider, worker.unit), 6)
    assert rows[0]["cost_usd"] > 0


# (d) a priced house call bills registry price x units, exactly ----------------------------------


@pytest.mark.parametrize("source", [None, "house"], ids=["legacy-vps-unset", "house"])
def test_meter_on_the_house_key_bills_the_registry_price_exactly(
    worker, ledger, monkeypatch, source
):
    if source:
        monkeypatch.setenv(worker.source_env, source)
    worker.mod._meter(3, *worker.meter_extra)
    (row,) = ledger.costs()
    price = reg.unit_price(worker.provider, worker.unit)
    assert row["cost_usd"] == round(3 * price, 6)
    assert row["cost_usd"] > 0
    assert row["units"] == {worker.units_key: 3}


# (e) unpriced refuses before any HTTP and says so in denials.jsonl ------------------------------


@pytest.mark.parametrize("source", [None, "house"], ids=["legacy-vps-unset", "house"])
def test_an_unpriced_provider_refuses_before_any_http_and_records_the_refusal(
    worker, ledger, monkeypatch, tmp_path, source
):
    monkeypatch.setenv(worker.api_key_env, "fake-key")
    if source:
        monkeypatch.setenv(worker.source_env, source)
    _use_registry(monkeypatch, tmp_path, UNPRICED)
    constructed, calls = _transport(monkeypatch, worker.ok_payload)

    out = worker.billable()

    assert out.startswith(f"[{worker.provider}-error]")
    assert f"provider_unpriced: {worker.provider}" in out
    assert constructed == [] and calls == [], "the transport must not be touched"
    assert ledger.costs() == [], "a refused call spent nothing and must record no spend"
    (denial,) = ledger.denials()
    assert denial["decision"] == "deny"
    assert denial["outcome"] == "denied"
    assert denial["tool"].startswith(f"mcp__{worker.provider}__")
    assert denial["detail"] == f"provider_unpriced: {worker.provider}"


def test_a_priced_call_still_proceeds_and_bills(worker, ledger, monkeypatch):
    """The other half of (e): refusing everything would pass the refusal test too."""
    monkeypatch.setenv(worker.api_key_env, "fake-key")
    monkeypatch.setenv(worker.source_env, "house")
    # a pooled tenant also needs its allocated share (MM4): without one it is refused, by design
    monkeypatch.setenv(_CAP_ENV[worker.provider], "1000")
    constructed, calls = _transport(monkeypatch, worker.ok_payload)

    out = worker.billable()

    assert not out.startswith(f"[{worker.provider}-error]"), out
    assert calls, "a priced provider must reach the transport"
    (row,) = ledger.costs()
    assert row["cost_usd"] == round(1 * reg.unit_price(worker.provider, worker.unit), 6)
    assert ledger.denials() == []


@pytest.mark.parametrize("raw", ["wharrgarbl", "BYOK", "true"])
def test_an_unrecognised_key_source_refuses_before_any_http(worker, ledger, monkeypatch, raw):
    """Only the exact string "byok" waives the price. A typo must not become a free lookup."""
    monkeypatch.setenv(worker.api_key_env, "fake-key")
    monkeypatch.setenv(worker.source_env, raw)
    constructed, calls = _transport(monkeypatch, worker.ok_payload)

    out = worker.billable()

    assert f"provider_key_source_invalid: {worker.provider}" in out
    assert constructed == [] and calls == []
    assert ledger.costs() == []
    (denial,) = ledger.denials()
    assert denial["detail"] == f"provider_key_source_invalid: {worker.provider}"


# (f) the tenant's own key is never billed to a wallet -------------------------------------------


def test_byok_records_the_units_at_zero_dollars_and_needs_no_price(
    worker, ledger, monkeypatch, tmp_path
):
    monkeypatch.setenv(worker.source_env, "byok")
    _use_registry(monkeypatch, tmp_path, UNPRICED)  # not even priced: BYOK must not care

    worker.mod._meter(3, *worker.meter_extra)

    (row,) = ledger.costs()
    assert row["units"] == {worker.units_key: 3}, "the unit COUNT is still recorded"
    assert row["cost_usd"] == 0.0, "a BYOK tenant must never be billed for its own vendor account"


def test_byok_is_zero_dollars_even_when_the_provider_is_priced(worker, ledger, monkeypatch):
    """Priced registry + byok: the price exists and must STILL not be charged."""
    assert reg.unit_price(worker.provider, worker.unit) > 0
    monkeypatch.setenv(worker.source_env, "byok")
    worker.mod._meter(3, *worker.meter_extra)
    (row,) = ledger.costs()
    assert row["cost_usd"] == 0.0


def test_byok_makes_its_billable_call_with_no_price_in_the_registry(
    worker, ledger, monkeypatch, tmp_path
):
    monkeypatch.setenv(worker.api_key_env, "fake-key")
    monkeypatch.setenv(worker.source_env, "byok")
    _use_registry(monkeypatch, tmp_path, UNPRICED)
    constructed, calls = _transport(monkeypatch, worker.ok_payload)

    out = worker.billable()

    assert not out.startswith(f"[{worker.provider}-error]"), out
    assert calls, "BYOK is not blocked by an unpriced registry"
    assert ledger.denials() == []
    (row,) = ledger.costs()
    assert row["cost_usd"] == 0.0
    assert row["units"] == {worker.units_key: 1}
