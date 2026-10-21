from __future__ import annotations

import re

from .model import Finding, Slide

_FM_KEY = re.compile(r"^[A-Za-z_][\w-]*:")
_NOTES = re.compile(r"<!--(?!\s*lint-ok)(.*?)-->", re.DOTALL)


def _looks_like_frontmatter(block: list[str]) -> bool:
    """A frontmatter block is key: value lines (plus continuations and comments)."""
    meaningful = [ln for ln in block if ln.strip() and not ln.lstrip().startswith("#")]
    if not meaningful:
        return False
    return any(_FM_KEY.match(ln) for ln in meaningful) and all(
        _FM_KEY.match(ln) or ln.startswith((" ", "\t", "-")) for ln in meaningful
    )


def _parse_frontmatter(block: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for ln in block:
        if ln.lstrip().startswith("#") or not ln.strip():
            continue
        if _FM_KEY.match(ln):
            key, _, value = ln.partition(":")
            out[key.strip()] = value.strip()
    return out


def parse_slides(text: str) -> list[Slide]:
    """Split a `slides.md` into slides. Fenced code blocks never contain separators."""
    lines = text.split("\n")
    fences: set[int] = set()
    in_fence = False
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
        if in_fence:
            fences.add(i)

    seps = [i for i, ln in enumerate(lines) if ln.strip() == "---" and i not in fences]

    slides: list[Slide] = []
    cursor = 0
    pending_fm: dict[str, str] = {}
    slide_start = 0

    def flush(end: int) -> None:
        body_lines = lines[slide_start:end]
        raw = "\n".join(body_lines)
        notes = ""
        found = _NOTES.findall(raw)
        if found:
            notes = found[-1].strip()
            raw = _NOTES.sub("", raw)
        slides.append(
            Slide(
                index=len(slides) + 1,
                line=slide_start + 1,
                frontmatter=dict(pending_fm),
                body=raw.strip(),
                notes=notes,
            )
        )

    i = 0
    while i < len(seps):
        sep = seps[i]
        # Does a frontmatter block open here (this separator and the next one bracket it)?
        if i + 1 < len(seps):
            block = lines[sep + 1 : seps[i + 1]]
            if _looks_like_frontmatter(block):
                if slides or cursor:  # not the headmatter — close the previous slide first
                    flush(sep)
                pending_fm = _parse_frontmatter(block)
                slide_start = seps[i + 1] + 1
                cursor = slide_start
                i += 2
                continue
        # A bare separator: end of slide.
        if cursor:
            flush(sep)
            pending_fm = {}
            slide_start = sep + 1
            cursor = slide_start
        i += 1

    if cursor and slide_start < len(lines):
        flush(len(lines))
    return slides


def headmatter(text: str) -> dict[str, str]:
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return _parse_frontmatter(lines[1:i])
    return {}


def _slide_suppressions(slides: list[Slide], finding: Finding) -> set[str]:
    for slide in slides:
        if slide.index == finding.slide:
            return slide.suppressed
    return set()
