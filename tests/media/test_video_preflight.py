"""Lane feasibility resolved before any creative work.

On 2026-08-28 a concept was written, revised twice and routed before dead-ending: the lane it
needed did not exist, and the fallback lane needed four identity handles the profile lacked. Both
were free reads. These tests pin the ordering fix — the cheap, fatal checks run first — and the
specific defect that let the old preflight pass: it audited a FIXED soul_id/voice_id pair, which
is the wrong pair for the HeyGen-backed presenter lane.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core import video_preflight as vp

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def profiles(tmp_path: Path) -> Path:
    """A minimal profile with the creator pack active and no identity handles at all."""
    root = tmp_path / "profiles"
    prof = root / "acme"
    (prof / "knowledge").mkdir(parents=True)
    (prof / "packs.toml").write_text('active = ["marketing", "creator"]\n', encoding="utf-8")
    (prof / "knowledge" / "BRAND.toml").write_text(
        "\n".join(
            [
                "[palette]",
                'primary = "#123456"',
                "[imagery]",
                'style = "calm, unhurried"',
                'negative = "no extra fingers"',
                "[disclosure]",
                'line = "Made with AI."',
                "[identity]",
                'soul_id = ""',
                'voice_id = ""',
            ]
        ),
        encoding="utf-8",
    )
    return root


# --- the lane matrix ---------------------------------------------------------------------------


def test_the_faceless_lane_needs_no_identity_handles_at_all():
    """The cold-start default. A new profile has no footage and no handles BY DEFINITION.

    Verified against render_engines.toml: `broll` binds higgsfield_i2v with
    requires = {output = "video"} and no identity constraint. video-storyboard generates the
    stills and video-render animates them, so the lane is self-supplying.
    """
    assert vp.LANE_REQUIREMENTS["short-form-video"].handles == ()
    assert not vp.LANE_REQUIREMENTS["short-form-video"].needs_footage


def test_the_presenter_lane_requires_the_heygen_keys_not_the_higgsfield_ones():
    """The exact defect that let the old fixed-pair preflight pass on an unrunnable profile.

    `presenter-video` renders on HeyGen via video-avatar. Auditing soul_id/voice_id — the
    Higgsfield-era pair — says nothing about whether it can run. Note heygen_voice_grade is a
    DIFFERENT key from voice_grade; they belong to different providers.
    """
    handles = vp.LANE_REQUIREMENTS["presenter-video"].handles
    assert "identity.heygen_avatar_id" in handles
    assert "identity.heygen_voice_grade" in handles
    assert "identity.soul_id" not in handles
    assert "identity.voice_id" not in handles


def test_every_lane_in_the_matrix_has_a_real_graph_file():
    """The matrix drifts silently if a variant is renamed. Pin it to the packs dir."""
    graphs = REPO / "packs" / "creator" / "graphs"
    for variant in vp.LANE_REQUIREMENTS:
        assert (graphs / f"{variant}.toml").is_file(), f"no graph for {variant!r}"


def test_a_blocked_lane_names_the_single_unlock_step():
    """A blocker without an unlock turns a routing decision into a dead end — the 2026-08-28
    failure. Every lane that can block must say what one action clears it."""
    for req in vp.LANE_REQUIREMENTS.values():
        if req.handles or req.needs_footage:
            assert req.unlock, f"{req.variant} can block but names no unlock"


# --- feasibility -------------------------------------------------------------------------------


def test_a_bare_profile_still_has_a_ready_default_lane(profiles: Path):
    """The whole ease-of-use claim: someone with nothing can ship today."""
    pf = vp.preflight("acme", profiles_root=profiles)
    assert pf.default_lane == "short-form-video"
    assert any(lane.variant == "short-form-video" and lane.ready for lane in pf.lanes)


def test_the_presenter_lane_is_blocked_on_a_bare_profile(profiles: Path):
    pf = vp.preflight("acme", profiles_root=profiles)
    lane = next(lane for lane in pf.lanes if lane.variant == "presenter-video")
    assert not lane.ready
    assert "heygen_avatar_id" in lane.blocked_reason
    assert "identity-kit" in lane.unlock


def test_an_empty_handle_counts_as_absent_not_as_set(profiles: Path):
    """In a product kit a present-but-empty value is an override-to-empty, not "inherit".
    Treating `soul_id = ""` as present is how an unrunnable lane reads as ready."""
    pf = vp.preflight("acme", profiles_root=profiles)
    kit_lane = next(lane for lane in pf.lanes if lane.variant == "restyle-shorts")
    assert "identity.restyle_preset_id" in kit_lane.missing_handles


def test_an_inactive_pack_blocks_every_lane(profiles: Path):
    """Fail-closed: reachability is decided by packs.toml, not by the graph file existing."""
    (profiles / "acme" / "packs.toml").write_text('active = ["marketing"]\n', encoding="utf-8")
    pf = vp.preflight("acme", profiles_root=profiles)
    assert pf.default_lane is None
    assert all(not lane.ready for lane in pf.lanes)


def test_a_profile_with_no_packs_file_reaches_nothing(profiles: Path):
    (profiles / "acme" / "packs.toml").unlink()
    pf = vp.preflight("acme", profiles_root=profiles)
    assert all(not lane.ready for lane in pf.lanes)


# --- constraints -------------------------------------------------------------------------------


def test_constraints_carry_the_kit_negatives_verbatim(profiles: Path):
    """imagery.negative is a CHECKLIST, not flavour. The 2026-08-28 concept proposed implied
    extra hands against a kit that forbids them — a mechanical check a human read straight past."""
    pf = vp.preflight("acme", profiles_root=profiles)
    assert pf.constraints.imagery_negative == "no extra fingers"
    assert pf.constraints.imagery_style == "calm, unhurried"


def test_an_undeclared_caption_preset_is_reported_as_unset_not_defaulted(profiles: Path):
    """captions.preset is not writable via `brandkit --set` (identity.* only), so it is normally
    a brief-level decision. Silently defaulting it is how a caption lands on a speaker's mouth."""
    pf = vp.preflight("acme", profiles_root=profiles)
    assert pf.constraints.captions_preset is None


def test_soul_training_photos_are_not_offered_as_broll(profiles: Path):
    """Identity TRAINING material is not a shot candidate — offering it is a category error."""
    training = profiles / "acme" / "knowledge" / "brand" / "soul-training"
    training.mkdir(parents=True)
    (training / "face.jpg").write_bytes(b"x")
    shots = profiles / "acme" / "knowledge" / "brand" / "product-screenshots"
    shots.mkdir(parents=True)
    (shots / "app.png").write_bytes(b"x")

    pf = vp.preflight("acme", profiles_root=profiles)
    assets = pf.constraints.existing_assets
    assert any("product-screenshots" in a for a in assets)
    assert not any("soul-training" in a for a in assets)


# --- CLI ---------------------------------------------------------------------------------------


def test_cli_exits_zero_when_a_lane_is_runnable(profiles: Path, capsys):
    code = vp.main(["--profile", "acme", "--profiles-root", str(profiles), "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["default_lane"] == "short-form-video"


def test_cli_exits_two_when_every_lane_is_blocked(profiles: Path, capsys):
    """Exit 2 is a REPORTABLE STATE, not a crash — same contract as render_engines."""
    (profiles / "acme" / "packs.toml").write_text("active = []\n", encoding="utf-8")
    code = vp.main(["--profile", "acme", "--profiles-root", str(profiles)])
    assert code == 2
    assert "BLOCKED" in capsys.readouterr().out


def test_cli_text_output_names_the_unlock_for_a_blocked_lane(profiles: Path, capsys):
    vp.main(["--profile", "acme", "--profiles-root", str(profiles)])
    out = capsys.readouterr().out
    assert "unlock:" in out and "identity-kit" in out


# --- VO capability (C1b) -----------------------------------------------------------------------
#
# The faceless lane is what the Step 1 menu recommends most confidently to a cold-start operator
# ("ready now, nothing needed from you"), and a bare profile has no voice_id BY DEFINITION. So the
# lane the router pushes hardest is the one that renders every shot and then plays silent. The
# preflight has to say so at routing time, and say it WITHOUT downgrading the lane: a captions-and-
# music cut is a legitimate deliverable. Ready-with-a-caveat, not blocked.


def test_a_profile_with_no_voice_id_reports_vo_unavailable(profiles: Path):
    pf = vp.preflight("acme", profiles_root=profiles)
    assert pf.constraints.vo_available is False


def test_the_faceless_lane_is_ready_but_caveated_without_a_voice(profiles: Path):
    """Ready-with-a-caveat is not blocked. Disclosing the scope of a deliverable is not the same
    as refusing it — a captions-only cut ships."""
    pf = vp.preflight("acme", profiles_root=profiles)
    lane = next(lane for lane in pf.lanes if lane.variant == "short-form-video")
    assert lane.ready, lane.blocked_reason
    assert lane.caveats, "the faceless lane must disclose that it cannot speak"
    assert any("voice" in c.lower() for c in lane.caveats), lane.caveats


def test_a_voice_id_clears_the_caveat(profiles: Path):
    """Positive control. Without it the caveat could be unconditional."""
    kit = profiles / "acme" / "knowledge" / "BRAND.toml"
    kit.write_text(
        kit.read_text(encoding="utf-8").replace('voice_id = ""', 'voice_id = "voice-abc-123"'),
        encoding="utf-8",
    )
    pf = vp.preflight("acme", profiles_root=profiles)
    assert pf.constraints.vo_available is True
    lane = next(lane for lane in pf.lanes if lane.variant == "short-form-video")
    assert lane.caveats == (), lane.caveats


def test_a_footage_lane_carries_no_vo_caveat(profiles: Path):
    """The caveat is keyed to lanes whose spoken audio comes from identity.voice_id. Footage
    brings its own audio, so warning about a missing voice there is noise."""
    pf = vp.preflight("acme", profiles_root=profiles)
    lane = next(lane for lane in pf.lanes if lane.variant == "repurpose-clips")
    assert lane.caveats == (), lane.caveats


def test_cli_text_output_shows_the_vo_caveat_on_the_faceless_lane(profiles: Path, capsys):
    """Operator-visible string, not just the dataclass — the menu is what anybody actually reads."""
    code = vp.main(["--profile", "acme", "--profiles-root", str(profiles)])
    assert code == 0
    out = capsys.readouterr().out
    assert "no voice-over" in out.lower(), out
    assert "identity-kit" in out, out


def test_the_json_output_carries_the_vo_fields(profiles: Path, capsys):
    code = vp.main(["--profile", "acme", "--profiles-root", str(profiles), "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["constraints"]["vo_available"] is False
    lane = next(entry for entry in payload["lanes"] if entry["variant"] == "short-form-video")
    assert lane["caveats"], lane


# --- derived, not restated (C3a / C3b) ---------------------------------------------------------
#
# Step 0.5 of the router used to hardcode both of these: "presenter share under 60%, at least one
# role:'screen'" and an eight-name list of upper-placement caption presets. Both drifted. The
# caption list drifted SILENTLY and the consequence is the one the placement rule exists to stop:
# `system_ember_duo` is upper-placement in gtm_core.captions and absent from the body's list, so a
# tenant on that preset would have had captions placed as if it were lower-placement, across the
# speaker's face.


def test_constraints_carry_the_live_shot_rule_set(profiles: Path):
    """The preflight relays what shots_lint publishes rather than a copy of it."""
    pf = vp.preflight("acme", profiles_root=profiles)
    assert pf.constraints.shot_rules, "no shot rules relayed from gtm_core.shots_lint"
    names = {rule["name"] for rule in pf.constraints.shot_rules}
    assert "_lint_audio_bed" in names, names
    assert "_lint_spoken_has_a_voice" in names, names


def test_the_presenter_ceiling_comes_from_the_linter_not_a_literal(profiles: Path):
    from gtm_core import shots_lint

    pf = vp.preflight("acme", profiles_root=profiles)
    assert pf.constraints.presenter_share_ceiling == shots_lint.MAX_PRESENTER_SHARE


def test_caption_placement_comes_from_the_captions_module(profiles: Path):
    """An upper-placement preset resolves through gtm_core.captions, not a copied list."""
    kit = profiles / "acme" / "knowledge" / "BRAND.toml"
    kit.write_text(
        kit.read_text(encoding="utf-8") + '\n[captions]\npreset = "system_indigo"\n',
        encoding="utf-8",
    )
    pf = vp.preflight("acme", profiles_root=profiles)
    assert pf.constraints.captions_preset == "system_indigo"
    assert pf.constraints.captions_placement == "upper"


def test_upper_placement_presets_match_the_captions_module_set(profiles: Path):
    """Fails the moment one is edited alone — the whole point of deriving rather than copying."""
    from gtm_core import captions

    pf = vp.preflight("acme", profiles_root=profiles)
    assert set(pf.constraints.upper_placement_presets) == set(captions._UPPER_PLACEMENT_PRESETS)


def test_system_ember_duo_resolves_to_upper_placement(profiles: Path):
    """Regression on the DEMONSTRATED drift, not a hypothetical one: this is the preset the
    router body's hand-copied eight-name list was missing."""
    kit = profiles / "acme" / "knowledge" / "BRAND.toml"
    kit.write_text(
        kit.read_text(encoding="utf-8") + '\n[captions]\npreset = "system_ember_duo"\n',
        encoding="utf-8",
    )
    pf = vp.preflight("acme", profiles_root=profiles)
    assert pf.constraints.captions_placement == "upper"
    assert "system_ember_duo" in pf.constraints.upper_placement_presets


def test_a_preset_with_no_upper_placement_resolves_lower(profiles: Path):
    """Positive control: the animated presets are genuinely not upper-placement, which is the
    real trade-off the body is allowed to keep describing."""
    kit = profiles / "acme" / "knowledge" / "BRAND.toml"
    kit.write_text(
        kit.read_text(encoding="utf-8") + '\n[captions]\npreset = "system_kinetic_typography"\n',
        encoding="utf-8",
    )
    pf = vp.preflight("acme", profiles_root=profiles)
    assert pf.constraints.captions_placement == "lower"


# --- the live-action lane (C8c) ----------------------------------------------------------------
#
# The distinction from `repurpose-clips` is the whole point: that lane needs footage UP FRONT,
# this one PRODUCES the footage as a step. So it is ready on a bare profile — no handles, no
# footage, no packs.toml edit — which makes it a legitimate cold-start option rather than an
# advanced one.


def test_the_live_action_lane_needs_no_identity_handles():
    """Nothing is synthesised, so no trained identity is involved at any point."""
    assert vp.LANE_REQUIREMENTS["live-action-video"].handles == ()


def test_the_live_action_lane_does_not_need_footage_up_front():
    """The distinction from repurpose-clips, and what makes it eligible as a cold-start answer."""
    assert vp.LANE_REQUIREMENTS["live-action-video"].needs_footage is False


def test_the_live_action_lane_is_ready_on_a_bare_profile(profiles: Path):
    pf = vp.preflight("acme", profiles_root=profiles)
    lane = next(lane for lane in pf.lanes if lane.variant == "live-action-video")
    assert lane.ready, lane.blocked_reason


def test_the_live_action_lane_carries_no_vo_caveat(profiles: Path):
    """The operator is the voice. Warning that this lane cannot speak would be nonsense."""
    pf = vp.preflight("acme", profiles_root=profiles)
    lane = next(lane for lane in pf.lanes if lane.variant == "live-action-video")
    assert lane.caveats == (), lane.caveats


# ── the story capture default (S5, 2026-09-07) ────────────────────────────────────────
#
# A story item's payoff beat needs a real face beside another person, which neither rendered lane
# can produce. That routing fact was true before this and stated nowhere, so an item carrying a
# protagonist could reach the faceless lane and end up with a still where the payoff should be.
#
# It is a CONSTRAINT, not a decision to ask: filled and labelled from the item. Contrast the
# approved-look block, which must be asked every run and is rendered apart from CONSTRAINTS.


def _story_item(protagonist: str = "a security lead who cannot attribute an agent's action"):
    return {
        "id": "ci-story-1",
        "pillar": "AI Trust",
        "story_id": "cluster-001",
        "platform": "linkedin",
        "format": "reel",
        "status": "approved",
        "brief": {"core_value": "control", "opposite": "drift", "protagonist": protagonist},
    }


def _plain_item():
    item = _story_item()
    item["brief"] = {"angle": "trust is an infra problem"}
    return item


def test_a_story_item_derives_the_live_action_capture_default(profiles: Path):
    """S5-T1. The item carries a protagonist, so the capture default is resolved and reported."""
    pf = vp.preflight("acme", profiles_root=profiles, item=_story_item())
    sc = pf.constraints.story_capture
    assert sc is not None, "a story item produced no capture default"
    assert sc.value == "live_action", f"expected live_action, got {sc.value!r}"
    assert sc.reason.strip(), (
        "the default carries no reason — an unexplained route is not relayable"
    )


def test_an_item_without_a_protagonist_derives_nothing(profiles: Path):
    """S5-T1. The negative half: most items are not stories and must route exactly as before."""
    pf = vp.preflight("acme", profiles_root=profiles, item=_plain_item())
    assert pf.constraints.story_capture is None, (
        "an item with no brief.protagonist produced a story-capture default — every ordinary "
        "explainer would then be routed to a shoot"
    )


def test_a_run_with_no_item_at_all_derives_nothing(profiles: Path):
    """S5-T1. The router runs this before an item exists; that path is unchanged."""
    pf = vp.preflight("acme", profiles_root=profiles)
    assert pf.constraints.story_capture is None


def test_the_capture_default_is_derived_never_operator(profiles: Path):
    """S5-T2. Provenance is the point: nobody was asked, and the brief must not claim they were."""
    sc = vp.preflight("acme", profiles_root=profiles, item=_story_item()).constraints.story_capture
    assert sc.source == "derived", (
        f"the story capture default claims source={sc.source!r}; only `derived` is honest here — "
        "`operator` would record a decision no human took"
    )


@pytest.mark.parametrize("blank", ["", "   ", None, 42])
def test_a_blank_protagonist_counts_as_absent(blank):
    """S5-T2. An empty handle is a real gap, not an unset one — the rule this module already uses."""
    item = _story_item()
    item["brief"]["protagonist"] = blank
    assert vp.story_capture(item) is None, (
        f"protagonist={blank!r} was read as a story item — a blank field would then route an "
        "ordinary piece to a shoot"
    )


def test_the_json_output_carries_the_story_capture_block(profiles: Path, tmp_path, capsys):
    """S5-T1. --json is what the router actually reads, and its keys are enumerated by hand."""
    item_path = tmp_path / "item.json"
    item_path.write_text(json.dumps(_story_item()), encoding="utf-8")
    code = vp.main(
        [
            "--profile",
            "acme",
            "--profiles-root",
            str(profiles),
            "--item-json",
            str(item_path),
            "--json",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    sc = payload["constraints"]["story_capture"]
    assert sc["value"] == "live_action" and sc["source"] == "derived", sc
    assert sc["reason"], "the JSON block carries no reason"


def test_the_json_output_reports_null_without_an_item(profiles: Path, capsys):
    """S5-T1. Positive control for the key itself: it is always present, sometimes null.

    A key that only appears on story items is indistinguishable, to a reader, from a key that was
    forgotten in `_as_dict` — which is exactly the silent error that hand-enumeration invites.
    """
    assert vp.main(["--profile", "acme", "--profiles-root", str(profiles), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["constraints"]["story_capture"] is None


def test_an_unreadable_item_is_an_error_not_a_silent_absence(profiles: Path, tmp_path, capsys):
    """S5-T1. A mistyped path must not read as "not a story item"."""
    code = vp.main(
        [
            "--profile",
            "acme",
            "--profiles-root",
            str(profiles),
            "--item-json",
            str(tmp_path / "nope.json"),
            "--json",
        ]
    )
    assert code == 1, "a missing --item-json path exited 0 — the routing decision would be taken "
    assert "error" in json.loads(capsys.readouterr().out)


def test_a_json_array_is_refused_rather_than_read_as_an_item(profiles: Path, tmp_path, capsys):
    """S5-T1. A plan FILE is a list of items; handing one over is the likely mistake."""
    path = tmp_path / "plan.json"
    path.write_text(json.dumps([_story_item()]), encoding="utf-8")
    code = vp.main(
        ["--profile", "acme", "--profiles-root", str(profiles), "--item-json", str(path), "--json"]
    )
    assert code == 1, "a whole plan file was accepted as one ContentItem"


def test_the_capture_line_sits_under_constraints_not_under_the_look_decision(
    profiles: Path, capsys
):
    """S5-T1. Placement is the semantic claim: a constraint is filled, a decision is asked.

    Printed under APPROVED AVATAR LOOK it would read as a question to put to the operator, which
    is precisely what a derived default is not.
    """
    item = _story_item()
    vp.main(["--profile", "acme", "--profiles-root", str(profiles)])  # warm the no-item path
    capsys.readouterr()
    pf = vp.preflight("acme", profiles_root=profiles, item=item)
    text = vp._render_text(pf)
    assert "story capture" in text, "the capture default never reached the text render"
    assert text.index("CONSTRAINTS") < text.index("story capture"), (
        "the story-capture line is printed above CONSTRAINTS — it would read as part of the "
        "approved-look DECISION block, which is asked every run rather than derived"
    )


def test_the_story_capture_default_never_changes_lane_readiness(profiles: Path):
    """S5-T5. It is a constraint, not a gate: the same lanes are ready with and without it."""
    without = vp.preflight("acme", profiles_root=profiles)
    with_item = vp.preflight("acme", profiles_root=profiles, item=_story_item())
    assert [lane.variant for lane in with_item.ready_lanes] == [
        lane.variant for lane in without.ready_lanes
    ], "passing an item changed which lanes are runnable — the default must bound, never block"


# --- K7: caption_mode derivation -------------------------------------------------------------


def test_caption_mode_narrative_when_no_voice(profiles: Path):
    """K7: preflight on a profile/lane with no voice derives caption_mode = 'narrative'."""
    pf = vp.preflight("acme", profiles_root=profiles)
    assert pf.constraints.caption_mode == "narrative"

    # Also check text render and dict serialization
    rendered = vp._render_text(pf)
    assert "caption mode      narrative" in rendered
    d = vp._as_dict(pf)
    assert d["constraints"]["caption_mode"] == "narrative"


def test_caption_mode_subtitles_for_presenter_lane(profiles: Path):
    """K7: preflight with --lane presenter-video derives caption_mode = 'subtitles' when voiced."""
    # When lane is specified as presenter-video and vo_available is True
    root = profiles
    brand = root / "acme" / "knowledge" / "BRAND.toml"
    brand.write_text(
        brand.read_text(encoding="utf-8")
        .replace('voice_id = ""', 'voice_id = "v123"')
        .replace('soul_id = ""', 'soul_id = "s123"'),
        encoding="utf-8",
    )
    pf = vp.preflight("acme", profiles_root=profiles, lane="presenter-video")
    assert pf.constraints.caption_mode == "subtitles"


def test_ffmpeg_availability_reported_in_constraints_and_render(profiles: Path, monkeypatch):
    """ffmpeg detection is surfaced in constraints, JSON dict, and human-readable text.

    Patches ``video_lint.ffmpeg_available`` — video_preflight owns no ``shutil.which`` call
    of its own, per tests/contracts/test_ffmpeg_module_boundary.py, and reports availability
    via that shared helper instead."""
    # When ffmpeg is found
    monkeypatch.setattr(vp.video_lint, "ffmpeg_available", lambda: True)
    pf_installed = vp.preflight("acme", profiles_root=profiles)
    assert pf_installed.constraints.ffmpeg_available is True
    assert vp._as_dict(pf_installed)["constraints"]["ffmpeg_available"] is True
    assert "ffmpeg            installed" in vp._render_text(pf_installed)

    # When ffmpeg is missing
    monkeypatch.setattr(vp.video_lint, "ffmpeg_available", lambda: False)
    pf_missing = vp.preflight("acme", profiles_root=profiles)
    assert pf_missing.constraints.ffmpeg_available is False
    assert vp._as_dict(pf_missing)["constraints"]["ffmpeg_available"] is False
    assert "ffmpeg            NOT FOUND" in vp._render_text(pf_missing)
