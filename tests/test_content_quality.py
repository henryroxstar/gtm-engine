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


def _make_profile_tree(tmp_path: Path, profile: str, hooks_toml: str | None = None) -> Path:
    """Create a minimal profile tree under tmp_path/profiles/<profile>.

    ``hooks_toml`` writes a real hook bank. Left None, the tree has only ``hook-matrix.md`` and
    ``load_hooks`` falls back to parsing it — the pre-hooks.toml tenant shape.
    """
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
    if hooks_toml is not None:
        (knowledge / "hooks.toml").write_text(hooks_toml, encoding="utf-8")
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
    # Every module that BINDS each helper — content_quality is a package since Phase 3A, and
    # patching the package alias would set a name none of the submodules read.
    for mod in (cq.guards, cq.post, cq.pre, cq.script):
        monkeypatch.setattr(mod, "resolve_profiles_root", lambda: tmp_path / "profiles")
    for mod in (cq.cli, cq.post, cq.pre, cq.register, cq.script):
        monkeypatch.setattr(mod, "resolve_content_root", lambda: tmp_path / "content")

    def person_path():
        return tmp_path / "identity" / "henry" / "hook-matrix.md"

    monkeypatch.setattr(cq.sources, "_person_hook_matrix_path", person_path)
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
    with (
        patch.object(cq.post, "video_probe") as mock_probe,
        patch.object(cq.post, "evaluate") as mock_eval,
    ):
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

    with (
        patch.object(cq.post, "video_probe") as mock_probe,
        patch.object(cq.post, "evaluate") as mock_eval,
    ):
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

    with (
        patch.object(cq.post, "video_probe") as mock_probe,
        patch.object(cq.post, "evaluate") as mock_eval,
    ):
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
    # The hook id is deliberately one that does NOT exist in the repo's real
    # identity/henry/hook-matrix.md. Until 2026-09-03 this test used a real id and the
    # fixture patched `_person_hook_matrix_path` on the package alias — a binding no
    # submodule reads since the Phase 3A split — so it passed by reading the REAL file.
    # A fixture-only id makes an inert mock fail here instead of staying silently green.
    tmp_path = setup
    _make_profile_tree(tmp_path, "example")
    person_dir = tmp_path / "identity" / "henry"
    person_dir.mkdir(parents=True, exist_ok=True)
    (person_dir / "hook-matrix.md").write_text(
        "## Henry\n"
        "| id | Persona | Signal to open on | Hook angle |\n"
        "|---|---|---|---|\n"
        "| henry-fixture-only-hook | Founder | Shipping in public | The real resume is what you ship. |\n",
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
                "hook_id": "henry-fixture-only-hook",
            }
        ],
    )
    result = cq.pre_check("example", "ci-9")
    assert result["proceed"] is True
    assert result["checks"]["hook_id_in_matrix"] is True
    assert cq.hook_id_to_product("henry-fixture-only-hook") == "henry"


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
    # Carries a real bank, hook_id and format so the second half can still assert a wholly
    # silent result — the hook x format gate (2026-09-04) warns on an unattributed script, and
    # a fixture missing a hook_id would drown the claims signal this test is about.
    _make_profile_tree(tmp_path, "example", hooks_toml=_BANK)
    content_root = _make_content_tree(tmp_path, "example", _ITEM)
    front = "source_item: ci-x\nhook_id: example-founder-conversation\nformat: reel\n"

    bare = _write_script(content_root, "example", front + "claims_verified: 0/0\n\n# T\n")
    assert cq.script_check("example", "ci-x", content_root=content_root)["warnings"]

    bare.write_text(
        front + "claims_verified: 0/0 · log: none: no external claims\n\n# T\n",
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


# --- script_check: the hook x format gate ---------------------------------------------------
#
# Added 2026-09-04. ``hook_score`` already refused an undeclared hook x format pair, but
# ``video-script`` caught that refusal as its "Part A-only fallback" and carried on — so the
# composite gate was skipped silently on exactly the scripts that used it. Two shipped acme
# ``format: short`` scripts cited a hook declaring only ``linkedin-text``/``carousel``. The six
# dedicated video graphs never run ``format-router``, so nothing else enforced this on the video
# lane. The remedy is one line in ``hooks.toml``, which is why it blocks rather than warns.

_BANK = """
[[hook]]
id = "example-founder-conversation"
angle = "Your niche is already in conversation."
payoff_promise = "Where the conversation is already happening."
formats = ["linkedin-text", "reel"]

[[hook.opening_beats]]
format = "linkedin-text"
variant = "A"
text = "Your niche is already in conversation."

[[hook.opening_beats]]
format = "reel"
variant = "A"
video = "Your niche is already in conversation. You are not in it yet."
text = "Your niche is already talking."
"""


def _script_with(front: str) -> str:
    return (
        f"source_item: ci-x\n{front}claims_verified: 0/0 · log: none: no external claims\n\n# T\n"
    )


def test_script_check_blocks_when_hook_does_not_declare_the_format(setup):
    tmp_path = setup
    _make_profile_tree(tmp_path, "example", hooks_toml=_BANK)
    content_root = _make_content_tree(tmp_path, "example", _ITEM)
    _write_script(
        content_root,
        "example",
        _script_with("hook_id: example-founder-conversation\nformat: short\n"),
    )

    result = cq.script_check("example", "ci-x", content_root=content_root)

    assert result["proceed"] is False
    assert result["checks"]["hook_declares_format"] is False
    assert any("does not declare format 'short'" in b for b in result["blocking"])
    # The message must carry the remedy, not just the refusal.
    assert any("opening beat" in b for b in result["blocking"])


def test_script_check_passes_when_hook_declares_the_format(setup):
    tmp_path = setup
    _make_profile_tree(tmp_path, "example", hooks_toml=_BANK)
    content_root = _make_content_tree(tmp_path, "example", _ITEM)
    _write_script(
        content_root,
        "example",
        _script_with("hook_id: example-founder-conversation\nformat: reel\n"),
    )

    result = cq.script_check("example", "ci-x", content_root=content_root)

    assert result["proceed"] is True
    assert result["checks"]["hook_declares_format"] is True
    assert result["checks"]["hook_not_fatigued"] is True


def test_script_check_blocks_hook_id_that_is_not_a_bare_id(setup):
    """A shipped script wrote ``hook_id: acme-... (PARTIAL — see note)``.

    A caveat in a join key is a note to a human that no join can read, so it fails loudly
    rather than resolving to nothing.
    """
    tmp_path = setup
    _make_profile_tree(tmp_path, "example", hooks_toml=_BANK)
    content_root = _make_content_tree(tmp_path, "example", _ITEM)
    _write_script(
        content_root,
        "example",
        _script_with("hook_id: example-founder-conversation (PARTIAL — see note)\nformat: reel\n"),
    )

    result = cq.script_check("example", "ci-x", content_root=content_root)

    assert result["proceed"] is False
    assert any("bare kebab-case hook id" in b for b in result["blocking"])


def test_script_check_blocks_hook_id_absent_from_the_bank(setup):
    tmp_path = setup
    _make_profile_tree(tmp_path, "example", hooks_toml=_BANK)
    content_root = _make_content_tree(tmp_path, "example", _ITEM)
    _write_script(content_root, "example", _script_with("hook_id: no-such-hook\nformat: reel\n"))

    result = cq.script_check("example", "ci-x", content_root=content_root)

    assert result["proceed"] is False
    assert any("not in the hook bank" in b for b in result["blocking"])


def test_script_check_warns_but_allows_a_script_with_no_hook_id(setup):
    """An ad-hoc operator script has no plan item behind it.

    Losing attribution is a cost to name in the report, not a reason to refuse the script —
    otherwise the only escape from a blocked format is to delete the hook_id, which trades a
    fixable gap for a permanent one.
    """
    tmp_path = setup
    _make_profile_tree(tmp_path, "example", hooks_toml=_BANK)
    content_root = _make_content_tree(tmp_path, "example", _ITEM)
    _write_script(content_root, "example", _script_with("format: reel\n"))

    result = cq.script_check("example", "ci-x", content_root=content_root)

    assert result["proceed"] is True
    assert result["checks"]["hook_declares_format"] is None
    assert any("no hook_id" in w for w in result["warnings"])


def test_script_check_blocks_a_fatigued_hook(setup):
    """``format-router`` stops on a fatigued hook; the six video graphs never run it."""
    tmp_path = setup
    bank = _BANK.replace(
        'formats = ["linkedin-text", "reel"]',
        'formats = ["linkedin-text", "reel"]\nmax_impressions = 100\nfatigue_window_days = 30',
    )
    _make_profile_tree(tmp_path, "example", hooks_toml=bank)
    content_root = _make_content_tree(tmp_path, "example", _ITEM)
    (content_root / "example" / "outcomes.jsonl").write_text(
        json.dumps(
            {
                "channel": "linkedin",
                "outcome": "impression",
                "ref": "r1",
                "value": 500,
                "ts": "2026-09-03T00:00:00Z",
                "tags": ["hook:example-founder-conversation"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _write_script(
        content_root,
        "example",
        _script_with("hook_id: example-founder-conversation\nformat: reel\n"),
    )

    result = cq.script_check("example", "ci-x", content_root=content_root)

    assert result["proceed"] is False
    assert result["checks"]["hook_not_fatigued"] is False
    assert any("fatigued" in b for b in result["blocking"])


# ── the message-share ceiling on a story script (S6, 2026-09-07) ──────────────────────
#
# 90% of a story's runtime is the story; the message lands in the last tenth, in one sentence.
# The failure this bounds is erosion by increments — no single "just one more proof point" ever
# looks like the one that broke it, which is why it is a ratio in the front block rather than a
# judgement per beat. Warns in both directions, blocks in neither.

_STORY_ITEM = [
    {
        **_ITEM[0],
        "brief": {
            "core_value": "perseverance",
            "opposite": "giving up",
            "protagonist": "a founder who kept the wrong bet running",
        },
    }
]


def _budget(rows: list[tuple[str, str, str]], header: str = "message") -> str:
    """A `## Caption budget` table. Each row is (beat, duration, message-cell)."""
    lines = [
        "",
        "## Caption budget",
        "",
        f"| Beat | Duration | Caption words | w/s | {header} |",
        "|---|---|---|---|---|",
    ]
    lines += [f"| {beat} | {dur} | 8 | 2.9 | {msg} |" for beat, dur, msg in rows]
    return "\n".join(lines) + "\n"


def _story_script(budget: str, front_extra: str = "") -> str:
    return f"source_item: ci-x\nclaims_verified: 0/0 · log: none: no external claims\n{front_extra}\n# T\n{_LOG}{budget}"


def _run(setup_path, item, body):
    _make_profile_tree(setup_path, "example")
    content_root = _make_content_tree(setup_path, "example", item)
    _write_script(content_root, "example", body)
    return cq.script_check("example", "ci-x", content_root=content_root)


def _shares(result):
    return [w for w in result["warnings"] if "message_share" in w]


def test_a_story_script_under_the_ceiling_is_quiet(setup):
    """S6-T1. 5 s of message in 60 s is the shape the rule describes; it must not nag."""
    body = _story_script(_budget([("1", "55", ""), ("9", "5", "yes")]), "message_share: 5/60\n")
    result = _run(setup, _STORY_ITEM, body)
    assert result["proceed"] is True
    assert _shares(result) == [], f"a compliant story script warned: {_shares(result)}"
    assert result["checks"]["message_share_within_ceiling"] is True


def test_a_story_script_over_the_ceiling_warns(setup):
    """S6-T1. 9 s in 60 s is 15% — the message has eaten into the story."""
    body = _story_script(_budget([("1", "51", ""), ("9", "9", "yes")]), "message_share: 9/60\n")
    result = _run(setup, _STORY_ITEM, body)
    assert result["proceed"] is True, "the ceiling warns; it must never block"
    assert any("above the" in w for w in _shares(result)), result["warnings"]


def test_a_story_script_with_no_message_beat_warns_for_the_opposite_reason(setup):
    """S6-T1. 0/60 reads as clean to anyone scanning for a high number. It is the mirror defect.

    A piece that spends its whole runtime on feeling and never says the thing it was made to say
    is story-washing inverted, and no ceiling check would ever catch it.
    """
    body = _story_script(_budget([("1", "60", "")]), "message_share: 0/60\n")
    result = _run(setup, _STORY_ITEM, body)
    assert any("no beat is declared message-bearing" in w for w in _shares(result)), result[
        "warnings"
    ]
    assert result["checks"]["message_share_within_ceiling"] is False


def test_exactly_at_the_ceiling_is_not_over_it(setup):
    """S6-T1. 6/60 is 0.10 exactly. Float arithmetic decides this case by representation error."""
    body = _story_script(_budget([("1", "54", ""), ("9", "6", "yes")]), "message_share: 6/60\n")
    result = _run(setup, _STORY_ITEM, body)
    assert not any("above the" in w for w in _shares(result)), (
        f"6/60 was reported as over a 1/10 ceiling: {_shares(result)}"
    )


def test_quarter_second_durations_sum_exactly(setup):
    """S6-T1. The real tables are in quarter-seconds; 2.5/30 must equal 5/60, not approximate it."""
    body = _story_script(_budget([("1", "27.5", ""), ("9", "2.5", "yes")]), "message_share: 5/60\n")
    result = _run(setup, _STORY_ITEM, body)
    assert not any("sums to" in w for w in _shares(result)), (
        f"2.5/30 and 5/60 compared unequal: {_shares(result)}"
    )


def test_a_non_story_script_is_never_measured(setup):
    """S6-T2. The ceiling is story-scoped: an item with no protagonist is untouched.

    Not merely unwarned — no `checks` key is written at all, because the gate's JSON is a byte-exact
    golden for scripts like this one.
    """
    body = _story_script(
        _budget([("1", "30", "yes"), ("9", "30", "yes")]), "message_share: 60/60\n"
    )
    result = _run(setup, _ITEM, body)
    assert _shares(result) == [], "a non-story script was measured against the story ceiling"
    assert not [k for k in result["checks"] if "message_share" in k], result["checks"]


def test_the_same_table_on_a_story_item_does_warn(setup):
    """S6-T2. Positive control for the scope: the silence above is about the ITEM, not the table."""
    body = _story_script(
        _budget([("1", "30", "yes"), ("9", "30", "yes")]), "message_share: 60/60\n"
    )
    result = _run(setup, _STORY_ITEM, body)
    assert any("above the" in w for w in _shares(result)), result["warnings"]


def test_an_unflagged_product_mention_is_not_counted(setup):
    """S6-T3. Declared, never detected: the ratio counts flags, not words.

    A beat whose caption names the product and carries no flag stays out of the numerator. That is
    a deliberate under-count, and the alternative — a regex over product terms — returns a
    plausible number that is wrong in a direction nobody checks.
    """
    lines = [
        "",
        "## Caption budget",
        "",
        "| Beat | Duration | Caption words | w/s | message |",
        "|---|---|---|---|---|",
        "| 1 | 54 | 8 | 2.9 | |",
        "| 8 | 1 | 6 | 2.0 | |",  # caption names the product; no flag
        "| 9 | 5 | 4 | 0.8 | yes |",
    ]
    body = _story_script("\n".join(lines) + "\n", "message_share: 5/60\n")
    result = _run(setup, _STORY_ITEM, body)
    assert _shares(result) == [], (
        f"an unflagged beat was counted into the message share: {_shares(result)}"
    )


def test_the_gate_contains_no_product_term_detector():
    """S6-T3. Asserted on purpose, so a later author does not add an under-matching one silently."""
    import inspect

    from gtm_core.content_quality import script as script_mod

    source = inspect.getsource(script_mod)
    for smell in ("product_terms", "_PRODUCT_RE", "brand_terms", "cta_words"):
        assert smell not in source, (
            f"{smell!r} appeared in the script gate — message beats are DECLARED on the caption "
            "budget row. A detector under-matches and reports a confident wrong ratio"
        )


def test_the_flag_column_is_actually_read(setup):
    """S6-T3. Positive control for the absence above: the declaration path works."""
    body = _story_script(_budget([("1", "30", ""), ("9", "30", "yes")]), "message_share: 30/60\n")
    result = _run(setup, _STORY_ITEM, body)
    assert any("above the" in w for w in _shares(result)), (
        "a flagged 30/60 beat produced no warning — the parser is not reading the message column, "
        "so the no-detector assertion above proves nothing"
    )


def test_a_malformed_claims_verified_still_blocks_and_is_not_masked(setup):
    """S6-T4. The new warning runs after the claims block, so it cannot mask an existing block."""
    body = (
        f"source_item: ci-x\nclaims_verified: yes\nmessage_share: 30/60\n\n# T\n{_LOG}"
        + _budget([("1", "30", "yes"), ("9", "30", "yes")])
    )
    result = _run(setup, _STORY_ITEM, body)
    assert result["proceed"] is False
    assert result["checks"]["claims_verified_parsed"] is False
    assert not [k for k in result["checks"] if "message_share" in k], (
        "the message-share check ran on a script that had already failed to parse — the early "
        "return is what keeps a warning from arriving beside a block it might soften"
    )


def test_a_story_script_with_no_caption_budget_warns_rather_than_raising(setup):
    """S6-T5. A legacy story script must earn a finding, never a traceback in a quality gate."""
    body = _story_script("", "message_share: 5/60\n")
    result = _run(setup, _STORY_ITEM, body)
    assert result["proceed"] is True
    assert any("cannot be read" in w for w in _shares(result)), result["warnings"]


def test_a_table_without_a_message_column_reads_as_nothing_declared(setup):
    """S6-T5. The pre-S6 table shape is not an error; it is a script that declared no message beat."""
    lines = [
        "",
        "## Caption budget",
        "",
        "| Beat | Duration | Caption words | w/s |",
        "|---|---|---|---|",
        "| 1 | **55s** | 8 | 2.9 |",
        "| 9 | 5 | 4 | 0.8 |",
        "| n/a | — | — | — |",
    ]
    result = _run(setup, _STORY_ITEM, _story_script("\n".join(lines) + "\n"))
    assert any("no beat is declared message-bearing" in w for w in _shares(result)), result[
        "warnings"
    ]
    assert not any("cannot be read" in w for w in _shares(result)), (
        "a bold duration, a trailing unit and an em-dash row must parse — those are the shapes "
        "already written in the tree"
    )


def test_a_front_block_share_that_disagrees_with_the_table_warns(setup):
    """S6-T6. Two numbers for one fact: one of them was edited and the other was not."""
    body = _story_script(_budget([("1", "55", ""), ("9", "5", "yes")]), "message_share: 4/60\n")
    result = _run(setup, _STORY_ITEM, body)
    assert any("sums to" in w for w in _shares(result)), result["warnings"]


def test_a_decimal_front_block_share_compares_equal(setup):
    """S6-T6. Positive control: the disagreement warning is about VALUE, not about spelling."""
    body = _story_script(_budget([("1", "55", ""), ("9", "5", "yes")]), "message_share: 5.0/60.0\n")
    result = _run(setup, _STORY_ITEM, body)
    assert not any("sums to" in w for w in _shares(result)), (
        f"5.0/60.0 was read as disagreeing with 5/60: {_shares(result)}"
    )


def test_a_story_script_missing_the_front_block_line_warns(setup):
    """S6-T6. The line is how a reader sees the shape without recomputing it."""
    body = _story_script(_budget([("1", "55", ""), ("9", "5", "yes")]))
    result = _run(setup, _STORY_ITEM, body)
    assert any("no `message_share:` front-block line" in w for w in _shares(result)), result[
        "warnings"
    ]


# --- K3: caption budget function column vs sibling .shots.json ------------------------------


def test_caption_budget_function_column_matches_shots(setup):
    """K3 case: budget table's function column matches shot list -> passes."""
    import json

    _make_profile_tree(setup, "example")
    content_root = _make_content_tree(setup, "example", _STORY_ITEM)
    shots_data = {
        "source_item": "ci-x",
        "shots": [
            {"n": 1, "duration_s": 55.0, "caption_function": "premise"},
            {"n": 9, "duration_s": 5.0, "caption_function": "message"},
        ],
    }
    shots_file = content_root / "example" / "scripts" / "2026-08-18-t.shots.json"
    shots_file.parent.mkdir(parents=True, exist_ok=True)
    shots_file.write_text(json.dumps(shots_data), encoding="utf-8")

    budget_table = "\n".join(
        [
            "## Caption budget",
            "",
            "| Beat | Duration | Caption words | w/s | function | message |",
            "|---|---|---|---|---|---|",
            "| 1 | 55s | 8 | 2.9 | premise | |",
            "| 9 | 5s | 4 | 0.8 | message | yes |",
        ]
    )
    body = _story_script(budget_table, "message_share: 5/60\n")
    _write_script(content_root, "example", body)
    result = cq.script_check("example", "ci-x", content_root=content_root)
    assert result["proceed"] is True
    assert result["checks"]["caption_functions_consistent"] is True


def test_caption_budget_function_column_disagrees_is_refused(setup):
    """K3 case: budget table's function column disagrees with shot list -> refused."""
    import json

    _make_profile_tree(setup, "example")
    content_root = _make_content_tree(setup, "example", _STORY_ITEM)
    shots_data = {
        "source_item": "ci-x",
        "shots": [
            {"n": 1, "duration_s": 55.0, "caption_function": "premise"},
            {"n": 9, "duration_s": 5.0, "caption_function": "message"},
        ],
    }
    shots_file = content_root / "example" / "scripts" / "2026-08-18-t.shots.json"
    shots_file.parent.mkdir(parents=True, exist_ok=True)
    shots_file.write_text(json.dumps(shots_data), encoding="utf-8")

    budget_table = "\n".join(
        [
            "## Caption budget",
            "",
            "| Beat | Duration | Caption words | w/s | function | message |",
            "|---|---|---|---|---|---|",
            "| 1 | 55s | 8 | 2.9 | describe | |",
            "| 9 | 5s | 4 | 0.8 | message | yes |",
        ]
    )
    body = _story_script(budget_table, "message_share: 5/60\n")
    _write_script(content_root, "example", body)
    result = cq.script_check("example", "ci-x", content_root=content_root)
    assert result["proceed"] is False
    assert result["checks"]["caption_functions_consistent"] is False
    assert any("disagrees with" in b for b in result["blocking"])
