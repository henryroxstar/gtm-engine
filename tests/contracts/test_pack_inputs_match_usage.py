"""F10 regression net: a pack's ``inputs.toml`` must not declare a knowledge topic
that NO node skill in that pack's own graphs actually reads.

The gap this closes: ``packs/creator/inputs.toml`` declared `company` as REQUIRED
(90d freshness) and `product` as optional, but per ``docs/knowledge-usage.md`` no
creator-pack node skill reads either — so a profile missing a `company.md` file
(or with one gone stale) failed pack readiness for a file nothing consumes. Fixed
by removing both and declaring `social-tuning` (which content-plan DOES read but
the file never named).

Scope: this test asserts the DECLARED-BUT-UNREAD direction only (a topic in
`inputs.toml` must be read by at least one node skill). The reverse — a topic a
skill reads that `inputs.toml` never declares — stays advisory: under-declaring
fails OPEN (readiness may not warn about a genuinely missing file) rather than
fails CLOSED (blocking readiness on a file nothing needs), and is the lower-risk
direction to leave unenforced here.

``packs/marketing/inputs.toml`` had the SAME declared-but-unread bug (`company`,
`product`) — fixed 2026-08-15 alongside the entangled
tests/backend/test_pack_readiness.py fixtures (input-name set, and the 90d-freshness
staleness case moved from `company` to `content-priority`, the corrected set's own
90d topic). No exemption remains; every pack's `inputs.toml` is enforced.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from gtm_core.packs.loader import load_pack_graph, load_pack_inputs

REPO = Path(__file__).resolve().parents[2]

#: Pre-existing gaps, tracked separately — see module docstring. Do not add new entries;
#: each pack here should be a single, dated, linked exception, not a growing allowlist.
_KNOWN_UNREAD_TOPIC_PACKS = frozenset()


def _skill_topics_from_usage_doc() -> dict[str, frozenset[str]]:
    """Parse the ``## Skill → topics`` table out of the generated usage index."""
    text = (REPO / "docs" / "knowledge-usage.md").read_text(encoding="utf-8")
    section = text.split("## Skill → topics", 1)[1]
    rows: dict[str, frozenset[str]] = {}
    for line in section.splitlines():
        m = re.match(r"^\|\s*([a-z0-9-]+)\s*\|\s*(.+?)\s*\|\s*$", line)
        if not m:
            continue
        skill, cell = m.group(1), m.group(2)
        if cell.strip() == "—":
            rows[skill] = frozenset()
            continue
        topics = frozenset(t.strip().strip("`") for t in cell.split(",") if t.strip())
        rows[skill] = topics
    assert rows, "the usage-doc table parser found nothing — the doc's shape has drifted"
    return rows


SKILL_TOPICS = _skill_topics_from_usage_doc()

PACK_INPUTS_FILES = sorted((REPO / "packs").glob("*/inputs.toml"))


def test_glob_is_non_vacuous():
    assert len(PACK_INPUTS_FILES) >= 3


@pytest.mark.parametrize("inputs_path", PACK_INPUTS_FILES, ids=lambda p: p.parent.name)
def test_every_declared_knowledge_topic_is_read_by_some_node_skill_in_the_pack(inputs_path):
    pack_name = inputs_path.parent.name
    if pack_name in _KNOWN_UNREAD_TOPIC_PACKS:
        pytest.skip(f"{pack_name}: pre-existing gap tracked separately (see module docstring)")

    inputs = load_pack_inputs(inputs_path)
    declared = {k.topic for k in inputs.knowledge}
    if not declared:
        return

    read_by_pack: set[str] = set()
    for graph_path in sorted((inputs_path.parent / "graphs").glob("*.toml")):
        graph = load_pack_graph(graph_path)
        for node in graph.nodes:
            if node.skill:
                read_by_pack |= SKILL_TOPICS.get(node.skill, frozenset())

    unread = declared - read_by_pack
    assert not unread, (
        f"{pack_name}/inputs.toml declares {sorted(unread)} but no node skill in "
        f"{pack_name}'s graphs reads {'it' if len(unread) == 1 else 'them'} "
        "(per docs/knowledge-usage.md) — a required-but-unread topic fails pack "
        "readiness for a file nothing consumes"
    )
