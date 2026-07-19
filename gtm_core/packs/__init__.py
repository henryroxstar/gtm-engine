"""gtm_core.packs — the pack layer: declarative, engine-agnostic domain workflows.

A pack is data (graph + inputs), never code — see docs/prds/2026-07-06-engine-pack-tenant-three-layer.md
§4. This package only loads and validates that data; the engine (agent/) converts a
loaded :class:`~gtm_core.packs.loader.PackGraph` into a runnable
:class:`agent.graph.Graph` — kept out of this package because gtm_core must never
import agent/ (see tests/contracts/test_layering.py).
"""

from __future__ import annotations
