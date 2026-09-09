"""gtm_core.vo_timings — cue phrases + measured word timings -> animation windows.

Pure: no audio file, no ffmpeg, no vendor. Everything here is a dict and a dataclass, which is the
point — the mapping that used to be a person reading timestamps off a screen and typing sixteen
floats into a function body is now something a test can assert.
"""

from __future__ import annotations

import json

import pytest

from gtm_core import captions as cap
from gtm_core import screen_ui as su
from gtm_core import vo_timings as vt
from gtm_core import word_timing as wt


def _words(pairs: list[tuple[str, float, float]]) -> list[wt.WordTiming]:
    return [wt.WordTiming(word=w, start_s=a, end_s=b) for w, a, b in pairs]


#: The h15 narration's opening at the timestamps the comment in screen_ui.py recorded, with the
#: product names replaced by placeholders (no cue depends on them).
_H15 = _words(
    [
        ("First", 2.90, 3.20),
        ("is", 3.22, 3.34),
        ("Nexus's", 3.36, 3.90),
        ("Reach", 3.95, 4.40),
        ("Guard.", 4.45, 5.12),
        ("Every", 5.58, 5.80),
        ("call", 5.85, 6.10),
        ("you", 6.12, 6.24),
        ("just", 6.28, 6.50),
        ("saw", 6.55, 6.80),
        ("passes", 6.85, 7.20),
        ("through", 7.24, 7.44),
        ("it.", 7.46, 7.54),
        ("You", 9.00, 9.20),
        ("get", 9.24, 9.44),
        ("observability", 11.32, 12.12),
        ("Second", 17.12, 17.60),
        ("is", 17.64, 17.78),
        ("Nexus's", 17.82, 18.30),
        ("Model", 18.36, 18.80),
        ("Guard.", 18.86, 19.24),
        ("input", 22.04, 22.40),
        ("and", 22.44, 22.60),
        ("output", 22.64, 23.00),
        ("filters", 23.05, 23.44),
        ("subjective", 24.02, 24.70),
        ("controls", 24.76, 25.30),
        ("on", 25.34, 25.50),
        ("what", 25.54, 25.80),
        ("it", 25.84, 25.96),
        ("may", 26.00, 26.20),
        ("say.", 26.24, 26.36),
    ]
)
_H15_TOTAL_S = 26.59


# ── the shipped fractions, now an executable claim ───────────────────────────────────────


def test_the_shipped_fractions_are_reproduced_from_the_measured_words():
    """The comment block in screen_ui.py listed six phrases, their word timestamps, and the
    fractions someone derived by dividing each by 26.59. That was documentation. This runs it.

    DROP and STREAM end on the enumerating clause ("First is" / "Second is"): since 2026-09-03
    the cues no longer name the company or the product (see checkpoint_timing.py), so each
    window closes on "is" rather than on the product name that followed it."""
    t = vt.timing_map(_H15, su._CHECKPOINT_CUES, total_s=_H15_TOTAL_S)
    assert t["DROP"] == pytest.approx((2.90 / 26.59, 3.34 / 26.59), abs=1e-3)
    assert t["REROUTE"] == pytest.approx((5.58 / 26.59, 7.54 / 26.59), abs=1e-3)
    assert t["NOTE"] == pytest.approx((11.32 / 26.59, 12.12 / 26.59), abs=1e-3)
    assert t["STREAM"] == pytest.approx((17.12 / 26.59, 17.78 / 26.59), abs=1e-3)
    assert t["SUB1"] == pytest.approx((22.04 / 26.59, 23.44 / 26.59), abs=1e-3)
    assert t["SUB2"] == pytest.approx((24.02 / 26.59, 25.30 / 26.59), abs=1e-3)


def test_windows_are_ordered_and_inside_the_clip():
    t = vt.timing_map(_H15, su._CHECKPOINT_CUES, total_s=_H15_TOTAL_S)
    ordered = [t[c.key] for c in su._CHECKPOINT_CUES]
    assert all(0.0 <= a < b <= 1.0 for a, b in ordered)
    assert ordered == sorted(ordered), "a re-cut that reorders the narration must be visible"


# ── matching ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "tokens",
    [
        [("Nexus's", 1.0, 1.5)],
        [("Nexus’s", 1.0, 1.5)],  # curly apostrophe
        [("Nexus", 1.0, 1.3), ("'s", 1.3, 1.5)],  # split by the tokeniser
        [("NEXUS'S", 1.0, 1.5)],
    ],
)
def test_apostrophe_and_tokenisation_variants_all_match(tokens):
    """A vendor may hand back `Nexus's`, `Nexus’s`, or `Nexus` + `'s`. Matching on a character
    stream rather than a token sequence absorbs all three with one mechanism."""
    words = _words([("First", 0.0, 0.5), ("is", 0.6, 0.8), *tokens, ("Gateway", 2.0, 2.5)])
    first, last = vt.match_phrase(words, "First is Nexus's Gateway")
    assert (first, last) == (0, len(words) - 1)


def test_punctuation_and_case_are_ignored():
    words = _words([("First", 0.0, 0.5), ("is", 0.6, 0.8), ("gateway.", 1.0, 1.5)])
    assert vt.match_phrase(words, "First is Gateway") == (0, 2)


def test_a_word_boundary_is_respected():
    """The character stream loses word boundaries, so they are re-imposed: a hit must begin at a
    word's first character and end at its last. Otherwise `gateway` matches inside `gatewayed`."""
    words = _words([("gatewayed", 0.0, 1.0)])
    with pytest.raises(vt.PhraseNotFound):
        vt.match_phrase(words, "gateway")


def test_occurrence_selects_a_later_hit():
    words = _words([("go", 0.0, 0.4), ("stop", 0.5, 0.9), ("go", 1.0, 1.4)])
    assert vt.match_phrase(words, "go", occurrence=1) == (0, 0)
    assert vt.match_phrase(words, "go", occurrence=2) == (2, 2)
    with pytest.raises(vt.PhraseNotFound, match="occurs 2 time"):
        vt.match_phrase(words, "go", occurrence=3)


def test_a_missing_phrase_raises_loudly_and_names_the_partial():
    """A miss must be loud. The error also says, in words, not to do the thing whoever hits it
    will be tempted to do."""
    words = _words([("Second", 0.0, 0.4), ("is", 0.5, 0.7), ("nexus", 0.8, 1.2)])
    with pytest.raises(vt.PhraseNotFound) as exc:
        vt.match_phrase(words, "Second is Nexus's Model Guard")
    msg = str(exc.value)
    assert "does not occur" in msg
    assert "Longest partial match" in msg
    assert "evenly" in msg, "the error must refuse the even-spread fallback in so many words"


def test_a_missing_phrase_returns_no_map_at_all():
    """Not a partial map, not a map with the failed key defaulted — nothing."""
    cues = (*su._CHECKPOINT_CUES, vt.PhraseCue(key="GHOST", phrase="a line nobody said"))
    with pytest.raises(vt.PhraseNotFound):
        vt.timing_map(_H15, cues, total_s=_H15_TOTAL_S)


# ── ingest ───────────────────────────────────────────────────────────────────────────────


def _payload(words, *, scale=1.0, wrap="flat"):
    items = [{"word": w, "start": a * scale, "end": b * scale} for w, a, b in words]
    return {
        "flat": {"word_timestamps": items},
        "nested": {"data": {"word_timestamps": items}},
        "bare": items,
    }[wrap]


@pytest.mark.parametrize("wrap", ["flat", "nested", "bare"])
def test_ingest_accepts_the_envelopes_vendors_actually_send(wrap):
    words, prov = vt.normalize_vendor_words(
        _payload([("hi", 0.0, 0.5)], wrap=wrap), audio_duration_s=1.0
    )
    assert [w.word for w in words] == ["hi"]
    assert prov["units"] == "s"


def test_ingest_converts_milliseconds_and_says_it_did():
    words, prov = vt.normalize_vendor_words(
        _payload([("hi", 0.0, 0.5), ("there", 0.6, 1.9)], scale=1000.0), audio_duration_s=2.0
    )
    assert prov["units"] == "ms->s"
    assert words[-1].end_s == pytest.approx(1.9)


def test_ingest_refuses_a_payload_that_still_overruns_after_rescaling():
    """Refuse rather than pick whichever reading is less embarrassing: a silently mis-scaled
    timeline makes every cue wrong by the same factor, which reads as a design choice."""
    with pytest.raises(vt.VoTimingError, match="not the same cut"):
        vt.normalize_vendor_words(_payload([("hi", 0.0, 5.0)]), audio_duration_s=2.0)


def test_ingest_refuses_words_out_of_speech_order():
    payload = {
        "word_timestamps": [
            {"word": "b", "start": 1.0, "end": 1.2},
            {"word": "a", "start": 0.1, "end": 0.3},
        ]
    }
    with pytest.raises(vt.VoTimingError, match="not in speech order"):
        vt.normalize_vendor_words(payload, audio_duration_s=2.0)


def test_ingest_refuses_a_word_that_ends_before_it_starts():
    payload = {"word_timestamps": [{"word": "a", "start": 1.0, "end": 0.5}]}
    with pytest.raises(vt.VoTimingError, match="ends"):
        vt.normalize_vendor_words(payload, audio_duration_s=2.0)


def test_ingest_refuses_an_unrecognisable_envelope():
    with pytest.raises(vt.VoTimingError, match="VERBATIM"):
        vt.normalize_vendor_words({"something": "else"}, audio_duration_s=1.0)


def test_the_sidecar_round_trips_and_carries_the_measured_duration(tmp_path):
    audio = tmp_path / "h15.wav"
    audio.write_bytes(b"")
    out = vt.write_words_sidecar(
        audio,
        _H15,
        audio_duration_s=_H15_TOTAL_S,
        source="create_speech",
        provenance={"units": "s", "word_count": len(_H15)},
    )
    assert out.name == "h15.words.json"
    assert out == vt.words_sidecar_path(audio)
    words, duration = vt.read_words_sidecar(out)
    assert duration == pytest.approx(_H15_TOTAL_S)
    assert [w.word for w in words] == [w.word for w in _H15]
    assert json.loads(out.read_text())["schema_version"] == vt.WORDS_SCHEMA_VERSION


# ── the scene's side of the contract ─────────────────────────────────────────────────────


def test_the_default_timing_covers_exactly_the_cue_keys():
    """Catches spec/literal drift with no setup: a cue added without a fallback window, or a
    stale window for a cue that no longer exists."""
    assert set(su._CHECKPOINT_DEFAULT_TIMING) == {c.key for c in su._CHECKPOINT_CUES}


def test_a_partial_timing_map_is_refused_not_merged():
    """Half-measured, half-literal is the silently-wrong state: if the VO was re-cut then every
    window is stale, and filling the gaps hides that behind a card that mostly works."""
    with pytest.raises(su.SceneError, match="missing"):
        su._resolve_timing("checkpoint-flow", su._CHECKPOINT_DEFAULT_TIMING, {"DROP": (0.1, 0.2)})


def test_an_unknown_timing_key_is_refused_and_named():
    full = dict(su._CHECKPOINT_DEFAULT_TIMING) | {"NOPE": (0.1, 0.2)}
    with pytest.raises(su.SceneError, match="NOPE"):
        su._resolve_timing("checkpoint-flow", su._CHECKPOINT_DEFAULT_TIMING, full)


def test_no_override_uses_the_documented_literals():
    assert su._resolve_timing("checkpoint-flow", su._CHECKPOINT_DEFAULT_TIMING, None) is (
        su._CHECKPOINT_DEFAULT_TIMING
    )


def test_the_scene_cue_registry_points_at_the_scene_that_owns_it():
    assert su.SCENE_CUES["checkpoint-flow"] is su._CHECKPOINT_CUES


def test_word_timing_is_defined_once():
    """`captions` is still WordTiming's public home; it must not become a second definition."""
    assert cap.WordTiming is wt.WordTiming


def test_a_repeated_phrase_warns_rather_than_silently_taking_the_first():
    """`cue_windows` built a warnings list, returned it, and never appended to it — while its own
    docstring promised the ambiguity would be reported. A cue phrase occurring twice resolved
    silently, which is how a card animates to the wrong sentence."""
    words = _words([("go", 0.0, 0.4), ("stop", 0.5, 0.9), ("go", 1.0, 1.4)])
    _, warnings = vt.cue_windows(words, [vt.PhraseCue(key="K", phrase="go")], total_s=2.0)
    assert len(warnings) == 1
    assert "occurs 2 times" in warnings[0]
    assert "0.00s" in warnings[0] and "1.00s" in warnings[0]


def test_an_explicit_occurrence_is_not_ambiguous():
    words = _words([("go", 0.0, 0.4), ("stop", 0.5, 0.9), ("go", 1.0, 1.4)])
    _, warnings = vt.cue_windows(
        words, [vt.PhraseCue(key="K", phrase="go", occurrence=2)], total_s=2.0
    )
    assert warnings == []


def test_an_unambiguous_cue_warns_about_nothing():
    _, warnings = vt.cue_windows(_H15, su._CHECKPOINT_CUES, total_s=_H15_TOTAL_S)
    assert warnings == []
