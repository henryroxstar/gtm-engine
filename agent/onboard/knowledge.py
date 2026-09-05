from __future__ import annotations

from datetime import date
from pathlib import Path

# ── render ────────────────────────────────────────────────────────────────────


def _source_label(draft: dict) -> str:
    """Provenance line for onboarded knowledge frontmatter — the real onboarding source when we
    have one (the crawled URL or the uploaded file), else ``manual`` for pasted text / stubs."""
    src = draft.get("source") or {}
    stype, value = src.get("type"), (src.get("value") or "").strip()
    if stype == "url" and value:
        return value
    if stype == "file" and value:
        return Path(value).name
    return "manual"


def _knowledge_topic_relpath(relpath: str) -> str | None:
    """The topic path of a rendered file as the freshness scanner will see it, or None when the
    file is not a managed topic at all (e.g. ``PROFILE.md``).

    Two managed roots exist (``knowledge_meta.MANAGED_ROOTS``). A file under any ``knowledge/`` dir
    is classified relative to the nearest one, so a per-product knowledge file gets the same rule as
    a profile-level one. Anything else under ``products/`` — ``products/<slug>/PRODUCT.md`` above
    all — is a managed topic in its own right and keeps its full prefixed path, which is exactly the
    relpath ``check`` reports it under. Before ``products/`` joined the scan (2026-08-29) this
    returned None for those, so a freshly onboarded profile shipped an unstamped PRODUCT.md and
    failed ``knowledge_meta_check`` — the very gap this helper exists to close."""
    parts = relpath.split("/")
    if "knowledge" in parts:
        last = len(parts) - 1 - parts[::-1].index("knowledge")
        return "/".join(parts[last + 1 :])
    if parts[0] == "products" and len(parts) > 1:
        return relpath
    return None


def _ensure_knowledge_frontmatter(files: dict[str, str], draft: dict, today: date) -> None:
    """Prepend lifecycle frontmatter (source/refreshed/review) to every managed knowledge topic in
    ``files`` that lacks it — in place, keys unchanged. Reuses the shipped ``knowledge_meta`` helpers so
    the output matches ``knowledge_meta seed`` + the parser by construction, so a freshly onboarded
    profile passes ``knowledge_meta_check`` with no manual seed. Idempotent: files that already
    carry frontmatter (e.g. the ``_template`` starters) are left byte-for-byte."""
    from gtm_core.knowledge_meta import (
        default_review,
        is_managed_topic,
        parse_frontmatter,
        upsert_frontmatter,
    )

    source = _source_label(draft)
    for relpath, content in list(files.items()):
        topic = _knowledge_topic_relpath(relpath)
        if topic is None or not is_managed_topic(topic):
            continue
        if parse_frontmatter(content)[0]:  # already stamped — never clobber
            continue
        files[relpath] = upsert_frontmatter(
            content,
            {"source": source, "refreshed": today.isoformat(), "review": default_review(topic)},
        )


def _supplement_from_template(files: dict[str, str], template_knowledge_dir: Path) -> None:
    """Add the ``_template`` knowledge starters that render() doesn't derive from the draft, so a
    new profile isn't missing files the skills expect (draft-outreach, content-plan, prospect, …
    would otherwise silently run on defaults). Copies each starter verbatim (it carries its own
    frontmatter) and never overwrites a draft-derived file. No-op when the template dir is absent
    (e.g. isolated test roots), so it only enriches a real profiles/ tree."""
    from gtm_core.knowledge_meta import EXCLUDED_DIRS

    if not template_knowledge_dir.is_dir():
        return
    for path in sorted(template_knowledge_dir.rglob("*")):
        # Text knowledge topics only — never the binary brand assets or raw source briefs
        # (those are company-specific, not inherited from the skeleton).
        if not path.is_file() or path.suffix not in (".md", ".txt"):
            continue
        rel = path.relative_to(template_knowledge_dir)
        if any(part in EXCLUDED_DIRS for part in rel.parts[:-1]):
            continue
        files.setdefault("knowledge/" + rel.as_posix(), path.read_text(encoding="utf-8"))
