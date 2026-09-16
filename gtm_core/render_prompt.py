"""Assemble one shot's frame-anchored video prompt — deterministically, so nobody types it.

Recipe shape A (``video-render/references/prompt-recipes.md``): the start frame IS the scene, so
the prompt says only what CHANGES, as short sentences in a fixed order — camera, subject action,
expression, environmental motion, stability, the trailing exclusion block, the reference
standard. Assembled by hand, eight prompts once came out as "holds" with negations mid-body
("lips stay pressed together... no open mouth, no teeth") and six of the shots rendered
motionless. Both defects are the ones the vendor guides name outright, and both are refused here
before any spend:

- an ``expression`` with no TIMING clause ("at 1.5s", "by 2s") is a hold, and a hold renders as a
  frozen frame — the performance lexicon's rule 6: "Video adds the timing; the change IS the
  performance";
- a negation anywhere in a body field is refused; exclusions live only in the trailing
  ``No <noun>.`` block (Veo's guide: describe what you want; "no"/"don't" are not recommended).

    python -m gtm_core.render_prompt --shots <shots.json> --shot <n> --seed <int> [--json]

The printed prompt is exactly what the render manifest records: single spaces, no trailing
whitespace. Exit 2 on a refusal (the message names the shot), 1 when inputs are unreadable.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .shots_coverage import sfx_declared
from .shots_lint.keyframe import KEYFRAME_MOTION_MAX_WORDS, is_keyframe_shot

__all__ = ["RenderPromptError", "build_prompt", "main"]

LEXICON_RULE = "Video adds the timing; the change IS the performance"
VEO_GUIDANCE = "describe what you want; 'no'/'don't' are not recommended"
REFERENCE_STANDARD = (
    "Match the start frame's wardrobe, lighting direction and lens character exactly."
)

#: "at 1.5s" / "by 2s" — the one thing that separates a performance from a hold.
_TIMING_RE = re.compile(r"\b(?:at|by)\s+\d+(?:\.\d+)?\s*s\b", re.IGNORECASE)
#: A still-state verb. Legal beside a timing clause ("the mouth stays closed; a swallow at
#: 2.5s"); on its own it is the hold that rendered six motionless shots.
_HOLD_RE = re.compile(r"\b(?:stays?|remains?|held)\b", re.IGNORECASE)
_NEGATION_RE = re.compile(r"\b(?:no|not|never|without|don't|doesn't)\b", re.IGNORECASE)
_BODY_FIELDS = ("camera", "motion_prompt", "expression", "environment_motion", "stability")
_AUDIO_ROLES = frozenset({"broll", "screen"})


class RenderPromptError(ValueError):
    """The shot cannot be turned into a prompt that will move."""


def _sentence(text: str) -> str:
    """One short sentence: collapsed whitespace, capital first letter, one terminal period."""
    words = " ".join(str(text or "").replace("’", "'").split())
    if not words:
        return ""
    words = words[0].upper() + words[1:]
    return words if words[-1] in ".!?" else words + "."


def _nouns(raw: object) -> list[str]:
    """``style_scaffold.negative`` is a comma-joined string or a list; both become plain nouns."""
    items = raw if isinstance(raw, list) else str(raw or "").split(",")
    out: list[str] = []
    for item in items:
        noun = " ".join(str(item or "").split()).strip(" .")
        noun = re.sub(r"^no\s+", "", noun, flags=re.IGNORECASE)
        if noun and noun.lower() not in {n.lower() for n in out}:
            out.append(noun)
    return out


def _refuse_negations(shot: dict, label: str) -> None:
    for field in _BODY_FIELDS:
        text = str(shot.get(field) or "").replace("’", "'")
        hit = _NEGATION_RE.search(text)
        if hit:
            raise RenderPromptError(
                f"{label}: `{field}` contains the negation {hit.group(0)!r} mid-body. Google's "
                f'Veo guide: {VEO_GUIDANCE}. State the wanted state positively ("mouth closed and '
                'still") and put any exclusion in `negative` as a plain noun, where it is emitted '
                "as the trailing `No <noun>.` block"
            )


def _refuse_hold(shot: dict, label: str) -> None:
    expression = str(shot.get("expression") or "").strip()
    if not expression or _TIMING_RE.search(expression):
        return
    why = (
        f"describes only a hold ({_HOLD_RE.search(expression).group(0)!r})"
        if _HOLD_RE.search(expression)
        else "carries no timing clause"
    )
    raise RenderPromptError(
        f"{label}: `expression` {why} — a hold renders as a frozen frame. Performance lexicon "
        f'§1 rule 6: "{LEXICON_RULE}". Say WHEN the change lands ("at 1.5s the inner brows '
        'lift a fraction")'
    )


def build_prompt(doc: dict, n: int, *, seed: int) -> dict:
    """The prompt record for 1-based shot ``n`` of ``doc``; raises RenderPromptError to refuse."""
    shots = doc.get("shots") if isinstance(doc.get("shots"), list) else []
    if n < 1 or n > len(shots) or not isinstance(shots[n - 1], dict):
        raise RenderPromptError(f"shot {n} is not in this list (it has {len(shots)} shots)")
    shot = shots[n - 1]
    label = f"shot {n} ({shot.get('id') or f'shot-{n - 1:02d}'})"
    scaffold = doc.get("style_scaffold") if isinstance(doc.get("style_scaffold"), dict) else {}

    _refuse_negations(shot, label)
    _refuse_hold(shot, label)

    keyframe = is_keyframe_shot(shot)
    motion = str(shot.get("motion_prompt") or "").strip()
    if keyframe and len(motion.split()) > KEYFRAME_MOTION_MAX_WORDS:
        # A WARN, not a refusal: shots_lint owns the ceiling; this only echoes it at spend time.
        print(
            f"[render-prompt] warning: {label} is a keyframe shot with a "
            f"{len(motion.split())}-word motion_prompt (over {KEYFRAME_MOTION_MAX_WORDS})",
            file=sys.stderr,
        )

    parts = [
        _sentence(shot.get("camera")),
        _sentence(motion),
        _sentence(shot.get("expression")),
        _sentence(shot.get("environment_motion")),
        _sentence(shot.get("stability")),
    ]
    parts.extend(
        f"No {noun}." for noun in _nouns(scaffold.get("negative")) + _nouns(shot.get("negative"))
    )
    parts.append(REFERENCE_STANDARD)
    prompt = " ".join(p for p in parts if p)

    role = str(shot.get("role") or "presenter")
    return {
        "shot": n,
        "prompt": prompt,
        "seed": int(seed),
        "model": scaffold.get("provider_model") or shot.get("engine") or None,
        "keyframe": keyframe,
        "generate_audio": bool(sfx_declared(shot)) and role in _AUDIO_ROLES,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="uv run python -m gtm_core.render_prompt")
    parser.add_argument("--shots", required=True, type=Path)
    parser.add_argument("--shot", required=True, type=int, help="1-based shot number")
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        doc = json.loads(args.shots.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[render-prompt] cannot read {args.shots}: {exc}", file=sys.stderr)
        return 1
    if not isinstance(doc, dict):
        print(f"[render-prompt] {args.shots} is not a shot list", file=sys.stderr)
        return 1
    try:
        record = build_prompt(doc, args.shot, seed=args.seed)
    except RenderPromptError as exc:
        print(f"[render-prompt] {exc}", file=sys.stderr)
        return 2

    print(json.dumps(record, indent=2) if args.json else record["prompt"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
