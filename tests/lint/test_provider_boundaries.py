"""Contract: which provider does which job is stated once, and no bare tool name is ambiguous.

Four media providers are connected — Higgsfield, Reap, HeyGen and Gemini — and their MCP tool
namespaces overlap. Four names collide outright:

    list_templates  HeyGen + Reap
    get_video       HeyGen + Reap
    reframe         Higgsfield + Reap
    list_voices     Higgsfield + HeyGen

Skill bodies name bare tool names. `video-avatar` names two providers in one body (HeyGen 5x,
Higgsfield 4x), so the ambiguity is live rather than theoretical: an agent reading "poll
`get_video`" in that body has two servers to choose from and nothing to choose with. The cost is
not a crash — it is a call to the wrong server that returns a plausible-looking 404 or, worse,
a real record for an unrelated asset.

The rule is cheap: where a body names a colliding tool, it names the server too — "Reap's
`reframe`", never bare `reframe`. Prose that merely uses the English verb ("no reframe pass
needed") is not a tool reference and is deliberately not matched; only backticked occurrences are.

Design: the 2026-08-29 video-router hardening note, item C10 (from P3).
"""

from __future__ import annotations

import re
from pathlib import Path

from gtm_core.gating import stub_list

REPO = Path(__file__).resolve().parents[2]

#: The nine video-lane skill bodies. Scoped deliberately: the repo-wide sweep is a separate
#: mechanical change with its own blast radius (same scoping rule as C7).
VIDEO_SKILLS = (
    "video-router",
    "video-script",
    "video-storyboard",
    "video-render",
    "video-avatar",
    "video-finish",
    "video-restyle",
    "video-clip",
    "video-score",
)

#: Tool names served by more than one connected MCP server. Probed live 2026-08-29.
COLLIDING_TOOLS = ("list_templates", "get_video", "reframe", "list_voices")

#: Any of these in the neighbourhood resolves the ambiguity.
_PROVIDERS = ("Reap", "HeyGen", "Higgsfield", "Gemini")

#: How far either side of the tool name a provider may sit and still be doing its job. A
#: qualifier three paragraphs up does not disambiguate anything for a reader who lands mid-file.
_LOOKBACK = 100
_LOOKAHEAD = 60


def _bodies() -> list[Path]:
    # A stub-carved skill ships no body in the public cut (`gtm_core.gating stub-list`); every
    # other body must still exist, so only a withheld one may be absent without failing loudly.
    withheld = stub_list()
    return [
        p
        for s in VIDEO_SKILLS
        if (p := REPO / "plugin" / "skills" / s / "body_template.md").exists() or s not in withheld
    ]


#: Inline code spans, correctly paired. Matching `...` non-greedily one pair at a time is what
#: keeps prose OUT: on a markdown table row carrying several spans, a greedy pattern happily pairs
#: the close of one span with the open of the next and swallows the sentence in between — which
#: is how "Native, so no reframe pass" first read as a call to Reap's `reframe` tool.
_CODE_SPAN = re.compile(r"`([^`\n]+)`")


def _unqualified(text: str, tool: str) -> list[int]:
    """Character offsets of ``tool`` used as a TOOL REFERENCE with no provider named nearby.

    A tool reference is the name inside an inline code span — `reframe`, `get_video(videoId=…)`.
    The bare English verb ("no reframe pass needed", "reframe the still") is not a call and is
    deliberately not matched; `reframeClips` is a different identifier and the word boundary
    excludes it.
    """
    offsets = []
    pattern = re.compile(rf"\b{tool}\b")
    for span in _CODE_SPAN.finditer(text):
        if not pattern.search(span.group(1)):
            continue
        window = text[max(0, span.start() - _LOOKBACK) : span.end() + _LOOKAHEAD]
        if not any(p in window for p in _PROVIDERS):
            offsets.append(span.start())
    return offsets


def test_every_colliding_tool_name_is_provider_qualified() -> None:
    """The regression test for P3.

    Fails today: `video-avatar` polls a bare `get_video` while naming two providers in the same
    body, and `video-render` reframes a still with a bare `reframe` that could be Reap's.
    """
    offenders: list[str] = []
    for path in _bodies():
        text = path.read_text(encoding="utf-8")
        for tool in COLLIDING_TOOLS:
            for offset in _unqualified(text, tool):
                line = text.count("\n", 0, offset) + 1
                offenders.append(f"{path.relative_to(REPO)}:{line} — bare `{tool}`")

    assert not offenders, (
        "these tool names are served by more than one connected MCP server, and nothing nearby "
        "says which one is meant:\n  "
        + "\n  ".join(offenders)
        + '\n\nName the server next to the tool — "Reap\'s `reframe`", not `reframe`.'
    )


def test_the_boundary_table_covers_every_connected_media_provider() -> None:
    """C10a. Four providers are connected; each needs a stated job or a stated removal.

    An unowned connected tool is a permission surface with no benefit — which is exactly what
    `gemini-image` was: connected, named once in the whole repo, with no boundary against
    Higgsfield's image generation.
    """
    body = (REPO / "plugin" / "skills" / "video-router" / "body_template.md").read_text(
        encoding="utf-8"
    )
    missing = [p for p in _PROVIDERS if p not in body]
    assert not missing, (
        f"the router's provider-boundary table does not account for: {missing}. Every connected "
        "media provider gets a stated job or a stated removal."
    )


def test_captions_are_routed_to_reap_with_a_recorded_reason() -> None:
    """Higgsfield ships a `subtitles` workflow and we route captions to Reap instead.

    That is a defensible call — per-word timings, 50+ presets, face-safe placement resolution —
    but an undocumented defensible call is indistinguishable from an oversight, and the next
    reader re-litigates it. The decline is the thing that has to be written down, not the choice.
    """
    body = (REPO / "plugin" / "skills" / "video-router" / "body_template.md").read_text(
        encoding="utf-8"
    )
    assert "subtitles" in body, (
        "the Higgsfield `subtitles` workflow is never mentioned, so routing captions to Reap "
        "reads as an oversight rather than a decision"
    )
    lowered = body.lower()
    assert "declin" in lowered, "the subtitles decline is not recorded as a decision"
