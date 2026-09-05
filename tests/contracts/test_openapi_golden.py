"""OpenAPI golden — the backend route contract, pinned (PRD 2026-09-01 §6.2 V2, §6.1 row 11).

``app.openapi()`` is dumped once and committed under ``golden/``; this test asserts SEMANTIC
equality (a parsed-JSON deep compare, not bytes) so a pure-motion refactor of the routers
cannot drift a route, a signature, a status code, or a response model without this test
naming the exact key that moved. Key order is noise; the schema is the contract.

Regenerate deliberately — in the same PR as an intentional route change, never to make a
red test green:

    uv run python -m tests.contracts.test_openapi_golden --write
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-for-unit-tests-only-32x")

GOLDEN = Path(__file__).resolve().parent / "golden" / "backend_openapi.json"


def current_schema() -> dict:
    """The live schema as plain JSON types (what the golden holds)."""
    from backend.main import create_app

    return json.loads(json.dumps(create_app().openapi(), sort_keys=True))


def diff(expected, actual, path: str = "$", limit: int = 25) -> list[str]:
    """Readable first-N differences between two JSON values, by JSON path."""
    out: list[str] = []

    def walk(e, a, p):
        if len(out) >= limit:
            return
        if type(e) is not type(a):
            out.append(f"{p}: golden {type(e).__name__} vs live {type(a).__name__}")
        elif isinstance(e, dict):
            for k in sorted(set(e) | set(a)):
                if k not in a:
                    out.append(f"{p}.{k}: missing from live schema")
                elif k not in e:
                    out.append(f"{p}.{k}: not in golden (new)")
                else:
                    walk(e[k], a[k], f"{p}.{k}")
        elif isinstance(e, list):
            if len(e) != len(a):
                out.append(f"{p}: golden has {len(e)} items, live has {len(a)}")
            for i, (x, y) in enumerate(zip(e, a, strict=False)):
                walk(x, y, f"{p}[{i}]")
        elif e != a:
            out.append(f"{p}: golden {e!r} vs live {a!r}")

    walk(expected, actual, path)
    return out


def test_openapi_schema_matches_the_committed_golden():
    assert GOLDEN.exists(), f"missing golden {GOLDEN.name} — see the module docstring"
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    diffs = diff(golden, current_schema())
    assert not diffs, (
        "OpenAPI drift against the committed golden:\n  "
        + "\n  ".join(diffs)
        + "\nIf the route change is intentional, regenerate in the same PR:\n"
        "  uv run python -m tests.contracts.test_openapi_golden --write"
    )


def test_the_golden_covers_the_run_lifecycle_routes():
    """A golden captured against a stub app would pass the equality test forever —
    pin that the committed one really is the full run surface."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    paths = golden["paths"]
    for route, method in [
        ("/v1/runs", "post"),
        ("/v1/runs", "get"),
        ("/v1/runs/{run_id}", "get"),
        ("/v1/runs/{run_id}/cancel", "post"),
        ("/v1/runs/{run_id}/gate", "post"),
        ("/v1/runs/{run_id}/stream", "get"),
        ("/v1/runs/{run_id}/artifacts", "get"),
        ("/v1/runs/{run_id}/artifacts/{artifact_id}", "get"),
    ]:
        assert method in paths.get(route, {}), f"{method.upper()} {route} missing from golden"


if __name__ == "__main__":
    if "--write" in sys.argv:
        os.environ.setdefault("ENV", "test")  # the CORS production guard is not the contract
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(
            json.dumps(current_schema(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"wrote {GOLDEN}")
    else:
        print(__doc__)
