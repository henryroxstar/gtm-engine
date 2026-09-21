"""D14 — the Fleet Phase A caller-admission route allowlist, enforced as a contract.

Every route in ``backend/routers/`` is on exactly ONE of: ``require_principal``
(admits a user JWT OR a service API key — backend/callers/rest.py), ``require_auth``
(user JWT only — backend/deps.py), ``require_service_auth`` (a service secret, never a
user JWT — the billing-sync/publish-settings routes), or is a named PUBLIC exception
(``backend/routers/auth.py``'s unauthenticated register/login/refresh/exchange). A
route also on ``require_principal`` additionally needs ``Depends(enforce_principal_rate)``
so Task 4's per-service-principal rate limit actually runs.

``decide_gate`` is the one route where two checks compose (G4): it resolves identity
via ``require_principal`` for machine+human alike, then calls ``require_human(...)`` in
its OWN body before any gate-kind branching, refusing every non-user principal. This
test tells that shape apart from the plain "require_principal only" set.

Pure AST, mirroring test_error_code_census.py's walking style. New route in any router
under ``backend/routers/`` that forgets to pick a lane, or picks the wrong one, fails
this test loudly — including a router this file has never heard of (the "every OTHER
router stays require_auth" catch-all below reaches every file in the directory, not
just the ones enumerated here).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ROUTERS_DIR = REPO / "backend" / "routers"

_HTTP_VERBS = frozenset({"get", "post", "put", "delete", "patch"})

#: (filename, function name) -> require_principal, plain (no require_human call).
#: Every one of these ALSO needs Depends(enforce_principal_rate).
_REQUIRE_PRINCIPAL_PLAIN: frozenset[tuple[str, str]] = frozenset(
    {
        ("runs.py", "create_run"),
        ("runs.py", "get_run"),
        ("runs.py", "list_runs"),
        ("runs.py", "cancel_run"),
        ("runs.py", "stream_run"),
        ("runs.py", "list_artifacts"),
        ("runs.py", "download_artifact"),
        ("packs.py", "list_packs"),
        ("packs.py", "variant_readiness_detail"),
        ("events.py", "stream_workspace_events"),
        ("ledger.py", "get_agents_rollup"),
    }
)

#: (filename, function name) -> require_principal for IDENTITY, PLUS an inline
#: require_human(...) call in the body — the gate admits people only (G4). Does NOT
#: get enforce_principal_rate (a service principal never clears require_human anyway,
#: so rate-limiting it is moot — see the task brief).
_REQUIRE_PRINCIPAL_PLUS_HUMAN: frozenset[tuple[str, str]] = frozenset({("runs.py", "decide_gate")})

#: (filename, function name) -> a service-secret-only route (never a user JWT, never a
#: service API key/Principal) — pre-existing, untouched by this task.
_REQUIRE_SERVICE_AUTH: frozenset[tuple[str, str]] = frozenset(
    {
        ("entitlement.py", "sync_entitlement"),
        ("publish_settings.py", "sync_publish_settings"),
    }
)

#: (filename, function name) -> genuinely unauthenticated (register/login/refresh/the
#: external-IdP exchange) — pre-existing, untouched by this task.
_PUBLIC: frozenset[tuple[str, str]] = frozenset(
    {
        ("api_keys.py", "verify_api_key"),
        ("auth.py", "register"),
        ("auth.py", "login"),
        ("auth.py", "refresh"),
        ("auth.py", "exchange"),
        ("webhooks.py", "handle_webhook"),
        ("webhooks.py", "handle_revenuecat_webhook"),
    }
)

_ALL_DEPS = frozenset({"require_principal", "require_auth", "require_service_auth"})


@dataclass(frozen=True)
class RouteInfo:
    file: str
    name: str
    deps: frozenset[str]  # every name reached via a Depends(X) call anywhere in the signature
    calls_require_human: bool


def _depends_names(node: ast.AST) -> frozenset[str]:
    """Every ``Depends(X)``'s ``X`` (a bare name or an attribute's final segment) found
    anywhere under ``node`` — walking the whole function's ``args`` tree covers a
    ``Depends(...)`` inside an ``Annotated[...]`` annotation AND one used as a plain
    default value (``_: None = Depends(x)``)."""
    found: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "Depends":
            if not n.args:
                continue
            target = n.args[0]
            if isinstance(target, ast.Name):
                found.add(target.id)
            elif isinstance(target, ast.Attribute):
                found.add(target.attr)
    return frozenset(found)


def _calls_require_human(body: list[ast.stmt]) -> bool:
    for n in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(n, ast.Call):
            if isinstance(n.func, ast.Name) and n.func.id == "require_human":
                return True
            if isinstance(n.func, ast.Attribute) and n.func.attr == "require_human":
                return True
    return False


def _route_decorator(func: ast.FunctionDef) -> bool:
    for dec in func.decorator_list:
        call = dec if isinstance(dec, ast.Call) else None
        target = call.func if call is not None else dec
        if isinstance(target, ast.Attribute) and target.attr in _HTTP_VERBS:
            if isinstance(target.value, ast.Name) and target.value.id == "router":
                return True
    return False


def routes_in(source: str, filename: str) -> list[RouteInfo]:
    tree = ast.parse(source, filename=filename)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _route_decorator(node):
            continue
        deps = _depends_names(node.args)
        out.append(
            RouteInfo(
                file=filename,
                name=node.name,
                deps=deps,
                calls_require_human=_calls_require_human(node.body),
            )
        )
    return out


def _all_router_routes() -> dict[tuple[str, str], RouteInfo]:
    routes: dict[tuple[str, str], RouteInfo] = {}
    for py in sorted(ROUTERS_DIR.glob("*.py")):
        if py.name == "__init__.py":
            continue
        for r in routes_in(py.read_text(encoding="utf-8"), py.name):
            routes[(r.file, r.name)] = r
    return routes


# ── the allowlist itself ───────────────────────────────────────────────────────


def test_require_principal_plain_routes_match_exactly():
    routes = _all_router_routes()
    for key in _REQUIRE_PRINCIPAL_PLAIN:
        assert key in routes, f"{key} not found as a decorated route — did it move/rename?"
        r = routes[key]
        assert "require_principal" in r.deps, f"{key} must depend on require_principal: {r.deps}"
        assert "enforce_principal_rate" in r.deps, (
            f"{key} is missing Depends(enforce_principal_rate): {r.deps}"
        )
        assert "require_auth" not in r.deps, f"{key} must not ALSO depend on require_auth: {r.deps}"
        assert not r.calls_require_human, (
            f"{key} calls require_human — that's the G4 set, not this one"
        )

    # No OTHER route in these files is on require_principal — the allowlist is exact.
    for key, r in routes.items():
        if key[0] not in ("runs.py", "packs.py", "events.py", "ledger.py"):
            continue
        if "require_principal" in r.deps and not r.calls_require_human:
            assert key in _REQUIRE_PRINCIPAL_PLAIN, (
                f"{key} depends on require_principal but is not in the D14 allowlist — "
                "add it there deliberately, or this is an accidental widening"
            )


def test_decide_gate_is_require_principal_plus_require_human():
    routes = _all_router_routes()
    for key in _REQUIRE_PRINCIPAL_PLUS_HUMAN:
        assert key in routes, key
        r = routes[key]
        assert "require_principal" in r.deps, f"{key}: {r.deps}"
        assert "require_auth" not in r.deps, f"{key}: {r.deps}"
        assert r.calls_require_human, f"{key} must call require_human(...) in its own body"


def test_service_auth_and_public_routes_are_unaffected_by_this_task():
    routes = _all_router_routes()
    for key in _REQUIRE_SERVICE_AUTH:
        assert key in routes, key
        r = routes[key]
        assert r.deps == {"require_service_auth"}, f"{key}: {r.deps}"
        assert not r.calls_require_human, key
    for key in _PUBLIC:
        assert key in routes, key
        r = routes[key]
        assert r.deps == frozenset(), f"{key} must carry none of {_ALL_DEPS}: {r.deps}"


def test_every_other_router_route_stays_on_require_auth_alone():
    """The catch-all: any route not named in one of the sets above — in ANY file under
    backend/routers/, including one this test has never heard of — must be plain
    require_auth. A new route that forgets to pick a lane, or a route silently moved
    onto require_principal without updating the allowlist above, fails HERE."""
    routes = _all_router_routes()
    named = (
        _REQUIRE_PRINCIPAL_PLAIN | _REQUIRE_PRINCIPAL_PLUS_HUMAN | _REQUIRE_SERVICE_AUTH | _PUBLIC
    )
    offenders = []
    for key, r in routes.items():
        if key in named:
            continue
        if r.deps != {"require_auth"} or r.calls_require_human:
            offenders.append((key, r.deps, r.calls_require_human))
    assert offenders == [], offenders


def test_api_keys_routes_stay_require_auth_only():
    """A human mints keys, never a machine — api_keys.py is deliberately absent from
    the require_principal allowlist even though it gained a Fleet-Phase-A `agent_id`
    binding field."""
    routes = _all_router_routes()
    for name in ("create_api_key", "list_api_keys", "revoke_api_key"):
        key = ("api_keys.py", name)
        assert key in routes, key
        assert routes[key].deps == {"require_auth"}, routes[key].deps


# ── the checker discriminates (§R18) ────────────────────────────────────────────


_GOOD_ROUTE = """
from typing import Annotated
from fastapi import APIRouter, Depends
router = APIRouter()

@router.get("/ok")
async def ok_route(
    principal: Annotated[object, Depends(require_principal)],
    _rate: Annotated[None, Depends(enforce_principal_rate)] = None,
) -> dict:
    return {}
"""

_MISSING_RATE_LIMIT = """
from typing import Annotated
from fastapi import APIRouter, Depends
router = APIRouter()

@router.get("/bad")
async def bad_route(
    principal: Annotated[object, Depends(require_principal)],
) -> dict:
    return {}
"""

_WRONG_DEP = """
from typing import Annotated
from fastapi import APIRouter, Depends
router = APIRouter()

@router.get("/bad2")
async def bad_route_2(
    ws: Annotated[object, Depends(require_auth)],
) -> dict:
    return {}
"""

_GATE_WITHOUT_HUMAN_CALL = """
from typing import Annotated
from fastapi import APIRouter, Depends
router = APIRouter()

@router.post("/gate")
async def decide_gate(
    principal: Annotated[object, Depends(require_principal)],
) -> dict:
    return {}
"""

_GATE_WITH_HUMAN_CALL = """
from typing import Annotated
from fastapi import APIRouter, Depends
router = APIRouter()

@router.post("/gate")
async def decide_gate(
    principal: Annotated[object, Depends(require_principal)],
) -> dict:
    require_human(principal)
    return {}
"""

_NOT_A_ROUTE = """
from fastapi import Depends

async def helper(principal=Depends(require_principal)) -> dict:
    return {}
"""


def test_negative_controls_are_each_correctly_classified():
    good = routes_in(_GOOD_ROUTE, "<synthetic>")[0]
    assert good.deps == {"require_principal", "enforce_principal_rate"}
    assert not good.calls_require_human

    missing_rate = routes_in(_MISSING_RATE_LIMIT, "<synthetic>")[0]
    assert missing_rate.deps == {"require_principal"}
    assert "enforce_principal_rate" not in missing_rate.deps

    wrong_dep = routes_in(_WRONG_DEP, "<synthetic>")[0]
    assert wrong_dep.deps == {"require_auth"}
    assert "require_principal" not in wrong_dep.deps

    gate_no_human = routes_in(_GATE_WITHOUT_HUMAN_CALL, "<synthetic>")[0]
    assert gate_no_human.deps == {"require_principal"}
    assert not gate_no_human.calls_require_human, "must be False when require_human is never called"

    gate_with_human = routes_in(_GATE_WITH_HUMAN_CALL, "<synthetic>")[0]
    assert gate_with_human.calls_require_human, "must be True when require_human(...) IS called"

    assert routes_in(_NOT_A_ROUTE, "<synthetic>") == [], (
        "a plain function with no @router.<verb> decorator must not be picked up as a route"
    )


def test_the_walk_reaches_every_router_file():
    """A router file added later is picked up automatically — proves the glob, not a
    hardcoded file list, drives the catch-all test above."""
    files = {p.name for p in ROUTERS_DIR.glob("*.py") if p.name != "__init__.py"}
    assert {"runs.py", "packs.py", "api_keys.py", "agents.py", "auth.py"} <= files
