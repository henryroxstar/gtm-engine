"""A real FastMCP streamable-HTTP origin for the bridge e2e test, behind a stand-in for the edge gateway.

Run as ``python mcp_bridge_origin.py <port>``. The app is the same library and transport the ``mcp``
container runs (``mcp_server/__main__.py``: ``mcp.run(transport="streamable-http")``); only the tools
are toys, so the test exercises the protocol the bridge must speak rather than a mock of it.

In front of it, a few bearer keys get the refusals ``deploy/mcp-gateway/src/index.ts`` returns — same
status, same body shape — before the request reaches FastMCP. Any other key passes through.
"""

from __future__ import annotations

import asyncio
import json
import sys

import uvicorn
from mcp.server.fastmcp import Context, FastMCP

mcp = FastMCP("bridge-e2e-origin")


@mcp.tool()
def echo(text: str) -> str:
    """Return the text unchanged."""
    return text


@mcp.tool()
async def slow(seconds: float, ctx: Context) -> str:
    """Log a notification on the request's stream, then answer after a delay."""
    await ctx.info("slow started")
    await asyncio.sleep(seconds)
    return "done"


# The Worker drops ``upgrade_url`` from the body when BILLING_URL / PRICING_URL is unset.
_REFUSALS: dict[str, tuple[int, dict]] = {
    "": (401, {"error": "unauthorized", "message": "Missing Bearer API key"}),
    "sk-test-revoked": (401, {"error": "unauthorized", "message": "API key is invalid or revoked"}),
    "sk-test-unpaid": (
        402,
        {
            "error": "payment_required",
            "message": "Insufficient MCP credits. Please top up your balance.",
            "upgrade_url": "https://billing.test/topup",
        },
    ),
    "sk-test-unpaid-nolink": (
        402,
        {"error": "payment_required", "message": "Active subscription required."},
    ),
}


def _gateway(app):
    async def wrapped(scope, receive, send):
        if scope["type"] == "http":
            auth = dict(scope["headers"]).get(b"authorization", b"").decode()
            token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
            if token in _REFUSALS:
                status, body = _REFUSALS[token]
                await send(
                    {
                        "type": "http.response.start",
                        "status": status,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send({"type": "http.response.body", "body": json.dumps(body).encode()})
                return
        await app(scope, receive, send)

    return wrapped


if __name__ == "__main__":
    uvicorn.run(
        _gateway(mcp.streamable_http_app()),
        host="127.0.0.1",
        port=int(sys.argv[1]),
        log_level="warning",
    )
