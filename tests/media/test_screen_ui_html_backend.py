"""Unit and integration tests for gtm_core.screen_ui.html_backend."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from gtm_core.screen_ui.html_backend import (
    HtmlBackendError,
    _prepare_wrapper,
    motion_render_lock,
    render_html_scene,
    render_html_shot,
)

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ROOT / "profiles"


@pytest.fixture
def sample_kit() -> dict:
    return {
        "palette": {
            "canvas": "#0B0B0F",
            "surface": "#14141E",
            "primary": "#3B82F6",
            "accent": "#22D3EE",
            "text": "#FFFFFF",
            "text_dim": "#94A3B8",
            "warn": "#F2A93B",
            "good": "#3BD68A",
        },
        "typography": {
            "headline": "Inter, sans-serif",
            "body": "Inter, sans-serif",
        },
    }


def test_prepare_wrapper_injects_brand_tokens_and_extras(sample_kit: dict):
    raw_html = """<!doctype html>
<html>
<head></head>
<body>
  <div id="root" data-no-timeline="true">
    <div id="badge">Default Badge</div>
    <h1 id="headline">Default Headline</h1>
    <p id="subline">Default Subline</p>
  </div>
</body>
</html>"""
    extras = {
        "badge": "Security",
        "headline": "Zero Egress Guaranteed",
        "subline": "Verified by tcpdump sidecar.",
    }
    wrapped = _prepare_wrapper(
        raw_html,
        kit=sample_kit,
        width=1080,
        height=1920,
        fps=30,
        duration_s=2.0,
        extras=extras,
    )
    assert '<style id="gtm-brand-tokens">' in wrapped
    assert "--brand-primary: #3b82f6;" in wrapped
    assert "--shot-width: 1080px;" in wrapped
    assert "--shot-height: 1920px;" in wrapped
    assert 'data-width="1080"' in wrapped
    assert 'data-height="1920"' in wrapped
    assert "Zero Egress Guaranteed" in wrapped
    assert "Security" in wrapped
    assert "Verified by tcpdump sidecar." in wrapped


@pytest.mark.parametrize(
    "ratio,expected_dim",
    [
        ("16:9", (1920, 1080)),
        ("9:16", (1080, 1920)),
        ("1:1", (1080, 1080)),
        ("4:5", (1080, 1350)),
    ],
)
def test_render_html_scene_multi_ratio_mock(
    tmp_path: Path, sample_kit: dict, ratio: str, expected_dim: tuple[int, int]
):
    out_dir = tmp_path / f"out_{ratio.replace(':', '_')}"
    res = render_html_scene(
        "kinetic-type",
        out_dir=out_dir,
        kit=sample_kit,
        ratio=ratio,
        fps=30,
        duration_s=1.0,
        content_root=tmp_path,
        mock_renderer=True,
    )
    assert res["frames"] == 30
    assert res["width"] == expected_dim[0]
    assert res["height"] == expected_dim[1]
    assert res["backend"] == "html"
    assert (out_dir / "frame-0000.png").is_file()
    assert (out_dir / "frame-0029.png").is_file()

    with Image.open(out_dir / "frame-0000.png") as img:
        assert img.size == expected_dim
        assert img.mode == "RGB"


def test_render_html_scene_preview_frame(tmp_path: Path, sample_kit: dict):
    out_dir = tmp_path / "out_preview"
    res = render_html_scene(
        "kinetic-type",
        out_dir=out_dir,
        kit=sample_kit,
        ratio="9:16",
        fps=30,
        duration_s=2.0,
        content_root=tmp_path,
        preview_frame=True,
        mock_renderer=True,
    )
    assert res["frames"] == 60
    assert res["preview_frame"] is not None
    assert res["preview_frame"].name == "frame-0030.png"
    assert res["preview_frame"].is_file()


def test_motion_render_lock_serialization(tmp_path: Path):
    content_root = tmp_path / "content"
    content_root.mkdir(parents=True, exist_ok=True)

    with motion_render_lock(content_root, timeout_s=5.0):
        # A second lock attempt with a very short timeout should fail
        with pytest.raises(HtmlBackendError, match="Timed out"):
            with motion_render_lock(content_root, timeout_s=0.2):
                pass


def test_render_html_shot_writes_build_record(tmp_path: Path, sample_kit: dict):
    out_dir = tmp_path / "shot_out"
    work_dir = tmp_path / "work"

    class DummyShot:
        id = "shot-01"
        scene = "kinetic-type"
        composition = None
        duration_s = 1.0
        extras = {"headline": "Build Record Test"}
        backend = "html"

    shot = DummyShot()
    import os

    os.environ["GTM_MOTION_RENDERER_MOCK"] = "1"
    try:
        res = render_html_shot(
            shot,
            out_dir=out_dir,
            kit=sample_kit,
            ratio="9:16",
            fps=30,
            work_dir=work_dir,
        )
        assert res["frames"] == 30
        record_file = out_dir / "screen-ui-build.json"
        assert record_file.is_file()

        import json

        doc = json.loads(record_file.read_text(encoding="utf-8"))
        assert doc["effective_backend"] == "html"
        assert doc["scene"] == "kinetic-type"
        assert doc["frames"] == 30
        assert "fingerprint" in doc
    finally:
        os.environ.pop("GTM_MOTION_RENDERER_MOCK", None)


def test_t2_cascade_order_and_dom_insertion(sample_kit: dict):
    """T2: <style id="gtm-brand-tokens"> is injected after tokens.css and before </head>."""
    kinetic_path = ROOT / "gtm_core" / "screen_ui" / "html" / "kinetic_type" / "index.html"
    raw_html = kinetic_path.read_text(encoding="utf-8")
    custom_kit = dict(sample_kit, palette=dict(sample_kit["palette"], primary="#EF4444"))

    wrapped = _prepare_wrapper(
        raw_html,
        kit=custom_kit,
        width=1080,
        height=1920,
        fps=30,
        duration_s=5.0,
    )

    tokens_css_pos = wrapped.find("/* tokens.css */")
    brand_tokens_pos = wrapped.find('<style id="gtm-brand-tokens">')
    head_close_pos = wrapped.find("</head>")

    assert tokens_css_pos != -1
    assert brand_tokens_pos != -1
    assert head_close_pos != -1

    # Cascade order: tokens.css appears before <style id="gtm-brand-tokens"> before </head>
    assert tokens_css_pos < brand_tokens_pos < head_close_pos

    # Tenant color wins cascade
    assert "--brand-primary: #ef4444;" in wrapped[brand_tokens_pos:head_close_pos]
    # Fallback appears in inlined tokens.css earlier
    assert "--brand-primary: #3B82F6;" in wrapped[:brand_tokens_pos]


def test_t3_safe_area_pixel_token_injection_and_auto_inference():
    """T3: ratio=None auto-infers from dimensions, injecting safe-area pixel tokens."""
    raw_html = "<html><head></head><body><div id='root'></div></body></html>"
    kit = {"palette": {"primary": "#3B82F6"}}

    wrapped = _prepare_wrapper(
        raw_html,
        kit=kit,
        width=1080,
        height=1920,
        fps=30,
        duration_s=5.0,
        ratio=None,
    )

    assert 'data-ratio="9:16"' in wrapped
    assert "--safe-top: 154px;" in wrapped
    assert "--safe-bottom: 269px;" in wrapped
    assert "--caption-band-top: 1651px;" in wrapped


def test_t4_backward_compatibility_with_partial_kit_fixtures(sample_kit: dict):
    """T4: Partial kit fixtures populate defaults without raising KeyError."""
    raw_html = "<html><head></head><body><div id='root'></div></body></html>"
    wrapped = _prepare_wrapper(
        raw_html,
        kit=sample_kit,
        width=1080,
        height=1920,
        fps=30,
        duration_s=5.0,
    )
    assert '<style id="gtm-brand-tokens">' in wrapped
    assert "--brand-primary: #3b82f6;" in wrapped


def test_t12_frame_dimensions_and_badge_token_binding():
    """T12: _prepare_wrapper stamps frame dimensions; kinetic_type binds badge tokens & safe padding."""
    raw_html = "<html><head></head><body><div id='root'></div></body></html>"
    wrapped = _prepare_wrapper(
        raw_html,
        kit={},
        width=1080,
        height=1920,
        fps=30,
        duration_s=5.0,
    )
    assert "--frame-w: 1080px;" in wrapped
    assert "--frame-h: 1920px;" in wrapped

    kinetic_path = ROOT / "gtm_core" / "screen_ui" / "html" / "kinetic_type" / "index.html"
    content = kinetic_path.read_text(encoding="utf-8")
    assert "background: var(--brand-primary);" in content
    assert "color: var(--brand-text-on-primary, var(--text-on-primary));" in content
    assert "padding-left: max(var(--safe-left, 65px), 8%);" in content
    assert "padding-right: max(var(--safe-right, 65px), 12%);" in content
