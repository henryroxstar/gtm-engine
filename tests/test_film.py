"""gtm_core.film — the voice-led timeline, caption mapping, lexicon and derived documents.

Pure logic, no ffmpeg: the media steps are covered by tests/media against video_finish itself.
Names and voices here are fictional.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core.film import captions_plan, doc, spec, timeline, voice

VOICES = {"narrator": {"id": "v-narr", "name": "Narrator"}, "ana": {"id": "v-ana", "name": "Ana"}}


def _film(tmp_path: Path, shots: list[dict], **extra) -> spec.Film:
    data = {
        "slug": "2026-01-01-example",
        "profile": "example",
        "voices": VOICES,
        "voice": {"engine": "elevenlabs_v4", "raw_dir": "raw"},
        "shots": shots,
        **extra,
    }
    p = tmp_path / "film.json"
    p.write_text(json.dumps(data))
    return spec.load(p)


def _line(sid, kind="graphic", text="one two three four", **kw):
    shot = {"id": sid, "kind": kind, "turns": [["narrator", text]], **kw}
    if kind == "graphic":
        shot.setdefault("graphic", {"type": "outcome"})
    return shot


def test_check_names_every_problem(tmp_path):
    p = tmp_path / "film.json"
    p.write_text(
        json.dumps(
            {
                "slug": "x",
                "profile": "x",
                "voices": VOICES,
                "shots": [
                    {"id": "a", "kind": "footage", "turns": [["ghost", "hi"]]},
                    {"id": "a", "kind": "bogus"},
                ],
            }
        )
    )
    with pytest.raises(spec.FilmError) as exc:
        spec.load(p)
    msg = str(exc.value)
    assert "speaker 'ghost'" in msg and "needs 'source'" in msg
    assert "duplicate shot id" in msg and "kind 'bogus'" in msg


def test_voice_sets_the_clock_and_lines_start_lead_into_their_shot(tmp_path):
    film = _film(tmp_path, [_line("a"), _line("b"), _line("c")])
    p = timeline.plan(film, {"a": 2.0, "b": 3.0, "c": 1.0})
    t = film.timing
    onsets = [s["line_onset"] for s in p["shots"]]
    assert onsets == [
        0.15,
        round(0.15 + 2.0 + t["gap_s"], 3),
        round(0.15 + 5.0 + 2 * t["gap_s"], 3),
    ]
    for s in p["shots"][1:]:
        assert s["line_onset"] - s["start"] == pytest.approx(t["lead_s"], abs=1e-3)
    assert p["total_s"] == pytest.approx(p["voice_total_s"], abs=1e-3)
    assert p["off_shot_lines"] == {}


def test_short_footage_hands_its_shortfall_to_the_next_graphic(tmp_path):
    film = _film(
        tmp_path,
        [
            _line("a", "footage", source="a.mp4", avail_s=2.0),
            _line("b"),
            _line("c"),
        ],
    )
    p = timeline.plan(film, {"a": 4.0, "b": 2.0, "c": 1.0})
    by = {s["id"]: s for s in p["shots"]}
    assert by["a"]["dur"] == 2.0
    # The graphic after it starts early and runs long: the line still starts inside it.
    assert by["b"]["start"] == 2.0
    assert p["total_s"] == pytest.approx(p["voice_total_s"], abs=1e-3)


def test_a_pin_is_never_resolved(tmp_path):
    film = _film(tmp_path, [_line("a", dur_s=9.0), _line("b")])
    p = timeline.plan(film, {"a": 2.0, "b": 2.0})
    assert p["shots"][0]["dur"] == 9.0
    assert "b" in p["off_shot_lines"]  # the pin pushed b's picture past its voice


def test_fixed_cutaway_is_paid_by_neighbouring_graphics(tmp_path):
    film = _film(
        tmp_path,
        [
            _line("a"),
            {"id": "x", "kind": "fixed", "source": "x.mp4", "avail_s": 2.0},
            _line("b"),
        ],
    )
    p = timeline.plan(film, {"a": 6.0, "b": 6.0})
    by = {s["id"]: s for s in p["shots"]}
    assert by["x"]["dur"] == 2.0
    assert p["total_s"] == pytest.approx(p["voice_total_s"], abs=1e-3)


def test_captions_follow_the_voice_across_a_cut(tmp_path):
    film = _film(
        tmp_path,
        [
            _line(
                "a",
                "footage",
                text="alpha beta gamma delta epsilon zeta eta theta",
                source="a.mp4",
                avail_s=1.0,
            ),
            _line("b", text="end"),
        ],
    )
    p = timeline.plan(film, {"a": 4.0, "b": 1.0})
    segs = captions_plan.caption_segments(film, p, "Made with AI.")
    texts = [s["text"] for s in segs["a"] + segs["b"]]
    assert "alpha beta gamma delta epsilon" in texts[0]
    assert segs["b"], "the second screen lands on the shot that is on screen"
    # disclosure is not configured on a shot, so it is not burned anywhere
    assert all("Made with AI." not in t for t in texts)


def test_screens_respect_word_and_character_caps():
    out = captions_plan.screens("a bb ccc dddd eeeee ffffff", 0, 6, max_words=5, max_chars=12)
    assert all(len(t) <= 12 for t, _a, _b in out)
    assert out[0][1] == 0 and out[-1][2] == 6


def test_lexicon_respells_for_the_voice_only(tmp_path):
    film = _film(tmp_path, [_line("a", text="Welcome to Examplo. Examplo works.")])
    lex = {"examplo": "Egzamploh"}
    turns = voice.tts_turns(film, film.shot("a"), lex)
    assert turns[0]["text"] == "Welcome to Egzamploh. Egzamploh works."
    assert turns[0]["voice_id"] == "v-narr"
    assert spec.caption_text(film.shot("a")) == "Welcome to Examplo. Examplo works."


def test_a_respelling_in_the_script_is_refused(tmp_path):
    film = _film(tmp_path, [_line("a", text="Egzamploh is here.")])
    problems = voice.check_film(film, {"examplo": "Egzamploh"})
    assert problems and "write 'examplo'" in problems[0]


def test_changing_words_marks_only_that_shot_for_voicing(tmp_path):
    film = _film(tmp_path, [_line("a"), _line("b")])
    req = voice.request(film, {})
    assert [j["shot_id"] for j in req["batches"][0]] == ["a", "b"]
    voiced = {}
    for sid in ("a", "b"):
        voiced[sid] = {
            "hash": voice._hash(voice.tts_turns(film, film.shot(sid), {})),
            "raw_speech_s": 1.0,
        }
    (film.out / "voice").mkdir(parents=True, exist_ok=True)
    (film.out / "voice" / "voiced.json").write_text(json.dumps(voiced))
    film.shot("b")["turns"] = [["narrator", "new words"]]
    assert voice.status(film, {})["needs_voice"] == ["b"]


def test_prep_refuses_an_old_take_for_new_words(tmp_path):
    film = _film(tmp_path, [_line("a")])
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "a.mp3").write_bytes(b"x")
    (film.out / "voice").mkdir(parents=True)
    (film.out / "voice" / "voiced.json").write_text(json.dumps({"a": {"hash": "old"}}))
    with pytest.raises(spec.FilmError, match="words changed"):
        voice.prep(film, {})


def test_cues_land_on_shot_offsets_and_words(tmp_path):
    film = _film(
        tmp_path,
        [_line("a"), _line("b", text="w0 w1 w2 w3")],
        sfx=[
            {"label": "hit", "file": "s.mp3", "shot": "b", "offset_s": 0.5, "gain_db": -10},
            {"label": "tap", "file": "s.mp3", "shot": "b", "word": 2},
        ],
    )
    p = timeline.plan(film, {"a": 2.0, "b": 2.0})
    by = {s["id"]: s for s in p["shots"]}
    cues = doc.cue_sheet(film, p)["cues"]
    assert cues[0]["at_s"] == pytest.approx(by["b"]["start"] + 0.5, abs=1e-3)
    assert cues[1]["at_s"] == pytest.approx(by["b"]["line_onset"] + 1.0, abs=1e-3)
    assert "trim_s" not in cues[1], "no trim given means the mixer measures it"


def test_shot_list_carries_meta_overlays_and_narration(tmp_path):
    film = _film(
        tmp_path,
        [
            _line(
                "a",
                "footage",
                source="a.mp4",
                avail_s=5.0,
                meta={"camera": "static"},
                overlay=[{"kind": "chip", "text": "1 of 3", "start_s": 0.1, "end_s": -0.2}],
            ),
            _line("b"),
        ],
        disclosure_shot="b",
    )
    p = timeline.plan(film, {"a": 2.0, "b": 2.0})
    segs = captions_plan.caption_segments(film, p, "Made with AI.")
    d = doc.shot_list(film, p, segs, repo_root=tmp_path)
    a, b = d["shots"]
    assert a["camera"] == "static" and a["file"] == "overlaid/a-overlaid.mp4"
    assert a["production"]["overlay"][0]["end_s"] == pytest.approx(a["duration_s"] - 0.2, abs=0.01)
    assert b["caption_segments"] == [
        {"text": "Made with AI.", "start_s": 0.0, "end_s": b["duration_s"]}
    ]
    assert [ln["line"] for ln in d["narration"]["lines"]] == ["one two three four"] * 2
    pre = doc.shot_list(film, p, segs, repo_root=tmp_path, overlaid=False)
    assert pre["shots"][0]["file"] == "cut/a.mp4"


def test_sync_rewrites_only_the_beats_section(tmp_path):
    film = _film(tmp_path, [_line("a", meta={"title": "Opening", "chapter": "One"})])
    p = timeline.plan(film, {"a": 2.0})
    md = "# Example — script v1 (10 s, 3 shots, 16:9)\n\nIntro.\n\n## Beats\n\nold\n\n## Clarity\n\nkept\n"
    out = doc.sync_script_md(md, film, p)
    assert "old" not in out and "kept" in out and "Intro." in out
    assert "### a · Opening (0–" in out and "· One" in out
    assert f"({p['total_s']:.2f} s, 1 shots, 16:9)" in out
