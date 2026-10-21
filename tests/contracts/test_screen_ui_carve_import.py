"""Test that gtm_core.screen_ui imports cleanly even when video_lint is withheld (OSS carve)."""

from __future__ import annotations

import sys
from unittest.mock import patch


def test_screen_ui_imports_without_video_lint():
    """Simulate OSS carve where gtm_core.video_lint does not exist."""
    # Temporarily remove and block gtm_core.video_lint
    modules_to_restore = {}
    for mod in list(sys.modules):
        if (
            mod == "gtm_core.video_lint"
            or mod.startswith("gtm_core.video_lint.")
            or mod == "gtm_core.screen_ui"
            or mod.startswith("gtm_core.screen_ui.")
        ):
            modules_to_restore[mod] = sys.modules.pop(mod)

    try:
        with patch.dict(sys.modules, {"gtm_core.video_lint": None}):
            import gtm_core.screen_ui as screen_ui

            assert hasattr(screen_ui, "SAFE_AREAS")
            assert set(screen_ui.SAFE_AREAS.keys()) == {"9:16", "4:5", "1:1", "16:9"}
            area = screen_ui.SAFE_AREAS["9:16"]
            assert area.width == 1080
            assert area.height == 1920
    finally:
        # Restore original modules
        for mod in list(sys.modules):
            if mod.startswith("gtm_core.screen_ui"):
                sys.modules.pop(mod, None)
        sys.modules.update(modules_to_restore)


def test_html_backend_imports_without_video_lint():
    """html_backend must take SAFE_AREAS from the guarded package copy, not video_lint directly."""
    modules_to_restore = {}
    for mod in list(sys.modules):
        if (
            mod == "gtm_core.video_lint"
            or mod.startswith("gtm_core.video_lint.")
            or mod == "gtm_core.screen_ui"
            or mod.startswith("gtm_core.screen_ui.")
        ):
            modules_to_restore[mod] = sys.modules.pop(mod)

    try:
        with patch.dict(sys.modules, {"gtm_core.video_lint": None}):
            import gtm_core.screen_ui.html_backend as html_backend

            assert set(html_backend.SAFE_AREAS.keys()) == {"9:16", "4:5", "1:1", "16:9"}
    finally:
        for mod in list(sys.modules):
            if mod.startswith("gtm_core.screen_ui"):
                sys.modules.pop(mod, None)
        sys.modules.update(modules_to_restore)
