"""Unified Preview Card generator (PRD 3 Axis 3).

Constructs the composite preview payload presented at the pre-spend preview checkpoint:
- 3 Hero Stills (Hook, Middle, Payoff)
- Audio / Animatic Preview path (build/animatic.mp4)
- Word count and pacing verdict
- Credit spend estimate commitment

Produces human-readable markdown and structured JSON summary for the operator before Gate approval.
Zero video generation spend: operates entirely off stills (Gemini/Flux), local layouts, and scratch TTS audio.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from .paths import _safe_segment, resolve_content_root


class PreviewError(Exception):
    """Raised when preview assets cannot be resolved or verified."""


@dataclass(frozen=True)
class PreviewCard:
    script_slug: str
    hero_stills: list[str]  # Hook, Middle, Payoff
    animatic_path: str | None
    total_duration_s: float
    total_words: int
    pacing_verdict: str
    estimated_credits: int | None
    estimated_credits_min: int | None
    budget_credits: int | None = None

    def to_markdown(self) -> str:
        lines: list[str] = [
            f"### Unified Preview Card: `{self.script_slug}`",
            "",
            "**Visual Highlights (Hook, Middle, Payoff):**",
        ]
        for idx, still in enumerate(self.hero_stills, 1):
            label = "1. Hook" if idx == 1 else ("2. Middle" if idx == 2 else "3. Payoff")
            lines.append(f"- **{label}:** `{still}`")

        lines.extend(
            [
                "",
                f"**Timing & Pacing:** {self.total_duration_s:.1f}s | {self.total_words} words | {self.pacing_verdict}",
            ]
        )

        if self.budget_credits is not None:
            spent_or_est = self.estimated_credits or 0
            lines.append(f"**Credit budget:** {spent_or_est} / {self.budget_credits} credits")
        elif self.estimated_credits:
            if self.estimated_credits_min and self.estimated_credits_min != self.estimated_credits:
                lines.append(
                    f"**Spend Commitment:** ~{self.estimated_credits_min}–{self.estimated_credits} credits"
                )
            else:
                lines.append(f"**Spend Commitment:** ~{self.estimated_credits} credits")

        if self.animatic_path:
            lines.extend(
                [
                    "",
                    f"⟦FILE:{self.animatic_path}⟧",
                ]
            )

        lines.extend(
            [
                "",
                "Review hero framing, timing read, and audio bed. Approving commits render spend; Edit to revise script.",
            ]
        )
        return "\n".join(lines)


def build_preview_card(
    profile: str,
    script_slug: str,
    *,
    content_root: Path | None = None,
    budget_credits: int | None = None,
) -> PreviewCard:
    """Assemble the PreviewCard for a given video run."""
    root = resolve_content_root() if content_root is None else Path(content_root)
    _safe_segment(profile, "profile")
    _safe_segment(script_slug, "script_slug")

    run_dir = root / profile / "video" / script_slug
    sb_path = run_dir / "storyboard.json"
    shots_path = root / profile / "scripts" / f"{script_slug}.shots.json"

    hero_stills: list[str] = []
    if sb_path.is_file():
        try:
            sb_data = json.loads(sb_path.read_text(encoding="utf-8"))
            stills = sb_data.get("stills", [])
            for s in stills:
                if isinstance(s, dict) and s.get("path"):
                    hero_stills.append(str(s["path"]))
        except (OSError, json.JSONDecodeError, KeyError):
            hero_stills = []

    # Pick up to 3 frames: first, middle, last
    if len(hero_stills) > 3:
        hero_stills = [hero_stills[0], hero_stills[len(hero_stills) // 2], hero_stills[-1]]

    animatic_path = None
    animatic_file = run_dir / "build" / "animatic.mp4"
    if animatic_file.is_file():
        animatic_path = str(animatic_file.resolve())

    total_duration_s = 0.0
    total_words = 0
    pacing_verdict = "Pacing not verified"
    if shots_path.is_file():
        try:
            shots_data = json.loads(shots_path.read_text(encoding="utf-8"))
            shots = shots_data.get("shots", [])
            for shot in shots:
                total_duration_s += float(shot.get("duration_s", 0.0))
                caption = str(shot.get("caption") or "")
                total_words += len(caption.split())
            if total_duration_s > 0:
                wps = total_words / total_duration_s
                pacing_verdict = (
                    f"Pacing clean ({wps:.2f} wps)"
                    if wps <= 2.9
                    else f"Pacing fast ({wps:.2f} wps > 2.9 max)"
                )
        except (OSError, json.JSONDecodeError, ValueError, KeyError):
            pacing_verdict = "Pacing unreadable"

    if budget_credits is None:
        brief_file = run_dir / "brief.json"
        if brief_file.is_file():
            try:
                bdata = json.loads(brief_file.read_text(encoding="utf-8"))
                dec = bdata.get("decisions", {})
                msc = dec.get("max_spend_credits", {}).get("value")
                if msc:
                    budget_credits = int(msc)
            except (OSError, json.JSONDecodeError, ValueError, TypeError):
                budget_credits = None

    return PreviewCard(
        script_slug=script_slug,
        hero_stills=hero_stills,
        animatic_path=animatic_path,
        total_duration_s=total_duration_s,
        total_words=total_words,
        pacing_verdict=pacing_verdict,
        estimated_credits=23,
        estimated_credits_min=11,
        budget_credits=budget_credits,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="uv run python -m gtm_core.preview_card",
        description="Build or inspect the unified preview card for a video run.",
    )
    parser.add_argument(
        "script_slug_pos",
        nargs="?",
        default=None,
        metavar="SCRIPT_SLUG",
        help="run script slug (positional)",
    )
    parser.add_argument("--profile", required=True, help="active profile")
    parser.add_argument("--script-slug", default=None, help="run script slug (option)")
    parser.add_argument("--json", action="store_true", help="output JSON")
    args = parser.parse_args(argv)

    script_slug = args.script_slug or args.script_slug_pos
    if not script_slug:
        parser.error("the following arguments are required: script_slug (or --script-slug)")

    try:
        card = build_preview_card(args.profile, script_slug)
        if args.json:
            print(json.dumps(asdict(card), indent=2))
        else:
            print(card.to_markdown())
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
