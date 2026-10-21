"""The voice-led timeline: the VOICE sets the clock and the picture is fitted to it.

Lines run back to back with a fixed gap (plus any ``pause_after_s`` a shot asks for, e.g. a beat
after a payoff). Each line's shot ideally starts ``lead_s`` before its first word and runs to the
next line's cut. A footage shot can never run past its take, so its shortfall moves outward to
the nearest shot that can grow: a cutaway up to its own source length, a graphic up to
``graphic_grow_max_s`` extra, other footage with slack. A graphic is a boundary — the search does
not cross it. A ``fixed`` cutaway takes its exact length from the nearest graphics on each side.

Ported from the 2026-10-20 scratch solver that cut the Boardroom Paradox film, unchanged in
behaviour, so a film built with it reproduces that cut.
"""

from __future__ import annotations

from typing import Any

from .spec import Film

_CODE = {"graphic": "G", "footage": "F", "cutaway": "C", "fixed": "X", "split": "S"}
#: A graphic a fixed cutaway borrows from is never squeezed below this.
GRAPHIC_FLOOR_S = 2.5


def _voice_clock(film: Film, ids: list[str], line_len: dict[str, float]) -> tuple[dict, float]:
    t = film.timing
    pause = {s["id"]: float(s.get("pause_after_s", 0.0)) for s in film.shots}
    onset: dict[str, float] = {}
    clock = t["start_s"]
    for sid in ids:
        if sid in line_len:
            onset[sid] = round(clock, 3)
            clock += line_len[sid] + t["gap_s"] + pause[sid]
    return onset, round(clock - t["gap_s"] + t["tail_s"], 3)


class _Fit:
    """Holds the per-shot lengths while shortfalls move between shots."""

    def __init__(self, film: Film, ids: list[str], dur: dict[str, float]) -> None:
        self.ids = ids
        self.dur = dur
        self.kind = {s["id"]: _CODE[s["kind"]] for s in film.shots}
        self.avail = {s["id"]: s.get("avail_s") for s in film.shots}
        default = film.timing["graphic_grow_max_s"]
        self.grow_max = {s["id"]: s.get("grow_max_s", default) for s in film.shots}
        self.pinned = {s["id"] for s in film.shots if s.get("dur_s") is not None}
        self.extra: dict[str, float] = {}

    def room(self, o: str) -> float:
        if o in self.pinned:
            return 0.0
        if self.kind[o] == "G":
            return self.grow_max[o] - self.extra.get(o, 0.0)
        if self.kind[o] == "X":
            return 0.0
        return self.avail[o] - self.dur[o]

    def grow(self, idx: int, amount: float, step: int) -> float:
        """Hand ``amount`` seconds outward from ``idx``; returns what is left over."""
        j = idx + step
        while 0 <= j < len(self.ids) and amount > 1e-6:
            o = self.ids[j]
            take = max(0.0, min(self.room(o), amount))
            self.dur[o] += take
            amount -= take
            if self.kind[o] == "G":  # a graphic is a run boundary
                self.extra[o] = self.extra.get(o, 0.0) + take
                break
            j += step
        return amount

    def pay_fixed(self, k: int) -> None:
        """A fixed cutaway takes its length from the nearest graphic on each side: half from the
        one before, the rest from the one after. A PINNED neighbour already paid its share — its
        length was set with the cutaway in place — so the search never reaches past it."""
        need = self.avail[self.ids[k]]
        for step in (-1, 1):
            share = need / 2 if step == -1 else need
            j = k + step
            while 0 <= j < len(self.ids) and self.kind[self.ids[j]] != "G":
                j += step
            if not 0 <= j < len(self.ids):
                continue
            g = self.ids[j]
            give = (
                share if g in self.pinned else max(0.0, min(share, self.dur[g] - GRAPHIC_FLOOR_S))
            )
            if g not in self.pinned:
                self.dur[g] -= give
            need -= give

    def cap_footage(self, k: int) -> float:
        i = self.ids[k]
        excess = self.dur[i] - self.avail[i]
        self.dur[i] = self.avail[i]
        return self.grow(k, self.grow(k, excess, +1), -1)


def plan(film: Film, line_len: dict[str, float]) -> dict[str, Any]:
    """Solve the timeline. ``line_len`` maps shot id -> voiced span (seconds) of its line."""
    missing = [s["id"] for s in film.shots if s.get("turns") and s["id"] not in line_len]
    if missing:
        raise ValueError(f"no voiced line for {', '.join(missing)}: run voice-prep first")
    ids = [s["id"] for s in film.shots]
    onset, voice_total = _voice_clock(film, ids, line_len)

    lead = film.timing["lead_s"]
    line_ids = [i for i in ids if i in onset]
    dur = dict.fromkeys(ids, 0.0)
    for a, b in zip(line_ids, line_ids[1:], strict=False):
        dur[a] = (onset[b] - lead) - (onset[a] - lead if a != ids[0] else 0.0)
    dur[line_ids[-1]] = voice_total - (onset[line_ids[-1]] - lead)
    fit = _Fit(film, ids, dur)
    for s in film.shots:
        if s.get("dur_s") is not None:  # a deliberate edit the operator approved; never re-solved
            dur[s["id"]] = float(s["dur_s"])
        elif fit.kind[s["id"]] == "X":
            dur[s["id"]] = fit.avail[s["id"]]
    for k, i in enumerate(ids):
        if fit.kind[i] == "X" and i not in fit.pinned:
            fit.pay_fixed(k)
    unresolved: dict[str, float] = {}
    for k, i in enumerate(ids):
        if fit.kind[i] in ("F", "S") and i not in fit.pinned and dur[i] > fit.avail[i]:
            left = fit.cap_footage(k)
            if left > 1e-3:
                unresolved[i] = round(left, 3)

    shots, cursor = [], 0.0
    for s in film.shots:
        i = s["id"]
        shots.append(
            {
                "id": i,
                "kind": fit.kind[i],
                "source": s.get("source"),
                "offset": s.get("offset_s", 0),
                "start": round(cursor, 3),
                "dur": round(dur[i], 3),
                "line_onset": onset.get(i),
                "line_len": line_len.get(i),
            }
        )
        cursor += dur[i]
    return {
        "total_s": round(cursor, 3),
        "voice_total_s": voice_total,
        "shots": shots,
        "unresolved_s": unresolved,
        "unused": [p["id"] for p in shots if p["dur"] < 0.05],
        # A line whose first word plays before its own shot starts, or after it ends. Sometimes
        # deliberate (a voice that leads its picture); listed so it is never accidental.
        "off_shot_lines": {
            p["id"]: round(p["line_onset"] - p["start"], 3)
            for p in shots
            if p["line_onset"] is not None
            and not p["start"] <= p["line_onset"] < p["start"] + p["dur"]
        },
    }
