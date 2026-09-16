"""C2 — the creator brief validates, cross-examines its shot list, and adds no gate.

The four properties under test are the four the module exists to hold: the brief is DATA (not prose
a skill may ignore), every decision carries its PROVENANCE, it CROSS-EXAMINES rather than
self-certifies (§R11), and it introduces NO GATE.

Every refusal here carries its positive control (§R12): a suite that only proves what is refused
would still pass if the writer were disabled entirely.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from gtm_core import creator_brief as cb

REPO = Path(__file__).resolve().parents[2]


def _decision(value, source="operator"):
    return {"value": value, "source": source}


def _brief(**overrides) -> dict:
    """A complete, schema-valid brief. Fictional throughout (§R9)."""
    doc = {
        "item_id": "item-2026-09-06-a",
        "script_slug": "2026-09-06-quiet-handoff",
        "written_on": "2026-09-06",
        "profile": "testco",
        "decisions": {
            "cover": _decision(
                {
                    "subject": "a founder mid-sentence at a whiteboard",
                    "headline": "The handoff nobody wrote down",
                    "palette": "warm neutrals",
                    "framing": "tight, off-centre",
                }
            ),
            "outlier_structure": _decision(
                {
                    "name": "cold open → reversal → proof → ask",
                    "beats": ["cold open", "reversal", "proof", "ask"],
                    "sampled": 4,
                },
                "derived",
            ),
            "slot_schema": _decision(
                ["hook", "problem", "demo", "proof", "ask"], "profile_default"
            ),
            "cheapest_medium": _decision(
                {
                    "chosen": "video",
                    "considered": ["carousel", "still"],
                    "reason": "the reversal only reads as motion; a still shows one state",
                },
                "derived",
            ),
            "capture_mode": _decision("rendered"),
            "invariant": _decision(
                {
                    "look": "editorial monochrome, fine grain, shallow depth of field",
                    "aspect_ratio": "9:16",
                    "negative": "text artifacts, logos",
                },
                "profile_default",
            ),
            "broll_list": _decision(["hands closing a laptop", "an empty meeting room at dusk"]),
            "sampling_curve": _decision(
                {
                    "per_shot": [{"shot": 1, "n": 3}, {"shot": 2, "n": 1}],
                    "criterion": "operator picks from a contact sheet of siblings",
                }
            ),
            "visual_hook": _decision(
                {
                    "subject": "a founder mid-sentence",
                    "framing": "medium close, eye level",
                    "focus_plane": "the eyes",
                    "expression": "caught mid-thought",
                    "recognisable_element": "a standard laptop on the desk",
                }
            ),
        },
    }
    doc["decisions"].update(overrides.pop("decisions", {}))
    doc.update(overrides)
    return doc


def _shots(**overrides) -> dict:
    doc = {
        "source_item": "item-2026-09-06-a",
        "total_duration_s": 9,
        "deliverable_ratios": ["9:16"],
        "style_scaffold": {
            "look": "editorial monochrome, fine grain, shallow depth of field",
            "provider_model": "wan2_7",
        },
        "shots": [
            {
                "duration_s": 5,
                "camera": "static, eye level",
                "visual": "a founder at a whiteboard",
                "motion_prompt": "she turns from the board and speaks to camera",
                "role": "presenter",
                # The hook frame's face. The brief decides one (`visual_hook.expression`), so the
                # shot list has to carry one too, or the frame renders with whatever the identity
                # anchor's source photo holds — which is what `check_against_shotlist` refuses.
                "expression": "caught mid-thought — the brows a few millimetres up, gaze coming "
                "back to the lens",
            },
            {
                "duration_s": 4,
                "camera": "handheld, medium",
                "visual": "hands closing a laptop",
                "motion_prompt": "the lid comes down and the hands withdraw",
                "role": "broll",
            },
        ],
    }
    doc.update(overrides)
    return doc


# ── C2-T1 · the schema, and refusal at write time ─────────────────────────────────────


def test_a_complete_brief_validates_and_round_trips(tmp_path, monkeypatch):
    """Positive control for every refusal below — without it they would pass on a broken writer."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    doc = _brief()
    path = cb.brief_path("testco", doc["script_slug"])
    written = cb.write(path, doc)
    assert json.loads(written.read_text()) == doc


@pytest.mark.parametrize("missing", cb.DECISIONS)
def test_a_brief_missing_any_one_decision_is_refused(missing, tmp_path, monkeypatch):
    """C2-T1 — all nine are required, and the refusal happens at WRITE time, not at read time.

    A half-written brief on disk is read downstream as a complete one, and the contradiction then
    surfaces after the spend this artifact exists to precede.
    """
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    doc = _brief()
    del doc["decisions"][missing]
    path = cb.brief_path("testco", doc["script_slug"])
    with pytest.raises(cb.BriefError) as excinfo:
        cb.write(path, doc)
    assert missing in str(excinfo.value)
    assert not path.exists(), "a brief that does not validate must leave no file behind"


def test_an_unknown_decision_key_is_refused():
    """`additionalProperties: false` — a typo'd decision must not read as a tenth one."""
    doc = _brief()
    doc["decisions"]["covr"] = _decision({"subject": "x", "headline": "y"})
    assert any("covr" in e for e in cb.validate(doc))


# ── C2-T2/T3 · cross-examination, §R11 ────────────────────────────────────────────────


def test_agreeing_brief_and_shot_list_report_nothing():
    """Positive control for the four contradictions below."""
    assert cb.check_against_shotlist(_brief(), _shots()) == []


def test_a_live_action_brief_beside_a_rendered_shot_list_fails_closed():
    """C2-T2 — the modes must agree; one of them is about to be shot the wrong way."""
    brief = _brief(decisions={"capture_mode": _decision("live_action")})
    problems = cb.check_against_shotlist(brief, _shots())
    assert problems and "capture_mode" in problems[0]


def test_a_live_action_brief_beside_engine_bearing_shots_is_refused():
    """C2-T3 — a live-action lane resolves no engine, so an engine-bearing list is a contradiction."""
    brief = _brief(decisions={"capture_mode": _decision("live_action")})
    shots = _shots(capture_mode="live_action")
    shots["shots"][0]["engine"] = "higgsfield_i2v"
    problems = cb.check_against_shotlist(brief, shots)
    assert any("render engine" in p for p in problems), problems


def test_a_live_action_brief_beside_a_synthetic_disclosure_is_refused():
    """Claiming synthesis on real footage is a FALSE provenance statement, not a cautious one."""
    brief = _brief(decisions={"capture_mode": _decision("live_action")})
    shots = _shots(capture_mode="live_action", synthetic_disclosure="Made with AI.")
    assert any("synthetic_disclosure" in p for p in cb.check_against_shotlist(brief, shots))


def test_the_invariant_reaches_the_shot_lists_style_scaffold():
    """C2-T4 — a field nobody reads is the failure this test exists for.

    The brief's chosen invariant must be the look actually being rendered; changing it in the brief
    and not in the shot list is a contradiction, not a nuance.
    """
    brief = _brief(
        decisions={
            "invariant": _decision(
                {"look": "hard studio light, deep shadows", "aspect_ratio": "9:16"}
            )
        }
    )
    problems = cb.check_against_shotlist(brief, _shots())
    assert any("style_scaffold" in p for p in problems), problems


def test_an_aspect_ratio_nothing_delivers_is_reported():
    """The composition was framed for a ratio the run does not produce."""
    brief = _brief(
        decisions={
            "invariant": _decision(
                {
                    "look": "editorial monochrome, fine grain, shallow depth of field",
                    "aspect_ratio": "16:9",
                }
            )
        }
    )
    assert any("aspect_ratio" in p for p in cb.check_against_shotlist(brief, _shots()))


def test_a_sampling_curve_budgeting_a_shot_that_does_not_exist_is_reported():
    """Spend planned against a shot nobody wrote is spend nobody will notice going missing."""
    brief = _brief(
        decisions={
            "sampling_curve": _decision(
                {
                    "per_shot": [{"shot": 1, "n": 3}, {"shot": 7, "n": 2}],
                    "criterion": "operator picks from a contact sheet",
                }
            )
        }
    )
    assert any("does not exist" in p for p in cb.check_against_shotlist(brief, _shots()))


# ── C2-T5 · the caller exists ─────────────────────────────────────────────────────────


def test_video_script_actually_reads_the_brief():
    """C2-T5 — the "declared contract nobody runs" guard, on the real call site.

    Not a mock: the shipped body must name the check command, and the CLI verb it names must
    dispatch to the checker. A brief every skill ignores is prose with a schema.
    """
    body_file = REPO / "plugin/skills/video-script/body_template.md"
    if not body_file.is_file():
        pytest.skip(
            "video-script/body_template.md not present in this distribution (paid-tier stub)"
        )
    body = body_file.read_text(encoding="utf-8")
    assert "gtm_core.creator_brief check" in body, (
        "video-script's body does not run the brief↔shot-list check — the brief would be written "
        "and never read"
    )
    assert "brief.json" in body

    proc = subprocess.run(
        [sys.executable, "-m", "gtm_core.creator_brief", "check", "--help"],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    assert proc.returncode == 0, proc.stderr
    assert "cross-examine" in proc.stdout.lower(), proc.stdout


def test_the_check_verb_refuses_a_contradiction_from_the_command_line(tmp_path):
    """Exit 2 is what makes `video-script` fail closed rather than warn."""
    brief = tmp_path / "brief.json"
    shots = tmp_path / "x.shots.json"
    brief.write_text(json.dumps(_brief(decisions={"capture_mode": _decision("live_action")})))
    shots.write_text(json.dumps(_shots()))
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "gtm_core.creator_brief",
            "check",
            "--brief",
            str(brief),
            "--shots",
            str(shots),
        ],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    assert proc.returncode == 2, proc.stdout

    shots.write_text(json.dumps(_shots(capture_mode="live_action")))
    ok = subprocess.run(
        [
            sys.executable,
            "-m",
            "gtm_core.creator_brief",
            "check",
            "--brief",
            str(brief),
            "--shots",
            str(shots),
        ],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    assert ok.returncode == 0, ok.stdout


# ── C2-T6 · no new gate ───────────────────────────────────────────────────────────────


def test_the_skill_body_emits_no_gate_marker():
    """C2-T6 — the brief is approved WITH the plan, never on its own."""
    body_file = REPO / "plugin/skills/creator-brief/body_template.md"
    if not body_file.is_file():
        pytest.skip(
            "creator-brief/body_template.md not present in this distribution (paid-tier stub)"
        )
    body = body_file.read_text(encoding="utf-8")
    generated = (REPO / "plugin/skills/creator-brief/SKILL.md").read_text(encoding="utf-8")
    for text, name in ((body, "body_template.md"), (generated, "SKILL.md")):
        assert "⟦GATE:" not in text.replace("⟦GATE:…⟧", ""), f"{name} emits a gate marker"


# ── C2-T7 · tenant confinement ────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["../escape", "a/b", "..", "", "nul\x00"])
def test_an_unsafe_profile_or_slug_is_refused_before_any_path_is_built(bad, tmp_path, monkeypatch):
    """C2-T7 — this path is where tenant PII lands; a traversal here is the tenant-boundary error."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    with pytest.raises(ValueError):
        cb.run_dir(bad, "2026-09-06-x")
    with pytest.raises(ValueError):
        cb.run_dir("testco", bad)


def test_a_normal_profile_and_slug_resolve_under_the_content_root(tmp_path, monkeypatch):
    """Positive control for the refusals above."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    path = cb.run_dir("testco", "2026-09-06-quiet-handoff")
    assert path == tmp_path / "content" / "testco" / "video" / "2026-09-06-quiet-handoff"


def test_a_brief_for_one_profile_is_not_found_while_bound_to_another(tmp_path, monkeypatch):
    """Tenant isolation: profile A's brief is invisible to a run bound to profile B."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    doc = _brief()
    cb.write(cb.brief_path("testco", doc["script_slug"]), doc)
    assert not cb.brief_path("othercо".replace("о", "o"), doc["script_slug"]).exists()


def test_a_write_outside_the_content_root_is_refused(tmp_path, monkeypatch):
    """The writer confines like every other writer in the tree, on the resolved root."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    (tmp_path / "content").mkdir()
    with pytest.raises(cb.BriefError) as excinfo:
        cb.write(tmp_path / "elsewhere" / "brief.json", _brief())
    assert "outside the resolved content root" in str(excinfo.value)


# ── C2-T10 · provenance ───────────────────────────────────────────────────────────────


def test_a_decision_with_a_value_but_no_source_is_refused():
    """C2-T10 — every decision carries where it came from."""
    doc = _brief()
    doc["decisions"]["capture_mode"] = {"value": "rendered"}
    assert any("source" in e for e in cb.validate(doc))


def test_an_all_default_brief_is_legal_and_says_so():
    """A brief complete because everything defaulted must not read as if a person decided.

    Schema-valid, every field filled, and nobody chose any of it — the silent case in §3.4 #16.
    """
    doc = _brief()
    for name in cb.DECISIONS:
        doc["decisions"][name]["source"] = "profile_default"
    assert cb.validate(doc) == []
    assert cb.all_defaulted(doc) is True
    assert "Nobody chose any of it" in cb.markdown_twin(doc)


def test_a_brief_with_one_operator_decision_does_not_claim_to_be_all_default():
    """Positive control for the sentence above."""
    doc = _brief()
    for name in cb.DECISIONS:
        doc["decisions"][name]["source"] = "profile_default"
    doc["decisions"]["capture_mode"]["source"] = "operator"
    assert cb.all_defaulted(doc) is False
    assert "Nobody chose any of it" not in cb.markdown_twin(doc)


# ── the markdown twin is derived ──────────────────────────────────────────────────────


def test_the_twin_is_derived_and_regenerating_it_is_byte_identical(tmp_path, monkeypatch):
    """One of the pair must be the source. Editing the twin changes nothing any skill reads."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    doc = _brief()
    written = cb.write(cb.brief_path("testco", doc["script_slug"]), doc)
    twin = cb.write_twin(doc, path=written.parent / cb.TWIN_FILENAME)
    first = twin.read_bytes()
    twin.write_text("hand-edited nonsense\n")
    assert cb.write_twin(doc, path=twin).read_bytes() == first
    assert cb.check_against_shotlist(json.loads(written.read_text()), _shots()) == []


def test_the_twin_names_every_decision_and_its_source():
    """Nine lines, one per decision — what a non-marketer reads on a phone."""
    twin = cb.markdown_twin(_brief())
    for name in cb.DECISIONS:
        assert cb._DECISION_TITLES[name] in twin
    assert twin.count("_(") >= len(cb.DECISIONS)


# ── the deterministic half of the reader (C2-T9's key-free control) ───────────────────


def test_a_filled_brief_reports_no_hollow_findings():
    """Positive control: the heuristic must be capable of staying quiet."""
    assert cb.hollow_findings(_brief()) == []


def test_a_structure_that_names_no_structure_is_flagged():
    """ "The usual" is the average wearing an outlier's clothes."""
    doc = _brief(
        decisions={
            "outlier_structure": _decision({"name": "the usual", "beats": ["open", "close"]})
        }
    )
    findings = cb.hollow_findings(doc)
    assert any(f["axis"] == "structure_is_named_not_described" for f in findings), findings


def test_a_one_word_invariant_is_flagged():
    """An invariant a viewer could not SEE holding is not an invariant."""
    doc = _brief(decisions={"invariant": _decision({"look": "clean", "aspect_ratio": "9:16"})})
    assert any(f["axis"] == "the_invariant_is_visible" for f in cb.hollow_findings(doc))


def test_a_circular_cheapest_medium_reason_is_flagged():
    """ "Because it is a video" is the answer that makes the question pointless."""
    doc = _brief(
        decisions={
            "cheapest_medium": _decision({"chosen": "video", "reason": "it is video"}),
        }
    )
    assert any(f["axis"] == "cheapest_medium_reason_holds" for f in cb.hollow_findings(doc))


# ── the story graph as a legal structure (S4, 2026-09-07) ─────────────────────────────
#
# The PRD's S4-T2 said a brief named "a story" would be refused by _HOLLOW_STRUCTURE_WORDS on the
# `check` verb. Both halves were wrong and are corrected in that PRD's §0: the matcher is an exact
# token intersection over nine words, none of which is "a" or "story", and the hollow findings are
# reached by the `hollow` verb (diagnostic, always exit 0) rather than by `check` (which
# cross-examines a brief against its shot list). The property S4 actually needs is below: the nine
# beats are a legal, non-hollow structure, and the checker that accepts them still rejects a hollow
# one.

_STORY_GRAPH_NAME = (
    "main character → inciting incident → goal → debate → decision 1 → "
    "escalating conflict → the low point → decision 2 → goal achieved"
)
_STORY_GRAPH_BEATS = [
    "main character",
    "inciting incident",
    "goal",
    "debate",
    "decision 1",
    "escalating conflict",
    "the low point",
    "decision 2",
    "goal achieved, message revealed",
]


def _story_graph_brief():
    return _brief(
        decisions={
            "outlier_structure": _decision(
                {"name": _STORY_GRAPH_NAME, "beats": _STORY_GRAPH_BEATS}, "model"
            )
        }
    )


def test_the_story_graph_is_a_legal_named_structure():
    """S4-T2. The nine beats pass the named-structure check the brief already enforces."""
    findings = cb.hollow_findings(_story_graph_brief())
    named = [f for f in findings if f["axis"] == "structure_is_named_not_described"]
    assert not named, (
        f"the story graph was refused as an unnamed structure: {named}. The reference exists to "
        "supply decision 2 a beat order it can legally commit to"
    )


def test_the_story_graph_brief_validates_and_survives_a_write(tmp_path, monkeypatch):
    """S4-T2. It is not merely non-hollow: the CLI writes it, so a run can actually record it."""
    content_root = tmp_path / "content"
    monkeypatch.setattr(cb, "resolve_content_root", lambda *a, **k: content_root)
    doc = tmp_path / "doc.json"
    doc.write_text(json.dumps(_story_graph_brief()), encoding="utf-8")
    code = cb.main(["write", "--profile", "testco", "--doc", str(doc)])
    assert code == 0, "the story-graph brief did not survive `creator_brief write`"


def test_the_structure_check_still_rejects_a_hollow_name():
    """S4-T2. Positive control: accepting the nine beats did not blunt the check.

    A test that only showed the graph passing would also pass against a checker that had stopped
    checking anything at all.
    """
    doc = _brief(
        decisions={
            "outlier_structure": _decision({"name": "the usual", "beats": _STORY_GRAPH_BEATS})
        }
    )
    findings = cb.hollow_findings(doc)
    assert any(f["axis"] == "structure_is_named_not_described" for f in findings), (
        "'the usual' was accepted as a named structure — the checker that let the story graph "
        "through is no longer refusing anything"
    )


def test_a_two_beat_floor_still_applies_to_the_story_graph_shape():
    """S4-T2. Positive control on the OTHER branch: a named order with one beat is still a label."""
    doc = _brief(
        decisions={
            "outlier_structure": _decision({"name": _STORY_GRAPH_NAME, "beats": ["the low point"]})
        }
    )
    findings = cb.hollow_findings(doc)
    assert any(f["axis"] == "structure_is_named_not_described" for f in findings), (
        "a one-beat 'story graph' passed — the >=2 beat floor is what stops a name standing in "
        "for an order"
    )


# ── C2-T7 · the hook frame's face, corroborated across the two artifacts ───────────────


def test_a_decided_expression_with_none_on_shot_one_is_a_contradiction():
    """The brief is the only place a face is decided before spend, and shot 1 is the only shot
    both artifacts describe. A decision that never reached the list is a decision nobody took."""
    shots = _shots()
    shots["shots"][0].pop("expression")
    problems = cb.check_against_shotlist(_brief(), shots)
    assert len(problems) == 1
    assert "carries none" in problems[0]


def test_a_brief_that_decided_no_expression_is_quiet():
    """Negative control. The field is optional, and a brief that left it open is not a
    contradiction — there is nothing for the shot list to disagree with."""
    brief = _brief()
    brief["decisions"]["visual_hook"]["value"].pop("expression")
    shots = _shots()
    shots["shots"][0].pop("expression")
    assert cb.check_against_shotlist(brief, shots) == []


def test_the_two_are_never_compared_as_text():
    """The brief and the shot list are written by different steps in different words. Comparing
    the strings would refuse every legitimate rewording, so only ABSENCE is a finding."""
    shots = _shots()
    shots["shots"][0]["expression"] = "the head stopping, one blink held, mouth closed"
    assert cb.check_against_shotlist(_brief(), shots) == []


def test_the_twin_carries_the_hook_expression():
    """It is the only face direction in the whole brief; a twin that drops it shows the operator
    a hook with no face and no sign that one was chosen."""
    assert "caught mid-thought" in cb.markdown_twin(_brief())


# ── C2-T8 · caption_voice: optional 10th decision, corroborated against shot list ───────


def test_caption_voice_validates_and_renders_in_twin():
    doc = _brief(
        decisions={
            "caption_voice": _decision(
                {
                    "mode": "narrative",
                    "voice": "close narrator",
                    "thread": [{"setup": 1, "callback": 2, "word": "quiet"}],
                    "anchor_beats": [1],
                }
            )
        }
    )
    assert cb.validate(doc) == []
    twin = cb.markdown_twin(doc)
    assert "Caption voice" in twin
    assert "narrative (close narrator)" in twin
    assert "quiet (1→2)" in twin


def test_caption_voice_narrative_mode_requires_narrative_captions_in_shotlist():
    doc = _brief(
        decisions={
            "caption_voice": _decision(
                {
                    "mode": "narrative",
                    "voice": "close narrator",
                }
            )
        }
    )
    # _shots() has no narrative captions (only visual/motion)
    shots = _shots()
    problems = cb.check_against_shotlist(doc, shots)
    assert any("has no narrative captions" in p for p in problems)


def test_caption_voice_thread_corroboration():
    doc = _brief(
        decisions={
            "caption_voice": _decision(
                {
                    "mode": "narrative",
                    "voice": "close narrator",
                    "thread": [{"setup": 1, "callback": 2, "word": "quiet"}],
                }
            )
        }
    )
    shots = _shots()
    # Add narrative captions with the thread word
    shots["shots"][0]["caption_text_override"] = "From outside, quiet looks like gone."
    shots["shots"][0]["caption_function"] = "setup"
    shots["shots"][0]["caption_pairs_with"] = 2
    shots["shots"][1]["caption_text_override"] = "The quiet ended here."
    shots["shots"][1]["caption_function"] = "callback"
    shots["shots"][1]["caption_pairs_with"] = 1

    # Positive control: thread word is present in both setup and callback
    assert cb.check_against_shotlist(doc, shots) == []

    # Negative control: missing in callback
    shots["shots"][1]["caption_text_override"] = "The silence ended here."
    problems = cb.check_against_shotlist(doc, shots)
    assert any("declares word 'quiet' in callback shot 2" in p for p in problems)


def test_caption_voice_hollow_findings():
    doc = _brief(
        decisions={
            "caption_voice": _decision(
                {
                    "mode": "invalid_mode",
                    "voice": "",
                }
            )
        }
    )
    findings = cb.hollow_findings(doc)
    axes = [f["axis"] for f in findings]
    assert "caption_voice_is_declared" in axes
    assert "caption_mode_is_valid" in axes
