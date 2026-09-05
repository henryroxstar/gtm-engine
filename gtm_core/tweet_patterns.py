"""X tweet-pattern catalog — a closed, validated vocabulary of post structures.

The single source of truth is :data:`REGISTRY_FILENAME` (``tweet_patterns.toml``), in the same
non-secret co-located-config shape :mod:`gtm_core.models` and :mod:`gtm_core.render_engines` use.
``docs/x-tweet-patterns.md`` is *generated* from this file (:func:`render`/:func:`generate`), the
same two-surface shape :mod:`gtm_core.knowledge_usage` uses for ``docs/knowledge-usage.md``:
a committed markdown artifact plus a ``check()`` drift gate.

Why a registry instead of prose in a skill body
------------------------------------------------
``content-studio`` already drafts X threads/singles end to end, and the repo already names the
*first line* of a post (``docs/hook-craft.md``, 10 archetypes). What was missing was a name for the
*shape* of the rest of the post — so a drafter reached for the same handful of structures every
time, and there was no way to tell which structure a post used after the fact. ``pattern_id`` on a
hook's opening beat (:mod:`gtm_core.hooks`) makes that choice a checkable, measurable property
instead of an unrecorded judgment call — the same fix :mod:`gtm_core.hook_coverage` applied to
persona coverage in outbound email.

The taxonomy (pattern **names** only — every template, transposition rule, and example below is
original) is triaged from two third-party ebooks: "101 Ways to Write a Tweet" and "Even More Ways
to Write a Tweet" (The Art of Purpose, 2021), read in full 2026-08-23. No book text is reproduced.
Only the ~50 patterns that survive this repo's brand-safety rule (``docs/hook-craft.md``: the enemy
is a belief or a default, never a named company; no outrage, no manufactured controversy) and claim
discipline (``docs/prose-craft.md``: concrete-anchor rule, no bare absolutes) are cataloged.

Note on the field name: ``hook_score.py``'s third scoring component is *also* called "pattern" (the
deterministic lint-score) — a persisted, unrelated concept. This module's vocabulary is exposed as
``pattern_id`` everywhere else in the codebase specifically to avoid that collision.

Stdlib-only (mirrors ``gtm_core.models``/``gtm_core.render_engines`` — no new dependency).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "TweetPatternError",
    "PatternSpec",
    "load_registry",
    "pattern_ids",
    "get",
    "render",
    "generate",
    "check",
]

REGISTRY_FILENAME = "tweet_patterns.toml"
ENV_OVERRIDE = "GTM_TWEET_PATTERNS_REGISTRY"

#: A pattern's `formats` must be a non-empty subset of this set. Beat-level, not hook-level —
#: see gtm_core.hooks.OpeningBeat.pattern_id.
VALID_FORMATS: frozenset[str] = frozenset({"single", "thread"})

#: docs/virality-engineering.md T1-T6. A pattern's `trigger` list is a subset; may be empty.
VALID_TRIGGERS: frozenset[str] = frozenset({"T1", "T2", "T3", "T4", "T5", "T6"})

#: docs/hook-craft.md's 10 archetype slugs, plus five structural classes that have no hook-craft
#: analogue (a hook-craft archetype names an OPENING LINE; these name a post SHAPE that doesn't
#: reduce to one).
HOOK_CRAFT_ARCHETYPES: frozenset[str] = frozenset(
    {
        "shipped-artifact",
        "counterintuitive-decision",
        "named-number",
        "receipts-first",
        "status-quo-fault-line",
        "before-after",
        "the-concession",
        "deep-cut-insider",
        "the-stakes",
        "reveal-the-ending",
    }
)
CATALOG_LOCAL_ARCHETYPES: frozenset[str] = frozenset(
    {"list-structure", "story-lesson", "frame-phrase", "question-post", "wordplay"}
)
VALID_ARCHETYPES: frozenset[str] = HOOK_CRAFT_ARCHETYPES | CATALOG_LOCAL_ARCHETYPES

VALID_FITS: frozenset[str] = frozenset({"core", "conditional"})

#: Mirrors gtm_core.hooks_lint._MAX_TEXT_BEAT_CHARS — a `single` post has one tweet's budget.
MAX_SINGLE_EXAMPLE_CHARS = 280

_REQUIRED_KEYS = frozenset(
    {
        "id",
        "name",
        "source",
        "formats",
        "archetype",
        "trigger",
        "fit",
        "template",
        "transposition",
        "example",
    }
)
_OPTIONAL_KEYS = frozenset({"guardrail"})
_KNOWN_KEYS = _REQUIRED_KEYS | _OPTIONAL_KEYS

#: Mirrors gtm_core.hooks_lint._ID_RE — same kebab-case shape, defined locally to avoid a
#: hooks_lint -> tweet_patterns -> hooks_lint import cycle (hooks_lint imports this module).
_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class TweetPatternError(ValueError):
    """The registry is unusable, or malformed. Fail-closed: never silently drop a bad row."""


@dataclass(frozen=True)
class PatternSpec:
    """One validated row from ``tweet_patterns.toml``."""

    id: str
    name: str
    source: str
    formats: tuple[str, ...]
    archetype: str
    trigger: tuple[str, ...]
    fit: str
    template: str
    transposition: str
    example: str
    guardrail: str = ""


def _registry_path(registry_path: Path | None = None) -> Path:
    if registry_path is not None:
        return registry_path
    override = os.getenv(ENV_OVERRIDE)
    if override:
        return Path(override)
    return Path(__file__).resolve().parent / REGISTRY_FILENAME


# Cache keyed by resolved path string. Production has one stable path; tests inject distinct tmp
# paths, so this needs no explicit invalidation — a different path is simply a different key.
_CACHE: dict[str, dict[str, PatternSpec]] = {}


def _validate_row(index: int, raw: dict) -> PatternSpec:
    prefix = f"pattern[{index}]"
    if not isinstance(raw, dict):
        raise TweetPatternError(f"{prefix} must be a table")

    unknown = set(raw) - _KNOWN_KEYS
    if unknown:
        raise TweetPatternError(f"{prefix} has unknown key(s): {sorted(unknown)}")
    missing = _REQUIRED_KEYS - set(raw)
    if missing:
        raise TweetPatternError(f"{prefix} is missing required field(s): {sorted(missing)}")

    pattern_id = raw["id"]
    if not isinstance(pattern_id, str) or not pattern_id:
        raise TweetPatternError(f"{prefix} id must be a non-empty string")
    if not _ID_RE.match(pattern_id):
        raise TweetPatternError(f"{prefix} id {pattern_id!r} must be kebab-case (a-z0-9-)")

    formats_raw = raw["formats"]
    if not isinstance(formats_raw, list) or not formats_raw:
        raise TweetPatternError(f"pattern {pattern_id!r}: formats must be a non-empty list")
    formats = tuple(str(f) for f in formats_raw)
    bad_formats = set(formats) - VALID_FORMATS
    if bad_formats:
        raise TweetPatternError(
            f"pattern {pattern_id!r}: unknown format(s) {sorted(bad_formats)}; "
            f"valid: {sorted(VALID_FORMATS)}"
        )

    trigger_raw = raw["trigger"]
    if not isinstance(trigger_raw, list):
        raise TweetPatternError(f"pattern {pattern_id!r}: trigger must be a list (may be empty)")
    trigger = tuple(str(t) for t in trigger_raw)
    bad_triggers = set(trigger) - VALID_TRIGGERS
    if bad_triggers:
        raise TweetPatternError(
            f"pattern {pattern_id!r}: unknown trigger(s) {sorted(bad_triggers)}; "
            f"valid: {sorted(VALID_TRIGGERS)}"
        )

    archetype = str(raw["archetype"])
    if archetype not in VALID_ARCHETYPES:
        raise TweetPatternError(
            f"pattern {pattern_id!r}: unknown archetype {archetype!r}; "
            f"valid: {sorted(VALID_ARCHETYPES)}"
        )

    fit = str(raw["fit"])
    if fit not in VALID_FITS:
        raise TweetPatternError(f"pattern {pattern_id!r}: fit must be one of {sorted(VALID_FITS)}")

    guardrail = str(raw.get("guardrail", ""))
    if fit == "conditional" and not guardrail.strip():
        raise TweetPatternError(
            f"pattern {pattern_id!r}: fit=conditional requires a non-empty guardrail"
        )
    if fit == "core" and guardrail.strip():
        raise TweetPatternError(
            f"pattern {pattern_id!r}: fit=core must not declare a guardrail "
            "(a core pattern is safe as written; a guardrail belongs on a conditional one)"
        )

    example = str(raw["example"])
    if not example.strip():
        raise TweetPatternError(f"pattern {pattern_id!r}: example must not be blank")
    if "single" in formats and len(example) > MAX_SINGLE_EXAMPLE_CHARS:
        raise TweetPatternError(
            f"pattern {pattern_id!r}: example is {len(example)} chars, exceeds "
            f"{MAX_SINGLE_EXAMPLE_CHARS} required for a format=single pattern"
        )

    for field in ("name", "source", "template", "transposition"):
        if not str(raw[field]).strip():
            raise TweetPatternError(f"pattern {pattern_id!r}: {field} must not be blank")

    return PatternSpec(
        id=pattern_id,
        name=str(raw["name"]),
        source=str(raw["source"]),
        formats=formats,
        archetype=archetype,
        trigger=trigger,
        fit=fit,
        template=str(raw["template"]),
        transposition=str(raw["transposition"]),
        example=example,
        guardrail=guardrail,
    )


def load_registry(registry_path: Path | None = None) -> dict[str, PatternSpec]:
    """Load and fail-closed validate ``tweet_patterns.toml``. Cached per resolved path."""
    path = _registry_path(registry_path)
    cache_key = str(path)
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    if not path.is_file():
        raise TweetPatternError(f"tweet-pattern registry not found: {path}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise TweetPatternError(f"tweet-pattern registry is unreadable: {path} ({exc})") from exc

    schema_version = data.get("schema_version")
    if schema_version is not None and schema_version != 1:
        raise TweetPatternError(f"unsupported schema_version {schema_version!r} in {path}")

    rows = data.get("pattern")
    if not isinstance(rows, list) or not rows:
        raise TweetPatternError(f"registry must define at least one [[pattern]] row: {path}")

    registry: dict[str, PatternSpec] = {}
    for index, raw in enumerate(rows):
        spec = _validate_row(index, raw)
        if spec.id in registry:
            raise TweetPatternError(f"duplicate pattern id {spec.id!r} in {path}")
        registry[spec.id] = spec

    _CACHE[cache_key] = registry
    return registry


def pattern_ids(registry_path: Path | None = None) -> frozenset[str]:
    return frozenset(load_registry(registry_path))


def get(pattern_id: str, registry_path: Path | None = None) -> PatternSpec:
    registry = load_registry(registry_path)
    try:
        return registry[pattern_id]
    except KeyError:
        raise TweetPatternError(
            f"unknown pattern_id {pattern_id!r}; known: {sorted(registry)}"
        ) from None


# --- generated markdown -----------------------------------------------------------------------


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def catalog_doc_path(repo_root: Path | None = None) -> Path:
    return (repo_root or _repo_root()) / "docs" / "x-tweet-patterns.md"


_NOT_IN_CATALOG = (
    "Deliberately absent: engagement-bait shapes (follow-trains, giveaway threads, outrage or "
    "shame framed at a person or group), anything reaching for a named competitor as the enemy, "
    "personal-life posts (spouse, co-workers, family), and any pattern whose only mechanism is "
    "speaking in bare absolutes rather than a sourced specific. See "
    "`docs/hook-craft.md`'s brand-safety section and each profile's `voice-bans.txt` for the "
    "standing rule these patterns would otherwise need to repeat."
)


def _pattern_block(spec: PatternSpec) -> list[str]:
    lines = [
        f"### {spec.name} (`{spec.id}`)",
        "",
        f"- **Formats:** {', '.join(spec.formats)}",
        f"- **Archetype:** `{spec.archetype}`",
        f"- **Triggers:** {', '.join(spec.trigger) if spec.trigger else '(none declared)'}",
        f"- **Source:** {spec.source}",
        "",
        "**Template**",
        "```",
        spec.template,
        "```",
        "",
        f"**Transposition.** {spec.transposition}",
        "",
        f"**Example.** {spec.example}",
    ]
    if spec.guardrail:
        lines += ["", f"**Guardrail.** {spec.guardrail}"]
    lines.append("")
    return lines


def render(registry_path: Path | None = None) -> str:
    """Deterministic markdown for ``docs/x-tweet-patterns.md``."""
    registry = load_registry(registry_path)
    ordered = sorted(registry.values(), key=lambda s: (0 if s.fit == "core" else 1, s.id))
    core = [s for s in ordered if s.fit == "core"]
    conditional = [s for s in ordered if s.fit == "conditional"]

    lines = [
        "<!-- GENERATED — DO NOT EDIT. X tweet-pattern catalog.",
        "     Source of truth: gtm_core/tweet_patterns.toml.",
        "     Regenerate: `python -m gtm_core.tweet_patterns generate`.",
        "     CI (tests/skills/test_tweet_patterns.py) fails if this drifts. -->",
        "",
        "# X tweet-pattern catalog",
        "",
        "**Owner of:** the closed vocabulary of X post STRUCTURES (`pattern_id` values) and their "
        "B2B transpositions.",
        "",
        "**Not the owner of:** the opening line (`docs/hook-craft.md`), post-level emotional "
        "engineering (`docs/virality-engineering.md`), sentence-level style "
        "(`docs/prose-craft.md`), or platform mechanics (`docs/x-optimization.md`). This doc picks "
        "the *shape*; those govern everything inside it.",
        "",
        "Pattern **names** are taxonomy drawn from two third-party ebooks ("
        '"101 Ways to Write a Tweet" and "Even More Ways to Write a Tweet", The Art of Purpose, '
        "2021; read in full 2026-08-23). Every template, transposition rule, and example below is "
        "original. No book text is reproduced. `source` cites the book index only.",
        "",
        "## How to choose",
        "",
        "Same discipline as `docs/hook-craft.md`'s Workflow: propose **3 candidates across "
        "distinct patterns**, self-check each against its guardrail (if `conditional`) and the "
        "concrete-anchor rule, then present all 3 as an operator choice. Never reach for a pattern "
        "silently, and never reuse the same one back to back for one hook. Check "
        "`content/<active>/history.jsonl` first.",
        "",
        "## Not in this catalog",
        "",
        _NOT_IN_CATALOG,
        "",
        "## Summary",
        "",
        "| id | name | formats | archetype | triggers | fit |",
        "|---|---|---|---|---|---|",
    ]
    for spec in ordered:
        lines.append(
            f"| `{spec.id}` | {spec.name} | {', '.join(spec.formats)} | `{spec.archetype}` | "
            f"{', '.join(spec.trigger) if spec.trigger else '—'} | {spec.fit} |"
        )
    lines += ["", "## Core patterns", ""]
    for spec in core:
        lines += _pattern_block(spec)
    lines += ["## Conditional patterns", ""]
    for spec in conditional:
        lines += _pattern_block(spec)

    return "\n".join(lines).rstrip("\n") + "\n"


def generate(repo_root: Path | None = None, registry_path: Path | None = None) -> Path:
    target = catalog_doc_path(repo_root)
    target.write_text(render(registry_path), encoding="utf-8")
    return target


def check(repo_root: Path | None = None, registry_path: Path | None = None) -> bool:
    """True if the committed ``docs/x-tweet-patterns.md`` matches a fresh render."""
    target = catalog_doc_path(repo_root)
    fresh = render(registry_path)
    return target.is_file() and target.read_text(encoding="utf-8") == fresh


# --- CLI ---------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m gtm_core.tweet_patterns")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("generate", help="regenerate docs/x-tweet-patterns.md")
    sub.add_parser("check", help="exit 1 if the committed catalog doc has drifted")
    list_p = sub.add_parser("list", help="list pattern ids, optionally filtered")
    list_p.add_argument("--fit", choices=sorted(VALID_FITS), default=None)
    list_p.add_argument("--format", choices=sorted(VALID_FORMATS), default=None)
    show_p = sub.add_parser("show", help="print one pattern's full row")
    show_p.add_argument("id")
    args = parser.parse_args(argv)

    try:
        if args.cmd == "generate":
            print(f"wrote {generate()}")
            return 0

        if args.cmd == "check":
            if check():
                print("✓ catalog: docs/x-tweet-patterns.md in sync")
                return 0
            print(
                "✗ catalog: docs/x-tweet-patterns.md is stale — run: "
                "uv run python -m gtm_core.tweet_patterns generate",
                file=sys.stderr,
            )
            return 1

        if args.cmd == "list":
            registry = load_registry()
            for spec in sorted(registry.values(), key=lambda s: s.id):
                if args.fit and spec.fit != args.fit:
                    continue
                if args.format and args.format not in spec.formats:
                    continue
                print(f"{spec.id}\t{spec.fit}\t{','.join(spec.formats)}\t{spec.name}")
            return 0

        # show
        spec = get(args.id)
        print(f"id: {spec.id}")
        print(f"name: {spec.name}")
        print(f"formats: {', '.join(spec.formats)}")
        print(f"archetype: {spec.archetype}")
        print(f"trigger: {', '.join(spec.trigger) if spec.trigger else '(none)'}")
        print(f"fit: {spec.fit}")
        print(f"source: {spec.source}")
        print(f"template: {spec.template}")
        print(f"transposition: {spec.transposition}")
        print(f"example: {spec.example}")
        if spec.guardrail:
            print(f"guardrail: {spec.guardrail}")
        return 0
    except TweetPatternError as exc:
        print(f"[tweet_patterns] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
