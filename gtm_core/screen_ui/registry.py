from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable
from functools import partial
from pathlib import Path
from typing import Any

from .mc_video.scene_bridge import MC_BLOCK_TYPES, render_mc_block_frames
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
_PILLOW_SCENES = {
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

_SCENES = dict(_PILLOW_SCENES)


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

for _btype in MC_BLOCK_TYPES:
    _fn = partial(render_mc_block_frames, _btype)
    _SCENES[_btype] = _fn
    _SCENES[f"mc-{_btype}"] = _fn
    _extras = frozenset(
        {
            "title",
            "line1",
            "line2",
            "line",
            "sub",
            "fields",
            "rules",
            "requests",
            "agents",
            "teams",
            "product",
            "logo",
            "cta",
            "url",
            "signoff",
            "mock",
        }
    )
    _SCENE_EXTRAS[_btype] = _extras
    _SCENE_EXTRAS[f"mc-{_btype}"] = _extras

#: HTML motion scenes -> template folder. A built-in scene maps to a folder NAME under
#: ``gtm_core/screen_ui/html/``; a scene added through :func:`register_html_scene` maps to the
#: absolute ``Path`` of its own template folder, wherever that lives.
_HTML_SCENES: dict[str, str | Path] = {
    "kinetic-type": "kinetic_type",
    "hero-reveal": "hero_reveal",
    "phone-walkthrough": "phone_walkthrough",
}

#: Extras accepted by HTML scenes (including string copy fields).
_HTML_SCENE_EXTRAS: dict[str, frozenset[str]] = {
    "kinetic-type": frozenset({"headline", "subline", "eyebrow", "badge", "kicker"}),
    "hero-reveal": frozenset({"image", "actions", "headline", "subline"}),
    "phone-walkthrough": frozenset({"image", "stills", "actions", "headline", "subline"}),
}

#: Extras a registered scene cannot render without, and the hook that turns its resolved extras
#: into a ``scene_payload.ScenePayload``. Both stay empty for the built-in scenes.
_HTML_SCENE_REQUIRED: dict[str, frozenset[str]] = {}
_HTML_SCENE_PREPARE: dict[str, Callable[..., Any]] = {}

_HTML_DIR = Path(__file__).resolve().parent / "html"
_SHARED_FILES = ("tokens.css", "animations.css", "motion.js")
_SCENE_NAME_RE = re.compile(r"[a-z][a-z0-9-]*")


def register_html_scene(
    name: str,
    template_dir: Path,
    extras: Iterable[str],
    *,
    required: Iterable[str] = (),
    prepare: Callable[..., Any] | None = None,
) -> None:
    """Add an HTML scene whose template folder lives outside ``gtm_core/screen_ui/html/``.

    ``template_dir`` is an absolute folder holding ``index.html``. ``extras`` is the closed set of
    shot keys the scene accepts, ``required`` the subset a shot must carry, and ``prepare`` an
    optional ``(extras, kit, *, content_root, ratio) -> ScenePayload`` hook for scenes whose input is
    structured (see :mod:`gtm_core.screen_ui.scene_payload`). Registering the identical scene
    twice is a no-op, so import order never matters; a different one under a taken name, or a
    name a Pillow scene owns, is refused.
    """
    if not isinstance(name, str) or not _SCENE_NAME_RE.fullmatch(name):
        raise ValueError(f"scene name {name!r} must match [a-z][a-z0-9-]*")
    folder = Path(template_dir)
    if not folder.is_absolute():
        raise ValueError(f"template_dir for {name!r} must be an absolute path, got {folder}")
    if not (folder / "index.html").is_file():
        raise ValueError(f"template_dir for {name!r} has no index.html: {folder}")
    entry = (folder, frozenset(extras), frozenset(required), prepare)
    unknown = entry[2] - entry[1]
    if unknown:
        raise ValueError(f"required extras {sorted(unknown)} of {name!r} are not in its extras")
    if name in _SCENES or (
        name in _HTML_SCENES
        and (
            _HTML_SCENES[name],
            _HTML_SCENE_EXTRAS[name],
            _HTML_SCENE_REQUIRED.get(name, frozenset()),
            _HTML_SCENE_PREPARE.get(name),
        )
        != entry
    ):
        raise ValueError(f"scene {name!r} is already registered")
    _HTML_SCENES[name] = folder
    _HTML_SCENE_EXTRAS[name] = entry[1]
    _HTML_SCENE_REQUIRED[name] = entry[2]
    if prepare is not None:
        _HTML_SCENE_PREPARE[name] = prepare


def html_scene_is_external(scene: str | None) -> bool:
    """True for a scene added by :func:`register_html_scene` (its template is not built in)."""
    return bool(scene) and isinstance(_HTML_SCENES.get(scene), Path)


def html_scene_template(scene: str) -> Path:
    """The ``index.html`` a registered HTML scene renders from (it may not exist: the caller says)."""
    target = _HTML_SCENES[scene]
    folder = target if isinstance(target, Path) else _HTML_DIR / target
    return folder / "index.html"


def html_scene_digest(scene: str | None) -> str | None:
    """sha256 over a library scene's template and the shared motion/token files it inlines.

    Part of the render fingerprint: without it, editing a template leaves the cached frames of
    every shot built from it looking current. ``None`` for a custom composition (its own folder
    hash covers it) or an unknown name.
    """
    if not scene or scene not in _HTML_SCENES:
        return None
    hasher = hashlib.sha256()
    parts = [html_scene_template(scene), *(_HTML_DIR / "_shared" / f for f in _SHARED_FILES)]
    for path in parts:
        hasher.update(path.name.encode("utf-8") + b"\0")
        hasher.update(path.read_bytes() if path.is_file() else b"missing")
        hasher.update(b"\0")
    return hasher.hexdigest()


# Scenes that live in the video craft package. That package is withheld from the public cut, so
# its absence is the normal public state (only a missing ``gtm_core.video_craft`` is tolerated:
# any other import failure inside it is a real bug and must surface).
try:
    from gtm_core.video_craft import scenes as _craft_scenes

    _craft_scenes.register()
except ModuleNotFoundError as _exc:
    if _exc.name != "gtm_core.video_craft":
        raise
