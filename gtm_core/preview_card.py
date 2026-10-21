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
import html
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from .paths import _safe_segment, resolve_content_root


class PreviewError(Exception):
    """Raised when preview assets cannot be resolved or verified."""


def _load_craft():
    """The craft verdict module, or ``None`` when ``video_craft`` is absent (the public cut).

    Same guarded-import pattern as the shots lint's craft hook: only ``video_craft`` ITSELF being
    missing means "absent"; a broken dependency inside it is a bug and surfaces. Callers use the
    module's own ``craft_verdict`` attribute, so the card and the terminal path share one function.
    """
    try:
        from .video_craft import craft_verdict as cv
    except ModuleNotFoundError as exc:
        if exc.name == "gtm_core.video_craft":
            return None
        raise
    return cv


def _craft_block(doc: object, run_dir: Path) -> str | None:
    """The one Gate 1 craft block for this shot list, and ``craft.json`` written once beside it."""
    cv = _load_craft()
    if cv is None:
        return None
    verdict = cv.craft_verdict(doc, require_craft=False)
    try:
        cv.write_craft_json(verdict, run_dir)
    except OSError:
        pass  # the card is still the operator's answer; the file is the terminal's copy
    return cv.render_card(verdict)


@dataclass(frozen=True)
class PreviewCard:
    script_slug: str
    hero_stills: list[str]  # Hook, Middle, Payoff OR Hook, Challenge, Solution, Payoff
    animatic_path: str | None
    total_duration_s: float
    total_words: int
    pacing_verdict: str
    estimated_credits: int | None
    estimated_credits_min: int | None
    budget_credits: int | None = None
    interactive_preview_path: str | None = None
    #: The craft check block (plain words, one per card); ``None`` when ``video_craft`` is absent.
    craft_block: str | None = None

    def craft_block_html(self) -> str:
        """The craft block escaped for a Telegram ``parse_mode=HTML`` message (empty when absent)."""
        return html.escape(self.craft_block or "")

    def to_markdown(self) -> str:
        header_label = (
            "**Visual Highlights (Hook, Challenge, Solution, Payoff):**"
            if len(self.hero_stills) == 4
            else "**Visual Highlights (Hook, Middle, Payoff):**"
        )
        lines: list[str] = [
            f"### Unified Preview Card: `{self.script_slug}`",
            "",
            header_label,
        ]
        labels_4 = ("1. Hook", "2. Challenge", "3. Solution", "4. Payoff")
        labels_3 = ("1. Hook", "2. Middle", "3. Payoff")
        for idx, still in enumerate(self.hero_stills):
            if len(self.hero_stills) == 4 and idx < 4:
                label = labels_4[idx]
            elif len(self.hero_stills) == 3 and idx < 3:
                label = labels_3[idx]
            else:
                label = f"{idx + 1}. Beat"
            lines.append(f"- **{label}:** `{still}`")

        lines.extend(
            [
                "",
                f"**Timing & Pacing:** {self.total_duration_s:.1f}s | {self.total_words} words | {self.pacing_verdict}",
            ]
        )
        if self.craft_block:
            lines.extend(["", self.craft_block, ""])

        if self.budget_credits is not None:
            spent_or_est = self.estimated_credits or 0
            lines.append(f"**Credit budget:** {spent_or_est} / {self.budget_credits} credits")
        elif self.estimated_credits is not None:
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

        if self.interactive_preview_path:
            lines.extend(
                [
                    "",
                    f"**Interactive Preview:** ⟦FILE:{self.interactive_preview_path}⟧",
                ]
            )

        footer_action = (
            "Approving proceeds to render (0 provider credits); Edit to revise script."
            if (
                self.estimated_credits == 0
                and (not self.estimated_credits_min or self.estimated_credits_min == 0)
            )
            else "Approving commits render spend; Edit to revise script."
        )
        lines.extend(
            [
                "",
                f"Review hero framing, timing read, and audio bed. {footer_action}",
            ]
        )
        return "\n".join(lines)


def build_preview_card(  # noqa: C901, PLR0912  # PRD 2026-10-16-html-motion-scenes
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
    shots: list[dict] = []
    craft_doc: object = None
    if shots_path.is_file():
        try:
            shots_data = json.loads(shots_path.read_text(encoding="utf-8"))
            craft_doc = shots_data
            shots = [s for s in shots_data.get("shots", []) if isinstance(s, dict)]
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

    craft_block = _craft_block(craft_doc, run_dir) if craft_doc is not None else None

    estimated_credits: int | None = 23
    estimated_credits_min: int | None = 11

    if shots:
        screen_ui_count = sum(
            1
            for s in shots
            if isinstance(s.get("production"), dict) and "screen_ui" in s["production"]
        )
        if screen_ui_count == len(shots):
            estimated_credits = 0
            estimated_credits_min = 0
        elif screen_ui_count > 0:
            provider_count = len(shots) - screen_ui_count
            estimated_credits = max(1, round(provider_count * 5.0))
            estimated_credits_min = max(1, round(provider_count * 2.5))

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

    # Check for stills directory (e.g. from mc-video render)
    stills_dir = run_dir / "stills"
    if stills_dir.is_dir():
        mc_stills = sorted(stills_dir.glob("still-*.png"))
        if len(mc_stills) >= 4:
            hero_stills = [str(s.resolve()) for s in mc_stills[:4]]

    # Check for interactive preview HTML
    interactive_preview_path = None
    preview_file = run_dir / "preview" / "index.html"
    if preview_file.is_file():
        interactive_preview_path = str(preview_file.resolve())

    return PreviewCard(
        script_slug=script_slug,
        hero_stills=hero_stills,
        animatic_path=animatic_path,
        total_duration_s=total_duration_s,
        total_words=total_words,
        pacing_verdict=pacing_verdict,
        estimated_credits=estimated_credits,
        estimated_credits_min=estimated_credits_min,
        budget_credits=budget_credits,
        interactive_preview_path=interactive_preview_path,
        craft_block=craft_block,
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
            payload = asdict(card)
            if payload["craft_block"] is None:
                del payload["craft_block"]  # public cut: the JSON is exactly what it always was
            print(json.dumps(payload, indent=2))
        else:
            print(card.to_markdown())
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
