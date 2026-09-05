"""Contract: the nine video-lane skill bodies invoke Python through ``uv run``.

The operator's laptop carries a ``modern-python`` shim that intercepts a bare ``python`` /
``python3`` and errors out. A skill body whose very first command is ``python -m
gtm_core.video_preflight`` therefore fails once per session before the agent works out to retry
under ``uv run`` — on the step whose entire purpose is to run *before* anything else.

**Scope is the nine video-lane bodies, deliberately.** Repo-wide the split is ~168 bare against
~82 ``uv run``; standardising all of it is a separate mechanical change with its own blast radius
and is explicitly out of scope. This test must not fail on that — it is written to the nine, and
widening it is a decision someone takes on purpose, not a side effect.

Design: the 2026-08-29 video-router hardening note, item C7.
"""

from __future__ import annotations

import re
from pathlib import Path

from gtm_core.gating import stub_list

REPO = Path(__file__).resolve().parents[2]

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

#: A `python -m` / `python3 -m` invocation NOT already prefixed by `uv run `. Matched in prose as
#: well as in fenced blocks: an agent copies whichever form it reads, and a bare command quoted
#: mid-sentence is copied exactly as often as one in a code fence.
_BARE_PYTHON = re.compile(r"(?<!uv run )\bpython3? -m ")


def test_video_skill_bodies_use_uv_run_python() -> None:
    offenders: list[str] = []
    # A stub-carved skill ships no body in the public cut (`gtm_core.gating stub-list`); every
    # other body must still exist, so only a withheld one may be skipped here.
    withheld = stub_list()
    for name in VIDEO_SKILLS:
        path = REPO / "plugin" / "skills" / name / "body_template.md"
        if not path.exists() and name in withheld:
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _BARE_PYTHON.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{i} — {line.strip()[:90]}")

    assert not offenders, (
        "these video-lane skill bodies invoke Python bare, which the `modern-python` shim "
        "intercepts and errors on:\n  " + "\n  ".join(offenders) + "\n\nUse `uv run python -m ...`."
    )
