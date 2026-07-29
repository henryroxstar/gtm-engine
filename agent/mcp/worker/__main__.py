"""Entrypoint for the DeepSeek worker MCP — ``python -m agent.mcp.worker``.

The Agent SDK spawns this as a stdio MCP server (see :mod:`agent.mcp_config`).
``--transport`` is accepted for symmetry with other MCP servers; ``stdio`` is the
only transport the SDK uses for a spawned child.
"""

from __future__ import annotations

import argparse

from .server import mcp


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="agent.mcp.worker",
        description="DeepSeek worker MCP server (downstream generation tool).",
    )
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio"],
        help="MCP transport (only stdio is supported; the SDK spawns this as a child).",
    )
    parser.parse_args(argv)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
