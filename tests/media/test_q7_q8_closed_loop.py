"""End-to-end reference film harness & closed loop verification (PRD §2.7, §2.8 / Q7, Q8).

Verifies the complete closed loop:
1. Post-render review manifest generation (`review.json`) with initial null human verdict.
2. Checking outcomes ledger before attribution (`unattributed()` reports the asset gap).
3. Simulating human-controlled audience reception dataset.
4. Evaluating reception against PRD §2.3 pre-registered thresholds (`evaluate_reception()`).
5. Persisting reception manifest (`reception.json`) with all judge rows.
6. Recording reception evaluation into `outcomes.jsonl` (`record_reception_outcome()`).
7. Attributing the finished film in `gtm_core.outcomes` (`attribute()`).
8. Asserting the film transitions from unattributed to attributed.
"""

from __future__ import annotations

import json
from pathlib import Path

from gtm_core import outcomes
from gtm_core import reception as rc
from gtm_core.review_json import ReviewManifest, build_review_entry


def test_q7_q8_closed_loop_attribution(tmp_path: Path):
    profile = "acme"
    slug = "film-launch-01"
    content_root = tmp_path / "content"
    film_dir = content_root / profile / "video" / slug
    film_dir.mkdir(parents=True, exist_ok=True)

    # 1. Provide finished video asset and finish manifest
    video_file = film_dir / "master-1080x1920.mp4"
    video_file.write_bytes(b"reference video film binary stream bytes")

    finish_manifest_file = film_dir / "finish-1080x1920.json"
    finish_data = {
        "profile": profile,
        "slug": slug,
        "ratio": "1080x1920",
        "asset_path": str(video_file),
        "stages": ["mux", "caption", "loudnorm"],
        "census": {"mux": 1, "caption": 1, "loudnorm": 1},
        "executed": True,
        "plan_id": "plan-film-launch-01",
    }
    finish_manifest_file.write_text(json.dumps(finish_data, indent=2), encoding="utf-8")

    # 2. Generate review.json with initial null human verdict (Q4)
    entries = [
        build_review_entry(
            shot_index=1,
            directed_expression="brow lowerer (AU4) with tense jaw",
            vlm_description="AU4 brow lowerer visible, slight jaw tension",
            beat_index=1,
        ),
        build_review_entry(
            shot_index=2,
            directed_expression="lip corner pull (AU12) of relief",
            vlm_description="AU12 lip corner pull visible, relaxed cheeks",
            beat_index=2,
        ),
    ]
    review_manifest = ReviewManifest(run_id=slug, entries=entries)
    review_path = film_dir / "review.json"
    review_manifest.write(review_path)

    assert review_path.is_file()
    review_raw = json.loads(review_path.read_text(encoding="utf-8"))
    assert review_raw["run_id"] == slug
    assert len(review_raw["entries"]) == 2
    for e in review_raw["entries"]:
        assert e["human_verdict"] is None

    # 3. Before attribution, film must show as UNATTRIBUTED in outcomes (Q8)
    initial_gaps = outcomes.unattributed(content_root, profile, days=None, repo_root=content_root)
    assert len(initial_gaps) == 1
    assert initial_gaps[0]["slug"] == slug
    assert initial_gaps[0]["ratio"] == "1080x1920"

    # 4. Simulate human-controlled reception survey dataset (Q3 / Q7)
    # 5 judges, all ICP, 100% act rate on both arms, mean moved 4.2
    responses: list[rc.AudienceResponse] = []
    for i in range(1, 6):
        jid = f"judge_{i}"
        responses.append(
            rc.AudienceResponse(
                judge_id=jid,
                condition="control_human",
                took_action=True,
                is_icp=True,
                moved_score=4.0,
                unprompted_recall_24h=True,
            )
        )
        responses.append(
            rc.AudienceResponse(
                judge_id=jid,
                condition="ours",
                took_action=True,
                is_icp=True,
                moved_score=4.2,
                unprompted_recall_24h=True,
            )
        )

    reception_verdict = rc.evaluate_reception(responses)
    assert reception_verdict.passed is True
    assert reception_verdict.judges == 5
    assert reception_verdict.control_act_gap == 0.0
    assert reception_verdict.mean_moved == 4.2

    # 5. Persist reception.json with all judge rows (PRD §2.3)
    reception_path = film_dir / "reception.json"
    rc.write_reception_manifest(reception_path, reception_verdict, responses)
    assert reception_path.is_file()

    reception_raw = json.loads(reception_path.read_text(encoding="utf-8"))
    assert reception_raw["verdict"]["passed"] is True
    assert len(reception_raw["responses"]) == 10

    # 6. Record reception outcome into outcomes ledger
    rc.record_reception_outcome(
        content_root=content_root,
        profile=profile,
        verdict=reception_verdict,
        ref=slug,
    )
    ledger_rows = outcomes.read_outcomes(content_root, profile)
    assert len(ledger_rows) == 1
    assert ledger_rows[0]["channel"] == "reception"
    assert "reception_pass" in ledger_rows[0]["tags"]
    assert ledger_rows[0]["ref"] == slug

    # 7. Attribute finished video asset to close the loop (Q8)
    outcomes.attribute(
        content_root=content_root,
        profile=profile,
        finish_path=finish_manifest_file,
        channel="linkedin",
        ref=slug,
        tags=["q7_reference_film", "primary_lane"],
        repo_root=content_root,
    )

    # 8. Verify the film has transitioned from UNATTRIBUTED to ATTRIBUTED
    post_gaps = outcomes.unattributed(content_root, profile, days=None, repo_root=content_root)
    assert len(post_gaps) == 0, f"expected 0 unattributed gaps, got {post_gaps}"

    # Verify both outcome rows exist in outcomes.jsonl
    all_rows = outcomes.read_outcomes(content_root, profile)
    assert len(all_rows) == 2
    published_row = [r for r in all_rows if r["outcome"] == "published"][0]
    assert published_row["ref"] == slug
    assert "q7_reference_film" in published_row["tags"]
    assert published_row["meta"]["plan_id"] == "plan-film-launch-01"
