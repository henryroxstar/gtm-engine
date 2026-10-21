"""Onboarding must not let model-derived text choose a filesystem path.

`render()` builds file keys like f"products/{product['slug']}/PRODUCT.md" from a slug the
BRAIN returned, and `stage()` writes `staging_root / rel_path` for every key. Neither
guarded the segment: `_safe_segment` was applied to the profile slug only. A product slug
of "../../../etc" therefore escaped the staging root — the highest-severity class of error
in this repo's threat model (CLAUDE.md: the tenant boundary).

Two layers, because they fail differently: render() normalises the slug (so the path is
well-formed), stage() confines the resolved destination (so a key from anywhere else, or a
symlink, still cannot escape).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

onboard_render = pytest.importorskip("agent.onboard.render")
onboard_staging = pytest.importorskip("agent.onboard.staging")

from tests.agent.test_onboard import _VALID_DRAFT  # noqa: E402


def _render(draft):
    return onboard_render.render(draft)


# ── characterisation: the fix must not silently move anybody's files ──────────
# A slug normaliser applied to an ALREADY-VALID slug must be a no-op. If it is not, every
# existing product silently acquires a second folder on the next onboarding run — this
# repo's known duplicate-slug failure mode. Pin the exact key set first.

# The knowledge/ segment these two once carried was dropped deliberately (2026-09-21): it put the
# files one level below where resolve_knowledge_file() reads a product override, so they were
# written and never read. The pin's JOB is unchanged — it still catches the slug normaliser moving
# anybody's files — only the layout it pins was corrected.
EXPECTED_PRODUCT_KEYS = {
    "products/acme-deploy/PRODUCT.md",
    "products/acme-deploy/icp-personas.md",
    "products/acme-deploy/market-scan-config.md",
}


def test_a_valid_product_slug_still_renders_to_exactly_the_same_paths():
    files = _render(_VALID_DRAFT)
    product_keys = {k for k in files if k.startswith("products/")}
    assert product_keys == EXPECTED_PRODUCT_KEYS


# ── render(): a model-derived slug is normalised ──────────────────────────────

TRAVERSAL_SLUGS = [
    "../../../etc/passwd",
    "../sibling",
    "/absolute/path",
    "a/b",
    "..",
    ".",
    "$HOME",
    "%TEMP%",
    "with space",
    "UPPER",
]


@pytest.mark.parametrize("bad", TRAVERSAL_SLUGS)
def test_a_model_supplied_product_slug_cannot_shape_the_path(bad):
    draft = {**_VALID_DRAFT, "products": [{**_VALID_DRAFT["products"][0], "slug": bad}]}
    try:
        files = _render(draft)
    except ValueError:
        return  # refusing outright is an acceptable outcome for an unusable slug
    for key in files:
        assert ".." not in key.split("/"), f"{bad!r} produced a traversing key {key!r}"
        assert not key.startswith("/"), f"{bad!r} produced an absolute key {key!r}"
        if key.startswith("products/"):
            segment = key.split("/")[1]
            assert segment not in ("..", ".", ""), f"{bad!r} produced segment {segment!r}"
            assert " " not in segment, f"{bad!r} left a space in {segment!r}"


# ── stage(): the resolved destination is confined ─────────────────────────────


def _cfg(tmp_path):
    from types import SimpleNamespace

    return SimpleNamespace(profiles_root=tmp_path / "profiles", content_root=tmp_path / "content")


@pytest.mark.parametrize(
    "rel",
    ["../escaped.md", "../../escaped.md", "a/../../escaped.md", "/tmp/escaped.md"],
)
def test_stage_refuses_a_relative_path_that_escapes_the_staging_root(tmp_path, rel):
    """render() is not the only caller shape — stage() takes whatever dict it is handed,
    so it confines independently rather than trusting its input."""
    cfg = _cfg(tmp_path)
    with pytest.raises(Exception) as exc:  # noqa: PT011 — ConfinementError or ValueError
        onboard_staging.stage("acme", {rel: "pwned"}, cfg)
    assert "escaped.md" not in {p.name for p in tmp_path.rglob("*")}, "the write happened anyway"
    assert exc.value is not None


def test_stage_refuses_a_symlink_that_points_out_of_the_staging_root(tmp_path):
    """A lexical check cannot catch this — only resolving the path can."""
    cfg = _cfg(tmp_path)
    staging_root = cfg.profiles_root / ".staging" / "acme"
    staging_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (staging_root / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(Exception):  # noqa: B017, PT011
        onboard_staging.stage("acme", {"link/escaped.md": "pwned"}, cfg)
    assert not (outside / "escaped.md").exists(), "wrote through the symlink"


def test_stage_still_writes_ordinary_files(tmp_path):
    """Positive control (§R12): confinement that refuses everything is not confinement."""
    cfg = _cfg(tmp_path)
    draft_id, root = onboard_staging.stage(
        "acme", {"PROFILE.md": "hello", "products/acme-deploy/PRODUCT.md": "x"}, cfg
    )
    assert (root / "PROFILE.md").read_text(encoding="utf-8") == "hello"
    assert (root / "products" / "acme-deploy" / "PRODUCT.md").read_text(encoding="utf-8") == "x"
    assert draft_id
