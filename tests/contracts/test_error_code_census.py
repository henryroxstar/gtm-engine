"""Every 401, 402, 409 and 429 raised in ``backend/`` names its own, known machine code.

A client switches on ``error.code``. A plain-string ``HTTPException`` detail gets a code from
``backend/errors.py``'s message heuristic instead, which collapses distinct outcomes — an
expired token and a forged one were both ``unauthorized``, every 429 was ``rate_limit_exceeded``,
the gate route's three 409s were all ``conflict``. These are the statuses where the client's
reaction depends on WHICH refusal it got (refresh vs sign out, back off vs close a stream,
re-render vs stop), so each site must carry a dict detail with a string-literal ``"code"`` that
is snake_case, not overridable by a ``**`` spread, and in the golden set below — a typo'd code
is as invisible to a client's switch as a missing one.

Pure AST over every ``.py`` under ``backend/``. The exception is recognised under any name it
is bound to: an aliased import, or a class in ``backend/`` that subclasses it. A site whose
status cannot be resolved statically fails rather than being skipped — a guard that ignores
what it cannot read is not a guard (§R18). The negative controls below prove the checker flags
each bad shape.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"

_EXCEPTION = "HTTPException"
_CENSUSED_STATUSES = frozenset({401, 402, 409, 429})
_STATUS_NAME = re.compile(r"^HTTP_(\d{3})(?:_[A-Z0-9_]+)?$")
_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")

#: Every code a client may meet on a censused status. Adding one is a client-contract change:
#: document it in the client API integration guide in the same change.
_KNOWN_CODES = frozenset(
    {
        # 401
        "federated_token_invalid",
        "invalid_credentials",
        "invalid_key",
        "invalid_signature",
        "refresh_token_expired",
        "service_auth_invalid",
        "token_expired",
        "token_invalid",
        "token_missing",
        "token_revoked",
        # 402
        "cost_cap_reached",
        # 409
        "agent_archived",
        "agent_name_taken",
        "agent_paused",
        "already_exists",
        "api_key_already_revoked",
        "cap_exceeds_plan",
        "content_sha_mismatch",
        "draft_not_staged",
        "gate_already_decided",
        "gate_not_open",
        "no_password_credential",
        "profile_already_exists",
        "run_already_terminal",
        # 429
        "too_many_concurrent_runs",
        "too_many_streams",
    }
)

#: (path relative to the repo, status) -> sites still carrying a string detail. Empty is the goal.
#: backend/callers/dependency.py: `refusal_to_http` is a generic Refusal -> HTTPException adapter
#: (backend/callers/ports.py's `Refusal` carries its own status/code, set at each raise site as a
#: literal — e.g. `Refusal(404, "run_not_found", ...)` — and always renders a {"code": ...} dict,
#: never a bare string). The adapter forwards `exc.status_code`/`exc.code` at runtime by design, so
#: it can serve every future Refusal site without editing this function again; that's exactly what
#: the AST census can't resolve statically. Every Refusal call site is still a literal int + a
#: snake_case code, so the client-switches-on-error.code invariant this test protects holds — it's
#: only the census's static reach that stops at the adapter boundary.
_ALLOWLIST: dict[tuple[str, int | None], int] = {("backend/callers/dependency.py", None): 1}


@dataclass(frozen=True)
class Site:
    path: str
    line: int
    status: int | None
    code: str | None
    problem: str | None


def _base_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def subclass_names(trees: Iterable[ast.Module]) -> frozenset[str]:
    """Class names that subclass ``HTTPException`` transitively, across all ``trees``."""
    classes = [n for tree in trees for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    found: set[str] = set()
    grew = True
    while grew:
        grew = False
        for cls in classes:
            if cls.name in found:
                continue
            if any(_base_name(b) in found | {_EXCEPTION} for b in cls.bases):
                found.add(cls.name)
                grew = True
    return frozenset(found)


def _exception_names(tree: ast.Module, subclasses: frozenset[str]) -> frozenset[str]:
    """Every name that refers to the exception in this module: the bare name, any alias it is
    imported under, and any subclass (defined here, or elsewhere in backend/ and imported)."""
    names = {_EXCEPTION} | set(subclasses)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name.rsplit(".", 1)[-1] in names:
                    names.add(alias.asname or alias.name)
    return frozenset(names | subclass_names([tree]))


def _arg(call: ast.Call, position: int, keyword: str) -> ast.expr | None:
    if len(call.args) > position:
        return call.args[position]
    return next((kw.value for kw in call.keywords if kw.arg == keyword), None)


def _status(node: ast.expr | None) -> int | None:
    if isinstance(node, ast.Constant) and type(node.value) is int:
        return node.value
    match = _STATUS_NAME.match((_base_name(node) if node is not None else None) or "")
    return int(match.group(1)) if match else None


def _code_problem(detail: ast.expr | None, known: frozenset[str]) -> tuple[str | None, str | None]:
    if not isinstance(detail, ast.Dict):
        return None, 'detail is not a dict literal with a string-literal "code"'
    if any(key is None for key in detail.keys):
        return None, 'detail dict has a ** spread that can override "code" at runtime'
    code_node = next(
        (v for k, v in zip(detail.keys, detail.values, strict=True) if _is_str(k, "code")), None
    )
    if not (isinstance(code_node, ast.Constant) and isinstance(code_node.value, str)):
        return None, 'detail is not a dict literal with a string-literal "code"'
    code = code_node.value
    if not _SNAKE_CASE.match(code):
        return code, f"code {code!r} is not snake_case"
    if code not in known:
        return code, f"code {code!r} is not in _KNOWN_CODES (typo, or document and add it)"
    return code, None


def _is_str(node: ast.expr | None, value: str) -> bool:
    return isinstance(node, ast.Constant) and node.value == value


def census(
    source: str,
    path: str,
    *,
    subclasses: frozenset[str] = frozenset(),
    known: frozenset[str] = _KNOWN_CODES,
) -> list[Site]:
    """Every exception call in ``source`` that is in scope or unreadable."""
    tree = ast.parse(source, filename=path)
    names = _exception_names(tree, subclasses)
    sites: list[Site] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _base_name(node.func) in names):
            continue
        status = _status(_arg(node, 0, "status_code"))
        if status is None:
            sites.append(Site(path, node.lineno, None, None, "status not statically resolvable"))
            continue
        if status not in _CENSUSED_STATUSES:
            continue
        code, problem = _code_problem(_arg(node, 1, "detail"), known)
        sites.append(Site(path, node.lineno, status, code, problem))
    return sites


def _backend_sites() -> list[Site]:
    files = sorted(BACKEND.rglob("*.py"))
    sources = {py: py.read_text(encoding="utf-8") for py in files}
    subclasses = subclass_names(ast.parse(src) for src in sources.values())
    return [
        site
        for py, src in sources.items()
        for site in census(src, str(py.relative_to(REPO)), subclasses=subclasses)
    ]


def _violations_by_key(sites: list[Site]) -> dict[tuple[str, int | None], list[Site]]:
    grouped: dict[tuple[str, int | None], list[Site]] = {}
    for site in sites:
        if site.problem:
            grouped.setdefault((site.path, site.status), []).append(site)
    return grouped


def test_every_censused_status_in_backend_carries_a_known_literal_code():
    grouped = _violations_by_key(_backend_sites())
    offenders = [
        f"{s.path}:{s.line} ({s.status}): {s.problem}"
        for key, sites in sorted(grouped.items(), key=str)
        if len(sites) > _ALLOWLIST.get(key, 0)
        for s in sites
    ]
    assert offenders == [], (
        "Raise these with HTTPException(status, {'code': 'snake_case_code', 'message': ...}) — "
        "the client switches on error.code:\n" + "\n".join(offenders)
    )


def test_the_allowlist_is_a_ceiling_not_a_promise():
    grouped = _violations_by_key(_backend_sites())
    stale = {key: n for key, n in _ALLOWLIST.items() if len(grouped.get(key, [])) < n}
    assert stale == {}, (
        f"allowlist entries with fewer violations than recorded — shrink them: {stale}"
    )


def test_every_golden_code_is_still_raised():
    """The golden set is a ceiling too: a code no site raises any more is dead contract, and a
    stale entry is exactly what a later typo could collide with."""
    raised = {site.code for site in _backend_sites()}
    assert sorted(_KNOWN_CODES - raised) == []


def test_the_census_reaches_the_backend():
    codes = {site.code for site in _backend_sites()}
    for known in ("token_expired", "too_many_streams", "gate_already_decided", "agent_name_taken"):
        assert known in codes, f"census never saw {known!r} — is it walking backend/?"


# ── the checker discriminates (§R18) ──────────────────────────────────────────


def _problems(source: str, **kwargs) -> list[str | None]:
    return [site.problem for site in census(source, "<synthetic>", **kwargs)]


def test_negative_controls_are_each_flagged():
    bad = {
        "plain-string 409": 'raise HTTPException(409, "Run is already terminal")',
        "429 attr + f-string": (
            "raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f'Too many runs (max {n})')"
        ),
        "detail= kwarg string": 'raise HTTPException(status_code=401, detail="Missing token")',
        "dict without code": (
            'raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, {"message": "upgrade"})'
        ),
        "no detail at all": "raise fastapi.HTTPException(status.HTTP_409_CONFLICT)",
        "non-literal code": 'raise HTTPException(409, {"code": f"agent_{status}"})',
        "code not snake_case": 'raise HTTPException(401, {"code": "TokenExpired"})',
        "unresolvable status": 'raise HTTPException(status_code, {"code": "token_expired"})',
        "bare imported name": 'raise HTTPException(HTTP_429_TOO_MANY_REQUESTS, "slow down")',
        "aliased import": (
            "from starlette.exceptions import HTTPException as StarletteHTTPException\n"
            'raise StarletteHTTPException(409, "stale")'
        ),
        "aliased import, attribute call": (
            'import fastapi as fa\nraise fa.HTTPException(401, {"message": "no code"})'
        ),
        "local subclass": 'class GateConflict(HTTPException):\n    pass\nraise GateConflict(409, "x")',
        "transitive local subclass": (
            "class Conflict(HTTPException):\n    pass\n"
            "class GateConflict(Conflict):\n    pass\n"
            'raise GateConflict(status.HTTP_409_CONFLICT, "x")'
        ),
        "subclass with its own signature": (
            'class GateConflict(HTTPException):\n    pass\nraise GateConflict("stale")'
        ),
        "** spread over code": 'raise HTTPException(409, {"code": "gate_not_open", **extra})',
        "typo'd code": 'raise HTTPException(401, {"code": "token_exipred"})',
    }
    for label, source in bad.items():
        problems = _problems(source)
        assert len(problems) == 1 and problems[0], (label, problems)


def test_a_subclass_defined_elsewhere_in_backend_is_flagged_where_it_is_raised():
    elsewhere = ast.parse("class RunConflict(HTTPException):\n    pass\n")
    source = 'from backend.errors_ext import RunConflict\nraise RunConflict(409, "x")'
    assert _problems(source) == []
    problems = _problems(source, subclasses=subclass_names([elsewhere]))
    assert len(problems) == 1 and problems[0]


def test_positive_controls_pass():
    good = [
        'raise HTTPException(status.HTTP_409_CONFLICT, {"code": "gate_already_decided", '
        '"message": "A decision has already been recorded"})',
        'raise HTTPException(status_code=429, detail={"code": "too_many_streams", "max": 5})',
        'raise HTTPException(401, {"code": "token_expired"}, headers={"WWW-Authenticate": "B"})',
        "from starlette.exceptions import HTTPException as SHE\n"
        'raise SHE(409, {"code": "gate_not_open", "status": s})',
    ]
    for source in good:
        assert _problems(source) == [None], source
    assert census('raise HTTPException(404, "Run not found")', "<synthetic>") == []
    assert census("raise ValueError(409)", "<synthetic>") == []
