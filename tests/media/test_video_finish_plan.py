"""gtm_core.video_finish.plan() — pure, no ffmpeg, no filesystem writes. This is what makes the
single-grade invariant and the census assertable without a subprocess.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

import pytest

from gtm_core import video_finish as vf


def test_plan_has_exactly_one_grade_stage_by_construction():
    p = vf.plan(profile="acme", slug="s", ratio="9:16", source="src.mp4", spec={})
    assert p.census()["grade"] == 1


def test_plan_with_no_captions_has_zero_overlay_census():
    p = vf.plan(profile="acme", slug="s", ratio="9:16", source="src.mp4", spec={})
    assert p.census()["overlay"] == 0
    assert not any(s.name == "captions" for s in p.stages)


def test_plan_with_captions_overlay_census_matches_screen_count():
    spec = {"caption_text": " ".join(["word"] * 12), "total_s": 6.0}
    p = vf.plan(profile="acme", slug="s", ratio="9:16", source="src.mp4", spec=spec)
    caption_stage = next(s for s in p.stages if s.name == "captions")
    assert p.census()["overlay"] == caption_stage.args["num_screens"] > 0


def test_plan_with_cuts_adds_a_cuts_stage():
    spec = {"cuts": [{"start": 0.0, "end": 2.0}]}
    p = vf.plan(profile="acme", slug="s", ratio="9:16", source="src.mp4", spec=spec)
    assert any(s.name == "cuts" for s in p.stages)


def test_plan_with_no_cuts_has_no_cuts_stage():
    p = vf.plan(profile="acme", slug="s", ratio="9:16", source="src.mp4", spec={})
    assert not any(s.name == "cuts" for s in p.stages)


def test_plan_stage_order_matches_f8_correction():
    """cuts -> grade -> captions, never captions before grade (that would invalidate the ΔE
    check by measuring pixels already composited with white caption glyphs) and never a crop
    after the caption burn (that would invalidate every recorded caption bbox)."""
    spec = {"cuts": [{"start": 0.0, "end": 1.0}], "caption_text": "hello there", "total_s": 2.0}
    p = vf.plan(profile="acme", slug="s", ratio="9:16", source="src.mp4", spec=spec)
    names = [s.name for s in p.stages]
    assert names.index("cuts") < names.index("grade") < names.index("captions")


def test_plan_id_is_deterministic_for_identical_inputs():
    spec = {"caption_text": "same text", "total_s": 3.0}
    p1 = vf.plan(profile="acme", slug="s", ratio="9:16", source="src.mp4", spec=spec)
    p2 = vf.plan(profile="acme", slug="s", ratio="9:16", source="src.mp4", spec=spec)
    assert p1.plan_id == p2.plan_id


def test_plan_id_changes_when_the_spec_changes():
    p1 = vf.plan(
        profile="acme",
        slug="s",
        ratio="9:16",
        source="src.mp4",
        spec={"caption_text": "a", "total_s": 1.0},
    )
    p2 = vf.plan(
        profile="acme",
        slug="s",
        ratio="9:16",
        source="src.mp4",
        spec={"caption_text": "b", "total_s": 1.0},
    )
    assert p1.plan_id != p2.plan_id


def test_plan_unknown_ratio_raises():
    with pytest.raises(ValueError, match="unknown ratio"):
        vf.plan(profile="acme", slug="s", ratio="21:9", source="src.mp4", spec={})


def test_plan_census_shape_has_all_four_keys():
    p = vf.plan(profile="acme", slug="s", ratio="9:16", source="src.mp4", spec={})
    assert set(p.census()) == {"grade", "overlay", "loudnorm", "concat"}


def test_plan_to_json_carries_the_census_and_plan_id():
    p = vf.plan(profile="acme", slug="s", ratio="9:16", source="src.mp4", spec={})
    d = p.to_json()
    assert d["plan_id"] == p.plan_id
    assert d["census"] == p.census()
    assert isinstance(d["stages"], list)


def test_plan_never_touches_the_filesystem(tmp_path, monkeypatch):
    """No ffmpeg, no subprocess, no writes — plan() is pure data."""
    import subprocess

    def _boom(*a, **kw):
        raise AssertionError("plan() must never invoke subprocess")

    monkeypatch.setattr(subprocess, "run", _boom)
    vf.plan(
        profile="acme",
        slug="s",
        ratio="9:16",
        source="src.mp4",
        spec={"caption_text": "x", "total_s": 1.0},
    )
    assert list(tmp_path.iterdir()) == []


# --- disclosure_line (Phase 16: burn disclosure onto the asset's own final seconds, never a
# separately appended black-card segment) ---


def test_plan_with_disclosure_line_adds_one_more_screen_to_the_census():
    without = vf.plan(
        profile="acme",
        slug="s",
        ratio="9:16",
        source="src.mp4",
        spec={"caption_text": "one two three four", "total_s": 10.0},
    )
    with_disc = vf.plan(
        profile="acme",
        slug="s",
        ratio="9:16",
        source="src.mp4",
        spec={
            "caption_text": "one two three four",
            "total_s": 10.0,
            "disclosure_line": "Made with AI.",
        },
    )
    assert with_disc.census()["overlay"] == without.census()["overlay"] + 1


def test_plan_disclosure_line_without_total_s_raises():
    with pytest.raises(vf.PlanError, match="disclosure_line but no total_s"):
        vf.plan(
            profile="acme",
            slug="s",
            ratio="9:16",
            source="src.mp4",
            spec={"caption_text": "hello", "disclosure_line": "Made with AI."},
        )


def test_plan_no_disclosure_line_leaves_captions_stage_unaffected():
    p = vf.plan(
        profile="acme",
        slug="s",
        ratio="9:16",
        source="src.mp4",
        spec={"caption_text": "hello there", "total_s": 3.0},
    )
    caption_stage = next(s for s in p.stages if s.name == "captions")
    assert caption_stage.args.get("disclosure_line") is None


def test_plan_id_changes_when_disclosure_line_changes():
    """Disclosure presence must be part of the idempotency key — a run with disclosure added
    later must not be treated as 'already produced' by execute()'s plan_id short-circuit."""
    base = {"caption_text": "same text", "total_s": 5.0}
    p1 = vf.plan(profile="acme", slug="s", ratio="9:16", source="src.mp4", spec=base)
    p2 = vf.plan(
        profile="acme",
        slug="s",
        ratio="9:16",
        source="src.mp4",
        spec={**base, "disclosure_line": "Made with AI."},
    )
    assert p1.plan_id != p2.plan_id


# --- Article 50: an out-of-pipeline synthetic render must still disclose --------------------
#
# Regression for 2026-08-20. `_identity_used_from_render` read identity ONLY off a sibling
# render-<ratio>.json, and returned () when there wasn't one. That was right while the only
# manifest-less asset was real footage (nothing to disclose). A HeyGen avatar render is a third
# case: no render manifest, but a trained likeness AND a cloned voice. It resolved to (), the
# disclosure duty never fired, and the finish pass emitted an undisclosed synthetic asset with
# no warning — the disclosure landed only because it was passed by hand that day.


def test_declaring_an_identity_without_a_disclosure_line_is_refused():
    """Fail-closed: naming an identity commits you to disclosing it. Same posture as
    validate_disclosure at the publish gate, moved earlier so it costs an encode, not a post."""
    spec = {"identity_used": ["avatar", "voice"], "caption_text": "HI", "total_s": 10.0}
    with pytest.raises(vf.PlanError, match="no disclosure_line"):
        vf.plan(profile="acme", slug="s", ratio="4:5", source="src.mp4", spec=spec)


def test_a_declared_identity_with_a_disclosure_line_is_carried_onto_the_plan():
    spec = {
        "identity_used": ["avatar", "voice"],
        "caption_text": "HI",
        "total_s": 10.0,
        "disclosure_line": "Made with AI.",
    }
    p = vf.plan(profile="acme", slug="s", ratio="4:5", source="src.mp4", spec=spec)
    assert p.spec_identity == ["avatar", "voice"]


def test_a_spec_identity_is_refused_when_a_render_manifest_already_owns_it(tmp_path):
    """The render manifest stays the single owner wherever it exists — two hand-kept copies of
    one fact is how they diverge. Absence of the file is what unlocks the spec fallback."""
    (tmp_path / "render-4x5.json").write_text('{"identity_used": ["soul"]}')
    with pytest.raises(vf.PlanError, match="single owner"):
        vf._identity_used_from_render(tmp_path, "4x5", ["avatar"])


def test_the_spec_fallback_supplies_identity_only_when_there_is_no_render_manifest(tmp_path):
    assert vf._identity_used_from_render(tmp_path, "4x5", ["avatar", "voice"]) == (
        "avatar",
        "voice",
    )


def test_real_footage_still_resolves_to_no_identity(tmp_path):
    """The original case must keep working: no manifest and no spec claim is genuinely nothing
    to disclose, NOT an error — this function must never turn a finish run into a failure."""
    assert vf._identity_used_from_render(tmp_path, "4x5", None) == ()


# --- caption route: a configured Reap preset may not be bypassed silently ----------------------
#
# The rule was already written, in prose, in the video-finish body: with `captions.preset`
# resolving, the Reap route is the default. It was skipped anyway on 2026-08-20 — the preset
# resolved (`system_indigo`) and captions were burned locally, which is how 24 caption screens
# landed over the speaker's face while the Reap plan sat at 0 of 600 credits used. Prose that gets
# skipped is not a gate, so the decision is now resolved at plan time, before any encode.


def _caption_spec(**kw) -> dict:
    spec = {"caption_text": " ".join(["word"] * 12), "total_s": 6.0}
    spec.update(kw)
    return spec


def _plan(spec: dict):
    return vf.plan(profile="acme", slug="s", ratio="9:16", source="src.mp4", spec=spec)


def test_local_burn_in_while_a_preset_resolves_is_refused_before_any_encode():
    with pytest.raises(vf.PlanError, match="caption_route_suppression"):
        _plan(_caption_spec(captions_preset="system_indigo"))


def test_the_same_plan_is_allowed_once_the_override_is_written_down():
    """The positive control. This is a RECORD-IT rule, not a ban — an operator may have a real
    reason to burn locally, and the failure mode being fixed is the silent choice, not the choice.
    """
    p = _plan(
        _caption_spec(
            captions_preset="system_indigo",
            caption_route_suppression="asset is captioned in a language Reap does not transcribe",
        )
    )
    assert p.caption_route == "local"
    assert p.caption_route_suppression


def test_local_burn_in_is_fine_when_the_tenant_configured_no_preset():
    """The rule keys off the tenant's own configuration, not on a preference for the vendor.
    A tenant with no preset has chosen nothing, so the local path is simply the route."""
    p = _plan(_caption_spec())
    assert p.caption_route == "local" and not p.caption_route_suppression


def test_the_route_is_derived_from_what_the_plan_actually_does():
    """Fail-closed default. An omitted `caption_route` must not read as "reap" and slip past the
    check — it is derived from the presence of a local captions stage."""
    assert _plan(_caption_spec()).caption_route == "local"
    assert _plan({}).caption_route == "none"


def test_declaring_the_reap_route_leaves_no_local_caption_stage_to_contradict_it():
    """Reap burns captions outside this module, so a Reap-routed plan carries no captions stage.
    Declaring `reap` while ALSO burning locally would double-caption the asset."""
    p = _plan({"captions_preset": "system_indigo", "caption_route": "reap"})
    assert not any(s.name == "captions" for s in p.stages)


def test_a_suppression_without_a_suppressed_route_is_refused():
    """A reason attached to a route that was never overridden is a record of a decision nobody
    made — worse than no record, because it reads as deliberate."""
    with pytest.raises(vf.PlanError, match="only means something"):
        _plan({"caption_route": "reap", "caption_route_suppression": "because"})


def test_an_unknown_route_is_refused_rather_than_stored():
    with pytest.raises(vf.PlanError, match="not one of"):
        _plan(_caption_spec(caption_route="handmade"))


def test_a_kit_that_declares_the_local_route_owes_no_suppression_beside_its_preset():
    """`[captions] route = "local"` next to a `preset` is a decision the kit already wrote down:
    the preset is a style/placement hint and the local burn is the sanctioned route, not a
    bypass. Demanding a suppression here asks the operator to apologise for the configured
    route."""
    p = _plan(_caption_spec(captions_preset="system_indigo", captions_route="local"))
    assert p.caption_route == "local"
    assert p.captions_route == "local"
    assert not p.caption_route_suppression


def test_a_kit_that_declares_the_reap_route_still_owes_the_suppression():
    with pytest.raises(vf.PlanError, match="caption_route_suppression"):
        _plan(_caption_spec(captions_preset="system_indigo", captions_route="reap"))


def test_an_unknown_declared_route_is_refused():
    with pytest.raises(vf.PlanError, match="captions_route"):
        _plan(_caption_spec(captions_route="handmade"))


# --- pre-burned sidecars beside the source ------------------------------------------------------
#
# Captions burned per shot and stitched reach `plan()` as a source with a merged
# `<source stem>.captions.json` beside it and a spec with no caption_text. Until 2026-09-11 that
# planned as `caption_route: "none"`, `captions: null` — the manifest of a captioned film said it
# had no captions, so the lint's contrast tier never ran on it.


def _sidecar(tmp_path, *, kind="captions", frame=(1080, 1920), entries=None) -> str:
    source = tmp_path / "master.mp4"  # plan() is pure: only the sidecar has to exist
    list_key = {"captions": "screens", "overlays": "overlays"}[kind]
    (tmp_path / f"master.{kind}.json").write_text(
        json.dumps({"frame": list(frame), list_key: entries or []})
    )
    return str(source)


_SCREEN = {
    "index": 0,
    "text": "hi",
    "start_s": 0.0,
    "end_s": 1.0,
    "box": {"x": 538, "y": 1600, "w": 538, "h": 100},
}


def test_a_source_with_a_caption_sidecar_plans_as_preburned_local_captions(tmp_path):
    source = _sidecar(tmp_path, entries=[_SCREEN])
    p = vf.plan(profile="acme", slug="s", ratio="9:16", source=source, spec={})
    assert p.captions_preburned is True
    assert p.caption_route == "local"
    assert not any(s.name == "captions" for s in p.stages), "nothing to render — already burned"
    assert p.preburned_captions["frame"] == [1080, 1920]
    assert p.preburned_captions["screens"][0]["box"] == _SCREEN["box"]  # same frame: untouched
    assert p.to_json()["captions_preburned"] is True


def test_preburned_boxes_are_scaled_to_the_finish_frame(tmp_path):
    """The lint measures the FINISHED file. A 1076x1926 source is scaled to 1080x1920 by the
    normalize/upscale stage, so a box in source pixels is the wrong box afterwards."""
    source = _sidecar(tmp_path, frame=(1076, 1926), entries=[_SCREEN])
    p = vf.plan(profile="acme", slug="s", ratio="9:16", source=source, spec={})
    sx, sy = 1080 / 1076, 1920 / 1926
    assert p.preburned_captions["frame"] == [1080, 1920]
    assert p.preburned_captions["screens"][0]["box"] == {
        "x": round(538 * sx),
        "y": round(1600 * sy),
        "w": round(538 * sx),
        "h": round(100 * sy),
    }
    assert p.preburned_captions["screens"][0]["box"] != _SCREEN["box"]


def test_an_overlays_sidecar_is_ingested_and_scaled_the_same_way(tmp_path):
    overlay = {
        "kind": "lower-third",
        "start_s": 0.0,
        "end_s": 1.0,
        "box": {"x": 0, "y": 0, "w": 538, "h": 963},
    }
    source = _sidecar(tmp_path, kind="overlays", frame=(1076, 1926), entries=[overlay])
    p = vf.plan(profile="acme", slug="s", ratio="9:16", source=source, spec={})
    assert p.captions_preburned is False and p.preburned_captions is None
    assert p.preburned_overlays == [{**overlay, "box": {"x": 0, "y": 0, "w": 540, "h": 960}}]
    assert p.caption_route == "none"


def test_without_a_sidecar_the_plan_is_unchanged(tmp_path):
    """Every plan_id minted before sidecars existed must still resolve: the hash payload gains
    a key only when a sidecar is present."""
    source = str(tmp_path / "m.mp4")
    p = vf.plan(profile="acme", slug="s", ratio="9:16", source=source, spec={})
    assert p.captions_preburned is False
    assert p.preburned_captions is None and p.preburned_overlays is None
    assert p.caption_route == "none"
    legacy_payload = {
        "profile": "acme",
        "slug": "s",
        "ratio": "9:16",
        "source": source,
        "stages": [asdict(st) for st in p.stages],
    }
    expected = hashlib.sha256(vf._canonical_json(legacy_payload).encode()).hexdigest()[:16]
    assert p.plan_id == expected


def test_the_sidecar_is_part_of_the_plan_id_only_when_present(tmp_path):
    """A sidecar that appears or changes after a run must not be short-circuited into a stale
    `captions: null` manifest — the manifest is part of the output the id guards."""
    source = str(tmp_path / "master.mp4")
    without = vf.plan(profile="acme", slug="s", ratio="9:16", source=source, spec={})
    _sidecar(tmp_path, entries=[_SCREEN])
    with_sidecar = vf.plan(profile="acme", slug="s", ratio="9:16", source=source, spec={})
    same_again = vf.plan(profile="acme", slug="s", ratio="9:16", source=source, spec={})
    assert with_sidecar.plan_id != without.plan_id
    assert with_sidecar.plan_id == same_again.plan_id
    moved = {**_SCREEN, "box": {**_SCREEN["box"], "y": 1500}}
    _sidecar(tmp_path, entries=[moved])
    assert vf.plan(profile="acme", slug="s", ratio="9:16", source=source, spec={}).plan_id not in (
        with_sidecar.plan_id,
        without.plan_id,
    )


def test_caption_text_over_a_preburned_source_is_refused(tmp_path):
    source = _sidecar(tmp_path, entries=[_SCREEN])
    with pytest.raises(vf.PlanError, match="already carries burned captions"):
        vf.plan(profile="acme", slug="s", ratio="9:16", source=source, spec=_caption_spec())


def test_a_declared_route_contradicting_a_preburned_source_is_refused(tmp_path):
    source = _sidecar(tmp_path, entries=[_SCREEN])
    with pytest.raises(vf.PlanError, match="pre-burned local captions"):
        vf.plan(
            profile="acme", slug="s", ratio="9:16", source=source, spec={"caption_route": "none"}
        )


def test_preburned_captions_still_answer_to_a_resolving_preset(tmp_path):
    """The route the asset took is local; a resolving preset makes that a recorded decision."""
    source = _sidecar(tmp_path, entries=[_SCREEN])
    with pytest.raises(vf.PlanError, match="caption_route_suppression"):
        vf.plan(
            profile="acme",
            slug="s",
            ratio="9:16",
            source=source,
            spec={"captions_preset": "system_indigo"},
        )
    p = vf.plan(
        profile="acme",
        slug="s",
        ratio="9:16",
        source=source,
        spec={"captions_preset": "system_indigo", "captions_route": "local"},
    )
    assert p.captions_preburned and p.caption_route == "local"


def test_a_sidecar_without_a_frame_is_refused_not_ingested_blind(tmp_path):
    (tmp_path / "master.captions.json").write_text(json.dumps({"screens": [_SCREEN]}))
    with pytest.raises(vf.PlanError, match="no usable frame"):
        vf.plan(
            profile="acme", slug="s", ratio="9:16", source=str(tmp_path / "master.mp4"), spec={}
        )
