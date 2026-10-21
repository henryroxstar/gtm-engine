"""Narrative caption function validation, describe_share ceiling, and setup/callback threads.

Enforces caption craft (caption-craft.md):
- Every narrative caption (caption_text_override with no spoken line) must declare its caption_function.
- describe_share ceiling (50% max across non-message narrative captions).
- Thread pairs: setup must pair with a later callback; callback must pair with an earlier setup.
- caption_pairs_with must point to an existing shot with a caption.
- Voiced shots whose caption is a subset of spoken text should not declare caption_function (subtitles vs supers).
"""

from __future__ import annotations

import re

CAPTION_FUNCTIONS: tuple[str, ...] = (
    "premise",
    "want",
    "cost",
    "stakes",
    "time",
    "interior",
    "irony",
    "setup",
    "callback",
    "turn",
    "describe",
    "message",
)

#: describe_share ceiling calibrated in 2026-09-11 PRD §4.2:
#: 50% max describe captions across non-message narrative beats.
DESCRIBE_SHARE_MAX = 0.50
DESCRIBE_SHARE_MAX_PCT = 50


def _words(text: str) -> set[str]:
    return set(re.findall(r"\b\w+\b", text.lower()))


def _check_pairs_with_validity(
    pairs_with: int | None,
    shots: list[dict],
    prefix: str,
    errors: list[str],
) -> None:
    if pairs_with is None:
        return
    if not isinstance(pairs_with, int) or pairs_with < 1 or pairs_with > len(shots):
        errors.append(
            f"{prefix} caption_pairs_with={pairs_with!r} names a shot that does not exist "
            f"(film has {len(shots)} shots)"
        )
        return
    target = shots[pairs_with - 1]
    target_has_caption = bool(
        str(target.get("caption_text_override", "") or "").strip()
        or str(target.get("spoken", "") or "").strip()
    )
    if not target_has_caption:
        errors.append(
            f"{prefix} caption_pairs_with={pairs_with} names shot[{pairs_with}] "
            "which has no caption"
        )


def _check_shot_threads(
    i: int,
    fn: str | None,
    pairs_with: int | None,
    shots: list[dict],
    prefix: str,
    errors: list[str],
) -> None:
    if fn == "setup":
        if pairs_with is not None:
            if pairs_with <= i:
                errors.append(
                    f"{prefix} declares caption_function='setup' with "
                    f"caption_pairs_with={pairs_with}, but setup must point to a later shot (> {i})"
                )
            elif 1 <= pairs_with <= len(shots):
                target = shots[pairs_with - 1]
                if target.get("caption_function") != "callback":
                    errors.append(
                        f"{prefix} declares caption_function='setup' paired with shot[{pairs_with}], "
                        f"but shot[{pairs_with}] does not declare caption_function='callback'"
                    )
        else:
            has_callback = any(
                s.get("caption_function") == "callback" and s.get("caption_pairs_with") == i
                for s in shots[i:]
                if isinstance(s, dict)
            )
            if not has_callback:
                errors.append(
                    f"{prefix} declares caption_function='setup' but no later shot declares "
                    "caption_function='callback' paired with it"
                )
    elif fn == "callback":
        if pairs_with is None:
            errors.append(
                f"{prefix} declares caption_function='callback' but caption_pairs_with is missing "
                "— a callback must name its setup shot"
            )
        elif pairs_with >= i:
            errors.append(
                f"{prefix} declares caption_function='callback' with "
                f"caption_pairs_with={pairs_with}, but callback must point to an earlier shot (< {i})"
            )
        elif 1 <= pairs_with <= len(shots):
            target = shots[pairs_with - 1]
            if target.get("caption_function") != "setup":
                errors.append(
                    f"{prefix} declares caption_function='callback' paired with shot[{pairs_with}], "
                    f"but shot[{pairs_with}] does not declare caption_function='setup'"
                )


def _lint_caption(shots: list[dict], errors: list[str], warnings: list[str]) -> None:
    """Check narrative caption functions, describe_share ceiling, and setup/callback threads."""
    narrative_shots: list[dict] = []

    for i, shot in enumerate(shots, 1):
        if not isinstance(shot, dict):
            continue
        prefix = f"shot[{i}]"
        spoken = str(shot.get("spoken", "") or "").strip()
        override = str(shot.get("caption_text_override", "") or "").strip()
        fn = shot.get("caption_function")
        pairs_with = shot.get("caption_pairs_with")

        # 1. Scoped to narrative captions: shot with caption_text_override and no spoken line
        if override and not spoken:
            narrative_shots.append(shot)
            if not fn or not str(fn).strip():
                errors.append(
                    f"{prefix} has a narrative caption ({override!r}) but no `caption_function` "
                    f"— declare its function ({', '.join(CAPTION_FUNCTIONS)})"
                )
            elif fn not in CAPTION_FUNCTIONS:
                errors.append(
                    f"{prefix} caption_function {fn!r} is not one of {sorted(CAPTION_FUNCTIONS)}"
                )

        # 2. Voiced shot with subtitle labeled as super
        if spoken and fn:
            caption_text = override or spoken
            caption_words = _words(caption_text)
            spoken_words = _words(spoken)
            if caption_words and caption_words.issubset(spoken_words):
                warnings.append(
                    f"{prefix} is a voiced shot whose caption is a subset of spoken text, but "
                    f"declares caption_function={fn!r} — subtitles should not be labeled as "
                    "narrative captions"
                )

        # 3. caption_pairs_with validity
        _check_pairs_with_validity(pairs_with, shots, prefix, errors)

        # 4 & 5. Thread logic: setup and callback
        _check_shot_threads(i, fn, pairs_with, shots, prefix, errors)

    # 6. describe_share ceiling check across narrative shots (excluding message beats)
    non_message = [s for s in narrative_shots if s.get("caption_function") != "message"]
    if non_message:
        describe_count = sum(1 for s in non_message if s.get("caption_function") == "describe")
        describe_share = describe_count / len(non_message)
        if describe_share > DESCRIBE_SHARE_MAX:
            warnings.append(
                f"describe_share is {describe_share:.0%} ({describe_count}/{len(non_message)}) "
                f"— exceeds the {DESCRIBE_SHARE_MAX_PCT}% ceiling"
            )
