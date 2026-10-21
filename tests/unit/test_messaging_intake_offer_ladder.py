"""Unit tests for Section 5 Offer Ladder & Value Equation in gtm_core.messaging_intake."""

from __future__ import annotations

from pathlib import Path

from gtm_core.messaging_intake import (
    parse_intake_markdown,
    stage_intake,
)

INTAKE_WITH_OFFER_LADDER = """\
# Messaging & Hook Intake

## 2. Capabilities & Claims

### Claim: gateway-core
- **Group:** security
- **Status:** verified
- **Statement:** Inline security gateway.
- **Source:** doc.md:1
- **Do Not Say:** []
- **Boundary:** false

---

## 3. Proof Points & Benchmarks

### Proof: bench-stat
- **Kind:** stat
- **Market:** global
- **Figure Kind:** none
- **Statement:** Fast performance.
- **Source:** doc.md:2
- **Binding:** false

---

## 4. Narrative Angles & Hooks

### Angle: main-angle
- **Seat:** tech-lead
- **Premise:** speed
- **Claim: gateway-core
- **Proof: bench-stat
- **Opener Kind:** account-event
- **Summary:** Fast gateway.
- **Status:** draft

---

## 5. Offer Ladder & Value Equation

### Offer: ladder
- **Dream Outcome:** Make your agents enterprise-ready in an afternoon—with zero code changes.
- **Scope Boundaries:** Never guarantee 100% security approval; inline proxy only.
- **Touch 1 Offers:** 1:1 Indicative Scorecard, Blueprint Alliance Readiness Brief
- **Touch 2 Commercial Step-Down:** 60-day free trial of hosted gateway
- **Internal Quota Bans:** 5,000 MAUs, 50 agents, 8 agents per user
"""


def test_parse_offer_ladder_markdown() -> None:
    doc = parse_intake_markdown(INTAKE_WITH_OFFER_LADDER)
    assert doc.offer_ladder is not None
    assert "Make your agents enterprise-ready in an afternoon" in doc.offer_ladder.dream_outcome
    assert any("Never guarantee" in b for b in doc.offer_ladder.scope_boundaries)
    assert "1:1 Indicative Scorecard" in doc.offer_ladder.touch1_offers
    assert "Blueprint Alliance Readiness Brief" in doc.offer_ladder.touch1_offers
    assert "60-day free trial" in doc.offer_ladder.touch2_stepdown
    assert "5,000 MAUs" in doc.offer_ladder.internal_quota_bans


def test_stage_offer_ladder_syncs_touch1_and_stepdown(tmp_path: Path) -> None:
    profiles_root = tmp_path / "profiles"
    content_root = tmp_path / "content"

    prof_dir = profiles_root / "testco"
    prof_dir.mkdir(parents=True, exist_ok=True)
    (prof_dir / "PROFILE.md").write_text("name: TestCo\n", encoding="utf-8")

    intake_file = tmp_path / "intake.md"
    intake_file.write_text(INTAKE_WITH_OFFER_LADDER, encoding="utf-8")

    staged_files, _ = stage_intake(
        "testco",
        intake_file,
        content_root=content_root,
        profiles_root=profiles_root,
        auto_backup=False,
    )

    assert "gift-artifacts.txt" in staged_files
    gift_text = staged_files["gift-artifacts.txt"].read_text(encoding="utf-8")
    assert "1:1 Indicative Scorecard" in gift_text
    assert "Blueprint Alliance Readiness Brief" in gift_text
    assert "60-day free trial of hosted gateway" in gift_text

    assert "voice-bans.txt" in staged_files
    bans_text = staged_files["voice-bans.txt"].read_text(encoding="utf-8")
    assert "5,000 MAUs" in bans_text
    assert "50 agents" in bans_text
