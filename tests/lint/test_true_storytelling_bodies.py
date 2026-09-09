"""Body regressions for the true-storytelling workstreams (PRD 2026-09-07, S1-S9).

Six of the nine workstreams are prose, and a prose rule's silent failure is that it stops being
loaded: a check whose numbering drifted so "re-run all N" points at the wrong N, an item list that
lost its sixth entry, a reference that lost its citation. Each test below pins one property of one
section, and every absence-check carries the positive control that proves the reader reached the
section at all (§R12).

Sections are matched by heading rather than scanned whole-file, so a string that happens to appear
elsewhere in a long body cannot make a test pass for the wrong reason.

Design: the 2026-09-07 true-storytelling method-integration PRD, §3.2 (S1-S9 test plan).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from gtm_core.gating import stub_list

REPO = Path(__file__).resolve().parents[2]
SKILLS = REPO / "plugin" / "skills"

CASE_STUDY = SKILLS / "case-study" / "body_template.md"
VIDEO_SCRIPT = SKILLS / "video-script" / "body_template.md"
CONTENT_PLAN = SKILLS / "content-plan" / "body_template.md"
INTERVIEW_PROTOCOL = SKILLS / "case-study" / "references" / "interview-protocol.md"
RETENTION_RUBRIC = SKILLS / "content-plan" / "references" / "retention-rubric.md"

#: The exact sentence S2 shipped for check 5's first half. Frozen as a control: S3 re-points that
#: half at `brief.core_value`, and the derivation must survive as the fallback for an item that
#: carries no field. A test that only looked for `brief.core_value` would pass on a check 5 that
#: had silently lost the ability to run at all on a plain item.
_S2_CHECK_5_DERIVATION = "Name, in one word, the human value the beats are *about*"

#: The three beats check 6 points at. Losing any one of them turns the bragging test back into the
#: accomplishment story it exists to refuse.
_S2_CHECK_6_BEATS = ("the first decision", "the low point", "the second decision")


def _body(path: Path) -> str:
    """Read a skill body, skipping when the OSS carve stubbed that skill away."""
    skill = path.parent.name if path.name == "body_template.md" else path.parents[1].name
    if skill in stub_list() and not path.is_file():
        pytest.skip(f"{skill} is withheld from this build (OSS carve)")
    assert path.is_file(), f"{path.relative_to(REPO)} is missing"
    return path.read_text(encoding="utf-8")


def _section(text: str, heading: str, *, level: str = "##") -> str:
    """The body of one heading, up to the next heading of the same level or EOF."""
    pattern = rf"^{re.escape(level)} {re.escape(heading)}\s*$\n(.*?)(?=^{re.escape(level)} |\Z)"
    m = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    assert m, f"heading {heading!r} not found — the section this test pins was renamed or removed"
    return m.group(1)


def _numbered_items(section: str) -> list[str]:
    """The `N. ` items of a numbered list, in order, with their continuation lines."""
    items: list[str] = []
    current: list[str] | None = None
    for line in section.splitlines():
        if re.match(r"^\d+\. ", line):
            if current is not None:
                items.append("\n".join(current))
            current = [line]
        elif current is not None:
            if line and not line.startswith((" ", "\t", ">")):
                items.append("\n".join(current))
                current = None
            else:
                current.append(line)
    if current is not None:
        items.append("\n".join(current))
    return items


# ------------------------------------------------------------------ the section reader itself


def test_the_section_reader_finds_a_body_and_excludes_its_neighbour():
    """Positive control on the helper every other test depends on.

    An absence-check run through a broken `_section` would pass on an empty string, so prove the
    reader returns real content AND stops at the next heading before trusting any of it.
    """
    text = _body(VIDEO_SCRIPT)
    step_1_6 = _section(text, "Step 1.6 — Comprehension and fingerprint self-check (before saving)")
    assert len(step_1_6) > 500, "the Step 1.6 section came back nearly empty — the reader is broken"
    assert "## Step 2 —" not in step_1_6, "the reader ran past its own heading into the next step"


# ------------------------------------------------------------------ S1 — the interview protocol


def test_case_study_step_1_enumerates_six_intake_items():
    """S1-T2. Item 6 (the interview) is part of intake, not an appendix nobody reaches."""
    section = _section(_body(CASE_STUDY), "Step 1 — Intake & evidence tier")
    items = _numbered_items(section)
    assert len(items) == 6, (
        f"Step 1 lists {len(items)} intake items, expected 6 — item 6 is the interview protocol, "
        "and an intake step that lost it goes back to asking for 'a story'"
    )


def test_case_study_intake_item_six_names_the_interview_protocol():
    """S1-T2. The protocol is reachable by name from the step that must run it."""
    section = _section(_body(CASE_STUDY), "Step 1 — Intake & evidence tier")
    item_6 = _numbered_items(section)[5]
    assert "interview-protocol.md" in item_6, (
        "Step 1 item 6 no longer names references/interview-protocol.md — the protocol is loaded "
        f"by that citation and nothing else. Item 6 reads: {item_6[:120]!r}"
    )
    assert "before" in item_6.lower(), (
        "item 6 must say the protocol runs BEFORE Step 4's research — research verifying a story "
        "that does not exist yet is the failure the ordering prevents"
    )


def test_case_study_step_8_lint_names_the_three_classifier_beats():
    """S1-T2. The anecdote/accomplishment/true-story classifier survives at validation."""
    section = _section(_body(CASE_STUDY), "Step 8 — Validate before calling it done")
    lowered = section.lower()
    for beat in ("decision 1", "the low point", "decision 2"):
        assert beat in lowered, (
            f"Step 8's structural lints no longer name {beat!r} — without all three the classifier "
            "cannot tell an accomplishment piece from a story, which is the whole check"
        )
    assert "interview-protocol.md" in section, (
        "Step 8's classifier bullet must cite the protocol it re-runs, or the two drift apart"
    )


def test_the_interview_protocol_carries_its_provenance_note():
    """S1-T3. The reference states its source as a third-party course and names nobody.

    The de-brand lint proves OUR name is gone; it can never prove someone else's is. This pins the
    §R9 shape the file shipped with.
    """
    text = _body(INTERVIEW_PROTOCOL)
    lowered = text.lower()
    assert "provenance" in lowered, "the interview protocol lost its provenance note"
    assert "third-party" in lowered and "course" in lowered, (
        "the provenance note must say the questions are adapted from a third-party course — "
        "a heuristic with no stated standing reads as this system's own measured finding"
    )
    assert "retention-rubric.md" in text, (
        "the note must back-reference retention-rubric.md's evidence note for its standing"
    )


# ------------------------------------------------------------------ S2 — the Step 1.6 checks

_STEP_1_6 = "Step 1.6 — Comprehension and fingerprint self-check (before saving)"


def test_step_1_6_has_exactly_six_numbered_checks():
    """S2-T1. The count in the list is the ground truth the other two assertions are checked against."""
    section = _section(_body(VIDEO_SCRIPT), _STEP_1_6)
    items = _numbered_items(section)
    assert len(items) == 6, (
        f"Step 1.6 lists {len(items)} checks, expected 6 — checks 5 (story-washing) and 6 "
        "(bragging) are the two that catch a payoff belonging to a different story"
    )


def test_step_1_6_intro_says_six_checks():
    """S2-T1. The prose count matches the list count — the numbering-drift silent error."""
    section = _section(_body(VIDEO_SCRIPT), _STEP_1_6)
    intro = section.split("1. ")[0]
    assert "six checks" in intro, (
        "Step 1.6's intro no longer says 'six checks'; a stale count is how a reader runs four of "
        f"six and reports a clean pass. Intro reads: {intro.strip()[-200:]!r}"
    )


def test_step_1_6_rerun_instruction_says_all_six():
    """S2-T1. The re-run instruction points at the same N as the list — the third drift site."""
    section = _section(_body(VIDEO_SCRIPT), _STEP_1_6)
    assert "re-run all six" in section, (
        "the re-run instruction must say 'all six' — a fix to one check can reintroduce a defect "
        "another one catches, and a stale N silently narrows the re-run"
    )


def test_step_1_6_check_six_points_at_all_three_story_beats():
    """S2-T1. The bragging test names decision 1, the low point and decision 2."""
    check_6 = _numbered_items(_section(_body(VIDEO_SCRIPT), _STEP_1_6))[5]
    for beat in _S2_CHECK_6_BEATS:
        assert beat in check_6, (
            f"check 6 no longer points at {beat!r} — a script missing any of the three is an "
            "accomplishment piece, and the check cannot say so if it stops asking"
        )


def test_the_retention_rubric_part_a_is_untouched_by_the_story_checks():
    """S2-T4. Checks 5-6 live in Step 1.6, never in the rubric's scored /14.

    Adding a story dimension to Part A would change every historical `part_a_score` silently.
    """
    text = _body(RETENTION_RUBRIC)
    lowered = text.lower()
    assert "/14" in text or "14" in text, "the rubric no longer states its Part A total"
    for stray in ("story-washing", "bragging test", "brief.protagonist"):
        assert stray not in lowered, (
            f"{stray!r} reached the retention rubric — the story checks belong to video-script's "
            "Step 1.6, and a new scored dimension would silently rebase every prior part_a_score"
        )


# ------------------------------------------------------------------ S3 — the story spine


def test_content_plan_derives_opposite_from_the_persona_line():
    """S3-T2. `opposite` is read from audience-psychology.md, never invented.

    A generic word ("frustration") is the plausible artifact this names the source to prevent.
    """
    section = _section(_body(CONTENT_PLAN), "Step 1 — Propose the plan")
    spine = [b for b in section.split("\n  - ") if "`opposite`" in b]
    assert spine, "content-plan Step 1 no longer describes the `opposite` brief field"
    bullet = "\n".join(spine)
    assert "audience-psychology.md" in bullet, (
        "the `opposite` derivation must name audience-psychology.md as its source"
    )
    assert "believed-but-never-said" in bullet, (
        "the derivation must name the persona's *believed-but-never-said* line specifically — "
        "that line IS the opposite value, and a bullet that only says 'the persona block' invites "
        "a generic word instead"
    )


def test_content_plan_names_all_three_spine_fields_in_derivation_order():
    """S3-T2. The chain pillar -> core_value -> opposite -> protagonist is stated, not assumed."""
    section = _section(_body(CONTENT_PLAN), "Step 1 — Propose the plan")
    positions = {f: section.find(f"`{f}`") for f in ("core_value", "opposite", "protagonist")}
    for field, at in positions.items():
        assert at != -1, f"content-plan Step 1 never names brief.{field}"
    assert positions["core_value"] < positions["protagonist"], (
        "the fields must be introduced in derivation order — a protagonist named before the value "
        "it costs is a character, not a spine"
    )


def test_content_plan_carries_the_information_pushes_out_emotion_routing_rule():
    """S3-T2. The rule that decides shape: a cold reach-goal item is not an explainer."""
    section = _section(_body(CONTENT_PLAN), "Step 1 — Propose the plan")
    assert "not** an explainer" in section or "not an explainer" in section, (
        "Step 1 lost the routing rule — without it a reach-goal item aimed at a cold persona is "
        "planned as an explainer, which is the format gap the spine exists to fill"
    )


def test_check_five_reads_the_core_value_field_when_the_item_carries_one():
    """S3-T4. Check 5's first half reads `brief.core_value` rather than re-deriving it.

    This is the assertion S2 shipped inverted (the field did not exist yet).
    """
    check_5 = _numbered_items(_section(_body(VIDEO_SCRIPT), _STEP_1_6))[4]
    assert "brief.core_value" in check_5, (
        "check 5 no longer reads brief.core_value — with the field present and unread, a script "
        "whose beats drifted off the brief's value passes the check that exists to catch it"
    )


def test_check_five_still_derives_the_value_when_there_is_no_field():
    """S3-T4. The positive control for the flip: check 5 must still run on a plain item.

    Most items carry no story spine. A check 5 that only knows how to read a field would silently
    stop running on all of them.
    """
    check_5 = _numbered_items(_section(_body(VIDEO_SCRIPT), _STEP_1_6))[4]
    assert _S2_CHECK_5_DERIVATION in check_5, (
        "check 5 lost its prose derivation — an item with no brief.core_value would then have no "
        f"story-washing test at all. Expected to still find: {_S2_CHECK_5_DERIVATION!r}"
    )


# ------------------------------------------------------------------ the untouched set (§5 DoD)


def test_virality_engineering_still_carries_six_triggers_and_no_seventh():
    """S4/S2 regression. The course's emotion is not a seventh trigger (PRD §1.1, §4)."""
    doc = (REPO / "docs" / "virality-engineering.md").read_text(encoding="utf-8")
    lowered = doc.lower()
    assert "six" in lowered, "virality-engineering.md no longer describes six triggers"
    for stray in ("seventh trigger", "kama muta", "story-washing"):
        assert stray not in lowered, (
            f"{stray!r} reached docs/virality-engineering.md — the PRD's §4 says that file is "
            "untouched, and a seventh trigger would rebase every trigger_stack ever written"
        )


# ------------------------------------------------------------------ manifests + generated bodies


def _version(module: str) -> tuple[int, ...]:
    import importlib

    skill = importlib.import_module(f"gtm_core.skills.{module}").SKILL
    return tuple(int(x) for x in skill.version.split("."))


def test_the_case_study_manifest_carries_the_interview_protocol():
    """S1-T4. The manifest is the canonical description; a reference nobody announces is invisible.

    Pinned as a floor, not an equality: a later unrelated bump is not an S1 regression.
    """
    from gtm_core.skills.case_study import SKILL

    assert _version("case_study") >= (0, 4, 0), (
        f"case-study is {SKILL.version}; S1 landed at 0.4.0 and versions only move forward"
    )
    assert "interview-protocol" in SKILL.description, (
        "the case-study description no longer mentions the interview protocol — the description is "
        "what a reader deciding whether to invoke the skill actually sees"
    )


def test_the_video_script_manifest_records_the_story_checks_and_the_core_value_read():
    """S2-T3 / S3-T4. The manifest moved with the body; a stale one is the codegen silent error."""
    from gtm_core.skills.video_script import SKILL

    assert _version("video_script") >= (0, 16, 0), (
        f"video-script is {SKILL.version}; S3 re-pointed check 5 and landed at 0.16.0"
    )
    doc = __import__("gtm_core.skills.video_script", fromlist=["x"]).__doc__ or ""
    assert "core_value" in doc, (
        "the video-script manifest docstring does not record the 0.16.0 change — the docstring is "
        "where a later reader learns why check 5 reads a field"
    )


def test_the_content_plan_manifest_records_the_story_spine():
    """S3. The derivation is announced where a caller reads it, not only in the body."""
    from gtm_core.skills.content_plan import SKILL

    assert _version("content_plan") >= (0, 6, 0), (
        f"content-plan is {SKILL.version}; S3 added the three brief fields and landed at 0.6.0"
    )
    doc = __import__("gtm_core.skills.content_plan", fromlist=["x"]).__doc__ or ""
    for field in ("core_value", "opposite", "protagonist"):
        assert field in doc, f"the content-plan manifest docstring never names brief.{field}"


def test_the_generated_skill_md_carries_the_core_value_read():
    """S1-T4/S2-T3. SKILL.md is generated; a body edit without codegen ships a stale skill.

    `skill_codegen_sync.sh` is the gate, but it compares whole files — this names the one line
    that had to survive regeneration, so a green gate over a reverted body is still caught.
    """
    skill_md = SKILLS / "video-script" / "SKILL.md"
    if not skill_md.is_file():
        pytest.skip("video-script is withheld from this build (OSS carve)")
    assert "brief.core_value" in skill_md.read_text(encoding="utf-8"), (
        "the generated video-script SKILL.md does not carry check 5's brief.core_value read — "
        "run `uv run python -m gtm_core.skills.codegen generate-all`"
    )


# ------------------------------------------------------------------ §R9 over the new prose


def _pii_check():
    import importlib.util

    spec = importlib.util.spec_from_file_location("pii_check", REPO / "tests/lint/pii_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_storytelling_prose_carries_no_third_party_contact():
    """S1-T3. The corpus's vendor, instructor and members never reach this tree (§R9).

    The de-brand lint proves OUR name is gone and can never prove someone else's is, which is how
    real contacts reached the public repo three times.
    """
    paths = [p for p in (INTERVIEW_PROTOCOL, CASE_STUDY, VIDEO_SCRIPT, CONTENT_PLAN) if p.is_file()]
    assert paths, "none of the storytelling prose files exist — nothing was scanned"
    assert _pii_check().main([str(p) for p in paths]) == 0, (
        "the PII checker refused one of the storytelling prose files — a real person or a real "
        "contact reached prose that ships in the public carve"
    )


def test_the_pii_scanner_reaches_the_storytelling_prose(tmp_path):
    """S1-T3. Positive control: a planted contact in a file of this kind IS caught.

    Without this, the clean result above is indistinguishable from a scanner that skipped .md.
    """
    # Assembled at runtime, never written as a literal: this file is itself inside the scanned
    # source surface, and a leak-shaped address spelled out here would fail the very check the
    # rest of this module asserts is clean. `test_pii_check.py` is exempted by name; this file is
    # deliberately not, so it stays covered.
    leak = "j.doe" + "@" + "unlisted-corp" + "." + "com"
    canary = tmp_path / "interview-protocol.md"
    canary.write_text(
        f"Ask them: *When did you doubt yourselves most?*\nReach the champion at {leak}\n",
        encoding="utf-8",
    )
    assert _pii_check().main([str(canary)]) == 1, (
        "the checker did not flag a planted contact in a markdown reference — the clean-tree "
        "result above would then mean nothing (§R12)"
    )


# ------------------------------------------------------------------ S4 — the story graph

CREATOR_BRIEF = SKILLS / "creator-brief" / "body_template.md"
STORY_GRAPH = SKILLS / "creator-brief" / "references" / "story-graph.md"

#: The five ingredients, each by the phrase the reference has to keep. Two of them are the ones a
#: still can actually show, and those two are what the storyboard gate asks about in S7.
_FIVE_INGREDIENTS = (
    "empathy",
    "sudden",
    "coming together",
    "moved",
    "redemption",
)


def test_the_story_graph_is_cited_from_the_skill_that_owns_it():
    """S4-T1. The citation walk enforces this for creator-brief; assert it directly too."""
    body = _body(CREATOR_BRIEF)
    assert "story-graph.md" in body, (
        "creator-brief does not cite its own references/story-graph.md — an uncited reference is "
        "never loaded, and reads as enforced"
    )


def test_the_story_graph_is_also_cited_from_video_script():
    """S4-T1. The citation walk's corpus is PER-SKILL, so this half is not covered by it.

    video-script reads a reference that lives under creator-brief; nothing else asserts that.
    """
    body = _body(VIDEO_SCRIPT)
    assert "story-graph.md" in body, (
        "video-script no longer cites story-graph.md — the script stage would then size a beat "
        "order the brief committed to without ever reading what it is"
    )


def test_video_script_loads_the_story_graph_conditionally_and_says_so():
    """S4-T1. Loaded on `brief.protagonist`, not on every script.

    A story graph applied to an explainer produces a nine-beat explainer. The condition has to be
    stated where the citation is, or a reader loads it unconditionally.
    """
    body = _body(VIDEO_SCRIPT)
    idx = body.index("story-graph.md")
    window = body[max(0, idx - 600) : idx + 600]
    assert "brief.protagonist" in window, (
        "the story-graph citation in video-script does not name its condition within 600 chars — "
        f"window was: {window[:200]!r}"
    )


def test_the_story_graph_names_all_five_payoff_ingredients():
    """S4-T3. Decision 7's check is only as good as the list it checks against."""
    text = _body(STORY_GRAPH).lower()
    missing = [i for i in _FIVE_INGREDIENTS if i not in text]
    assert not missing, (
        f"story-graph.md no longer names {missing} — the payoff b-roll check is against five "
        "ingredients, and a list that lost one silently passes the beat it should have caught"
    )


def test_the_story_graph_ties_the_moved_face_to_the_expression_field():
    """S4-T3. The one ingredient that is already a field must say so by name.

    This is the whole link between the prose and something the tree can actually check.
    """
    text = _body(STORY_GRAPH)
    assert "`expression`" in text, (
        "story-graph.md does not name the shot `expression` field — the 'a face visibly moved' "
        "ingredient is then advice with nowhere to land"
    )
    assert "check 4" in text, (
        "the reference must point at Step 1.6 check 4, which is what already polices a blanket "
        "expression repeated across every shot"
    )


def test_the_story_graph_carries_its_provenance_note():
    """S4/§R9. Same shape as the interview protocol's: a third-party course, named by nobody."""
    text = _body(STORY_GRAPH)
    lowered = text.lower()
    assert "provenance" in lowered, "story-graph.md has no provenance note"
    assert "third-party" in lowered and "course" in lowered, (
        "the note must state the source is a third-party course — an adopted heuristic with no "
        "stated standing reads as this system's own measured finding"
    )
    assert "retention-rubric.md" in text, (
        "the note must back-reference retention-rubric.md's evidence note for its standing"
    )
    assert "unsourced" in lowered, (
        "the note must say the course's effectiveness claim is not carried here — it is the one "
        "thing this PRD quarantines outright"
    )


def test_the_story_graph_emits_no_gate_marker():
    """S4. creator-brief adds no gate, and a reference under it cannot smuggle one in."""
    assert "⟦GATE:" not in _body(STORY_GRAPH), (
        "a gate marker reached story-graph.md — creator-brief emits no gate of any kind, and a "
        "run parked at one the cockpit cannot answer is the failure that pins"
    )


# ------------------------------------------------------------------ S5 — capture, not disclosure

VIDEO_ROUTER = SKILLS / "video-router" / "body_template.md"


def test_the_disclosure_gate_has_no_capture_mode_shaped_branch():
    """S5-T3. A capture decision must not be able to reach the Article 50 duty.

    S5 makes a story item default to a real shoot. That is a decision about a camera, and the one
    way it could become a security change is if anything downstream started reading capture as a
    reason to skip disclosure. `validate_disclosure` keys on identity_used and nothing else.
    """
    import inspect

    from agent.publish import validate_disclosure

    params = inspect.signature(validate_disclosure).parameters
    assert "capture_mode" not in params, (
        f"validate_disclosure grew a capture_mode parameter ({list(params)}) — capture would then "
        "be able to switch off a duty that attaches to synthesis, not to how it was filmed"
    )
    source = inspect.getsource(validate_disclosure)
    for token in ("capture_mode", "live_action", "story_capture"):
        assert token not in source, (
            f"{token!r} appears inside validate_disclosure — the gate keys on identity_used alone, "
            "and a capture-shaped branch is how a real-footage label starts excusing a synthetic post"
        )


def test_the_disclosure_gate_still_refuses_an_undisclosed_synthetic_post():
    """S5-T3. Positive control: the gate above is intact, not merely free of capture words."""
    from agent.publish import validate_disclosure

    refused = validate_disclosure("a post with no disclosure line", ("soul",), ("Made with AI.",))
    assert refused, (
        "an undisclosed synthetic post was accepted — the absence-checks above would then be "
        "asserting the shape of a gate that no longer gates"
    )
    cleared = validate_disclosure("a post about a real shoot", (), ("Made with AI.",))
    assert cleared is None, (
        "a post using no synthetic identity was refused — real footage owes no disclosure"
    )


def test_the_router_relays_the_capture_default_without_restating_its_reason():
    """S5. The body names the field and never copies the sentence the module owns.

    A reason copied into prose is a reason that goes stale where nobody looks — the exact defect
    the router's own relay rule was written for after the shot-mix literals drifted.
    """
    body = _body(VIDEO_ROUTER)
    assert "story_capture" in body, (
        "video-router Step 0.5 no longer names constraints.story_capture — the routing fact would "
        "then be resolved by the preflight and read by nobody"
    )
    from gtm_core.video_preflight import _STORY_CAPTURE_REASON

    assert _STORY_CAPTURE_REASON not in body, (
        "the router body restates the capture reason verbatim instead of relaying it; the module "
        "owns that sentence and the copy will drift"
    )


def test_the_creator_brief_derives_decision_five_rather_than_asking_for_it():
    """S5-T2. The body's own rule is that a decision with a default is filled, never asked."""
    body = _body(CREATOR_BRIEF)
    assert "story_capture" in body, "creator-brief does not read the preflight's story_capture"
    assert "non-story" in body, (
        "the Step 1 preamble still says the capture contract is asked on a first run without "
        "excepting a story item — a story item now has a default, so asking contradicts the rule "
        "stated two lines above it"
    )


# ------------------------------------------------------------------ S6 — the message budget


def test_the_caption_budget_spec_names_the_message_column():
    """S6. The gate reads columns by name, so the body has to name them.

    Before S6 the columns existed only as prose ("beat id, duration, caption word count, and words
    per second") with no header written down anywhere — nothing could have parsed that.
    """
    section = _section(_body(VIDEO_SCRIPT), "`## Caption budget` — the per-beat table", level="###")
    assert "| Beat | Duration |" in section, (
        "the caption-budget spec no longer shows a header row; the gate locates the duration and "
        "message columns by name and would report every story script as unreadable"
    )
    assert "message" in section, "the spec does not name the `message` column"


def test_the_body_says_message_beats_are_declared_not_detected():
    """S6-T3. The prose has to carry the reason, or a later author adds the detector.

    A regex over product terms under-matches and returns a plausible ratio. The test asserting the
    absence of one lives beside the gate; this makes sure the body explains why it is absent.
    """
    section = _section(_body(VIDEO_SCRIPT), "`## Caption budget` — the per-beat table", level="###")
    lowered = section.lower()
    assert "declared" in lowered and "detect" in lowered, (
        "the caption-budget spec no longer explains that message beats are declared rather than "
        "detected — that is the whole reason the column exists instead of a scanner"
    )


def test_the_front_block_declares_message_share():
    """S6. The ratio is visible in the front block, where a reader sees the shape at a glance."""
    body = _body(VIDEO_SCRIPT)
    assert "message_share: <message-bearing s>/<total s>" in body, (
        "the front-block spec lost its message_share line — the ratio would then be computed by "
        "the gate and shown to nobody"
    )


# ------------------------------------------------------------------ S7 — the storyboard gate

VIDEO_STORYBOARD = SKILLS / "video-storyboard" / "body_template.md"
_STEP_5 = "Step 5 — Present the gate"

#: The two questions, frozen. They are the whole workstream: two of the five payoff ingredients are
#: visible in a still and invisible in prose, so a script can promise an emotional beat the shot
#: list has no way to deliver and nothing before the render would notice.
_S7_QUESTIONS = ("who is in frame", "what is on their face")


def test_the_storyboard_gate_asks_both_payoff_questions():
    """S7-T1. Both, not one: a face alone and two people alone are different ingredients."""
    section = _section(_body(VIDEO_STORYBOARD), _STEP_5).lower()
    missing = [q for q in _S7_QUESTIONS if q not in section]
    assert not missing, (
        f"the storyboard gate no longer asks {missing} — those two are the payoff ingredients a "
        "still can actually show, and prose cannot"
    )


def test_the_payoff_questions_are_scoped_to_a_story_item():
    """S7-T1. Asked on a story item, not on every gate.

    Unconditional, they would put two irrelevant lines on every storyboard gate the system ever
    presents, which is how a check stops being read.
    """
    section = _section(_body(VIDEO_STORYBOARD), _STEP_5)
    idx = section.lower().index(_S7_QUESTIONS[0])
    window = section[max(0, idx - 500) : idx + 200]
    assert "brief.protagonist" in window, (
        "the two payoff questions do not name their condition nearby — an unconditional pair of "
        f"questions is asked of every gate. Window: {window[:160]!r}"
    )


def test_the_payoff_questions_name_the_cell_they_are_about():
    """S7-T1. "The payoff cell", by shot number — a question about no particular still is rhetoric."""
    section = _section(_body(VIDEO_STORYBOARD), _STEP_5).lower()
    assert "payoff cell" in section, "the gate does not name the payoff cell"
    assert "shot number" in section, (
        "the gate does not say to name the cell by shot number — 'the payoff cell' is ambiguous on "
        "a multi-shot board, and the operator cannot check a still nobody identified"
    )


def test_the_storyboard_still_emits_exactly_one_gate_kind():
    """S7-T2. No new gate kind: a run parked at a gate the cockpit cannot answer never resumes."""
    body = _body(VIDEO_STORYBOARD)
    kinds = set(re.findall(r"⟦GATE:(\w+)⟧", body))
    assert kinds == {"plan"}, (
        f"video-storyboard emits gate kind(s) {sorted(kinds)}; the storyboard gate is ⟦GATE:plan⟧ "
        "and adding a kind is a boundary change, not a skill edit"
    )
    assert "⟦FILE:" in body, (
        "the gate no longer attaches the animatic — the operator would be asked to approve pacing "
        "they cannot watch"
    )


# ------------------------------------------------------------------ S8 — the outcome tag

VIDEO_SCORE = SKILLS / "video-score" / "body_template.md"
OUTCOMES_SYNC = SKILLS / "content-outcomes-sync" / "body_template.md"


def test_the_score_body_puts_story_format_at_the_top_level():
    """S8-T1. Placement is the whole correctness argument, so the body must state it.

    Inside `recommended`, the field is swept into the row's `meta` — unqueryable — and only when a
    band happens to be set. A reader who puts it there gets a plausible artifact and no error.
    """
    body = _body(VIDEO_SCORE)
    assert "story_format" in body, "video-score Step 3b never mentions story_format"
    assert "top level" in body, (
        "the body does not say story_format goes at the top level of score.json — nested in "
        "`recommended` it lands in meta, which the correlator cannot query"
    )
    assert "never inside `recommended`" in body, (
        "the body does not warn against the one placement that fails silently"
    )


def test_both_bodies_say_absent_is_not_false():
    """S8-T2. The three-state rule, stated where each writer of the field will read it.

    A `false` backfilled onto rows nobody checked destroys the absent/false distinction, and does
    so irreversibly — nothing downstream could tell a migrated row from a measured one.
    """
    for path in (VIDEO_SCORE, OUTCOMES_SYNC):
        text = _body(path).lower()
        assert "unknown" in text, (
            f"{path.parent.name} does not explain that an absent story_format means unknown — "
            "the next author writes false and the comparison this axis exists for is gone"
        )


def test_the_sync_body_lists_story_format_among_the_prefixes():
    """S8. A tag without its `key:` prefix is invisible to the correlator, not unknown to it."""
    body = _body(OUTCOMES_SYNC)
    assert "`story_format:`" in body, (
        "the tag-prefix list does not name story_format: — a bare tag lands the row in the "
        "baseline instead of its bucket, silently"
    )


# ------------------------------------------------------------------ S9 — the low point as a moment

CASE_STUDY_STRUCTURE = SKILLS / "case-study" / "references" / "case-study-structure.md"


def test_the_challenge_section_asks_for_a_moment_and_keeps_the_numbers():
    """S9-T1. A state is what an accomplishment story has; a moment is what a story has.

    The numbers stay: the moment is added beside the buyer's measured pain, never instead of it.
    """
    section = _section(
        _body(CASE_STUDY_STRUCTURE), "6. The challenge — the buyer's unsolved job", level="###"
    )
    lowered = section.lower()
    assert "moment" in lowered, (
        "the challenge spec no longer asks for the low point as a moment — a state is all an "
        "accomplishment story ever has"
    )
    assert "doubt" in lowered, (
        "the spec does not say which moment: the point at which the people in it doubted "
        "themselves most. 'A moment' unqualified invites a dramatic scene instead of the true one"
    )
    assert "in the buyer's numbers" in section, (
        "the buyer's numbers were displaced by the moment — the moment is added BESIDE them, and a "
        "challenge section without measured pain is back to narrating current state"
    )


def test_the_moment_is_sourced_from_the_interview_protocol():
    """S9-T1. Depends on S1: the moment comes from question 1, or it is written as inferred."""
    section = _section(
        _body(CASE_STUDY_STRUCTURE), "6. The challenge — the buyer's unsolved job", level="###"
    )
    assert "interview-protocol.md" in section, (
        "the moment names no source — with no protocol behind it, a writer invents one, which is "
        "the melodrama the protocol exists to prevent"
    )
    assert "inferred" in section.lower(), (
        "the spec does not say to mark an un-interviewed moment as inferred. Inferred is a "
        "legitimate state; inferred presented as told is not"
    )


def test_the_front_half_budget_table_sums_to_its_stated_total():
    """S9-T1. §R14: the total is derived from the rows, not typed beside them.

    The low-point row added words to a budget the file calls a hard ceiling. A stated total that
    no longer matches its own rows is how a "measured" budget quietly becomes a guess.
    """
    text = _body(CASE_STUDY_STRUCTURE)
    m = re.search(r"\*\*Front half — (\d+) prose words\*\*(.*?)(?=\*\*Back half)", text, re.DOTALL)
    assert m, "the front-half budget block was renamed or removed"
    stated = int(m.group(1))
    rows = [int(n) for n in re.findall(r"^\|[^|]+\|\s*(\d+)\s*\|$", m.group(2), re.MULTILINE)]
    assert rows, "no budget rows parsed — the check would pass on an empty table (§R12)"
    assert sum(rows) == stated, (
        f"the front-half rows sum to {sum(rows)} but the heading says {stated} — one was edited "
        "without the other, and the budget stops being measured the moment they disagree"
    )


def test_the_front_half_budget_stays_inside_its_measured_tolerance():
    """S9-T1. 215 against a recorded "220 still fits" — the added row must not exceed what was measured."""
    text = _body(CASE_STUDY_STRUCTURE)
    m = re.search(r"\*\*Front half — (\d+) prose words\*\*[^\n]*?(\d+) still fits", text)
    assert m, "the front-half tolerance line no longer records what still fits"
    stated, still_fits = int(m.group(1)), int(m.group(2))
    assert stated <= still_fits, (
        f"the front-half budget is now {stated} words against a measured ceiling of {still_fits} — "
        "past that, the page count is a guess and the spec says never to ship over cap"
    )
