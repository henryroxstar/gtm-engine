"""Subprocess tests for agent/onboard_cli.py — the deterministic front door the setup
skill shells out to via Bash. Each subcommand is driven as a real `python -m` subprocess
(not imported in-process) so these tests exercise the actual CLI entry point, argv
parsing, and JSON-on-stdout discipline end to end.

`GTM_PROFILES_ROOT` is set to a tmp_path per test so nothing here touches the real
profiles/ tree (mirrors the `cfg_isolated` fixture used by test_onboard_settings.py,
but via the env var since the CLI resolves its own Config).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC
from pathlib import Path

from tests.agent.test_onboard_settings import _base_draft

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(args, profiles_root):
    env = {**os.environ, "GTM_PROFILES_ROOT": str(profiles_root)}
    return subprocess.run(
        [sys.executable, "-m", "agent.onboard_cli", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )


def _write_draft(tmp_path) -> Path:
    draft_file = tmp_path / "draft.json"
    draft_file.write_text(json.dumps(_base_draft()))
    return draft_file


def _write_draft_named(tmp_path, filename: str, company_name: str) -> Path:
    draft = _base_draft()
    draft["company"]["name"] = company_name
    draft_file = tmp_path / filename
    draft_file.write_text(json.dumps(draft))
    return draft_file


def _backdate_meta(tmp_path, slug: str, days: int) -> None:
    from datetime import datetime, timedelta

    meta_file = tmp_path / ".staging" / slug / ".onboard-meta.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    old_ts = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    meta["created_at"] = old_ts
    meta["updated_at"] = old_ts
    meta_file.write_text(json.dumps(meta), encoding="utf-8")


def test_render_stage_then_promote(tmp_path):
    draft_file = _write_draft(tmp_path)

    r = _run(["render-stage", "--draft", str(draft_file)], tmp_path)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    # _base_draft()'s company name is "Acme Inc" — slugify() normalises to "acme-inc".
    assert out["slug"] == "acme-inc"
    assert out["draft_id"]
    assert out["gaps"] == []
    assert out["confidence"] == "high"
    assert "PROFILE.md" in out["files"]
    assert (tmp_path / ".staging" / "acme-inc" / "PROFILE.md").exists()

    p = _run(["promote", "--draft-id", out["draft_id"], "--draft", str(draft_file)], tmp_path)
    assert p.returncode == 0, p.stderr
    promoted = json.loads(p.stdout)
    assert promoted["status"] == "promoted"
    assert promoted["slug"] == "acme-inc"
    assert (tmp_path / "acme-inc" / "PROFILE.md").exists()
    # staging dir is gone after the atomic rename
    assert not (tmp_path / ".staging" / "acme-inc").exists()


def test_promote_resumes_without_original_draft_file(tmp_path):
    """Resume scenario: a session that only has a draft_id (from `status`), never the
    original temp draft file the live agent session wrote — e.g. the founder abandoned
    mid-flow and came back later. promote() must still work from the persisted staged copy.
    """
    draft_file = _write_draft(tmp_path)

    r = _run(["render-stage", "--draft", str(draft_file)], tmp_path)
    assert r.returncode == 0, r.stderr
    draft_id = json.loads(r.stdout)["draft_id"]

    # Simulate the original temp draft file being gone by the time the session resumes.
    draft_file.unlink()

    p = _run(["promote", "--draft-id", draft_id], tmp_path)
    assert p.returncode == 0, p.stderr
    promoted = json.loads(p.stdout)
    assert promoted["status"] == "promoted"
    assert promoted["slug"] == "acme-inc"
    assert (tmp_path / "acme-inc" / "PROFILE.md").exists()
    # The persisted draft copy is cleaned up, not left dangling in the live profile.
    assert not (tmp_path / "acme-inc" / ".draft.json").exists()


def test_status_lists_staged_drafts(tmp_path):
    # No staging dir at all yet.
    r = _run(["status"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"staged": []}

    draft_file = _write_draft(tmp_path)
    staged = _run(["render-stage", "--draft", str(draft_file)], tmp_path)
    draft_id = json.loads(staged.stdout)["draft_id"]

    r = _run(["status"], tmp_path)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert len(out["staged"]) == 1
    assert out["staged"][0]["draft_id"] == draft_id
    assert out["staged"][0]["slug"] == "acme-inc"
    assert out["staged"][0]["status"] == "staged"
    assert out["staged"][0]["stale"] is False


def test_status_flags_stale_and_prunes(tmp_path):
    draft_file = _write_draft(tmp_path)
    staged = _run(["render-stage", "--draft", str(draft_file)], tmp_path)
    assert staged.returncode == 0, staged.stderr

    # Backdate the staged draft's meta to 20 days ago — old enough to be both stale (>3d)
    # and eligible for prune (>14d).
    _backdate_meta(tmp_path, "acme-inc", 20)

    r = _run(["status"], tmp_path)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["staged"][0]["stale"] is True

    p = _run(["status", "--prune"], tmp_path)
    assert p.returncode == 0, p.stderr
    pruned = json.loads(p.stdout)
    assert pruned["pruned"] == ["acme-inc"]
    assert not (tmp_path / ".staging" / "acme-inc").exists()


def test_status_stale_retention_window_survives_prune(tmp_path):
    """The critical safety boundary of auto-cancel: a draft that is stale (>3d) but still
    inside the 14-day retention window must be flagged stale and NOT pruned. A refactor
    that accidentally reused _STALE_DAYS as the prune threshold would delete a founder's
    in-progress draft with no warning — this is the test that would catch that.
    """
    draft_file = _write_draft_named(tmp_path, "draft-mid.json", "Beta LLC")
    staged = _run(["render-stage", "--draft", str(draft_file)], tmp_path)
    assert staged.returncode == 0, staged.stderr
    slug = json.loads(staged.stdout)["slug"]
    assert slug == "beta-llc"

    # 7 days: past the 3-day stale threshold, well inside the 14-day prune threshold.
    _backdate_meta(tmp_path, slug, 7)

    r = _run(["status"], tmp_path)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["staged"][0]["slug"] == slug
    assert out["staged"][0]["stale"] is True

    p = _run(["status", "--prune"], tmp_path)
    assert p.returncode == 0, p.stderr
    pruned = json.loads(p.stdout)
    assert pruned["pruned"] == []
    assert (tmp_path / ".staging" / slug).exists()


def test_render_stage_flags_slug_collision(tmp_path):
    # A live profile already exists at profiles/acme-inc/ before render-stage runs.
    (tmp_path / "acme-inc").mkdir(parents=True)
    (tmp_path / "acme-inc" / "PROFILE.md").write_text("# existing")

    draft_file = _write_draft(tmp_path)
    r = _run(["render-stage", "--draft", str(draft_file)], tmp_path)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["collision"] is True
    assert out["slug"] == "acme-inc"
    # Staging still proceeds despite the collision — promote() remains the final gate.
    assert (tmp_path / ".staging" / "acme-inc" / "PROFILE.md").exists()


def test_render_stage_no_collision_when_slug_is_free(tmp_path):
    draft_file = _write_draft(tmp_path)
    r = _run(["render-stage", "--draft", str(draft_file)], tmp_path)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["collision"] is False


def test_diff_then_cancel(tmp_path):
    draft_file = _write_draft(tmp_path)
    staged = _run(["render-stage", "--draft", str(draft_file)], tmp_path)
    draft_id = json.loads(staged.stdout)["draft_id"]

    d = _run(["diff", "--draft-id", draft_id], tmp_path)
    assert d.returncode == 0, d.stderr
    out = json.loads(d.stdout)
    assert out["slug"] == "acme-inc"
    # No live profile yet, so every staged file diffs against `old: None`.
    assert out["diff"]["PROFILE.md"]["old"] is None
    assert "# Acme Inc" in out["diff"]["PROFILE.md"]["new"]

    c = _run(["cancel", "--draft-id", draft_id], tmp_path)
    assert c.returncode == 0, c.stderr
    cancelled = json.loads(c.stdout)
    assert cancelled["status"] == "cancelled"
    assert not (tmp_path / ".staging" / "acme-inc").exists()

    # A second cancel (or diff) against the now-gone draft_id fails cleanly, not by
    # printing a bogus JSON object to stdout.
    again = _run(["cancel", "--draft-id", draft_id], tmp_path)
    assert again.returncode != 0
    assert again.stdout == ""
