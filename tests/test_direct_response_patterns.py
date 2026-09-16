"""Tests for Direct-Response Desire Frameworks & Lead-Magnet Bridges.

Validates that:
1. docs/direct-response-patterns.md exists and contains all 5 core B2B DR patterns.
2. The written copy examples from the catalog pass content_linter with zero errors and zero warnings
   (confirming no antithetical parallelism, no AI tell words, and length bounds).
3. The video shotlist durations comply with short-form provider limits (total duration 30-50s,
   individual shots <= 15s).
4. content-studio, video-script, and content-plan skill templates cite the catalog.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from tests.linter.content_linter import lint, lint_prose_quality

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / "docs" / "direct-response-patterns.md"

PATTERNS = [
    "dr-symptom-root-cause",
    "dr-earned-authority",
    "dr-gap-roadblock",
    "dr-empirical-test",
    "dr-industry-benchmark",
]


def test_catalog_exists_and_contains_all_five_patterns():
    assert CATALOG.is_file(), "docs/direct-response-patterns.md missing"
    text = CATALOG.read_text(encoding="utf-8")
    for pat in PATTERNS:
        assert pat in text, f"Pattern {pat} not found in catalog"


def test_dual_action_bridge_rules_defined():
    text = CATALOG.read_text(encoding="utf-8")
    assert "Dual-Action Platform Bridge" in text
    assert "LinkedIn Bridge" in text
    assert "X Bridge" in text


def test_written_templates_pass_content_linter():
    """Verify concrete written examples for each pattern compile with 0 errors and 0 warnings."""
    examples = [
        {
            "pattern": "dr-symptom-root-cause",
            "hook": "Ten hours of sprint meetings, zero code shipped, release slipping.",
            "body": (
                "Ten hours of sprint meetings, zero code shipped, release slipping. "
                "The issue is not developer discipline. It is calendar architecture. "
                "Our 3-tier meeting firewall template reclaimed 14 engineering hours a week. "
                "Drop 'FIREWALL' below (or DM me) and I'll send over the raw sheet."
            ),
        },
        {
            "pattern": "dr-earned-authority",
            "hook": "We audited 340 enterprise agent gateways last quarter.",
            "body": (
                "We audited 340 enterprise agent gateways last quarter. "
                "Teams achieving high compliance were not running larger models: "
                "they restricted agent authorization to screen-level mocks. "
                "I documented the 3 prerequisites in an 8-page brief. "
                "Comment 'GATEWAY' to grab the PDF."
            ),
        },
        {
            "pattern": "dr-gap-roadblock",
            "hook": "Autonomous deployments in production.",
            "body": (
                "Autonomous deployments in production. "
                "Most platform teams remain stalled on manual code reviews for 48 hours. "
                "The single blocker: non-deterministic tests. "
                "We built a deterministic mock generator for pytest that stabilizes evaluations. "
                "Drop 'mock' for the repo."
            ),
        },
        {
            "pattern": "dr-empirical-test",
            "hook": "We tested an alternative enrichment waterfall against the standard baseline.",
            "body": (
                "We tested an alternative enrichment waterfall against the standard baseline. "
                "Account response rates changed from 1.8% to 6.2% across 800 accounts. "
                "The entire routing logic is mapped in this diagram. "
                "Comment 'WATERFALL' to inspect the flowchart."
            ),
        },
        {
            "pattern": "dr-industry-benchmark",
            "hook": "Top engineering teams enforce egress filtering at the Linux kernel layer.",
            "body": (
                "Top engineering teams enforce egress filtering at the Linux kernel layer rather than the application gateway. "
                "Kernel-level filtering eliminates bypasses from compromised dependencies before user code executes. "
                "We broke down the topologies in a brief. "
                "Comment 'kernel'."
            ),
        },
    ]

    for ex in examples:
        # LinkedIn post body prose linting
        violations = lint_prose_quality(ex["body"])
        assert len(violations) == 0, (
            f"Pattern {ex['pattern']} has prose quality violations: {violations}"
        )

        # Test as X single
        x_single = {
            "platform": "x",
            "format": "single",
            "tweets": [ex["body"]],
        }
        x_violations = lint(x_single)
        # Verify 0 errors and 0 warnings
        assert len(x_violations) == 0, (
            f"Pattern {ex['pattern']} failed X single lint: {x_violations}"
        )


def test_skills_reference_direct_response_patterns():
    """Verify that content-studio, video-script, and content-plan cite direct-response-patterns."""
    for skill_name in ["content-studio", "video-script", "content-plan"]:
        skill_file = REPO / "plugin" / "skills" / skill_name / "SKILL.md"
        assert skill_file.is_file(), f"{skill_name}/SKILL.md missing"
        text = skill_file.read_text(encoding="utf-8")
        assert "docs/direct-response-patterns.md" in text, (
            f"{skill_name}/SKILL.md does not cite docs/direct-response-patterns.md"
        )


def test_video_shotlist_specs_abide_by_provider_ceilings():
    """Verify each shot spec in the catalog does not exceed the 15s provider duration ceiling."""
    text = CATALOG.read_text(encoding="utf-8")
    # Matches lines like: `Shot 1` (0–3s, Presenter): Camera slow push-in...
    shot_matches = re.findall(r"`Shot \d+`\s*\((\d+)[–-](\d+)s", text)
    assert len(shot_matches) >= 20, (
        f"Expected at least 20 shot duration specs, found {len(shot_matches)}"
    )
    for start_s, end_s in shot_matches:
        dur = int(end_s) - int(start_s)
        assert dur <= 15, f"Shot duration {dur}s ({start_s}-{end_s}s) exceeds 15s provider ceiling"
        assert dur >= 1, f"Shot duration {dur}s ({start_s}-{end_s}s) is too short"


def test_direct_response_shotlist_validates_against_shots_schema_and_lint():
    """Verify that a canonical 4-shot direct response shotlist validates against shots.schema.json and shots_lint."""
    from gtm_core.shots_lint import lint_shotlist
    from tests.contracts.minijsonschema import validate

    schema = json.loads((REPO / "schemas" / "shots.schema.json").read_text(encoding="utf-8"))
    dr_shotlist = {
        "source_item": "ci-direct-response-01",
        "total_duration_s": 32,
        "synthetic_disclosure": "AI-generated avatar and voice per tenant configuration.",
        "vo_source": "operator-recorded WAV",
        "style_scaffold": {
            "look": "cinematic dark tech office, subtle blue rim light",
            "provider_model": "seedance_2_0",
            "negative": "glitch, cartoon, distortion",
        },
        "shots": [
            {
                "n": 1,
                "duration_s": 4,
                "camera": "static, slow push-in",
                "motion_prompt": "she sits at her laptop, looks up toward camera and speaks directly",
                "visual": "modern engineering workspace at twilight",
                "role": "presenter",
                "spoken": "Ten hours of sprint meetings and zero code shipped.",
                "audio_bed": "subtle low ambient drone under the room",
            },
            {
                "n": 2,
                "duration_s": 6,
                "camera": "locked wide, camera remains still",
                "motion_prompt": "camera glides across whiteboard diagram showing meeting firewall tiers",
                "visual": "clean whiteboard showing meeting firewall diagram",
                "role": "broll",
                "spoken": "The issue is not engineer discipline, it is calendar architecture.",
                "audio_bed": "room tone under the speech",
            },
            {
                "n": 3,
                "duration_s": 12,
                "camera": "static, slow push-in",
                "motion_prompt": "cursor clicks through the three firewall tiers in the spreadsheet",
                "visual": "screen recording of calendar firewall template in Google Sheets",
                "role": "screen",
                "audio_bed": "soft rhythmic electronic pulse",
            },
            {
                "n": 4,
                "duration_s": 10,
                "camera": "static, slow push-in",
                "motion_prompt": "she addresses the camera directly with a firm closing look",
                "visual": "clean studio desk backdrop",
                "role": "presenter",
                "spoken": "Comment firewall below and I will send over the raw sheet.",
                "caption_text_override": 'Comment "firewall" below',
                "caption_override_reason": "lower third keyword CTA",
                "audio_bed": "fade out ambient room tone",
            },
        ],
    }

    # 1. JSON Schema validation
    schema_errors = validate(dr_shotlist, schema)
    assert schema_errors == [], f"DR shotlist failed schema validation: {schema_errors}"

    # 2. shots_lint verification
    lint_errors, lint_warnings = lint_shotlist(dr_shotlist)
    assert lint_errors == [], f"DR shotlist failed shots_lint with errors: {lint_errors}"
