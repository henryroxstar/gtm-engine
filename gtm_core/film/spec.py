"""Load and check ``film.json`` — the single source for one narrated film.

Shape (paths are relative to the folder holding ``film.json``)::

    {
      "slug": "2026-10-18-example", "profile": "<tenant>", "product": null, "ratio": "16:9",
      "out_dir": "film-build",
      "voices": {"narrator": {"id": "<voice id>", "name": "<display name>"}},
      "voice": {"engine": "elevenlabs_v4", "raw_dir": "audio/raw", "batch_size": 6, "prep": {}},
      "timing": {"start_s": 0.15, "gap_s": 0.35, "lead_s": 0.12, "tail_s": 1.2,
                 "graphic_grow_max_s": 3.5},
      "captions": {"max_words": 5, "max_chars": 30, "min_screen_s": 0.4, "emphasis": []},
      "disclosure_shot": "s14",
      "graphics": {"fps": 25, "product": "...", "title_prefix": "...", "brief": {...}},
      "shot_list": {...top-level fields copied onto the derived shot list...},
      "shots": [{
        "id": "s01", "kind": "graphic|footage|cutaway|fixed|split",
        "turns": [["narrator", "What is said, in its TRUE spelling."]],
        "caption": "optional; defaults to the spoken text; null = no caption",
        "pause_after_s": 0.0,
        "source": "takes/s01.mp4", "offset_s": 0.0, "avail_s": 5.0, "conform": false,
        "graphic": {...one Mission Control scene...} | "graphic_doc": "path/to/x.mc.json",
        "split": {"left": "...", "right": "...", "left_x_frac": 0.5, "right_x_frac": 0.5},
        "overlay": [...video_finish overlay entries; a negative end_s counts from the shot end...],
        "meta": {...shot-list fields carried verbatim: role, camera, visual, ...}
      }],
      "sfx": [{"label": "...", "file": "sfx/x.mp3", "shot": "s01", "offset_s": 0.4,
               "word": 12, "gain_db": -12, "trim_s": "auto"}],
      "music": {"path": "music/bed.wav", "gain_db": -18, "loop_from_s": 55, "body_end_s": "auto",
                "duck_ratio": 4, "fade_out_s": 3},
      "finish": {"crf": 12, "loudness_target": -14.0}
    }

A shot's ``dur_s`` pins its length: the solver leaves it alone (a deliberate edit, e.g. a voice
that leads its picture). Kinds decide how the timeline may stretch every other shot:

* ``graphic`` — rendered to the planned length; may grow up to ``graphic_grow_max_s`` (a shot's
  own ``grow_max_s`` overrides it, for a card meant to hold).
* ``footage`` — cut from a take; never longer than ``avail_s`` (the shortfall moves on).
* ``cutaway`` — footage with no line of its own that absorbs a neighbour's shortfall.
* ``fixed`` — a cutaway held at exactly ``avail_s``, paid for by the nearest graphics.
* ``split`` — two takes side by side (``split``), capped like footage.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

KINDS = ("graphic", "footage", "cutaway", "fixed", "split")
_TIMING_DEFAULTS = {
    "start_s": 0.15,
    "gap_s": 0.35,
    "lead_s": 0.12,
    "tail_s": 1.2,
    "graphic_grow_max_s": 3.5,
}
_CAPTION_DEFAULTS = {"max_words": 5, "max_chars": 30, "min_screen_s": 0.4, "emphasis": []}


class FilmError(ValueError):
    """A film.json that cannot be built, said in words the operator can act on."""


@dataclass
class Film:
    path: Path
    data: dict[str, Any]
    timing: dict[str, float] = field(default_factory=dict)
    captions: dict[str, Any] = field(default_factory=dict)

    @property
    def root(self) -> Path:
        return self.path.parent

    @property
    def out(self) -> Path:
        return self.root / self.data.get("out_dir", "film-build")

    @property
    def shots(self) -> list[dict[str, Any]]:
        return self.data["shots"]

    @property
    def ratio(self) -> str:
        return self.data.get("ratio", "16:9")

    @property
    def ratio_tag(self) -> str:
        return self.ratio.replace(":", "x")

    def shot(self, sid: str) -> dict[str, Any]:
        for s in self.shots:
            if s["id"] == sid:
                return s
        raise FilmError(f"no shot {sid!r} in {self.path}")

    def resolve(self, rel: str) -> Path:
        return (self.root / rel).resolve()


def spoken_text(shot: dict[str, Any]) -> str:
    """The shot's words in their TRUE spelling, turns joined — what captions default to."""
    return " ".join(text for _speaker, text in shot.get("turns") or [])


def caption_text(shot: dict[str, Any]) -> str | None:
    if "caption" in shot:
        return shot["caption"] or None
    return spoken_text(shot) or None


def load(path: Path) -> Film:
    data = json.loads(path.read_text())
    film = Film(
        path=path.resolve(),
        data=data,
        timing={**_TIMING_DEFAULTS, **data.get("timing", {})},
        captions={**_CAPTION_DEFAULTS, **data.get("captions", {})},
    )
    check(film)
    return film


def check(film: Film) -> None:
    """Refuse a film.json the build would trip over later, naming every problem at once."""
    problems: list[str] = []
    data = film.data
    for key in ("slug", "profile", "shots"):
        if key not in data:
            problems.append(f"missing top-level {key!r}")
    voices = data.get("voices", {})
    seen: set[str] = set()
    for s in data.get("shots", []):
        sid = s.get("id", "?")
        if sid in seen:
            problems.append(f"{sid}: duplicate shot id")
        seen.add(sid)
        problems.extend(_shot_problems(s, voices))
    shots = data.get("shots", [])
    if shots and shots[0].get("kind") not in ("graphic", "footage", "split"):
        problems.append("the first shot must carry the opening line (graphic, footage or split)")
    for cue in data.get("sfx", []):
        if cue.get("shot") not in seen:
            problems.append(f"sfx {cue.get('label', '?')!r}: unknown shot {cue.get('shot')!r}")
    if problems:
        raise FilmError(f"{film.path} cannot be built:\n  - " + "\n  - ".join(problems))


def _shot_problems(s: dict[str, Any], voices: dict[str, Any]) -> list[str]:
    sid, kind = s.get("id", "?"), s.get("kind")
    if kind not in KINDS:
        return [f"{sid}: kind {kind!r} is not one of {', '.join(KINDS)}"]
    problems = [
        f"{sid}: speaker {speaker!r} has no entry in 'voices'"
        for speaker, _text in s.get("turns") or []
        if speaker not in voices
    ]
    if kind in ("footage", "cutaway", "fixed") and not (s.get("source") and s.get("avail_s")):
        problems.append(f"{sid}: a {kind} shot needs 'source' and 'avail_s'")
    if kind == "split" and not (s.get("split") and s.get("avail_s")):
        problems.append(f"{sid}: a split shot needs 'split' and 'avail_s'")
    if kind == "graphic" and not (s.get("graphic") or s.get("graphic_doc") or s.get("file")):
        problems.append(f"{sid}: a graphic shot needs 'graphic', 'graphic_doc' or 'file'")
    if kind in ("cutaway", "fixed") and s.get("turns"):
        problems.append(f"{sid}: a {kind} carries no line; move its turns to a footage shot")
    return problems
