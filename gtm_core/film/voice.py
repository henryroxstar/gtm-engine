"""Voice: the text a TTS engine receives, which shots need (re)voicing, and voice prep.

THE LEXICON IS APPLIED HERE, IN CODE, ON EVERY ENGINE. On 2026-10-20 a film was re-voiced on a
new engine with the brand name in its plain spelling: the brand kit's ``[pronunciation]`` table
existed, but applying it was a step a session had to remember, and it did not. ``tts_turns`` is
now the only sanctioned source of TTS text — it respells every lexicon term — and ``check_film``
refuses a film whose script carries a respelling (that would put the sound on a caption).

Which shots need voicing is decided by a hash of what would be sent (speaker, voice id, respelled
text). Change a word and only that shot shows up; ``prep`` refuses to treat an old take as the
voice of new words.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from .spec import Film, FilmError


def lexicon(kit: dict[str, Any]) -> dict[str, str]:
    table = kit.get("pronunciation") or {}
    return {k: v for k, v in table.items() if isinstance(v, str) and v}


def respell(text: str, lex: dict[str, str]) -> str:
    for term, sound in lex.items():
        text = re.sub(rf"\b{re.escape(term)}\b", sound, text, flags=re.IGNORECASE)
    return text


def check_film(film: Film, lex: dict[str, str]) -> list[str]:
    """Respellings in the script itself — they would reach the captions."""
    problems = []
    for shot in film.shots:
        for _speaker, text in shot.get("turns") or []:
            for term, sound in lex.items():
                if re.search(rf"\b{re.escape(sound)}\b", text, re.IGNORECASE):
                    problems.append(
                        f"{shot['id']}: the script says {sound!r}; write {term!r} — the "
                        "respelling is applied for the voice only"
                    )
        cap = shot.get("caption")
        for sound in lex.values():
            if cap and re.search(rf"\b{re.escape(sound)}\b", cap, re.IGNORECASE):
                problems.append(f"{shot['id']}: caption shows the respelling {sound!r}")
    return problems


def tts_turns(film: Film, shot: dict[str, Any], lex: dict[str, str]) -> list[dict[str, str]]:
    voices = film.data["voices"]
    return [
        {"speaker": sp, "voice_id": voices[sp]["id"], "text": respell(text, lex)}
        for sp, text in shot.get("turns") or []
    ]


def _hash(turns: list[dict[str, str]]) -> str:
    return hashlib.sha256(json.dumps(turns, sort_keys=True).encode()).hexdigest()[:16]


def _paths(film: Film) -> tuple[Path, Path, Path]:
    vdir = film.out / "voice"
    return vdir / "requested.json", vdir / "voiced.json", film.root / film.data["voice"]["raw_dir"]


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {}


def status(film: Film, lex: dict[str, str]) -> dict[str, Any]:
    _req, voiced_p, _raw = _paths(film)
    voiced = _read(voiced_p)
    stale = []
    for shot in film.shots:
        if not shot.get("turns"):
            continue
        h = _hash(tts_turns(film, shot, lex))
        if voiced.get(shot["id"], {}).get("hash") != h:
            stale.append(shot["id"])
    return {"needs_voice": stale, "voiced": len(voiced)}


def request(film: Film, lex: dict[str, str], *, only: list[str] | None = None) -> dict[str, Any]:
    """The TTS jobs to send, batched; records what was asked so ``prep`` can match takes to it."""
    req_p, _voiced, raw_dir = _paths(film)
    ids = only or status(film, lex)["needs_voice"]
    req = _read(req_p)
    now = time.time()
    jobs = []
    for sid in ids:
        turns = tts_turns(film, film.shot(sid), lex)
        req[sid] = {"hash": _hash(turns), "turns": turns, "requested_at": now}
        jobs.append({"shot_id": sid, "turns": turns, "save_as": str(raw_dir / f"{sid}.mp3")})
    req_p.parent.mkdir(parents=True, exist_ok=True)
    req_p.write_text(json.dumps(req, indent=2, ensure_ascii=False) + "\n")
    size = int(film.data["voice"].get("batch_size", 6))
    return {
        "engine": film.data["voice"].get("engine"),
        "batches": [jobs[i : i + size] for i in range(0, len(jobs), size)],
        "note": "Send one batch at a time. A rate-limit (429) reply: wait ~20 s and resend only "
        "the refused jobs, one per call. Save each take to its save_as path, then run voice-prep.",
    }


def prep(film: Film, lex: dict[str, str], *, adopt: bool = False) -> dict[str, Any]:
    """Prep every take whose words are new or whose raw file changed. Returns the line lengths.

    ``adopt`` accepts raw takes recorded before this film.json existed as the voice of the
    CURRENT words — for migrating a film, never for a re-voice.
    """
    req_p, voiced_p, raw_dir = _paths(film)
    req, voiced = _read(req_p), _read(voiced_p)
    settings = None
    refused, done = [], []
    for shot in film.shots:
        if not shot.get("turns"):
            continue
        sid = shot["id"]
        turns = tts_turns(film, shot, lex)
        h, raw = _hash(turns), raw_dir / f"{sid}.mp3"
        wav = film.out / "voice" / f"{sid}.wav"
        if not raw.exists():
            refused.append(f"{sid}: no take at {raw}")
            continue
        raw_mtime = raw.stat().st_mtime
        rec = voiced.get(sid, {})
        if rec.get("hash") == h and rec.get("raw_mtime") == raw_mtime and wav.exists():
            continue
        asked = req.get(sid, {})
        fresh = asked.get("hash") == h and raw_mtime >= asked.get("requested_at", float("inf"))
        if rec.get("hash") != h and not (fresh or adopt):
            refused.append(f"{sid}: its words changed since this take was made; voice it again")
            continue
        try:
            from ..video_finish.voice_prep import prep_line, preset
        except ImportError as exc:
            raise FilmError(
                "voice-prep needs gtm_core.video_finish, which this build does not ship"
            ) from exc
        if settings is None:
            settings = preset(
                film.data["voice"].get("engine", "default"), film.data["voice"].get("prep")
            )
        info = prep_line(raw, wav, settings=settings)
        voiced[sid] = {"hash": h, "raw_mtime": raw_mtime, "tts_turns": turns, **info}
        done.append(sid)
    voiced_p.parent.mkdir(parents=True, exist_ok=True)
    voiced_p.write_text(json.dumps(voiced, indent=2, ensure_ascii=False) + "\n")
    if refused:
        raise FilmError("voice-prep refused:\n  - " + "\n  - ".join(refused))
    return {"prepped": done, "line_len": line_lengths(film)}


def line_lengths(film: Film) -> dict[str, float]:
    voiced = _read(_paths(film)[1])
    return {sid: rec["raw_speech_s"] for sid, rec in voiced.items()}
