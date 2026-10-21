"""Structured input for HTML library scenes (PRD 2026-10-19 R7).

The string-copy props channel in ``html_backend`` carries scalars only and escapes just ``</``.
A scene whose input is a diagram, a list or a nested object needs two more things, both here so
the backend stays small:

* a **data channel** — one ``<script type="application/json">`` element carrying JSON, with
  ``<``, ``>``, ``&`` and the two JS line separators written as ``\\uXXXX`` escapes. Nothing in the
  payload can then end the element, open a comment or add a tag, whatever a label contains. The
  scene's script reads it with ``JSON.parse``; values never become script text or selectors.
* **mounts** — trusted markup (engine output, never user markup) placed inside an empty element
  of the template, after every other substitution has run so none of them can touch it.

``check_prepared`` then lints the PREPARED page, which the raw-template lint cannot see, and
applies the tenant's ``[design_system]`` denied fonts and hexes to it (PRD R14).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .base import SceneError
from .html_lint import HtmlLintError, lint_composition

DATA_SCRIPT_ID = "gtm-scene-data"


class ScenePrepareError(SceneError):
    """A scene's input could not be turned into a page; the message names what to fix."""


@dataclass(frozen=True)
class ScenePayload:
    """What a scene's ``prepare`` hook hands the backend."""

    mounts: dict[str, str] = field(default_factory=dict)  # element id -> trusted markup
    data: dict[str, Any] | None = None  # JSON-able; becomes the data channel


def data_script(data: Any, script_id: str = DATA_SCRIPT_ID) -> str:
    """``<script type="application/json" id=...>`` holding ``data``, safe against any content."""
    body = json.dumps(data, separators=(",", ":"), ensure_ascii=True)
    for ch, esc in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026")):
        body = body.replace(ch, esc)
    body = body.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return f'<script type="application/json" id="{script_id}">{body}</script>\n'


def apply(text: str, payload: ScenePayload) -> str:
    """Insert the payload's mounts and data channel into an already-prepared page."""
    for mount_id, markup in payload.mounts.items():
        pattern = re.compile(
            rf"(<div\b[^>]*\bid=[\"']{re.escape(mount_id)}[\"'][^>]*>)(\s*)(</div>)"
        )
        if len(pattern.findall(text)) != 1:
            raise ScenePrepareError(
                f'the scene template needs exactly one empty <div id="{mount_id}"></div> mount'
            )
        text = pattern.sub(
            lambda m, markup=markup: f"{m.group(1)}{markup}{m.group(3)}", text, count=1
        )
    if payload.data is not None:
        script = data_script(payload.data)
        if "</head>" in text:
            text = text.replace("</head>", f"{script}</head>", 1)
        else:
            text = text.replace("<body>", f"<body>\n{script}", 1)
    return text


# ── prepared-page checks ─────────────────────────────────────────────────────────────────────

_TOKENS_STYLE = re.compile(r'<style id="gtm-brand-tokens">.*?</style>', re.DOTALL)
_SHARED_STYLE = re.compile(r"<style>/\* (?:tokens|animations)\.css \*/.*?</style>", re.DOTALL)
_SHARED_SCRIPT = re.compile(r"<script>/\* motion\.js \*/.*?</script>", re.DOTALL)
_DATA_SCRIPT = re.compile(r'<script type="application/json" id="[^"]+">.*?</script>', re.DOTALL)
_SVG_BLOCK = re.compile(r"<svg\b.*?</svg>", re.DOTALL)


def _mask_svg_text(text: str) -> str:
    """Empty every text node inside an ``<svg>``: labels are data the engine has escaped, and a
    label such as ``PR #1234`` or ``see http://x`` is not a style choice the lint should judge."""
    return _SVG_BLOCK.sub(lambda m: re.sub(r">[^<]*<", "><", m.group(0)), text)


def lint_view(prepared: str, *, keep_tokens: bool = False) -> str:
    """The part of a prepared page the scene is responsible for.

    Engine-owned blocks are left out: the shared stylesheets and ``motion.js`` (the raw-template
    lint never sees them either; they carry neutral palette hexes), the JSON data element
    (inert by type), and — for the lint — the brand token block, whose whole job is to state the
    tenant's colours as hex. ``keep_tokens`` keeps that block for the design-system scan.
    """
    view = _SHARED_STYLE.sub("", prepared)
    view = _SHARED_SCRIPT.sub("", view)
    view = _DATA_SCRIPT.sub("", view)
    if not keep_tokens:
        view = _TOKENS_STYLE.sub("", view)
    return _mask_svg_text(view)


def check_prepared(prepared: str, *, kit: dict, ratio: str, fps: int, duration_s: float) -> None:
    """Lint the prepared page and apply the tenant's denied fonts/hexes to it (R14)."""
    try:
        lint_composition(
            lint_view(prepared),
            kit=kit,
            ratio=ratio,
            fps=fps,
            duration_s=duration_s,
            is_library_scene=True,
        )
    except HtmlLintError as exc:
        raise ScenePrepareError(f"Pre-render lint failed on the prepared page: {exc}") from exc

    from ..brandkit import design_system_denied, scan_denied

    fonts, hexes = design_system_denied(kit)
    hits = scan_denied(lint_view(prepared, keep_tokens=True), fonts, hexes)
    if hits:
        named = ", ".join(f"{kind} {token}" for kind, token in hits)
        raise ScenePrepareError(
            f"this brand's design system forbids {named}, and the prepared scene uses it. "
            "Change the brand kit, or pick a scene that does not draw it."
        )
