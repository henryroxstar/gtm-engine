from __future__ import annotations

import re
import statistics
from pathlib import Path
from typing import Any

from ..paths import resolve_content_root
from .script import _find_script
from .sources import _load_text

#: Words a person actually starts a spoken sentence with. Written prose drops these because a
#: reader can see the paragraph break; a listener cannot, so a script with none of them reads as a
#: string of disconnected aphorisms rather than someone talking.
_CONNECTIVES = frozenset(
    {
        "and",
        "so",
        "but",
        "because",
        "look",
        "now",
        "then",
        "which",
        "or",
        "anyway",
        "meanwhile",
        "except",
        "plus",
        "still",
        "yet",
        "though",
    }
)

#: Sentence openers whose repetition is ordinary speech rather than a rhetorical mirror. A person
#: really does say "It found a way. It cancelled someone else's spot." — that is not the tell.
_NATURAL_REPEAT_OPENERS = frozenset(
    {"it", "i", "you", "we", "they", "he", "she", "there", "and", "so", "but", "then"}
)

#: Minimum share of spoken sentences (after the cold open) that should open with a connective.
_MIN_CONNECTIVE_SHARE = 0.25

#: Sentence-length standard deviation, in words, below which the script reads as metronomic —
#: every line the same size, which is how a list of slogans sounds.
_MIN_SENTENCE_LEN_STDEV = 2.5

#: Below this many spoken sentences the statistical tells are noise, not signal.
_MIN_SENTENCES_FOR_REGISTER = 5

_SPOKEN_RE = re.compile(r"\*\*\[SPOKEN\]\*\*\s*[\"“](.+?)[\"”]", re.DOTALL)


def extract_spoken(text: str) -> list[str]:
    """Every ``[SPOKEN]`` line in a script, in order, quotes stripped."""
    return [re.sub(r"\s+", " ", m.group(1)).strip() for m in _SPOKEN_RE.finditer(text)]


def _sentences(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        for part in re.split(r"(?<=[.!?])\s+", line):
            part = part.strip()
            if part:
                out.append(part)
    return out


def register_findings(spoken_lines: list[str]) -> tuple[list[str], dict[str, Any]]:
    """Countable tells that a spoken script was written rather than spoken.

    Mechanises three of the four tells in ``video-script``'s Register section. Advisory by design
    — register is a judgement and a linter that blocked on it would be wrong more often than the
    writer. It exists so the failure is *visible* rather than silent, because the 2026-08-18
    script cleared every existing gate (Part A 11/14, claims 8/8) and still landed as
    "too polished, doesn't feel authentic".

    The fourth tell — slogan endings — is deliberately not mechanised: no counter distinguishes a
    tagline from a plain strong closing line, and a bad proxy would push writers toward weak
    endings to satisfy it.
    """
    sentences = _sentences(spoken_lines)
    stats: dict[str, Any] = {"sentences": len(sentences)}
    if len(sentences) < _MIN_SENTENCES_FOR_REGISTER:
        stats["skipped"] = "too few sentences for the statistical tells to mean anything"
        return [], stats

    findings: list[str] = []

    # 1. Parallel construction — consecutive sentences opening on the same word.
    #
    # Matched on the FIRST word, not the first two: the canonical tell is
    # "That's not a gym problem. / That's every system your AI touches." — same opener, mirrored
    # shape, second word deliberately different. A two-word rule misses exactly the case this
    # check exists for.
    #
    # Personal pronouns are excluded because repeating them is ordinary speech, not a cadence
    # ("It found a way. It cancelled someone else's spot." is fine). Demonstratives are what turn
    # a repetition into an advertisement.
    mirrors: list[str] = []
    for a, b in zip(sentences, sentences[1:], strict=False):
        wa = re.findall(r"[a-z']+", a.lower())[:1]
        wb = re.findall(r"[a-z']+", b.lower())[:1]
        if wa and wa == wb and wa[0] not in _NATURAL_REPEAT_OPENERS:
            mirrors.append(f"{a!r} / {b!r}")
    stats["parallel_openings"] = len(mirrors)
    if mirrors:
        findings.append(
            f"{len(mirrors)} parallel construction(s) — consecutive sentences opening on the same "
            f"word, which is a copywriter's cadence a listener hears as an ad: {mirrors[0]}. "
            "Break the mirror and use the connective a person would actually say."
        )

    # 2. Connective tissue — the cold open is exempt, it earns its abruptness.
    body = sentences[1:]
    with_conn = sum(
        1 for s in body if (re.findall(r"[a-z']+", s.lower()) or [""])[0] in _CONNECTIVES
    )
    share = with_conn / len(body) if body else 0.0
    stats["connective_share"] = round(share, 3)
    if share < _MIN_CONNECTIVE_SHARE:
        findings.append(
            f"only {with_conn}/{len(body)} sentences after the cold open ({share:.0%}) open with a "
            f"connective (and/so/but/look/…), under the {_MIN_CONNECTIVE_SHARE:.0%} floor — "
            "written prose drops these because a reader sees the paragraph break; a listener "
            "cannot, so this reads as disconnected aphorisms"
        )

    # 3. Metronomic length.
    lengths = [len(s.split()) for s in sentences]
    stdev = statistics.pstdev(lengths) if len(lengths) > 1 else 0.0
    stats["sentence_len_mean"] = round(statistics.fmean(lengths), 2)
    stats["sentence_len_stdev"] = round(stdev, 2)
    if stdev < _MIN_SENTENCE_LEN_STDEV:
        findings.append(
            f"sentence length is metronomic (mean {statistics.fmean(lengths):.1f} words, stdev "
            f"{stdev:.2f} < {_MIN_SENTENCE_LEN_STDEV}) — real speech varies hard; a run of "
            "same-length lines sounds recited"
        )

    return findings, stats


def register_check(profile: str, item_id: str, content_root: Path | None = None) -> dict[str, Any]:
    """Advisory register report for a script's spoken lines. Never blocks — see
    :func:`register_findings`. ``proceed`` is always True unless the script cannot be found."""
    content_root = content_root or resolve_content_root()
    script_path = _find_script(content_root, profile, item_id)
    if script_path is None:
        return {
            "proceed": False,
            "blocking": [f"no script in content/{profile}/scripts/ carries source_item: {item_id}"],
            "warnings": [],
            "checks": {},
        }

    spoken = extract_spoken(_load_text(script_path) or "")
    if not spoken:
        return {
            "proceed": True,
            "blocking": [],
            "warnings": [f"{script_path.name}: no [SPOKEN] lines found — nothing to read aloud"],
            "checks": {"script": script_path.name, "spoken_lines": 0},
        }

    findings, stats = register_findings(spoken)
    return {
        "proceed": True,
        "blocking": [],
        "warnings": findings,
        "checks": {"script": script_path.name, "spoken_lines": len(spoken), **stats},
    }
