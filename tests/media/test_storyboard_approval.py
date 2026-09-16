"""gtm_core.storyboard — the recorded half of video-storyboard's ⟦GATE:plan⟧.

Calibrated against 2026-08-18: a synthetic render ran end to end with the storyboard stage never
invoked, because the gate lived only in a skill's prose. Approval is now a verified write that
render_manifest refuses to proceed without.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core import storyboard as sb


def _write(tmp_path, **kw):
    data = {"entries": [{"n": 1, "image_path": "a.png"}]}
    data.update(kw)
    p = tmp_path / "storyboard.json"
    p.write_text(json.dumps(data))
    return p


def test_a_fresh_storyboard_is_not_approved(tmp_path):
    assert sb.status(_write(tmp_path))["approved"] is False


def test_approve_records_who_and_when_and_round_trips(tmp_path):
    p = _write(tmp_path)
    sb.approve(p, by="henry", at="2026-08-18T10:00:00Z")
    on_disk = json.loads(p.read_text())
    assert on_disk["approved"] is True
    assert on_disk["approved_by"] == "henry"
    assert on_disk["approved_at"] == "2026-08-18T10:00:00Z"


def test_approving_an_empty_storyboard_is_refused(tmp_path):
    """Nothing was shown, so nothing can have been approved."""
    p = _write(tmp_path, entries=[])
    with pytest.raises(sb.StoryboardError, match="no entries"):
        sb.approve(p, by="henry", at="2026-08-18T10:00:00Z")


@pytest.mark.parametrize("field", ["by", "at"])
def test_approval_must_name_who_and_when(tmp_path, field):
    p = _write(tmp_path)
    kw = {"by": "henry", "at": "2026-08-18T10:00:00Z"}
    kw[field] = "  "
    with pytest.raises(sb.StoryboardError, match=f"--{field} is required"):
        sb.approve(p, **kw)


def test_a_missing_storyboard_is_refused_not_created(tmp_path):
    with pytest.raises(sb.StoryboardError, match="no such storyboard"):
        sb.approve(tmp_path / "nope.json", by="henry", at="2026-08-18T10:00:00Z")
    assert not (tmp_path / "nope.json").exists()


def test_approve_refuses_identity_anchor_without_job_id(tmp_path: Path):
    """Q6: An identity-bearing frame without an image_job_id cannot be approved."""
    p = tmp_path / "storyboard.json"
    p.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "n": 1,
                        "image_path": "frame.png",
                        "identity_anchor": {"kind": "element", "id": "el-1"},
                        # image_job_id missing
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(sb.StoryboardError, match="has an identity_anchor but no image_job_id"):
        sb.approve(p, by="operator", at="2026-09-12T00:00:00Z")


def test_an_unreadable_storyboard_is_refused(tmp_path):
    p = tmp_path / "storyboard.json"
    p.write_text("{not json")
    with pytest.raises(sb.StoryboardError, match="unreadable"):
        sb.status(p)


def test_approval_unblocks_the_render_manifest_gate(tmp_path):
    """The two halves meet: this is the whole point of recording the decision."""
    from gtm_core import render_manifest as rm

    (tmp_path / "asset.mp4").write_bytes(b"x")
    p = _write(tmp_path)
    m = rm.RenderManifest(
        profile="acme",
        slug="s",
        ratio="9x16",
        asset_path="asset.mp4",
        identity_used=("generated",),
        prompt="Static camera, slow push-in.",
        storyboard_path="storyboard.json",
        cost_source="preflight",
        cost_credits=10.0,
        cost_ledger_ts="2026-08-20T00:00:00Z",
    )
    with pytest.raises(rm.ManifestError, match="not marked approved"):
        rm.write_render_manifest(m, out_dir=tmp_path, synthetic=True, repo_root=tmp_path)

    sb.approve(p, by="henry", at="2026-08-18T10:00:00Z")
    out = rm.write_render_manifest(m, out_dir=tmp_path, synthetic=True, repo_root=tmp_path)
    assert out.exists()


def test_cli_status_and_approve(tmp_path, capsys):
    p = _write(tmp_path)
    assert sb.main(["approve", str(p), "--by", "henry", "--at", "2026-08-18T10:00:00Z"]) == 0
    assert sb.main(["status", str(p)]) == 0
    assert '"approved": true' in capsys.readouterr().out


def test_cli_reports_a_refusal_as_exit_2(tmp_path):
    p = _write(tmp_path, entries=[])
    assert sb.main(["approve", str(p), "--by", "henry", "--at", "2026-08-18T10:00:00Z"]) == 2


# ── the spoiler gate ─────────────────────────────────────────────────────────────────────


def _sb(tmp_path, entry_extra: dict) -> Path:
    """A minimal one-entry storyboard, plus whatever the test is actually about."""
    path = tmp_path / "storyboard.json"
    path.write_text(
        json.dumps({"entries": [{"shot_n": 2, "image_job_id": "job-1", **entry_extra}]}, indent=2),
        encoding="utf-8",
    )
    return path


_FINDING = {
    "term": "Fabric Gateway",
    "shot_n": 2,
    "shot_tc": "1:48",
    "first_spoken_tc": "3:02",
    "lead_s": 74.0,
}


def test_an_unaccepted_spoiler_finding_blocks_approval(tmp_path):
    """Prose is what failed the first time: a console screenshot named the product 75 seconds
    early and every gate passed it. A step telling someone to look is the kind of instruction
    that gets skipped, so the refusal lives where the approval is written."""
    path = _sb(tmp_path, {"spoiler_check": {"checked": True, "findings": [_FINDING]}})
    with pytest.raises(sb.StoryboardError) as exc:
        sb.approve(path, by="op", at="2026-08-31T09:00:00Z")
    msg = str(exc.value)
    assert "1:48" in msg and "3:02" in msg
    assert "crop" in msg, "the refusal must name the cheapest fix, not just say no"


def test_a_checked_and_clean_entry_approves(tmp_path):
    path = _sb(tmp_path, {"spoiler_check": {"checked": True, "findings": []}})
    assert sb.approve(path, by="op", at="2026-08-31T09:00:00Z")["approved"] is True


def test_an_entry_with_no_spoiler_block_still_approves(tmp_path):
    """Additive by design: this gate does not retroactively block storyboards written before the
    check existed. What it refuses is a check that ran, found something, and was ignored."""
    assert sb.approve(_sb(tmp_path, {}), by="op", at="2026-08-31T09:00:00Z")["approved"] is True


def test_an_accepted_finding_no_longer_blocks(tmp_path):
    path = _sb(
        tmp_path,
        {
            "spoiler_check": {
                "checked": True,
                "findings": [_FINDING],
                "accepted": [
                    {
                        "term": "Fabric Gateway",
                        "by": "op",
                        "at": "2026-08-31",
                        "reason": "9px, in a corner",
                    }
                ],
            }
        },
    )
    assert sb.approve(path, by="op", at="2026-08-31T09:00:00Z")["approved"] is True


def test_there_is_no_count_based_override(tmp_path):
    """Deliberately ONE hatch, not two.

    `--allow-anchors N` is right for identity anchors: a distinct image_job_id has no name a
    person would recognise, so a count is the only handle. A spoiler finding is the opposite — it
    HAS an identity (a term, a shot, a timecode), and a flag waving through "N of them" with one
    shared sentence records strictly less than the per-finding path already writes, while looking
    like the easier road. Accept by name, or fix it.
    """
    import inspect

    assert "allow_spoilers" not in inspect.signature(sb.approve).parameters
    assert "--allow-spoilers" not in inspect.getsource(sb.main)


def test_the_refusal_points_at_the_only_way_through(tmp_path):
    """A refusal that does not name its own escape gets resolved by deleting the check."""
    path = _sb(tmp_path, {"spoiler_check": {"checked": True, "findings": [_FINDING]}})
    with pytest.raises(sb.StoryboardError) as exc:
        sb.approve(path, by="op", at="2026-08-31T09:00:00Z")
    msg = str(exc.value)
    assert "--accept" in msg and "--storyboard" in msg
    assert "no count-based override" in msg


def test_status_reports_open_spoilers(tmp_path):
    path = _sb(tmp_path, {"spoiler_check": {"checked": True, "findings": [_FINDING]}})
    assert sb.status(path)["unaccepted_spoilers"] == 1
