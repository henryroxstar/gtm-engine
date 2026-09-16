from __future__ import annotations

from functools import partial

from .scenes.call_ui import (
    _CALL_BANK,
    _CALL_CLINIC,
    _CALL_HOTEL,
    _CALL_TELCO,
    render_call_ui_frames,
)
from .scenes.caller_record import (
    _AGENT_IDENTITY,
    _BANK,
    _CLEAN,
    _CLINIC,
    _HOTEL,
    _TELCO,
    render_caller_record_frames,
)
from .scenes.caller_row import (
    _ROW_BANK,
    _ROW_CLINIC,
    _ROW_HOTEL,
    _ROW_TELCO,
    render_caller_row_frames,
)
from .scenes.chat_bubble import render_chat_bubble_frames
from .scenes.checkpoint_flow import render_checkpoint_flow_frames
from .scenes.class_booking import render_class_booking_frames
from .scenes.compare_rows import render_compare_rows_frames
from .scenes.compare_rows_specs import (
    _ROWS_CLAIM,
    _ROWS_EVIDENCE,
    _ROWS_IDENTITY,
    _ROWS_LAYERS,
    _ROWS_SCOPE,
)
from .scenes.hero_reveal import render_hero_reveal_frames
from .scenes.message_card import render_message_card_frames
from .scenes.phone_walkthrough import render_phone_walkthrough_frames
from .scenes.record_grid import render_record_grid_frames
from .scenes.request_inspector import render_request_inspector_frames
from .scenes.still_push import render_still_push_frames
from .scenes.title_card import _TITLE_CLAIM, _TITLE_CLOSE, render_title_card_frames

# ── CLI ─────────────────────────────────────────────────────────────────────────────────────────

#: Each ``record-*`` entry is the same scene bound to one call's content. Registering them as
#: separate scene NAMES rather than adding a ``--variant`` flag keeps the CLI shape unchanged and
#: makes the whole set visible in ``--help``, which is where a caller actually looks.
_SCENES = {
    "class-booking": render_class_booking_frames,
    "request-inspector": render_request_inspector_frames,
    "record-bank": partial(render_caller_record_frames, spec=_BANK),
    "record-clinic": partial(render_caller_record_frames, spec=_CLINIC),
    "record-hotel": partial(render_caller_record_frames, spec=_HOTEL),
    "record-telco": partial(render_caller_record_frames, spec=_TELCO),
    "record-clean": partial(render_caller_record_frames, spec=_CLEAN),
    "record-agent-identity": partial(render_caller_record_frames, spec=_AGENT_IDENTITY),
    "record-grid": render_record_grid_frames,
    "message-card": render_message_card_frames,
    "chat-bubble": render_chat_bubble_frames,
    "title-claim": partial(render_title_card_frames, spec=_TITLE_CLAIM),
    "title-close": partial(render_title_card_frames, spec=_TITLE_CLOSE),
    "rows-claim": partial(render_compare_rows_frames, spec=_ROWS_CLAIM),
    "rows-identity": partial(render_compare_rows_frames, spec=_ROWS_IDENTITY),
    "rows-scope": partial(render_compare_rows_frames, spec=_ROWS_SCOPE),
    "rows-evidence": partial(render_compare_rows_frames, spec=_ROWS_EVIDENCE),
    "rows-layers": partial(render_compare_rows_frames, spec=_ROWS_LAYERS),
    "checkpoint-flow": render_checkpoint_flow_frames,
    "still-push": render_still_push_frames,
    "phone-walkthrough": render_phone_walkthrough_frames,
    "hero-reveal": render_hero_reveal_frames,
    "call-ui-bank": partial(render_call_ui_frames, spec=_CALL_BANK),
    "call-ui-clinic": partial(render_call_ui_frames, spec=_CALL_CLINIC),
    "call-ui-hotel": partial(render_call_ui_frames, spec=_CALL_HOTEL),
    "call-ui-telco": partial(render_call_ui_frames, spec=_CALL_TELCO),
    "caller-row-bank": partial(render_caller_row_frames, spec=_ROW_BANK),
    "caller-row-clinic": partial(render_caller_row_frames, spec=_ROW_CLINIC),
    "caller-row-hotel": partial(render_caller_row_frames, spec=_ROW_HOTEL),
    "caller-row-telco": partial(render_caller_row_frames, spec=_ROW_TELCO),
}


#: Which extra kwargs each scene accepts on the CLI. Declared once so a flag meant for one scene
#: can never be quietly forwarded to another — the property the old `if scene == "still-push"`
#: branch had for exactly one flag.
_SCENE_EXTRAS: dict[str, frozenset[str]] = {
    "still-push": frozenset({"image", "crop_frac"}),
    # A product's own screens in one phone: screen 0 is --image, each --still is the next screen a
    # `swap` can slide to, and --actions is the timed list. Declared here so a walkthrough's action
    # file can never reach a scene that draws something else.
    "phone-walkthrough": frozenset({"image", "stills", "actions"}),
    # One pre-composited still and its timed enter/exit/highlight list — no `stills`, since this
    # scene never swaps between screens the way phone-walkthrough does.
    "hero-reveal": frozenset({"image", "actions"}),
    "checkpoint-flow": frozenset({"timing", "labels"}),
    # ONLY the film's own statement cards, per the "logo compositing" section above
    # `_load_palette` — never a `record-*`/`call-ui-*` scene, which illustrates fictional
    # third-party software. Extend this set deliberately, not by widening the default.
    "title-claim": frozenset({"logo", "title"}),
    "title-close": frozenset({"logo", "title"}),
    # The card's three strings are FILM content, not engine content — see the module comment in
    # scenes/message_card.py. Declared here so a --message meant for this card can never reach a
    # scene that illustrates a fictional third party's software.
    "message-card": frozenset({"message"}),
    # A bubble's words are the film's own (what a character types or reads) — the same boundary
    # as `message-card`, and the same reason the flag is declared for this one scene only.
    "chat-bubble": frozenset({"bubble"}),
    **{
        f"caller-row-{sector}": frozenset({"stills", "speak_from_s"})
        for sector in ("bank", "clinic", "hotel", "telco")
    },
}
