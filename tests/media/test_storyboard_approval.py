"""gtm_core.storyboard — the recorded half of video-storyboard's ⟦GATE:plan⟧.

Calibrated against 2026-08-18: a synthetic render ran end to end with the storyboard stage never
invoked, because the gate lived only in a skill's prose. Approval is now a verified write that
render_manifest refuses to proceed without.
"""

from __future__ import annotations

import json

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
