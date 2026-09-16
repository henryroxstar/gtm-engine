"""Deterministic, brand-styled UI-mockup frame sequences for the ``screen`` shot role.

THIRD (and last) MODULE IN gtm_core/ PERMITTED TO IMPORT PILLOW. ``captions.py`` carries the
original exception for text rasterization; ``cover_frame.py`` widened it (Phase E) for frame
scoring. This is a further deliberate widening, same shape as both — pixel-level image work stays
confined to named, reviewed modules rather than scattered across the ffmpeg-orchestration layer
(``video_finish.py``) or, worse, into a skill body as prose describing pixels nobody checks.
``tests/media/test_captions_module_boundary.py`` enforces the exact three-module allowlist; adding
a fourth needs the same reasoning here, not just a passing test.

WHY THIS EXISTS AT ALL
-----------------------
``gtm_core.shots_lint._lint_no_in_frame_text`` refuses a generation prompt that asks a video model
to render legible text in frame: diffusion video models paint shapes that *resemble* text rather
than typesetting it, which is where garbled on-screen UI came from before this module existed. But
some shots ARE the payload of on-screen text — a request inspector panel with a blank field
labelled "acting agent" is the argument the shot exists to make, and describing it in prose to a
generation model produces exactly the garbled result the lint bans. The lint's own fix is named in
its message: "let gtm_core.captions burn it in at finish time" — this module is that fix's sibling
for a `screen` shot that is a UI, not a caption. Every pixel is drawn deterministically from code,
so it is checkable before it ships, the same property that makes a caption safe to burn where a
generated video is not.

DELIBERATELY NOT ANIMATED VIA A GENERATION MODEL. A UI mockup with legible text drawn frame-by-
frame from code cannot drift, hallucinate a word, or fail to typeset — cost is zero, and the result
is pixel-identical on a second run given the same inputs. That determinism is the point: this is
the STATIC-LOOKING failure `shots_lint`'s V9 warns about turned into something that genuinely
isn't static — a cursor moves, a value changes state, a field pulses — using the same easing curve
every time rather than a random flourish that would make two renders of the "same" shot differ.

CONTENT BOUNDARY. These mockups are GENERIC illustrations for a narrative beat — a gym booking
app, a request inspector — never a real product's screen. Reusing an actual tenant product
screenshot (profiles/<tenant>/knowledge/brand/product-screenshots/) for a shot illustrating a
generic authorization failure would misrepresent our own product as carrying that flaw; the
INDEX.md there is explicit that those illustrate *our proposed solution*, never a hypothetical
failure case. Brand color/type still comes from the kit so the shot matches the rest of the video.
"""

from __future__ import annotations

# Part of the pre-split import surface: callers read `screen_ui.SAFE_AREAS`.
from ..video_lint import SAFE_AREAS  # noqa: F401
from . import fit  # noqa: F401 — `screen_ui.fit._FIT_LOG` is the collector's one home

# Eager, complete re-export of the pre-split module surface (PRD §5 rule 1):
# every submodule is imported here, so module-level registrations run on
# `import <package>` exactly as they did on `import <module>`.
from .base import SceneError  # noqa: F401
from .cli import audit_fit, main  # noqa: F401
from .draw import (  # noqa: F401
    _BACKDROP_MAX_DELTA,
    _FOOT_BAND_TOP_FRAC,
    _LABEL_TRACKING,
    CAPTION_BAND_TOP_FRAC,
    _arrive,
    _arrow_head,
    _backdrop,
    _dashed_rect,
    _draw_cursor,
    _ease_in_out,
    _ease_named,
    _ease_out,
    _entrance,
    _fit_font,
    _font,
    _glow,
    _join_dot,
    _lerp,
    _lerp_color,
    _panel,
    _text_band,
    _text_tracked,
    _text_w,
    _tracked_w,
    _window,
)
from .fit import (  # noqa: F401
    _CITATION_MIN_H_FRAC,
    _PAYLOAD_MIN_H_FRAC,
    _SECONDARY_MIN_H_FRAC,
    _fit_or_refuse,
    _fit_tracked,
    _fit_trial,
    fit_tolerance,
)
from .frames import _frame_count, _write_frame_stream, _write_frames  # noqa: F401
from .palette import (  # noqa: F401
    _hex_to_rgb,
    _load_logo_asset,
    _load_palette,
    _logo_background,
    _Palette,
    _paste_logo,
    _prepare_logo,
    _resolve_area,
)
from .registry import _SCENE_EXTRAS, _SCENES  # noqa: F401
from .scenes.call_ui import (  # noqa: F401
    _CALL_BANK,
    _CALL_CLINIC,
    _CALL_HOTEL,
    _CALL_TELCO,
    _CALL_UI_MAX_RIGHT_FRAC,
    _CallUiSpec,
    render_call_ui_frames,
)
from .scenes.caller_record import (  # noqa: F401
    _AGENT_IDENTITY,
    _BANK,
    _CLEAN,
    _CLINIC,
    _HOTEL,
    _TELCO,
    _RecordSpec,
    render_caller_record_frames,
)
from .scenes.checkpoint_flow import render_checkpoint_flow_frames  # noqa: F401
from .scenes.checkpoint_timing import (  # noqa: F401
    _CHECKPOINT_CUES,
    _CHECKPOINT_DEFAULT_TIMING,
    _CHECKPOINT_DEFAULT_TIMING_SOURCE,
    SCENE_CUES,
    _resolve_timing,
)
from .scenes.class_booking import render_class_booking_frames  # noqa: F401
from .scenes.compare_rows import render_compare_rows_frames  # noqa: F401
from .scenes.compare_rows_specs import (  # noqa: F401
    _MAX_PANEL_ROWS,
    _ROWS_CLAIM,
    _ROWS_EVIDENCE,
    _ROWS_IDENTITY,
    _ROWS_LAYERS,
    _ROWS_SCOPE,
    _Panel,
    _Row,
    _RowsSpec,
)
from .scenes.message_card import (  # noqa: F401
    _MAX_BODY_LINES,
    _MESSAGE_DEFAULTS,
    render_message_card_frames,
)
from .scenes.phone_walkthrough import render_phone_walkthrough_frames  # noqa: F401
from .scenes.record_grid import _CALLS, render_record_grid_frames  # noqa: F401
from .scenes.request_inspector import render_request_inspector_frames  # noqa: F401
from .scenes.still_push import render_still_push_frames  # noqa: F401
from .scenes.title_card import (  # noqa: F401
    _SPINE_ONE,
    _SPINE_TWO,
    _TITLE_CLAIM,
    _TITLE_CLOSE,
    _apply_title_override,
    _TitleSpec,
    _wrap_px,
    render_title_card_frames,
)

__all__ = [
    "SceneError",
    "render_caller_record_frames",
    "render_record_grid_frames",
    "render_message_card_frames",
    "render_title_card_frames",
    "render_checkpoint_flow_frames",
    "render_still_push_frames",
    "render_phone_walkthrough_frames",
    "render_class_booking_frames",
    "render_request_inspector_frames",
]
