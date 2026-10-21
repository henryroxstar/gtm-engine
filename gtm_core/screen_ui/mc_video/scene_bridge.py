"""Bridge allowing single MCDL motion blocks to be rendered as screen_ui scenes."""

from __future__ import annotations

import json
import math
import shutil
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]

MC_BLOCK_TYPES = [
    "hook-constellation",
    "hook-traffic",
    "intro",
    "idcard",
    "gate",
    "log",
    "traffic-gate",
    "reviewers",
    "budgets",
    "outcome",
    "endcard",
]

_DEFAULT_PAYLOADS: dict[str, dict[str, Any]] = {
    "hook-constellation": {
        "line1": "Your AI agents act for your business.",
        "line2": "Do you know which ones?",
    },
    "hook-traffic": {
        "line1": "Every AI call is a risk, and a cost.",
        "line2": "Who's watching them?",
    },
    "intro": {
        "logo": "logos/brand/logo.svg",
        "line": "Control for every AI agent you run.",
    },
    "idcard": {
        "title": "Every agent gets an identity.",
        "fields": [["Agent", "Finance agent"], ["Status", "Verified"]],
    },
    "gate": {
        "title": "Every action checked against rules.",
        "rules": ["Known agent", "Under limit"],
        "requests": [{"at": 0.5, "label": "Invoice $500", "pass": [True, True]}],
    },
    "log": {
        "title": "Every decision on record.",
        "agents": [["Agent 1", "reads ledger"], ["Agent 2", "approves order"]],
    },
    "traffic-gate": {
        "title": "Unsafe requests, blocked.",
    },
    "reviewers": {
        "title": "Every answer, double-checked.",
    },
    "budgets": {
        "title": "Every team's spend, under control.",
        "teams": [["Engineering", 10000, [0.2, 0.5, 0.8, 1.0, 1.0]]],
    },
    "outcome": {
        "line1": "Put AI agents to work.",
        "line2": "[Stay in control.]",
    },
    "endcard": {
        "logo": "logos/brand/logo.svg",
        "cta": "Try it now",
        "url": "example.com",
    },
}


def render_mc_block_frames(
    block_type: str,
    *,
    kit: dict[str, Any],
    ratio: str = "16:9",
    fps: int = 25,
    duration_s: float = 2.0,
    out_dir: Path,
    repo_root: Path | None = None,
    mock: bool = False,
    **kwargs: Any,
) -> None:
    out_dir = Path(out_dir).resolve()
    from ...confine import ConfinementError

    repo_forbidden = [
        ROOT / "profiles",
        ROOT / "gtm_core",
        ROOT / ".git",
        ROOT / "plugin",
        ROOT / "tests",
        ROOT / "docs",
        ROOT / "docker",
    ]
    for forbidden in repo_forbidden:
        try:
            out_dir.relative_to(forbidden.resolve())
            raise ConfinementError(
                f"Refusing to write block frames inside protected repository directory: {out_dir}"
            )
        except ValueError:
            pass

    out_dir.mkdir(parents=True, exist_ok=True)

    payload = dict(_DEFAULT_PAYLOADS.get(block_type, {}))
    payload["type"] = block_type
    payload["dur"] = duration_s

    # Override defaults with any keyword args passed
    for k, v in kwargs.items():
        if v is not None:
            payload[k] = v

    brief = {
        "placement": "single-beat",
        "formats": "16:9 1080p",
        "maxDuration": int(math.ceil(duration_s)),
        "sound": "off",
        "audience": "technical",
        "message": "single-beat insertion",
        "cta": "learn more",
        "claimsOwner": "engineering",
        "confirmed": True,
    }

    script = {
        "title": f"MCDL Block · {block_type}",
        "product": kwargs.get("product", "Mission Control"),
        "fps": fps,
        "width": 1920,
        "height": 1080,
        "brief": brief,
        "scenes": [payload],
    }

    from .render import render_mc_video

    with tempfile.TemporaryDirectory() as td:
        s_file = Path(td) / "single_block.json"
        s_file.write_text(json.dumps(script), encoding="utf-8")
        render_out = Path(td) / "render_out"

        render_mc_video(
            s_file,
            render_out,
            scale=1,
            preview_only=False,
            mock=mock,
            keep_frames=True,
            content_root=repo_root,
        )

        frames_src = render_out / ".frames_scratch"
        if not frames_src.is_dir():
            frames_src = render_out / "frames"
        if not frames_src.is_dir():
            frames_src = render_out

        # Copy frames to out_dir
        for f in sorted(frames_src.glob("*.png")):
            shutil.copy2(f, out_dir / f.name)
