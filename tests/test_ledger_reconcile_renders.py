"""`reconcile-renders`: recover video spend the ledger never saw.

W0.3. In August 2026 the video programme spent
~580 Higgsfield credits that `costs.jsonl` records as $0. Generations drew a PRE-PURCHASED credit
pool, so no code path was forced to meter them, and the profile's $368 monthly cap never saw the
spend. `render_manifest` now refuses an unmetered synthetic manifest — but that binds only renders
written after the gate. This verb reads what is already on disk, so the historical spend is
recoverable rather than archaeological.
"""

from __future__ import annotations

import json
from pathlib import Path

from gtm_core.ledger_cli import reconcile_renders


def _manifest(video_dir: Path, slug: str, ratio: str, **fields) -> None:
    d = video_dir / slug
    d.mkdir(parents=True, exist_ok=True)
    payload = {"profile": "acme", "slug": slug, "ratio": ratio, "asset_path": "a.mp4"}
    payload.update(fields)
    (d / f"render-{ratio}.json").write_text(json.dumps(payload))


def test_it_reproduces_the_august_shape_credits_spent_nothing_metered(tmp_path):
    video = tmp_path / "content" / "acme" / "video"
    _manifest(video, "gym-incident", "4x5", cost_credits=361.8)
    _manifest(video, "gym-incident-v2", "4x5", cost_credits=168.0)

    out = reconcile_renders("acme", repo_root=tmp_path)

    assert out["manifests"] == 2
    assert out["credits_claimed"] == 529.8
    # The whole point: every credit is claimed by a manifest and none reaches the cap.
    assert out["credits_unmetered"] == 529.8
    assert out["unmetered_count"] == 2


def test_a_manifest_citing_a_ledger_row_is_counted_as_metered(tmp_path):
    video = tmp_path / "content" / "acme" / "video"
    _manifest(
        video,
        "properly-costed",
        "9x16",
        cost_credits=22.5,
        cost_source="preflight",
        cost_ledger_ts="2026-08-20T12:00:00Z",
    )
    out = reconcile_renders("acme", repo_root=tmp_path)
    assert out["credits_claimed"] == 22.5
    assert out["credits_unmetered"] == 0
    assert out["unmetered"] == []


def test_a_free_path_needs_no_ledger_row_to_count_as_metered(tmp_path):
    video = tmp_path / "content" / "acme" / "video"
    _manifest(video, "free-fallback", "1x1", cost_credits=0.0, cost_source="free")
    out = reconcile_renders("acme", repo_root=tmp_path)
    assert out["unmetered_count"] == 0


def test_a_month_filter_never_hides_an_unmetered_render(tmp_path):
    """An unmetered manifest has no ts to filter on — excluding it would hide the population
    this verb exists to surface, so it survives every window."""
    video = tmp_path / "content" / "acme" / "video"
    _manifest(video, "unmetered", "4x5", cost_credits=100.0)
    _manifest(
        video,
        "july",
        "4x5",
        cost_credits=5.0,
        cost_source="preflight",
        cost_ledger_ts="2026-07-01T00:00:00Z",
    )
    out = reconcile_renders("acme", month="2026-08", repo_root=tmp_path)
    assert [r["slug"] for r in out["rows"]] == ["unmetered"]
    assert out["credits_unmetered"] == 100.0


def test_an_unreadable_manifest_is_reported_not_crashed_on(tmp_path):
    video = tmp_path / "content" / "acme" / "video" / "broken"
    video.mkdir(parents=True)
    (video / "render-4x5.json").write_text("{not json")
    out = reconcile_renders("acme", repo_root=tmp_path)
    assert out["manifests"] == 1
    assert "error" in out["rows"][0]


def test_a_profile_with_no_video_directory_is_zero_not_an_error(tmp_path):
    out = reconcile_renders("acme", repo_root=tmp_path)
    assert out["manifests"] == 0 and out["credits_claimed"] == 0
