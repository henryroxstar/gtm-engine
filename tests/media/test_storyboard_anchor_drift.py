"""gtm_core.storyboard — the one-hero-still rule (2026-08-19).

A trained likeness reaches video as a start frame, and every independent still generation
re-invents whatever the prompt did not pin: outfit, lighting, room, hair. So N stills for N shots
means N independently invented wardrobes. On 2026-08-18 five presenter shots produced five
distinct ``image_job_id`` values and five different jackets inside one 32-second video, and
``video-storyboard`` Step 3 was at the time *instructing* exactly that. ``video-render``'s own
guardrail already said "never re-roll the identity anchor per shot" in prose while nothing checked
it, which is why the check now lives in code.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core import storyboard as sb


def _write(path: Path, job_ids: list[str]) -> Path:
    path.write_text(
        json.dumps(
            {
                "approved": False,
                "entries": [
                    {
                        "n": i,
                        "image_job_id": j,
                        "image_path": f"s-{i}.png",
                        "identity_anchor": {"kind": "element", "id": "el-1"},
                    }
                    for i, j in enumerate(job_ids, start=1)
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_anchor_drift_groups_shots_by_job_id(tmp_path):
    entries = json.loads(_write(tmp_path / "s.json", ["a", "a", "b"]).read_text())["entries"]
    groups = sb.anchor_drift(entries)
    assert groups == {"a": [1, 2], "b": [3]}


def test_anchor_drift_ignores_entries_with_no_job_id(tmp_path):
    """A broll/screen entry legitimately has no still — it must not read as a distinct anchor."""
    anchor = {"kind": "element", "id": "el-1"}
    groups = sb.anchor_drift(
        [
            {"n": 1, "image_job_id": "a", "identity_anchor": anchor},
            {"n": 2, "identity_anchor": anchor},
            {"n": 3, "image_job_id": "", "identity_anchor": anchor},
        ]
    )
    assert groups == {"a": [1]}


def test_a_scene_still_with_no_identity_is_not_drift(tmp_path):
    """The mixed-role case: one hero presenter still reused across shots 1/4/6, plus separate
    screen and b-roll frames. A screen capture has no wardrobe to re-invent, so it is not an
    identity anchor — counting it would force --allow-anchors 4, which would simultaneously
    re-permit the four presenter looks this check exists to catch."""
    anchor = {"kind": "element", "id": "el-1"}
    entries = [
        {"n": 1, "image_job_id": "hero", "identity_anchor": anchor},
        {"n": 2, "image_job_id": "screen-a"},
        {"n": 3, "image_job_id": "broll"},
        {"n": 4, "image_job_id": "hero", "identity_anchor": anchor},
        {"n": 5, "image_job_id": "screen-b"},
        {"n": 6, "image_job_id": "hero", "identity_anchor": anchor},
    ]
    assert sb.anchor_drift(entries) == {"hero": [1, 4, 6]}


def test_a_mixed_role_storyboard_approves_without_raising_the_ceiling(tmp_path):
    """The whole point of the scoping: the shape this pipeline actually produces must approve at
    the default allow_anchors=1."""
    anchor = {"kind": "element", "id": "el-1"}
    path = tmp_path / "s.json"
    path.write_text(
        json.dumps(
            {
                "approved": False,
                "entries": [
                    {"n": 1, "image_job_id": "hero", "identity_anchor": anchor},
                    {"n": 2, "image_job_id": "screen-a"},
                    {"n": 3, "image_job_id": "broll"},
                    {"n": 4, "image_job_id": "hero", "identity_anchor": anchor},
                    {"n": 5, "image_job_id": "screen-b"},
                    {"n": 6, "image_job_id": "hero", "identity_anchor": anchor},
                ],
            }
        ),
        encoding="utf-8",
    )
    out = sb.approve(path, by="Operator", at="2026-08-19T09:00:00Z")
    assert out["approved"] is True
    assert out["identity_anchor_count"] == 1


def test_two_presenter_looks_are_still_refused_among_scene_stills(tmp_path):
    """Scoping must not become a loophole: hiding a second wardrobe among screen frames still
    trips the check."""
    anchor = {"kind": "element", "id": "el-1"}
    entries = [
        {"n": 1, "image_job_id": "hero", "identity_anchor": anchor},
        {"n": 2, "image_job_id": "screen-a"},
        {"n": 3, "image_job_id": "second-look", "identity_anchor": anchor},
    ]
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"approved": False, "entries": entries}), encoding="utf-8")
    with pytest.raises(sb.StoryboardError, match="2 distinct identity anchors"):
        sb.approve(path, by="Operator", at="2026-08-19T09:00:00Z")


def test_one_anchor_approves_cleanly(tmp_path):
    path = _write(tmp_path / "s.json", ["hero", "hero", "hero"])
    out = sb.approve(path, by="Operator", at="2026-08-19T09:00:00Z")
    assert out["approved"] is True
    assert out["identity_anchor_count"] == 1


def test_multiple_anchors_are_refused_by_default(tmp_path):
    path = _write(tmp_path / "s.json", ["a", "b", "c"])
    with pytest.raises(sb.StoryboardError) as exc:
        sb.approve(path, by="Operator", at="2026-08-19T09:00:00Z")

    msg = str(exc.value)
    assert "3 distinct identity anchors" in msg
    # Must name the fix AND the escape hatch, not just refuse.
    assert "ONE hero still" in msg
    assert "--allow-anchors 3" in msg


def test_refusal_does_not_write_approval_to_disk(tmp_path):
    """A refused approval must leave the file unapproved — otherwise the render gate opens on a
    storyboard the operator's own tooling just rejected."""
    path = _write(tmp_path / "s.json", ["a", "b"])
    with pytest.raises(sb.StoryboardError):
        sb.approve(path, by="Operator", at="2026-08-19T09:00:00Z")

    assert json.loads(path.read_text())["approved"] is False


def test_raising_the_ceiling_requires_a_reason(tmp_path):
    path = _write(tmp_path / "s.json", ["a", "b"])
    with pytest.raises(sb.StoryboardError, match="--reason is required"):
        sb.approve(path, by="Operator", at="2026-08-19T09:00:00Z", allow_anchors=2)


def test_a_deliberate_look_change_is_approvable_and_records_why(tmp_path):
    path = _write(tmp_path / "s.json", ["office", "street"])
    out = sb.approve(
        path,
        by="Operator",
        at="2026-08-19T09:00:00Z",
        allow_anchors=2,
        reason="script moves from office to street at beat 4",
    )
    assert out["approved"] is True
    assert out["identity_anchor_count"] == 2
    assert "office to street" in out["anchor_exception_reason"]


def test_status_reports_the_anchor_count_without_approving(tmp_path):
    path = _write(tmp_path / "s.json", ["a", "b", "c"])
    st = sb.status(path)
    assert st["identity_anchors"] == 3
    assert st["approved"] is False


def test_the_real_2026_08_18_storyboard_shape_is_refused(tmp_path):
    """The exact defect: five presenter shots, five independently generated stills."""
    path = _write(
        tmp_path / "s.json",
        ["51a7c697", "80e75eb8", "14fd65ed", "191ba746", "2636dede"],
    )
    with pytest.raises(sb.StoryboardError, match="5 distinct identity anchors"):
        sb.approve(path, by="Henry Roxas", at="2026-08-19T09:00:00Z")


def test_allow_anchors_below_one_is_rejected(tmp_path):
    path = _write(tmp_path / "s.json", ["a"])
    with pytest.raises(sb.StoryboardError, match="must be >= 1"):
        sb.approve(path, by="Operator", at="2026-08-19T09:00:00Z", allow_anchors=0)


# ═════════════════════════════════════════════════════════════════════════════
# lineage (2026-09-06, C1) — a delta-edit chain is ONE anchor and N job ids
# ═════════════════════════════════════════════════════════════════════════════


def _chain(path: Path, job_ids: list[str], *, depths: bool = False) -> Path:
    """A delta-edit chain: entry 1 is the hero, each later entry derives from the one before."""
    anchor = {"kind": "element", "id": "el-1"}
    entries = []
    for i, j in enumerate(job_ids, start=1):
        entry = {"n": i, "image_job_id": j, "identity_anchor": anchor}
        if i > 1:
            entry["derived_from"] = job_ids[i - 2]
        if depths:
            entry["edit_depth"] = i - 1
        entries.append(entry)
    path.write_text(json.dumps({"approved": False, "entries": entries}), encoding="utf-8")
    return path


def test_a_delta_edit_chain_from_one_hero_approves_at_the_default_ceiling(tmp_path):
    """The workflow reference-image conditioning exists to enable: one hero, four derivatives.

    Before lineage this was four distinct image_job_ids and the drift check refused it — while
    the skill body simultaneously said not to reach for --allow-anchors to make something pass.
    """
    path = _chain(tmp_path / "s.json", ["hero", "d1", "d2", "d3"])
    out = sb.approve(path, by="Operator", at="2026-09-06T09:00:00Z")
    assert out["approved"] is True
    assert out["identity_anchor_count"] == 1, "a chain from one hero is one anchor"


def test_the_chain_groups_under_the_root_not_the_leaf(tmp_path):
    entries = json.loads(_chain(tmp_path / "s.json", ["hero", "d1", "d2"]).read_text())["entries"]
    assert sb.anchor_drift(entries) == {"hero": [1, 2, 3]}


def test_lineage_free_stills_are_still_refused(tmp_path):
    """The positive control for the whole lineage feature, and the one that matters most.

    If a missing `derived_from` ever resolved to a shared root instead of the entry's own id,
    the five-jacket rule would go silently green while still being described as enforced.
    """
    path = _write(tmp_path / "s.json", ["a", "b", "c"])
    with pytest.raises(sb.StoryboardError, match="3 distinct identity anchors"):
        sb.approve(path, by="Operator", at="2026-09-06T09:00:00Z")

    real = _write(
        tmp_path / "real.json", ["51a7c697", "80e75eb8", "14fd65ed", "191ba746", "2636dede"]
    )
    with pytest.raises(sb.StoryboardError, match="5 distinct identity anchors"):
        sb.approve(real, by="Operator", at="2026-09-06T09:00:00Z")


def test_two_chains_from_two_heroes_are_still_two_anchors(tmp_path):
    """Lineage collapses a chain, never two genuinely different looks."""
    anchor = {"kind": "element", "id": "el-1"}
    entries = [
        {"n": 1, "image_job_id": "office", "identity_anchor": anchor},
        {"n": 2, "image_job_id": "office-d1", "derived_from": "office", "identity_anchor": anchor},
        {"n": 3, "image_job_id": "street", "identity_anchor": anchor},
        {"n": 4, "image_job_id": "street-d1", "derived_from": "street", "identity_anchor": anchor},
    ]
    assert sb.anchor_drift(entries) == {"office": [1, 2], "street": [3, 4]}


def test_a_chain_through_a_non_anchored_frame_still_reaches_the_anchored_root(tmp_path):
    """A derivative may legitimately pass through a frame carrying no identity_anchor of its own."""
    anchor = {"kind": "element", "id": "el-1"}
    entries = [
        {"n": 1, "image_job_id": "hero", "identity_anchor": anchor},
        {"n": 2, "image_job_id": "plate", "derived_from": "hero"},  # no anchor on this one
        {"n": 3, "image_job_id": "leaf", "derived_from": "plate", "identity_anchor": anchor},
    ]
    assert sb.anchor_drift(entries) == {"hero": [1, 3]}


def test_a_dangling_derived_from_is_refused_and_names_the_entry(tmp_path):
    """An unverifiable parent is how two real anchors could be laundered into one."""
    anchor = {"kind": "element", "id": "el-1"}
    entries = [
        {"n": 1, "image_job_id": "hero", "identity_anchor": anchor},
        {"n": 2, "image_job_id": "orphan", "derived_from": "ghost", "identity_anchor": anchor},
    ]
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"approved": False, "entries": entries}), encoding="utf-8")
    with pytest.raises(sb.StoryboardError) as exc:
        sb.approve(path, by="Operator", at="2026-09-06T09:00:00Z")
    msg = str(exc.value)
    assert "orphan"[:8] in msg and "ghost"[:8] in msg, "the refusal must name both ends"
    assert json.loads(path.read_text())["approved"] is False


def test_a_circular_derived_from_is_refused_rather_than_looping(tmp_path):
    anchor = {"kind": "element", "id": "el-1"}
    entries = [
        {"n": 1, "image_job_id": "a", "derived_from": "b", "identity_anchor": anchor},
        {"n": 2, "image_job_id": "b", "derived_from": "a", "identity_anchor": anchor},
    ]
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"approved": False, "entries": entries}), encoding="utf-8")
    with pytest.raises(sb.StoryboardError, match="circular"):
        sb.approve(path, by="Operator", at="2026-09-06T09:00:00Z")


def test_a_declared_edit_depth_must_agree_with_the_walked_chain(tmp_path):
    """Depth is derived; the field merely records it. A stored number nobody checks is how a
    confidently wrong number survives review."""
    ok = _chain(tmp_path / "ok.json", ["hero", "d1", "d2"], depths=True)
    assert sb.approve(ok, by="Operator", at="2026-09-06T09:00:00Z")["approved"] is True

    bad = json.loads(_chain(tmp_path / "bad.json", ["hero", "d1"], depths=True).read_text())
    bad["entries"][1]["edit_depth"] = 3
    (tmp_path / "bad.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(sb.StoryboardError, match="declares edit_depth=3"):
        sb.approve(tmp_path / "bad.json", by="Operator", at="2026-09-06T09:00:00Z")

    root_lies = json.loads(_chain(tmp_path / "r.json", ["hero", "d1"], depths=True).read_text())
    root_lies["entries"][0]["edit_depth"] = 2
    (tmp_path / "r.json").write_text(json.dumps(root_lies), encoding="utf-8")
    with pytest.raises(sb.StoryboardError, match="declares edit_depth=2"):
        sb.approve(tmp_path / "r.json", by="Operator", at="2026-09-06T09:00:00Z")


def test_a_chain_past_the_depth_cap_warns_and_still_approves(tmp_path):
    """Boundary, both sides — and a WARN, never a refusal: re-anchoring is the operator's call."""
    at_cap = _chain(tmp_path / "four.json", ["hero", "d1", "d2", "d3", "d4"])
    clean = sb.approve(at_cap, by="Operator", at="2026-09-06T09:00:00Z")
    assert clean["max_edit_depth"] == sb.MAX_EDIT_DEPTH == 4
    assert clean["edit_depth_warnings"] == [], "depth 4 is at the cap, not past it"

    over = _chain(tmp_path / "five.json", ["hero", "d1", "d2", "d3", "d4", "d5"])
    out = sb.approve(over, by="Operator", at="2026-09-06T09:00:00Z")
    assert out["approved"] is True, "the depth cap warns; it never blocks"
    assert len(out["edit_depth_warnings"]) == 1
    warning = out["edit_depth_warnings"][0]
    assert "d5"[:8] in warning and "5 edit(s)" in warning and "re-anchor" in warning.lower()


def test_status_reports_the_deepest_chain_without_approving(tmp_path):
    path = _chain(tmp_path / "s.json", ["hero", "d1", "d2"])
    st = sb.status(path)
    assert st["max_edit_depth"] == 2
    assert st["identity_anchors"] == 1
    assert st["approved"] is False


def test_a_storyboard_with_no_lineage_reports_depth_zero(tmp_path):
    """Additive: every storyboard written before this field existed reads as all-roots."""
    assert sb.status(_write(tmp_path / "s.json", ["a", "b"]))["max_edit_depth"] == 0
