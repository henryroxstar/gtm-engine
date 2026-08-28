"""Hook pattern linter — deterministic checks on the hook bank.

Validates `hooks.toml` against schema, enforces banned stems/angles, and reports
why a hook would fail the cheap pre-render gate.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from . import hooks as hk
from . import tweet_patterns as tp
from .paths import resolve_profiles_root

#: Hook statuses supported by the engine.
_VALID_STATUSES = frozenset({"candidate", "test", "proven", "banned"})

#: A hook id must be a kebab-case slug.
_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

#: Angle texts that look like placeholders.
_PLACEHOLDER_RE = re.compile(r"\b(lorem|ipsum|todo|fixme|xxx|placeholder)\b", re.IGNORECASE)

#: LinkedIn-text opening beats should be short enough to fit a hook.
_MAX_TEXT_BEAT_CHARS = 280

#: Fraction of a payoff_promise's content words that must appear in the rendered text.
_PAYOFF_OVERLAP_MIN = 0.5

#: Function words that carry no payoff meaning. Small and deliberate; this is a
#: delivery check, not a semantic model.
_STOPWORDS = frozenset(
    {
        "that",
        "this",
        "with",
        "your",
        "from",
        "what",
        "when",
        "which",
        "they",
        "them",
        "their",
        "then",
        "than",
        "into",
        "onto",
        "over",
        "under",
        "about",
        "have",
        "has",
        "had",
        "been",
        "being",
        "does",
        "did",
        "done",
        "will",
        "would",
        "could",
        "should",
        "must",
        "just",
        "only",
        "also",
        "more",
        "most",
        "some",
        "such",
        "here",
        "there",
        "where",
        "while",
        "because",
        "before",
        "after",
        "again",
        "every",
        "each",
        "both",
        "same",
        "other",
        "another",
        "leave",
        "leaves",
        "knowing",
        "know",
        "learn",
        "learns",
        "understand",
    }
)


def _content_words(value: str) -> set[str]:
    """Lowercase alphanumeric tokens longer than 3 chars, minus function words."""
    return {
        w for w in re.findall(r"[a-z0-9]+", value.lower()) if len(w) > 3 and w not in _STOPWORDS
    }


def _payoff_delivered(payoff: str, text: str) -> bool:
    """True when ``text`` delivers ``payoff`` -- verbatim, or by content-word overlap.

    A ``payoff_promise`` is a *description* of what the reader walks away with
    ("what a structured consent record carries that a flag cannot"), not a line that
    can appear word for word in the copy. The original substring test therefore failed
    every caption that paid the promise off in its own words, which is what a caption
    is supposed to do. Overlap preserves the intent -- a caption that never delivers the
    payoff still fails -- without demanding the promise be quoted.
    """
    if payoff.lower() in text.lower():
        return True
    want = _content_words(payoff)
    if not want:
        return True
    return len(want & _content_words(text)) / len(want) >= _PAYOFF_OVERLAP_MIN


def lint_bank(bank: hk.HookBank) -> list[str]:
    """Return a list of validation errors; empty list means the bank is valid."""
    errors: list[str] = []

    # Banned config is informational only at load time; runtime lint checks use it.
    for idx, hook in enumerate(bank.hooks, 1):
        errors.extend(_lint_hook(hook, idx, bank.banned))

    return errors


def _lint_hook(hook: hk.Hook, index: int, banned: hk.BannedConfig) -> list[str]:
    errors: list[str] = []
    prefix = f"hook[{index}] `{hook.id}`"

    if not hook.id:
        errors.append(f"hook[{index}] missing required field `id`")
        return errors

    if not _ID_RE.match(hook.id):
        errors.append(f"{prefix} id is not kebab-case: {hook.id!r}")

    if not hook.angle or not hook.angle.strip():
        errors.append(f"{prefix} missing required field `angle`")

    if not hook.payoff_promise or not hook.payoff_promise.strip():
        errors.append(f"{prefix} missing required field `payoff_promise`")

    if hook.status not in _VALID_STATUSES:
        errors.append(f"{prefix} invalid status {hook.status!r}")

    if _PLACEHOLDER_RE.search(hook.angle):
        errors.append(f"{prefix} angle contains placeholder text")

    for stem in banned.stems:
        if stem and stem.lower() in hook.angle.lower():
            errors.append(f"{prefix} angle contains banned stem {stem!r}")

    for angle in banned.angles:
        if angle and angle.lower() in hook.angle.lower():
            errors.append(f"{prefix} angle matches banned angle {angle!r}")

    for beat in hook.opening_beats:
        if not beat.format:
            errors.append(f"{prefix} opening_beat missing format")
        if beat.format and beat.format not in hook.formats:
            errors.append(f"{prefix} opening_beat format {beat.format!r} not in hook.formats")
        if beat.text is not None and len(beat.text) > _MAX_TEXT_BEAT_CHARS:
            errors.append(
                f"{prefix} opening_beat text for {beat.format} exceeds {_MAX_TEXT_BEAT_CHARS} chars"
            )
        if beat.pattern_id is not None:
            try:
                spec = tp.get(beat.pattern_id)
            except tp.TweetPatternError:
                errors.append(
                    f"{prefix} opening_beat pattern_id {beat.pattern_id!r} is not in the "
                    "X tweet-pattern catalog (gtm_core/tweet_patterns.toml)"
                )
            else:
                if beat.format and beat.format not in spec.formats:
                    errors.append(
                        f"{prefix} opening_beat pattern_id {beat.pattern_id!r} does not support "
                        f"format {beat.format!r} (pattern supports: {sorted(spec.formats)})"
                    )

    # A hook with formats declared should have at least one beat for each,
    # otherwise it cannot be scheduled for that format.
    declared = set(hook.formats)
    beat_formats = {b.format for b in hook.opening_beats}
    missing = declared - beat_formats
    if missing and hook.opening_beats:
        errors.append(
            f"{prefix} missing opening_beat for declared format(s): {', '.join(sorted(missing))}"
        )

    return errors


def lint_text(
    text: str,
    hook: hk.Hook,
    banned: hk.BannedConfig | None = None,
) -> list[str]:
    """Lint a concrete execution (caption/script/storyboard) against the hook policy.

    Used by `gtm_core.hook_score` for the deterministic pattern component.
    """
    errors: list[str] = []
    banned = banned or hk.BannedConfig()

    if not text or not text.strip():
        errors.append("text is empty")
        return errors

    if hook.payoff_promise and not _payoff_delivered(hook.payoff_promise, text):
        errors.append("payoff_promise not present in text")

    for stem in banned.stems:
        if stem and stem.lower() in text.lower():
            errors.append(f"contains banned stem {stem!r}")

    # Caption-load heuristic: long dense paragraphs without line breaks are hard
    # to read on mobile.
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    if len(text) > 1200 and len(paragraphs) <= 1:
        errors.append("caption load too high for mobile — add paragraph breaks")

    # Payoff presence is required; opening angle presence is required.
    if hook.angle and hook.angle.lower() not in text.lower():
        # Allow a beat variant to satisfy the angle presence test.
        beat_texts = " ".join((b.text or "").lower() for b in hook.opening_beats if b.text)
        if hook.angle.lower() not in beat_texts:
            errors.append("hook angle not present in text or opening beats")

    return errors


def lint(
    profiles_root: Path,
    profile: str,
    *,
    product: str | None = None,
) -> list[str]:
    """Load and lint a profile's hook bank."""
    bank = hk.load_hooks(profiles_root, profile, product=product)
    return lint_bank(bank)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.hooks_lint",
        description="Lint a profile's hooks.toml / hook-matrix.md against schema and policy.",
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument("--product", default=None)
    parser.add_argument("--profiles-root", default=None)
    args = parser.parse_args(argv)

    profiles_root = (
        Path(args.profiles_root).expanduser().resolve()
        if args.profiles_root
        else resolve_profiles_root()
    )
    errors = lint(profiles_root, args.profile, product=args.product)
    if errors:
        print("hooks_lint errors:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("hooks_lint: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
