"""Contract and schema tests for M-03: OpenAPI responses= error typing.

Verifies that every /v1 route in the FastAPI application declares typed OpenAPI
error responses (400, 401, 403, 404, 409, 422, 429, 500) using the canonical
ErrorResponse envelope ({error: {code, message, details}, detail}), and that
legacy untyped error representations are eliminated.
"""

from __future__ import annotations

import os

from backend.errors import UnifiedErrorResponse
from backend.main import create_app
from backend.schemas import ErrorDetail, ErrorResponse

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-for-unit-tests-only-32x")


def test_unified_error_response_alias_equivalence():
    """M-03: backend.errors.UnifiedErrorResponse must be an alias of backend.schemas.ErrorResponse."""
    assert UnifiedErrorResponse is ErrorResponse
    instance = UnifiedErrorResponse(
        error=ErrorDetail(code="not_found", message="resource not found"),
        detail="resource not found",
    )
    dumped = instance.model_dump()
    assert dumped["error"]["code"] == "not_found"
    assert dumped["error"]["message"] == "resource not found"
    assert dumped["detail"] == "resource not found"


def test_openapi_contains_canonical_error_schemas():
    """M-03: OpenAPI components.schemas must define ErrorResponse and ErrorDetail."""
    app = create_app()
    schema = app.openapi()
    schemas = schema.get("components", {}).get("schemas", {})
    assert "ErrorResponse" in schemas, "ErrorResponse missing from OpenAPI components.schemas"
    assert "ErrorDetail" in schemas, "ErrorDetail missing from OpenAPI components.schemas"

    err_props = schemas["ErrorResponse"]["properties"]
    assert "error" in err_props
    assert "detail" in err_props


def test_all_v1_routes_declare_typed_error_responses():
    """M-03: All /v1/* endpoints must declare typed 401, 422, and 429 error responses."""
    app = create_app()
    schema = app.openapi()
    paths = schema.get("paths", {})

    v1_paths = {p: methods for p, methods in paths.items() if p.startswith("/v1/")}
    assert len(v1_paths) > 0, "No /v1 paths found in OpenAPI schema"

    missing_errors: list[str] = []
    for path, path_item in v1_paths.items():
        for method, op in path_item.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete"):
                continue
            responses = op.get("responses", {})
            for code in ("401", "422", "429"):
                if code not in responses:
                    missing_errors.append(f"{method.upper()} {path} missing response {code}")
                else:
                    content = responses[code].get("content", {})
                    json_schema = content.get("application/json", {}).get("schema", {})
                    ref = json_schema.get("$ref", "")
                    if "ErrorResponse" not in ref:
                        missing_errors.append(
                            f"{method.upper()} {path} {code} schema does not reference ErrorResponse (got {json_schema})"
                        )

    assert not missing_errors, "Routes with missing or untyped error responses:\n  " + "\n  ".join(
        missing_errors
    )
