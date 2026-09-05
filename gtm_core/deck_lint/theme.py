from __future__ import annotations

import re
from pathlib import Path

from .catalog import GUARDED_AMBIENT_CLASSES, GUARDED_GRADIENT_TEXT_CLASSES, PHOTO_BOUND_CLASSES
from .model import ERROR, WARN, Finding

_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_STYLE_TAG = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL)
_KEYFRAMES = re.compile(r"@keyframes\s+([\w-]+)\s*\{")
_FILTER_DECL = re.compile(r"filter\s*:\s*([^;]+);")
_BACKDROP_FILTER_DECL = re.compile(r"backdrop-filter\s*:\s*([^;]+);")
# Any explicit `filter:` value — not just blur() — forces Chromium to keep the element on its
# own compositing layer for print-to-PDF (saturate/contrast/drop-shadow are no exception).
_RAW_FILTER = re.compile(r"(?<!backdrop-)filter\s*:\s*([^;]+);")
_BG_CLIP_TEXT = re.compile(r"background-clip\s*:\s*text", re.IGNORECASE)
_CLASS_IN_SELECTOR = re.compile(r"\.([\w-]+)")


def _theme_css(path: Path) -> str:
    text = _COMMENT.sub("", path.read_text(encoding="utf-8"))
    if path.suffix == ".vue":
        return "\n".join(_STYLE_TAG.findall(text))
    return text


def _brace_blocks(css: str) -> list[tuple[str, str]]:
    """Top-level `selector { body }` blocks. `@media`/`@supports` are unwrapped (their
    contents matter); `@keyframes` is left alone — `_keyframes_blocks` handles those."""
    out: list[tuple[str, str]] = []
    i, n = 0, len(css)
    while i < n:
        brace = css.find("{", i)
        if brace == -1:
            break
        selector = css[i:brace].strip()
        depth, j = 1, brace + 1
        while j < n and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        body = css[brace + 1 : j - 1]
        if selector.startswith("@keyframes"):
            pass
        elif selector.startswith("@"):
            out.extend(_brace_blocks(body))
        else:
            out.append((selector, body))
        i = j
    return out


def _keyframes_blocks(css: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for m in _KEYFRAMES.finditer(css):
        depth, j, n = 1, m.end(), len(css)
        while j < n and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        out.append((m.group(1), css[m.end() : j - 1]))
    return out


def _strip_keyframes(css: str) -> str:
    """Remove every `@keyframes … { … }` block by exact character span (not string
    replacement) — a keyframes body containing `filter: blur(...)` is exactly the content
    the D9 checks below must never see as an ordinary rule, so silent under-stripping here
    would misfire the very check the keyframes fix (animations.css) exists to satisfy."""
    spans: list[tuple[int, int]] = []
    for m in _KEYFRAMES.finditer(css):
        depth, j, n = 1, m.end(), len(css)
        while j < n and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        spans.append((m.start(), j))
    for start, end in reversed(spans):
        css = css[:start] + css[end:]
    return css


def lint_theme(theme_dir: Path) -> list[Finding]:
    out: list[Finding] = []
    overrides = theme_dir / "styles" / "slidev-overrides.css"
    guard_text = overrides.read_text(encoding="utf-8") if overrides.exists() else ""
    if "html.deck-export" not in guard_text or "--card-blur: none" not in guard_text:
        out.append(
            Finding(
                "D9",
                "print guard missing",
                ERROR,
                0,
                str(overrides),
                "styles/slidev-overrides.css must define `html.deck-export { --card-blur: none; }` "
                "— every component's backdrop-filter routes through that token to survive export",
            )
        )

    for path in sorted(theme_dir.rglob("*.vue")) + sorted(theme_dir.rglob("*.css")):
        rel = path.relative_to(theme_dir)
        css = _theme_css(path)
        if not css.strip():
            continue

        for name, body in _keyframes_blocks(css):
            filters = _FILTER_DECL.findall(body)
            if filters and "blur" in filters[-1] and filters[-1].strip() != "none":
                out.append(
                    Finding(
                        "D9",
                        "keyframes ends non-none filter",
                        ERROR,
                        0,
                        f"@keyframes {name} in {rel}",
                        "Chromium keeps a compositing layer alive for any explicit filter value, "
                        "including blur(0) — end the keyframe on `filter: none`, not `blur(0)`",
                    )
                )

        for selector, body in _brace_blocks(_strip_keyframes(css)):
            classes = set(_CLASS_IN_SELECTOR.findall(selector))

            m = _BACKDROP_FILTER_DECL.search(body)
            if m and m.group(1).strip() != "var(--card-blur)":
                out.append(
                    Finding(
                        "D9",
                        "hardcoded backdrop-filter",
                        ERROR,
                        0,
                        f"{selector} in {rel}",
                        "route through var(--card-blur) so html.deck-export can neutralize it — "
                        "a literal blur() value survives export and rasterizes",
                    )
                )

            m = _RAW_FILTER.search(body)
            if (
                m
                and m.group(1).strip() != "none"
                and not (classes & (GUARDED_AMBIENT_CLASSES | PHOTO_BOUND_CLASSES))
            ):
                out.append(
                    Finding(
                        "D9",
                        "unguarded filter",
                        ERROR,
                        0,
                        f"{selector} in {rel}",
                        "no vector PDF equivalent — either hide this element under html.deck-export "
                        "in slidev-overrides.css and add its classname to GUARDED_AMBIENT_CLASSES, add "
                        "it to PHOTO_BOUND_CLASSES if it's applied to an already-rasterized "
                        "background photo, or explain in a comment why this one is safe",
                    )
                )

            if _BG_CLIP_TEXT.search(body) and not (classes & GUARDED_GRADIENT_TEXT_CLASSES):
                out.append(
                    Finding(
                        "D9",
                        "unguarded gradient text",
                        WARN,
                        0,
                        f"{selector} in {rel}",
                        "background-clip:text has no vector PDF equivalent — add the classname to "
                        "the print-mode fallback in slidev-overrides.css (see .gt / .gradient-text), "
                        "unless it's small enough that the raster cost doesn't matter (a badge, not "
                        "a headline)",
                    )
                )
    return out
