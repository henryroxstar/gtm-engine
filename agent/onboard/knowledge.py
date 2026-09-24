from __future__ import annotations

from datetime import date
from pathlib import Path

#: Template knowledge files a new tenant must NOT inherit, by filename.
#:
#: A module constant rather than a local, so the contract has exactly one home and its test can
#: import it. `BRAND.toml` is here because it ships
#: `[disclosure].line = "<your disclosure line>"` — a placeholder that
#: `agent/publish.py:validate_disclosure` would accept as a configured line, downgrading the
#: hard "no disclosure line is configured" refusal to "the post does not carry" and inviting an
#: operator to paste the placeholder into a synthetic-media post as its EU AI Act Article 50
#: disclosure. A tenant that configured nothing has not opted out of the duty; it fails closed.
#:
#: Keep this set small and keep the reason on the entry. It is a list of things the skeleton
#: knows about and deliberately withholds, not a junk filter — the suffix check in
#: :func:`_supplement_from_template` is where shape-based exclusions belong.
TEMPLATE_KNOWLEDGE_EXCLUDED: frozenset[str] = frozenset({"BRAND.toml"})

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

    Two managed roots exist (``knowledge_meta.MANAGED_ROOTS``). A file under a ``knowledge/`` dir is
    classified relative to the nearest one, which keeps the bare topic vocabulary
    (``industry/cx-ai.md``) every skill and ledger row already uses. Everything under ``products/``
    — ``PRODUCT.md`` and the per-product ``icp-personas.md`` / ``market-scan-config.md`` overrides
    alike, all FLAT under ``products/<slug>/`` because that is the only level
    ``resolve_knowledge_file`` reads — is a managed topic in its own right and keeps its full
    prefixed path, which is exactly the relpath ``check`` reports it under. Before ``products/``
    joined the scan (2026-08-29) this returned None for those, so a freshly onboarded profile
    shipped an unstamped PRODUCT.md and failed ``knowledge_meta_check`` — the very gap this helper
    exists to close."""
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
    # Widening the suffix filter below to `.toml` (2026-09-24) pulled the template's BRAND.toml
    # along with the fact registry's tables, because the filter is by suffix and a brand kit is
    # also TOML. That one file is not inheritable: it ships
    # `[disclosure].line = "<your disclosure line>"`, a PLACEHOLDER, and
    # `agent/publish.py:validate_disclosure` treats any non-empty line as configured. A tenant
    # that inherited it would move from the hard refusal "no disclosure line is configured"
    # (go write one) to "the post does not carry a configured disclosure line" (paste the one
    # you have) — and the obvious thing to paste is the placeholder, which would then ship as a
    # synthetic-media post's EU AI Act Article 50 disclosure. CLAUDE.md: "a tenant that never
    # configured one has not opted out of the duty, it fails closed."
    #
    # An explicit filename rather than a broader rule, because the hazard is specific to this
    # file's contents, not to its shape. tests/agent/test_onboard_disclosure_fails_closed.py
    # pins both halves: that BRAND.toml stays out, and that the registry tables stay in.
    excluded_names = TEMPLATE_KNOWLEDGE_EXCLUDED
    for path in sorted(template_knowledge_dir.rglob("*")):
        # Text knowledge topics only — never the binary brand assets or raw source briefs
        # (those are company-specific, not inherited from the skeleton).
        #
        # `.toml` was missing here until 2026-09-24, so none of the tenant's MACHINE-READABLE
        # knowledge was inherited: `role-vocabulary.toml`, `premise-vocab.toml`, `hooks.toml`,
        # `web-sweep.toml`, `competitors.toml`, and the fact registry's `claims`/`proof`/
        # `angles` tables. The filter's comment aimed at binary assets; a TOML is neither
        # binary nor a raw brief, so it was excluded by a rule never written for it. Measured
        # before the fix: 23 files carried, 0 of them TOML.
        #
        # It matters most for the registry, because `registry.load` is all-or-nothing: an
        # onboarded tenant refused on its first read, naming three files the operator never
        # touched and could not find in their own profile. A TOML carried here is never
        # frontmatter-stamped — `_ensure_knowledge_frontmatter` stamps only `is_managed_topic`
        # paths and that requires `.md` — which is what makes widening safe rather than a way
        # to prepend `---` to a config file.
        if not path.is_file() or path.suffix not in (".md", ".txt", ".toml"):
            continue
        if path.name in excluded_names:
            continue
        rel = path.relative_to(template_knowledge_dir)
        if any(part in EXCLUDED_DIRS for part in rel.parts[:-1]):
            continue
        files.setdefault("knowledge/" + rel.as_posix(), path.read_text(encoding="utf-8"))
