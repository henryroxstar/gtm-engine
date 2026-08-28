"""Tests for ``gtm_core.content_quality`` deterministic quality gates."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from gtm_core import content_quality as cq
from gtm_core.video_lint import Finding, Probe


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _make_profile_tree(tmp_path: Path, profile: str) -> Path:
    """Create a minimal profile tree under tmp_path/profiles/<profile>."""
    profiles_root = tmp_path / "profiles"
    profile_dir = profiles_root / profile
    knowledge = profile_dir / "knowledge"
    knowledge.mkdir(parents=True, exist_ok=True)

    (profile_dir / "PROFILE.md").write_text(
        "## Content OS\n```\n"
        "content_pillars: [content creation, AI tools for creators, solopreneur growth]\n"
        "monthly_tool_budget_usd: 100\n"
        "```\n",
        encoding="utf-8",
    )
    (knowledge / "hook-matrix.md").write_text(
        "## Example\n"
        "| id | Persona | Signal to open on | Hook angle |\n"
        "|---|---|---|---|\n"
        "| example-founder-conversation | Founder | Posting inconsistently | Your niche... |\n",
        encoding="utf-8",
    )
    (knowledge / "icp-personas.md").write_text(
        "## Founder\nA founder building a personal brand.", encoding="utf-8"
    )
    (knowledge / "voice-bans.txt").write_text("game-changing\nleverage\n", encoding="utf-8")
    (knowledge / "BRAND.toml").write_text(
        '[meta]\nsource = "test"\n\n[disclosure]\nline = "This content was created with AI assistance."\n',
        encoding="utf-8",
    )
    return profiles_root


def _make_content_tree(tmp_path: Path, profile: str, items: list[dict]) -> Path:
    content_root = tmp_path / "content"
    plans_dir = content_root / profile / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    _write_json(plans_dir / "2026-W33-plan.json", items)
    return content_root


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """Patch path resolvers to point at the temp tree."""
    monkeypatch.setattr(cq, "resolve_profiles_root", lambda: tmp_path / "profiles")
    monkeypatch.setattr(cq, "resolve_content_root", lambda: tmp_path / "content")

    def person_path():
        return tmp_path / "identity" / "henry" / "hook-matrix.md"

    monkeypatch.setattr(cq, "_person_hook_matrix_path", person_path)
    return tmp_path


def test_pre_check_warns_when_hook_id_missing(setup):
    tmp_path = setup
    _make_profile_tree(tmp_path, "example")
    _make_content_tree(
        tmp_path,
        "example",
        [
            {
                "id": "ci-1",
                "pillar": "content creation",
                "story_id": "s1",
                "platform": "linkedin",
                "format": "text",
                "status": "planned",
            }
        ],
    )
    result = cq.pre_check("example", "ci-1")
    assert result["proceed"] is True
    assert result["checks"]["pillar_valid"] is True
    assert result["checks"]["hook_id_in_matrix"] is None
    assert any("no hook_id" in w.lower() for w in result["warnings"])


def test_pre_check_blocks_unknown_pillar(setup):
    tmp_path = setup
    _make_profile_tree(tmp_path, "example")
    _make_content_tree(
        tmp_path,
        "example",
        [
            {
                "id": "ci-2",
                "pillar": "not a pillar",
                "story_id": "s1",
                "platform": "linkedin",
                "format": "text",
                "status": "planned",
            }
        ],
    )
    result = cq.pre_check("example", "ci-2")
    assert result["proceed"] is False
    assert result["checks"]["pillar_valid"] is False
    assert any("not in profile content_pillars" in b for b in result["blocking"])


def test_pre_check_blocks_video_missing_disclosure(setup):
    tmp_path = setup
    _make_profile_tree(tmp_path, "example")
    # Remove disclosure line.
    brand_path = tmp_path / "profiles" / "example" / "knowledge" / "BRAND.toml"
    brand_path.write_text('[meta]\nsource = "test"\n', encoding="utf-8")
    _make_content_tree(
        tmp_path,
        "example",
        [
            {
                "id": "ci-3",
                "pillar": "content creation",
                "story_id": "s1",
                "platform": "instagram",
                "format": "reel",
                "status": "planned",
            }
        ],
    )
    result = cq.pre_check("example", "ci-3")
    assert result["proceed"] is False
    assert result["checks"]["video_disclosure_configured"] is False
    assert any("disclosure.line" in b for b in result["blocking"])


def test_pre_check_hook_id_found_in_matrix(setup):
    tmp_path = setup
    _make_profile_tree(tmp_path, "example")
    _make_content_tree(
        tmp_path,
        "example",
        [
            {
                "id": "ci-4",
                "pillar": "content creation",
                "story_id": "s1",
                "platform": "linkedin",
                "format": "text",
                "status": "planned",
                "hook_id": "example-founder-conversation",
            }
        ],
    )
    result = cq.pre_check("example", "ci-4")
    assert result["proceed"] is True
    assert result["checks"]["hook_id_in_matrix"] is True


def test_post_check_blocks_text_asset_with_url(setup):
    tmp_path = setup
    _make_profile_tree(tmp_path, "example")
    content_root = _make_content_tree(
        tmp_path,
        "example",
        [
            {
                "id": "ci-5",
                "pillar": "content creation",
                "story_id": "s1",
                "platform": "linkedin",
                "format": "text",
                "status": "drafted",
            }
        ],
    )
    asset = {
        "platform": "linkedin",
        "format": "text",
        "hook": "A hook without digits",
        "body": "Read more at https://example.com please.",
        "hashtags": ["#ai"],
    }
    assets_dir = content_root / "example" / "assets"
    _write_json(assets_dir / "ci-5.asset.json", asset)

    result = cq.post_check("example", "ci-5")
    assert result["proceed"] is False
    assert result["checks"]["linter_pass"] is False
    assert result["checks"]["safe_to_share_pass"] is True


def test_post_check_blocks_voice_ban(setup):
    tmp_path = setup
    _make_profile_tree(tmp_path, "example")
    content_root = _make_content_tree(
        tmp_path,
        "example",
        [
            {
                "id": "ci-6",
                "pillar": "content creation",
                "story_id": "s1",
                "platform": "linkedin",
                "format": "text",
                "status": "drafted",
            }
        ],
    )
    asset = {
        "platform": "linkedin",
        "format": "text",
        "hook": "Game-changing news",
        "body": "This is game-changing for founders.",
        "hashtags": ["#ai"],
    }
    _write_json(content_root / "example" / "assets" / "ci-6.asset.json", asset)

    result = cq.post_check("example", "ci-6")
    assert result["proceed"] is False
    assert result["checks"]["voice_bans_pass"] is False
    assert any("game-changing" in b.lower() for b in result["blocking"])


def test_post_check_blocks_synthetic_video_missing_disclosure(setup):
    tmp_path = setup
    _make_profile_tree(tmp_path, "example")
    # Remove disclosure line.
    brand_path = tmp_path / "profiles" / "example" / "knowledge" / "BRAND.toml"
    brand_path.write_text('[meta]\nsource = "test"\n', encoding="utf-8")
    content_root = _make_content_tree(
        tmp_path,
        "example",
        [
            {
                "id": "ci-7",
                "pillar": "content creation",
                "story_id": "s1",
                "platform": "instagram",
                "format": "reel",
                "status": "drafted",
            }
        ],
    )
    video_dir = content_root / "example" / "video" / "reel-1"
    video_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        video_dir / "finish-9x16.json",
        {
            "profile": "example",
            "slug": "reel-1",
            "ratio": "9:16",
            "asset_path": str(video_dir / "asset.mp4"),
            "stages": ["loudnorm"],
            "census": {"loudnorm": 1},
            "plan_id": "a1b2c3d4e5f60718",
            "captions": {"frame": [1080, 1920], "screens": []},
        },
    )
    _write_json(
        video_dir / "render-9x16.json",
        {
            "profile": "example",
            "slug": "reel-1",
            "ratio": "9:16",
            "asset_path": str(video_dir / "asset.mp4"),
            "identity_used": ["soul"],
            "source_item": "ci-7",
        },
    )
    (video_dir / "asset.mp4").write_bytes(b"fake video")

    # Patch probe/evaluate so we don't need ffmpeg.
    with patch.object(cq, "video_probe") as mock_probe, patch.object(cq, "evaluate") as mock_eval:
        mock_probe.return_value = Probe(
            width=1080, height=1920, fps=30, duration_s=10, bit_rate=8_000_000
        )
        mock_eval.return_value = []
        result = cq.post_check("example", "ci-7")

    assert result["proceed"] is False
    assert result["checks"]["disclosure_configured"] is False
    assert any("disclosure.line" in b for b in result["blocking"])


def test_post_check_blocks_video_failing_v1_v3_v4(setup):
    tmp_path = setup
    _make_profile_tree(tmp_path, "example")
    content_root = _make_content_tree(
        tmp_path,
        "example",
        [
            {
                "id": "ci-8",
                "pillar": "content creation",
                "story_id": "s1",
                "platform": "instagram",
                "format": "reel",
                "status": "drafted",
            }
        ],
    )
    video_dir = content_root / "example" / "video" / "reel-2"
    video_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        video_dir / "finish-9x16.json",
        {
            "profile": "example",
            "slug": "reel-2",
            "ratio": "9:16",
            "asset_path": str(video_dir / "asset.mp4"),
            "stages": ["loudnorm"],
            "census": {"loudnorm": 1},
            "plan_id": "8071f6e5d4c3b2a1",
            "captions": {"frame": [1080, 1920], "screens": []},
        },
    )
    _write_json(
        video_dir / "render-9x16.json",
        {
            "profile": "example",
            "slug": "reel-2",
            "ratio": "9:16",
            "asset_path": str(video_dir / "asset.mp4"),
            "identity_used": ["generated"],
            "source_item": "ci-8",
        },
    )
    (video_dir / "asset.mp4").write_bytes(b"fake video")

    with patch.object(cq, "video_probe") as mock_probe, patch.object(cq, "evaluate") as mock_eval:
        mock_probe.return_value = Probe(
            width=720, height=1280, fps=24, duration_s=10, bit_rate=4_000_000
        )
        mock_eval.return_value = [
            Finding(
                tier="V1",
                rule="resolution",
                severity="error",
                asset="",
                excerpt="720x1280 below floor",
                fix="re-render",
            ),
            Finding(
                tier="V3",
                rule="caption",
                severity="error",
                asset="",
                excerpt="caption outside safe area",
                fix="reframe",
            ),
        ]
        result = cq.post_check("example", "ci-8")

    assert result["proceed"] is False
    assert result["checks"]["video_lint_v1_v4_pass"] is False
    assert any("V1" in b for b in result["blocking"])
    assert any("V3" in b for b in result["blocking"])


def test_post_check_matches_render_manifest_by_source_item_not_plan_id(setup):
    """Regression: _find_video_manifests() used to match a finish manifest's plan_id (a
    content hash, e.g. "b6d2f9088d1abd28") against the ContentItem id — the two can never be
    equal, so post_check failed with 'finish manifest not found' for every video item, always
    (reproduced live on an already-shipped asset). The fix matches on render-<ratio>.json's
    source_item field instead. This test's finish manifest deliberately carries a plan_id that
    does NOT equal the item id, to prove the old matching logic is truly gone, not just no
    longer exercised."""
    tmp_path = setup
    _make_profile_tree(tmp_path, "example")
    content_root = _make_content_tree(
        tmp_path,
        "example",
        [
            {
                "id": "ci-9",
                "pillar": "content creation",
                "story_id": "s1",
                "platform": "instagram",
                "format": "reel",
                "status": "drafted",
            }
        ],
    )
    video_dir = content_root / "example" / "video" / "reel-3"
    video_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        video_dir / "finish-9x16.json",
        {
            "profile": "example",
            "slug": "reel-3",
            "ratio": "9x16",  # filename-safe form, as gtm_core.video_finish actually writes
            "asset_path": str(video_dir / "asset.mp4"),
            "stages": ["loudnorm"],
            "census": {"loudnorm": 1},
            # Deliberately NOT "ci-9" — proves matching no longer depends on this field.
            "plan_id": "deadbeefcafef00d",
            "captions": {"frame": [1080, 1920], "screens": []},
        },
    )
    _write_json(
        video_dir / "render-9x16.json",
        {
            "profile": "example",
            "slug": "reel-3",
            "ratio": "9:16",
            "asset_path": str(video_dir / "asset.mp4"),
            "identity_used": ["element", "voice"],
            "lip_sync_source": "audio_references",
            "source_item": "ci-9",
            # Extra prose fields no real render-*.json in this repo has ever omitted — proves
            # load-side tolerance of the hand-authored shape, not just the id match.
            "hook_id": "example-hook",
            "shots": [{"n": 1, "spoken": "hello"}],
            "cost": {"grand_total": 12.3},
        },
    )
    (video_dir / "asset.mp4").write_bytes(b"fake video")

    with patch.object(cq, "video_probe") as mock_probe, patch.object(cq, "evaluate") as mock_eval:
        mock_probe.return_value = Probe(
            width=1080, height=1920, fps=30, duration_s=10, bit_rate=8_000_000
        )
        mock_eval.return_value = []
        result = cq.post_check("example", "ci-9")

    # The whole point: it reaches and passes every real check, not just "not blocked".
    assert result["proceed"] is True
    assert result["blocking"] == []
    assert result["checks"]["finish_manifest_valid"] is True
    assert result["checks"]["render_manifest_valid"] is True
    assert result["checks"]["identity_recorded"] is True
    assert result["checks"]["disclosure_configured"] is True
    assert result["checks"]["captions_inside_safe_area"] is True
    assert result["checks"]["loudness_target_met"] is True
    assert result["checks"]["video_lint_v1_v4_pass"] is True

    # Verify the evaluate() call actually received the colon-form ratio SAFE_AREAS is keyed
    # by, not the "9x16" filename-safe form stored in finish.ratio.
    _, call_kwargs = mock_eval.call_args
    assert call_kwargs["ratio"] == "9:16"


def test_find_video_manifests_ignores_finish_plan_id(tmp_path):
    """Unit-level regression on _find_video_manifests directly: a finish manifest's plan_id
    must never be read as a match key, even if it happens to collide with the item id."""
    video_dir = tmp_path / "example" / "video" / "reel-4"
    video_dir.mkdir(parents=True)
    _write_json(
        video_dir / "finish-9x16.json",
        {"plan_id": "ci-10", "asset_path": "asset.mp4"},  # collides with item_id on purpose
    )
    # No render-9x16.json with source_item == "ci-10" — must NOT match on plan_id alone.
    result = cq._find_video_manifests(tmp_path, "example", "ci-10")
    assert result["finish"] is None
    assert result["render"] is None


def test_find_video_manifests_matches_on_source_item(tmp_path):
    video_dir = tmp_path / "example" / "video" / "reel-5"
    video_dir.mkdir(parents=True)
    _write_json(video_dir / "render-9x16.json", {"source_item": "ci-11"})
    _write_json(video_dir / "finish-9x16.json", {"plan_id": "unrelated-hash", "asset_path": None})
    result = cq._find_video_manifests(tmp_path, "example", "ci-11")
    assert result["render"] == video_dir / "render-9x16.json"
    assert result["finish"] == video_dir / "finish-9x16.json"


def test_person_layer_henry_hook_resolves(setup):
    tmp_path = setup
    _make_profile_tree(tmp_path, "example")
    person_dir = tmp_path / "identity" / "henry"
    person_dir.mkdir(parents=True, exist_ok=True)
    (person_dir / "hook-matrix.md").write_text(
        "## Henry\n"
        "| id | Persona | Signal to open on | Hook angle |\n"
        "|---|---|---|---|\n"
        "| henry-build-in-public | Founder | Shipping in public | The real resume is what you ship. |\n",
        encoding="utf-8",
    )
    _make_content_tree(
        tmp_path,
        "example",
        [
            {
                "id": "ci-9",
                "pillar": "content creation",
                "story_id": "s1",
                "platform": "linkedin",
                "format": "text",
                "status": "planned",
                "hook_id": "henry-build-in-public",
            }
        ],
    )
    result = cq.pre_check("example", "ci-9")
    assert result["proceed"] is True
    assert result["checks"]["hook_id_in_matrix"] is True
    assert cq.hook_id_to_product("henry-build-in-public") == "henry"


def test_cli_pre_returns_nonzero_on_block(setup):
    tmp_path = setup
    _make_profile_tree(tmp_path, "example")
    _make_content_tree(
        tmp_path,
        "example",
        [
            {
                "id": "ci-10",
                "pillar": "bad pillar",
                "story_id": "s1",
                "platform": "linkedin",
                "format": "text",
                "status": "planned",
            }
        ],
    )
    assert cq.main(["pre", "--profile", "example", "--item", "ci-10"]) == 1


def test_cli_pre_returns_zero_on_pass(setup):
    tmp_path = setup
    _make_profile_tree(tmp_path, "example")
    _make_content_tree(
        tmp_path,
        "example",
        [
            {
                "id": "ci-11",
                "pillar": "content creation",
                "story_id": "s1",
                "platform": "linkedin",
                "format": "text",
                "status": "planned",
            }
        ],
    )
    assert cq.main(["pre", "--profile", "example", "--item", "ci-11"]) == 0


# --- script_check: the claims_verified front-block gate -----------------------------------
#
# Added 2026-08-19. The defect this gate exists for: a script asserted an incident mechanism
# sourced only from an internal harvest file's paraphrase, never checked against the primary,
# and shipped four factual errors into a draft that was one step from paid render. The field
# being *present* is what a deterministic check can prove; whether the log is honest is a
# human/model call. Same division of labour as ``visual_template`` — mandating the field is
# the lever, because the failure mode is silence, not a wrong number.


def _write_script(
    content_root: Path, profile: str, body: str, name: str = "2026-08-18-t.md"
) -> Path:
    scripts_dir = content_root / profile / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    path = scripts_dir / name
    path.write_text(body, encoding="utf-8")
    return path


_ITEM = [
    {
        "id": "ci-x",
        "pillar": "content creation",
        "story_id": "s",
        "platform": "linkedin",
        "format": "reel",
        "status": "approved",
    }
]

_LOG = "\n## Verification log\n\n| claim | source | quote | verdict |\n"


def test_script_check_passes_on_fully_verified_script(setup):
    tmp_path = setup
    _make_profile_tree(tmp_path, "example")
    content_root = _make_content_tree(tmp_path, "example", _ITEM)
    _write_script(
        content_root,
        "example",
        "source_item: ci-x\nclaims_verified: 7/7 · log: see below\n\n# T\n" + _LOG,
    )

    result = cq.script_check("example", "ci-x", content_root=content_root)

    assert result["proceed"] is True
    assert result["blocking"] == []
    assert result["checks"]["claims_verified_present"] is True
    assert result["checks"]["verification_log_present"] is True


def test_script_check_blocks_when_claims_verified_absent(setup):
    """The regression that motivated the gate: a script with no claims_verified at all."""
    tmp_path = setup
    _make_profile_tree(tmp_path, "example")
    content_root = _make_content_tree(tmp_path, "example", _ITEM)
    _write_script(content_root, "example", "source_item: ci-x\npart_a_score: 13/14\n\n# T\n" + _LOG)

    result = cq.script_check("example", "ci-x", content_root=content_root)

    assert result["proceed"] is False
    assert any("claims_verified" in b for b in result["blocking"])
    assert result["checks"]["claims_verified_present"] is False


def test_script_check_blocks_malformed_claims_verified(setup):
    tmp_path = setup
    _make_profile_tree(tmp_path, "example")
    content_root = _make_content_tree(tmp_path, "example", _ITEM)
    _write_script(
        content_root, "example", "source_item: ci-x\nclaims_verified: yes\n\n# T\n" + _LOG
    )

    result = cq.script_check("example", "ci-x", content_root=content_root)

    assert result["proceed"] is False
    assert result["checks"]["claims_verified_parsed"] is False


def test_script_check_blocks_claims_without_verification_log(setup):
    """A count with no log is an unfalsifiable assertion, which is the thing being prevented."""
    tmp_path = setup
    _make_profile_tree(tmp_path, "example")
    content_root = _make_content_tree(tmp_path, "example", _ITEM)
    _write_script(
        content_root, "example", "source_item: ci-x\nclaims_verified: 7/7 · log: see below\n\n# T\n"
    )

    result = cq.script_check("example", "ci-x", content_root=content_root)

    assert result["proceed"] is False
    assert any("Verification log" in b for b in result["blocking"])
    assert result["checks"]["verification_log_present"] is False


def test_script_check_warns_but_allows_marked_shortfall(setup):
    """Shipping a *marked* unverified claim is an audited choice; silence about it is not."""
    tmp_path = setup
    _make_profile_tree(tmp_path, "example")
    content_root = _make_content_tree(tmp_path, "example", _ITEM)
    _write_script(
        content_root,
        "example",
        "source_item: ci-x\nclaims_verified: 5/7 · log: see below\n\n# T\n" + _LOG,
    )

    result = cq.script_check("example", "ci-x", content_root=content_root)

    assert result["proceed"] is True
    assert any("2 of 7" in w for w in result["warnings"])


def test_script_check_blocks_verified_exceeding_total(setup):
    tmp_path = setup
    _make_profile_tree(tmp_path, "example")
    content_root = _make_content_tree(tmp_path, "example", _ITEM)
    _write_script(
        content_root,
        "example",
        "source_item: ci-x\nclaims_verified: 9/7 · log: see below\n\n# T\n" + _LOG,
    )

    result = cq.script_check("example", "ci-x", content_root=content_root)

    assert result["proceed"] is False


def test_script_check_zero_claims_needs_a_stated_reason(setup):
    tmp_path = setup
    _make_profile_tree(tmp_path, "example")
    content_root = _make_content_tree(tmp_path, "example", _ITEM)

    bare = _write_script(
        content_root, "example", "source_item: ci-x\nclaims_verified: 0/0\n\n# T\n"
    )
    assert cq.script_check("example", "ci-x", content_root=content_root)["warnings"]

    bare.write_text(
        "source_item: ci-x\nclaims_verified: 0/0 · log: none: no external claims\n\n# T\n",
        encoding="utf-8",
    )
    result = cq.script_check("example", "ci-x", content_root=content_root)
    assert result["proceed"] is True
    assert result["warnings"] == []


def test_script_check_matches_on_source_item_not_filename(setup):
    """Filenames are <date>-<slug>, carrying no item id — globbing for the id finds nothing."""
    tmp_path = setup
    _make_profile_tree(tmp_path, "example")
    content_root = _make_content_tree(tmp_path, "example", _ITEM)
    _write_script(
        content_root,
        "example",
        "source_item: ci-other\nclaims_verified: 1/1 · log: see below\n\n# T\n" + _LOG,
        name="2026-08-18-decoy.md",
    )
    _write_script(
        content_root,
        "example",
        "source_item: ci-x\nclaims_verified: 3/3 · log: see below\n\n# T\n" + _LOG,
        name="2026-08-19-real.md",
    )

    result = cq.script_check("example", "ci-x", content_root=content_root)

    assert result["proceed"] is True
    assert result["checks"]["script_found"] is True


def test_script_check_blocks_when_no_script_exists(setup):
    tmp_path = setup
    _make_profile_tree(tmp_path, "example")
    content_root = _make_content_tree(tmp_path, "example", _ITEM)

    result = cq.script_check("example", "ci-x", content_root=content_root)

    assert result["proceed"] is False
    assert result["checks"]["script_found"] is False
