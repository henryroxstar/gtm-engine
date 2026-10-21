"""Caption screens timed to the VOICE, then mapped onto whichever shot is on screen.

A caption belongs to its line, not its shot: a line often starts in one shot and ends in the next
(a cutaway, a graphic that grew). So each line's caption is chunked into screens of at most
``max_words`` words / ``max_chars`` characters, each screen is timed by its share of the line's
words, and lands on the shot that holds its midpoint, in that shot's local time. A screen left
with less than ``min_screen_s`` on its host is dropped rather than flashed.
"""

from __future__ import annotations

from typing import Any

from .spec import Film, caption_text


def screens(
    text: str, t0: float, t1: float, *, max_words: int, max_chars: int
) -> list[tuple[str, float, float]]:
    words = text.split()
    chunks: list[list[str]] = []
    cur: list[str] = []
    for w in words:
        if cur and (len(cur) >= max_words or len(" ".join([*cur, w])) > max_chars):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    n, out, k = len(words), [], 0
    for c in chunks:
        a = t0 + (t1 - t0) * k / n
        k += len(c)
        out.append((" ".join(c), a, t0 + (t1 - t0) * k / n))
    return out


def caption_segments(
    film: Film, plan: dict[str, Any], disclosure_line: str | None
) -> dict[str, list[dict[str, Any]]]:
    """Shot id -> its ``caption_segments`` (shot-local seconds)."""
    cfg = film.captions
    by = {p["id"]: p for p in plan["shots"]}
    segs: dict[str, list[dict[str, Any]]] = {p["id"]: [] for p in plan["shots"]}
    for shot in film.shots:
        text = caption_text(shot)
        p = by[shot["id"]]
        if not text or p["line_onset"] is None:
            continue
        on, ln = p["line_onset"], p["line_len"]
        for txt, a, b in screens(
            text, on, on + ln, max_words=cfg["max_words"], max_chars=cfg["max_chars"]
        ):
            mid = (a + b) / 2
            host = next(
                (h for h in plan["shots"] if h["start"] <= mid < h["start"] + h["dur"]),
                plan["shots"][-1],
            )
            la, lb = max(0.0, a - host["start"]), min(host["dur"], b - host["start"])
            if lb - la >= cfg["min_screen_s"]:
                segs[host["id"]].append(
                    {"text": txt, "start_s": round(la, 3), "end_s": round(lb, 3)}
                )
    dshot = film.data.get("disclosure_shot")
    if dshot and disclosure_line:
        segs[dshot] = [
            {"text": disclosure_line, "start_s": 0.0, "end_s": round(by[dshot]["dur"], 3)}
        ]
    return segs
