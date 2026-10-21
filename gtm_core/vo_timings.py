"""Turn a TTS vendor's word timestamps into cue WINDOWS a renderer can use.

WHAT THIS EXISTS FOR. `render_checkpoint_flow_frames` carried sixteen timing fractions as local
variables, hand-derived once from the word timestamps of one 26.59s narration and copied into the
function body by a person reading them off a screen. The comment above them said "Re-time these if
that VO is ever re-cut" — which is a hope, not a mechanism. Nothing would have complained; the
card would simply have desynchronised from the voice, silently, exactly the way it did on
2026-08-30 when the gateway dropped in at 0.38 under a voice that had named it at 0.11.

The vendor already returns those timestamps. HeyGen's `create_speech` hands back
`word_timestamps` alongside the audio, for zero extra credits, and the pipeline threw them away:
only the `.wav` was fetched. So the fix starts by keeping them, and ends with the scene reading
them instead of literals.

THE SEAM. `create_speech` is an MCP tool, so only the brain can call it — this module never
touches a vendor. The brain writes the response verbatim to `<id>.words.raw.json` (untrusted, as
all tool output is), and `ingest` validates and normalises it into `<id>.words.json`, which is
this repo's shape and the only file a renderer reads.

It also never shells out. The one impure thing it needs — the audio's real duration — is borrowed
LAZILY from :mod:`gtm_core.video_finish`, which is one of the three modules allowed to execute the
media binaries. This is not one of them, and should not become one.

`WordTiming` is NOT redefined here; :mod:`gtm_core.word_timing` owns it and
`gtm_core.captions.split_screens_from_word_timings` already consumes it.

CLI::

    python -m gtm_core.vo_timings ingest --raw <raw.json> --audio <vo.wav> [--json]
    python -m gtm_core.vo_timings map   --words <sidecar.json> --scene checkpoint-flow [--json]
    python -m gtm_core.vo_timings check --words <sidecar.json> --scene checkpoint-flow
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .word_timing import WordTiming

#: Bumped when the sidecar's shape changes in a way a reader must notice.
WORDS_SCHEMA_VERSION = 1
WORDS_SIDECAR_SUFFIX = ".words.json"
RAW_SIDECAR_SUFFIX = ".words.raw.json"

#: How far past the audio's measured end a vendor's last word may claim to run. Vendors round;
#: this is the rounding, not a licence for a different cut.
_END_TOLERANCE_S = 0.05


class VoTimingError(ValueError):
    """A vendor payload and its audio disagree, or a sidecar is malformed."""


class PhraseNotFound(VoTimingError):
    """A cue phrase does not occur in this VO.

    Its own class because the correct response is specific: re-time the cue spec against the VO
    that was actually cut, or re-cut the VO. It is never "spread the windows evenly" — that is
    the defect the whole module exists to remove.
    """


def words_sidecar_path(audio_path: Path) -> Path:
    """``audio/vo/h15.wav`` -> ``audio/vo/h15.words.json``. One rule in one place, so no caller
    reconstructs the convention and gets it subtly different."""
    return audio_path.with_suffix("").with_name(audio_path.stem + WORDS_SIDECAR_SUFFIX)


# ── normalising a vendor payload ─────────────────────────────────────────────────────────

_WORD_KEYS = ("word", "text", "token")
_START_KEYS = ("start", "start_s", "start_time", "startTime", "begin")
_END_KEYS = ("end", "end_s", "end_time", "endTime", "stop")


def _first(d: dict, keys: Sequence[str]):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _word_list(payload: object) -> list[dict]:
    """Find the word array in whichever envelope the vendor used."""
    if isinstance(payload, list):
        return [w for w in payload if isinstance(w, dict)]
    if isinstance(payload, dict):
        for container in (payload, payload.get("data")):
            if isinstance(container, dict):
                for key in ("word_timestamps", "wordTimestamps", "words", "word_timing"):
                    value = container.get(key)
                    if isinstance(value, list):
                        return [w for w in value if isinstance(w, dict)]
    raise VoTimingError(
        "could not find a word-timestamp array in this payload. Expected a bare list, "
        '{"word_timestamps": [...]}, or {"data": {"word_timestamps": [...]}}. '
        "Write the tool's response VERBATIM — reshaping it by hand is how the shape stops "
        "matching what the vendor actually sends."
    )


def normalize_vendor_words(
    payload: object, *, audio_duration_s: float
) -> tuple[list[WordTiming], dict]:
    """Vendor payload -> validated ``WordTiming``s plus a provenance record.

    UNITS ARE SNIFFED, THEN CHECKED — never guessed. Some vendors emit milliseconds. If the last
    word ends beyond ten times the audio's length the payload is milliseconds and is rescaled; if
    it STILL overruns after rescaling, this refuses rather than picking whichever reading is less
    embarrassing. A silently mis-scaled timeline produces cue fractions that are all wrong by the
    same factor, which looks like a design choice rather than a bug.
    """
    raw = _word_list(payload)
    if not raw:
        raise VoTimingError("the vendor payload contains no words")
    if audio_duration_s <= 0:
        raise VoTimingError(f"audio_duration_s must be positive, got {audio_duration_s!r}")

    parsed: list[tuple[str, float, float]] = []
    for i, item in enumerate(raw):
        word = _first(item, _WORD_KEYS)
        start = _first(item, _START_KEYS)
        end = _first(item, _END_KEYS)
        if word is None or start is None or end is None:
            raise VoTimingError(
                f"word[{i}] is missing a word/start/end field (has {sorted(item)}). "
                f"Recognised keys: word={_WORD_KEYS}, start={_START_KEYS}, end={_END_KEYS}."
            )
        try:
            parsed.append((str(word), float(start), float(end)))
        except (TypeError, ValueError) as exc:
            raise VoTimingError(f"word[{i}] has non-numeric timings: {item!r}") from exc

    units = "s"
    last_end = max(e for _, _, e in parsed)
    if last_end > audio_duration_s * 10:
        units = "ms->s"
        parsed = [(w, s / 1000.0, e / 1000.0) for w, s, e in parsed]
        last_end = max(e for _, _, e in parsed)

    if last_end > audio_duration_s + _END_TOLERANCE_S:
        raise VoTimingError(
            f"the last word ends at {last_end:.3f}s but the audio measures "
            f"{audio_duration_s:.3f}s (units read as {units!r}). These are not the same cut — "
            "re-ingest the response that belongs to this wav rather than rescaling it to fit."
        )

    prev_start = float("-inf")
    words: list[WordTiming] = []
    for i, (word, start, end) in enumerate(parsed):
        if end < start:
            raise VoTimingError(f"word[{i}] {word!r} ends ({end}) before it starts ({start})")
        if start < prev_start:
            raise VoTimingError(
                f"word[{i}] {word!r} starts at {start} after a word that started at {prev_start} "
                "— the payload is not in speech order, so no phrase match over it is meaningful"
            )
        prev_start = start
        words.append(WordTiming(word=word, start_s=start, end_s=end))

    return words, {"units": units, "word_count": len(words), "vendor_last_end_s": last_end}


def write_words_sidecar(
    audio_path: Path,
    words: Sequence[WordTiming],
    *,
    audio_duration_s: float,
    source: str,
    provenance: dict,
) -> Path:
    out = words_sidecar_path(audio_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "schema_version": WORDS_SCHEMA_VERSION,
                "audio": audio_path.name,
                # MEASURED off the wav, never taken from the vendor: this is the denominator of
                # every fraction a scene will use, and a declared duration 30ms off shears every
                # cue on the card by the same amount.
                "audio_duration_s": round(audio_duration_s, 6),
                "source": source,
                "provenance": provenance,
                "words": [
                    {"word": w.word, "start_s": round(w.start_s, 6), "end_s": round(w.end_s, 6)}
                    for w in words
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return out


def read_words_sidecar(path: Path) -> tuple[list[WordTiming], float]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VoTimingError(f"could not read words sidecar {path}: {exc}") from exc
    raw = doc.get("words")
    duration = doc.get("audio_duration_s")
    if not isinstance(raw, list) or not raw or not isinstance(duration, (int, float)):
        raise VoTimingError(f"{path} is not a words sidecar (needs 'words' and 'audio_duration_s')")
    return (
        [
            WordTiming(word=str(w["word"]), start_s=float(w["start_s"]), end_s=float(w["end_s"]))
            for w in raw
        ],
        float(duration),
    )


# ── phrase -> window ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PhraseCue:
    """Which narration phrase drives which animation window.

    ``key`` names a WINDOW in a scene ("DROP"), not a sentence. The phrases belong beside the
    drawing they animate — the binding is a fact about the card — while the timings belong with
    the audio. That split is the whole design.
    """

    key: str
    phrase: str
    #: 1-based, for a phrase that recurs.
    occurrence: int = 1
    pad_before_s: float = 0.0
    pad_after_s: float = 0.0


@dataclass(frozen=True)
class CueWindow:
    key: str
    start_s: float
    end_s: float
    start_frac: float
    end_frac: float
    matched_words: tuple[str, ...]
    first_index: int
    last_index: int


_APOSTROPHES = {"’": "'", "ʼ": "'", "＇": "'", "`": "'", "´": "'"}
_DASHES = {"–": "-", "—": "-", "−": "-"}


def normalize_text(text: str) -> str:
    """Fold away everything that differs between how a phrase is written and how a vendor
    tokenised it: accents, case, curly quotes, dash flavours, and all punctuation."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(_APOSTROPHES.get(ch, _DASHES.get(ch, ch)) for ch in text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    return "".join(ch for ch in text if ch.isalnum() or ch in "'-")


def _char_stream(words: Sequence[WordTiming]) -> tuple[str, list[int], list[int], list[int]]:
    """Concatenated normalised characters, plus, per character, its word index; plus the stream
    offsets at which each word starts and ends.

    Matching on a character stream rather than a token sequence absorbs every real tokenisation
    difference at once — ``Nexus's`` as one token or as ``Nexus`` + ``'s``, punctuation attached
    to a word, a hyphenated word split in two. Because word boundaries vanish in the stream, the
    caller re-imposes them: a hit must begin at a word's first character and end at a word's last,
    so ``gateway`` cannot match inside ``gatewayed``.
    """
    stream_parts: list[str] = []
    owner: list[int] = []
    starts: list[int] = []
    ends: list[int] = []
    pos = 0
    for i, wt in enumerate(words):
        norm = normalize_text(wt.word)
        starts.append(pos)
        stream_parts.append(norm)
        owner.extend([i] * len(norm))
        pos += len(norm)
        ends.append(pos - 1)
    return "".join(stream_parts), owner, starts, ends


def phrase_hits(words: Sequence[WordTiming], phrase: str) -> list[tuple[int, int]]:
    """EVERY (first_word_index, last_word_index) this phrase matches, in speech order.

    Separate from :func:`match_phrase` because the COUNT is information the caller needs and a
    single index pair throws away: a phrase occurring twice resolves fine and silently, and
    silent ambiguity in a cue spec is how a card ends up animating to the wrong sentence.
    """
    needle = normalize_text(phrase)
    if not needle:
        raise VoTimingError(f"phrase {phrase!r} normalises to nothing")
    stream, owner, starts, ends = _char_stream(words)
    start_set, end_set = set(starts), set(ends)

    hits: list[tuple[int, int]] = []
    at = stream.find(needle)
    while at != -1:
        last = at + len(needle) - 1
        if at in start_set and last in end_set:
            hits.append((owner[at], owner[last]))
        at = stream.find(needle, at + 1)
    if not hits:
        raise PhraseNotFound(_no_match_message(words, phrase, stream, needle))
    return hits


def match_phrase(
    words: Sequence[WordTiming], phrase: str, *, occurrence: int = 1
) -> tuple[int, int]:
    """(first_word_index, last_word_index) for ``phrase``. Raises :class:`PhraseNotFound`."""
    hits = phrase_hits(words, phrase)
    if occurrence < 1 or occurrence > len(hits):
        raise PhraseNotFound(
            f"phrase {phrase!r} occurs {len(hits)} time(s) in this VO but occurrence "
            f"{occurrence} was asked for"
        )
    return hits[occurrence - 1]


def _no_match_message(words: Sequence[WordTiming], phrase: str, stream: str, needle: str) -> str:
    longest, at = "", -1
    for size in range(len(needle), 3, -1):
        for offset in range(0, len(needle) - size + 1):
            found = stream.find(needle[offset : offset + size])
            if found != -1:
                longest, at = needle[offset : offset + size], found
                break
        if longest:
            break
    _, owner, _, _ = _char_stream(words)
    near = ""
    if at != -1 and at < len(owner):
        w = words[owner[at]]
        near = f" Longest partial match: {longest!r} at word {owner[at]} ({w.start_s:.2f}s)."
    opening = " ".join(w.word for w in words[:12])
    return (
        f"phrase {phrase!r} does not occur in this VO ({len(words)} words)."
        f"{near} The VO opens: {opening!r}. Re-time the cue spec against the VO that was actually "
        "cut, or re-cut the VO — do NOT spread the windows evenly. An evenly-spread card "
        "desynchronises every element on it, which is the defect this parameter exists to fix."
    )


def cue_windows(
    words: Sequence[WordTiming], cues: Sequence[PhraseCue], *, total_s: float
) -> tuple[dict[str, CueWindow], list[str]]:
    """Resolve every cue to a window. Returns (windows, warnings).

    A warning is not a failure: a phrase that occurs more than once still resolves (occurrence 1
    is a defensible default), but the ambiguity is reported with each hit's timestamp. Ambiguity
    that is visible is survivable; ambiguity that is silent is not.
    """
    if total_s <= 0:
        raise VoTimingError(f"total_s must be positive, got {total_s!r}")
    out: dict[str, CueWindow] = {}
    warnings: list[str] = []
    for cue in cues:
        hits = phrase_hits(words, cue.phrase)
        if len(hits) > 1 and cue.occurrence == 1:
            where = ", ".join(f"{words[f].start_s:.2f}s" for f, _ in hits)
            warnings.append(
                f"cue {cue.key!r} phrase {cue.phrase!r} occurs {len(hits)} times in this VO "
                f"(at {where}); taking the first. Set occurrence= on the PhraseCue if a later "
                "one is meant — ambiguity that is visible is survivable, silent ambiguity is not."
            )
        first, last = match_phrase(words, cue.phrase, occurrence=cue.occurrence)
        start_s = max(0.0, words[first].start_s - cue.pad_before_s)
        end_s = min(total_s, words[last].end_s + cue.pad_after_s)
        if end_s <= start_s:
            raise VoTimingError(
                f"cue {cue.key!r} resolves to an empty window ({start_s:.3f}-{end_s:.3f}s) "
                f"against a {total_s:.3f}s VO"
            )
        out[cue.key] = CueWindow(
            key=cue.key,
            start_s=start_s,
            end_s=end_s,
            start_frac=start_s / total_s,
            end_frac=end_s / total_s,
            matched_words=tuple(w.word for w in words[first : last + 1]),
            first_index=first,
            last_index=last,
        )
    return out, warnings


def timing_map(
    words: Sequence[WordTiming], cues: Sequence[PhraseCue], *, total_s: float
) -> dict[str, tuple[float, float]]:
    """The shape a scene consumes: ``{cue_key: (start_frac, end_frac)}``.

    Discards :func:`cue_windows`' warnings — a scene has nowhere to put them. Use ``cue_windows``
    directly, or the ``map``/``check`` CLI verbs, when the ambiguity report matters.
    """
    windows, _ = cue_windows(words, cues, total_s=total_s)
    return {k: (v.start_frac, v.end_frac) for k, v in windows.items()}


# ── CLI ──────────────────────────────────────────────────────────────────────────────────


def _scene_cues(scene: str) -> tuple[PhraseCue, ...]:
    """Cue specs live with their scenes, so this looks them up rather than owning a copy."""
    from .screen_ui import SCENE_CUES

    if scene not in SCENE_CUES:
        raise VoTimingError(
            f"no cue spec for scene {scene!r} — known: {sorted(SCENE_CUES)}. A scene whose "
            "animation is not narration-cued has no timing map to build."
        )
    return SCENE_CUES[scene]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m gtm_core.vo_timings")
    sub = parser.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest", help="validate a vendor word-timestamp payload into a sidecar")
    ing.add_argument("--raw", type=Path, required=True, help="the vendor response, verbatim")
    ing.add_argument("--audio", type=Path, required=True, help="the .wav it belongs to")
    ing.add_argument("--source", default="create_speech")
    ing.add_argument("--json", action="store_true", dest="as_json")

    mp = sub.add_parser("map", help="cue phrases + measured words -> fractions for a scene")
    mp.add_argument("--words", type=Path, required=True)
    mp.add_argument("--scene", required=True)
    mp.add_argument("--json", action="store_true", dest="as_json")

    ck = sub.add_parser("check", help="exit 4 if any cue fails to match this VO")
    ck.add_argument("--words", type=Path, required=True)
    ck.add_argument("--scene", required=True)

    args = parser.parse_args(argv)

    if args.cmd == "ingest":
        from .video_finish import FfmpegUnavailable, _probe_duration

        try:
            payload = json.loads(args.raw.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"vo-timings: could not read --raw: {exc}", file=sys.stderr)
            return 2
        try:
            duration = _probe_duration(args.audio, stream_selector="a:0")
        except FfmpegUnavailable as exc:
            print(f"vo-timings: {exc}", file=sys.stderr)
            return 3
        try:
            words, provenance = normalize_vendor_words(payload, audio_duration_s=duration)
        except VoTimingError as exc:
            print(f"vo-timings: {exc}", file=sys.stderr)
            return 4
        out = write_words_sidecar(
            args.audio,
            words,
            audio_duration_s=duration,
            source=args.source,
            provenance=provenance,
        )
        if args.as_json:
            print(
                json.dumps(
                    {"out_path": str(out), "audio_duration_s": duration, **provenance}, indent=2
                )
            )
        else:
            print(
                f"vo-timings: wrote {out} ({provenance['word_count']} words, "
                f"{duration:.3f}s, units {provenance['units']})"
            )
        return 0

    if args.cmd in {"map", "check"}:
        try:
            words, duration = read_words_sidecar(args.words)
            cues = _scene_cues(args.scene)
            windows, warnings = cue_windows(words, cues, total_s=duration)
        except VoTimingError as exc:
            print(f"vo-timings: {exc}", file=sys.stderr)
            return 4
        for note in warnings:
            print(f"vo-timings: warning: {note}", file=sys.stderr)
        if args.cmd == "check":
            print(f"vo-timings: all {len(windows)} cues matched {args.words}")
            return 0
        if args.as_json:
            print(
                json.dumps(
                    {
                        k: {
                            "start_s": round(v.start_s, 4),
                            "end_s": round(v.end_s, 4),
                            "start_frac": round(v.start_frac, 4),
                            "end_frac": round(v.end_frac, 4),
                            "matched": list(v.matched_words),
                        }
                        for k, v in windows.items()
                    },
                    indent=2,
                )
            )
        else:
            for k, v in windows.items():
                print(
                    f"  {k:8} {v.start_frac:.3f}-{v.end_frac:.3f}  "
                    f"({v.start_s:.2f}-{v.end_s:.2f}s)  {' '.join(v.matched_words)}"
                )
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
