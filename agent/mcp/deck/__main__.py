import argparse

from agent.mcp.deck.server import _build_mcp

p = argparse.ArgumentParser()
p.add_argument("--transport", default="stdio", choices=["stdio"])
p.parse_args()

_build_mcp().run(transport="stdio")
