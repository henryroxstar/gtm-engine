"""Skill↔knowledge usage map (knowledge-lifecycle PRD, Phase 2).

Scans ``plugin/skills/`` for every knowledge reference and builds the reverse dependency graph —
*which skills read which knowledge topics* — so that when a knowledge file is added or changed you
can see, deterministically, which skills already consume that topic and which topics no skill reads
yet. Answers pain #3 ("many skills rely on the data; hard to track which uses what, and which should
adopt a new source").

Two surfaces, mirroring ``gtm_core.skills.codegen``:

* A **committed, generated** artifact ``docs/knowledge-usage.md`` (``<!-- GENERATED — DO NOT
  EDIT -->``). It is profile-agnostic — skills are de-branded, so the map depends only on
  ``plugin/skills/`` (+ ``profiles/_template/`` as the neutral orphan baseline). ``check()``
  byte-compares a fresh render against the committed file (drift detection), gated in CI + the
  ``usage_index_sync`` lint exactly like the SKILL.md codegen gate.
* An **on-demand** ``coverage`` command that cross-references the skill-referenced topic set against
  ONE profile's actual ``knowledge/`` files — the per-profile half of #3: topics a profile provides
  that no skill uses (candidate dead/unadopted knowledge), and topics skills want that the profile
  lacks (a readiness gap). Not committed — it changes with every knowledge edit and is per-profile.

Stdlib-only. Reuses ``gtm_core.knowledge_meta.is_managed_topic`` for the managed-topic baseline.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from . import knowledge_meta as km

# Reference forms a skill uses (see resolve_knowledge.py + the skill corpus):
#   knowledge/<topic>.md | .txt              — literal file (also the tail of the profiles/ form)
#   profiles/<active>/knowledge/<topic>.md   — full path (contains the knowledge/ form as a substring)
#   resolve_knowledge <topic>                — the CLI form (arg may or may not carry an extension)
#   knowledge/<dir>/...                       — a DIRECTORY reference ("read every file under
#                                               knowledge/guidance/") — credits the whole subtree
_KNOWLEDGE_PATH = re.compile(r"knowledge/([A-Za-z0-9][A-Za-z0-9_/-]*)\.(?:md|txt)\b")
_RESOLVE = re.compile(r"resolve_knowledge(?:\.py)?\s+([A-Za-z0-9][A-Za-z0-9_/.-]*)")
_KNOWLEDGE_DIR = re.compile(r"knowledge/([a-z0-9-]+)/")
_TOPIC_OK = re.compile(r"^[a-z0-9][a-z0-9_/-]*$")

#: A directory reference is recorded as the pseudo-topic ``"<dir>/*"`` in a skill's topic set.
_DIR_SUFFIX = "/*"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_skills_root(repo_root: Path | None = None) -> Path:
    return (repo_root or _repo_root()) / "plugin" / "skills"


def default_template_knowledge(repo_root: Path | None = None) -> Path:
    return (repo_root or _repo_root()) / "profiles" / "_template" / "knowledge"


def usage_doc_path(repo_root: Path | None = None) -> Path:
    return (repo_root or _repo_root()) / "docs" / "knowledge-usage.md"


def _normalize(raw: str) -> str | None:
    """A raw capture → a canonical topic key (extension stripped), or None if it's a placeholder."""
    topic = raw.strip()
    for ext in (".md", ".txt"):
        if topic.endswith(ext):
            topic = topic[: -len(ext)]
    return topic if _TOPIC_OK.match(topic) else None


def _topics_in_text(text: str) -> set[str]:
    topics: set[str] = set()
    for m in _KNOWLEDGE_PATH.finditer(text):
        t = _normalize(m.group(1))
        if t:
            topics.add(t)
    for m in _RESOLVE.finditer(text):
        t = _normalize(m.group(1))
        if t:
            topics.add(t)
    for m in _KNOWLEDGE_DIR.finditer(text):
        d = m.group(1)
        if d not in km.EXCLUDED_DIRS:  # brand/ + source/ aren't knowledge topics
            topics.add(f"{d}{_DIR_SUFFIX}")
    return topics


def scan_skills(skills_root: Path) -> dict[str, set[str]]:
    """{skill_name: {topic, ...}} for every skill dir, scanning its ``.md`` + ``.txt`` files.

    Scans the whole skill directory (SKILL.md, body_template.md, references/**) and unions the
    topics, so a reference in any of them counts and SKILL.md/body overlap dedupes naturally."""
    out: dict[str, set[str]] = {}
    if not skills_root.is_dir():
        return out
    for skill_dir in sorted(p for p in skills_root.iterdir() if p.is_dir()):
        if skill_dir.name == "__pycache__":
            continue
        topics: set[str] = set()
        for f in sorted(skill_dir.rglob("*")):
            if f.suffix in (".md", ".txt") and f.is_file():
                topics |= _topics_in_text(f.read_text(encoding="utf-8", errors="replace"))
        out[skill_dir.name] = topics
    return out


def topic_to_skills(scan: dict[str, set[str]]) -> dict[str, list[str]]:
    """Invert the scan into {topic: [skills, ...]} with sorted skill lists."""
    rev: dict[str, set[str]] = {}
    for skill, topics in scan.items():
        for topic in topics:
            rev.setdefault(topic, set()).add(skill)
    return {topic: sorted(skills) for topic, skills in rev.items()}


def template_managed_topics(template_knowledge_dir: Path) -> list[str]:
    """The managed-topic keys (extension stripped) present in the ``_template`` skeleton — the
    profile-agnostic baseline for orphan detection in the committed doc."""
    topics = []
    for path in km.iter_managed_topics(template_knowledge_dir):
        rel = path.relative_to(template_knowledge_dir).as_posix()
        topics.append(rel[:-3] if rel.endswith(".md") else rel)
    return sorted(topics)


def render(skills_root: Path, template_knowledge_dir: Path) -> str:
    """Deterministic markdown for ``docs/knowledge-usage.md``."""
    scan = scan_skills(skills_root)
    rev = topic_to_skills(scan)
    referenced = set(rev)
    referenced_exact = {t for t in referenced if not t.endswith(_DIR_SUFFIX)}
    baseline = template_managed_topics(template_knowledge_dir)
    orphans = [t for t in baseline if t not in referenced]
    dangling = sorted(referenced_exact - _provided_stems(template_knowledge_dir))
    no_dep = sorted(s for s, topics in scan.items() if not topics)

    lines = [
        "<!-- GENERATED — DO NOT EDIT. The skill↔knowledge dependency map.",
        "     Regenerate: `python -m gtm_core.knowledge_usage generate`.",
        "     CI (tests/skills/test_knowledge_usage.py / usage_index_sync) fails if this drifts. -->",
        "",
        "# Knowledge usage map",
        "",
        "Which skills read which knowledge topics, scanned from `plugin/skills/`. Profile-agnostic "
        "(skills are de-branded), so this depends only on the skill corpus. Use it to see, when you "
        "add or change a knowledge file, which skills already consume that topic — and which topics "
        "no skill reads yet.",
        "",
        f"**{len(scan)} skills · {len(rev)} referenced topics.** Regenerated by "
        "`gtm_core.knowledge_usage`; per-profile coverage (orphans / gaps against a specific "
        "profile's `knowledge/`) is on-demand via `knowledge_usage coverage --profile <p>`.",
        "",
        "## Topic → skills",
        "",
        "| Topic | Skills | # |",
        "|---|---|---|",
    ]
    for topic in sorted(rev):
        skills = rev[topic]
        lines.append(f"| `{topic}` | {', '.join(skills)} | {len(skills)} |")

    lines += ["", "## Skill → topics", "", "| Skill | Topics |", "|---|---|"]
    for skill in sorted(scan):
        topics = sorted(scan[skill])
        cell = ", ".join(f"`{t}`" for t in topics) if topics else "—"
        lines.append(f"| {skill} | {cell} |")

    lines += [
        "",
        "## Orphan topics (in `_template`, referenced by no skill)",
        "",
        "Managed knowledge files present in the profile skeleton that **no skill reads** — either "
        "dead knowledge to retire, or a source not yet wired into any skill. (Per-profile orphans "
        "for the tail topics live behind `coverage --profile <p>`.)",
        "",
    ]
    if orphans:
        lines += [f"- `{t}`" for t in orphans]
    else:
        lines.append("_None — every `_template` managed topic is read by at least one skill._")

    lines += [
        "",
        "## Dangling references (skills read them, `_template` doesn't provide them)",
        "",
        "Topics a skill reads by name for which the profile skeleton has **no file** — a skill that "
        "will silently degrade on any profile that hasn't added the file. Fix by adding the file to "
        "`profiles/_template/knowledge/` (so every profile inherits it) or narrowing the skill.",
        "",
    ]
    if dangling:
        lines += [f"- `{t}`" for t in dangling]
    else:
        lines.append("_None — every topic a skill reads exists in the skeleton._")

    lines += [
        "",
        "## Skills with no knowledge dependency",
        "",
        (", ".join(f"`{s}`" for s in no_dep) if no_dep else "_None._"),
        "",
    ]
    return "\n".join(lines) + "\n"


def generate(repo_root: Path | None = None) -> Path:
    target = usage_doc_path(repo_root)
    target.write_text(
        render(default_skills_root(repo_root), default_template_knowledge(repo_root)),
        encoding="utf-8",
    )
    return target


def check(repo_root: Path | None = None) -> bool:
    """True if the committed ``docs/knowledge-usage.md`` matches a fresh render (in sync)."""
    target = usage_doc_path(repo_root)
    fresh = render(default_skills_root(repo_root), default_template_knowledge(repo_root))
    return target.is_file() and target.read_text(encoding="utf-8") == fresh


# --- per-profile coverage (on-demand; not committed) --------------------------


def _referenced(repo_root: Path | None = None) -> tuple[set[str], set[str]]:
    """(exact topics, referenced directories) across all skills — the directory set is credited to
    every file beneath it (e.g. a skill that reads ``knowledge/guidance/`` consumes every file
    under ``guidance/``)."""
    all_topics: set[str] = set()
    for topics in scan_skills(default_skills_root(repo_root)).values():
        all_topics |= topics
    dirs = {t[: -len(_DIR_SUFFIX)] for t in all_topics if t.endswith(_DIR_SUFFIX)}
    exact = {t for t in all_topics if not t.endswith(_DIR_SUFFIX)}
    return exact, dirs


def _provided_stems(knowledge_dir: Path) -> set[str]:
    """Every knowledge file a profile provides (any extension), keyed by extension-stripped
    relpath — the test for 'does a file for this topic exist at all', managed or not. Excludes the
    refresh SOP, brand/source dirs, and README/INDEX/SOURCES docs (the same non-topics Phase 1
    excludes)."""
    stems: set[str] = set()
    if not knowledge_dir.is_dir():
        return stems
    for f in knowledge_dir.rglob("*"):
        if not f.is_file() or f.suffix not in (".md", ".txt"):
            continue
        rel = f.relative_to(knowledge_dir).as_posix()
        parts = rel.split("/")
        # Only REFRESH.md is a genuine non-topic here — deck-composer.md is excluded from *managed*
        # topics (it's a skill def) but it IS a real file skills reference, so it counts as provided.
        if any(p in km.EXCLUDED_DIRS for p in parts[:-1]) or parts[-1] == "REFRESH.md":
            continue
        if f.stem.upper() in km.EXCLUDED_STEMS:
            continue
        stems.add(rel[: -len(f.suffix)])
    return stems


def coverage(
    profiles_root: Path, profile: str, repo_root: Path | None = None
) -> dict[str, list[str]]:
    """Cross-reference the skill-referenced topic set against ONE profile's knowledge.

    Returns ``{"orphans": [...], "unprovided": [...]}``:
      * ``orphans``    — MANAGED topics the profile provides that no skill reads (by file or via a
        directory reference). Candidate dead knowledge, or a source not yet wired into any skill.
      * ``unprovided`` — topics skills reference by name for which the profile has NO file at all
        (any extension) — a genuine readiness gap.
    """
    exact, dirs = _referenced(repo_root)
    knowledge_dir = profiles_root / profile / "knowledge"

    def consumed(topic: str) -> bool:
        if topic in exact:
            return True
        return "/" in topic and topic.split("/", 1)[0] in dirs

    orphans = []
    for path in km.iter_managed_topics(knowledge_dir):
        rel = path.relative_to(knowledge_dir).as_posix()
        topic = rel[:-3] if rel.endswith(".md") else rel
        if not consumed(topic):
            orphans.append(topic)

    provided = _provided_stems(knowledge_dir)
    return {
        "orphans": sorted(orphans),
        "unprovided": sorted(exact - provided),
    }


# --- CLI ----------------------------------------------------------------------


def _all_profiles(profiles_root: Path) -> list[str]:
    if not profiles_root.is_dir():
        return []
    return sorted(
        c.name for c in profiles_root.iterdir() if c.is_dir() and (c / "PROFILE.md").is_file()
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m gtm_core.knowledge_usage")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("generate", help="regenerate docs/knowledge-usage.md")
    sub.add_parser("check", help="exit 1 if the committed usage map has drifted")
    cov = sub.add_parser("coverage", help="per-profile orphans / gaps vs the skill-referenced set")
    cov.add_argument("--profile", default=None)
    cov.add_argument("--all", action="store_true")
    cov.add_argument("--profiles-root", default=None)
    args = parser.parse_args(argv)

    if args.cmd == "generate":
        print(f"wrote {generate()}")
        return 0

    if args.cmd == "check":
        if check():
            print("✓ usage-index: docs/knowledge-usage.md in sync")
            return 0
        print(
            "✗ usage-index: docs/knowledge-usage.md is stale — run: "
            "uv run python -m gtm_core.knowledge_usage generate",
            file=sys.stderr,
        )
        return 1

    # coverage
    from .paths import PathConfig

    profiles_root = (
        Path(args.profiles_root).expanduser().resolve()
        if args.profiles_root
        else PathConfig.from_env().profiles_root
    )
    profiles = _all_profiles(profiles_root) if args.all else None
    if profiles is None:
        if not args.profile:
            raise SystemExit("[knowledge-usage] pass --profile <slug> or --all")
        profiles = [args.profile]

    for profile in profiles:
        cov_result = coverage(profiles_root, profile)
        print(f"\nprofile: {profile}")
        orphans = cov_result["orphans"]
        unprovided = cov_result["unprovided"]
        print(f"  orphan topics (provided, no skill uses them): {len(orphans)}")
        for t in orphans:
            print(f"    - {t}")
        print(f"  unprovided topics (skills want them, profile lacks them): {len(unprovided)}")
        for t in unprovided:
            print(f"    - {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
