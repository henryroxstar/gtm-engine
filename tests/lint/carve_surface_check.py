#!/usr/bin/env python3
"""Lint: the export's cheap sweeps, run at commit time over exactly what the carve ships.

Two rules, both borrowed from `scripts/oss-export.sh` §6 rather than reimplemented:

* **withheld-doc / unresolved-doc** — nothing shipped may cite a withheld design doc
  (`docs/prds/`, `docs/archive/` — never allowlistable), and nothing shipped may cite ANY
  other `docs/*.md` path that will not actually exist in the carve (the export's own
  `DOC_REF_ALLOW`, read from the script, is the one exemption — a path the shipped code
  already handles being absent). Both are the export's §2 doc-reference-integrity gate,
  and both are the wrong moment to find out: they fire on export day, after every citation
  has landed. On 2026-08-28 the withheld class found 197; six days later, 23 more — every
  new module and test written against a PRD had cited its PRD, as good private practice
  says to. On 2026-09-03 the broader class caught a fixture literal that merely LOOKED like
  a doc path. Both classes regrow at the rate the repo writes design notes and test data.
* **tenant-marker** — none of the tenant narrative markers the export sweeps for (`MARKERS=`
  in the script — product names, taglines, a CRM prefix) may appear on the shipped surface.
  Same list, same case-sensitivity, one improvement: matching runs over text with line
  breaks and comment prefixes collapsed, so a product name wrapped across two comment lines
  (`Agent` / `#:      Gateway`, the 2026-09-03 finding) is caught here even though the
  export's line-oriented grep cannot see it.

What this does NOT do: keep a second copy of "what ships". Every earlier attempt at a
private-tree lint of the carve was rejected because it would restate the allowlist, the rsync
excludes, the stub list and the overlay set — four things that drift. This module READS them:
the shipped surface is parsed out of `scripts/oss-export.sh` at run time (the same way
`tests/lint/test_oss_export_sweeps.py` reads the token list), the stub list comes from
`gtm_core.gating`, and the overlay set is the `oss/overlays/` tree itself. Restructure the
script and this lint fails loudly on the missing anchor rather than silently scanning less.

Fix for a hit: reword the sentence so it stands on its own — keep the reasoning, drop the path
or the name. Never an allowlist here: a withheld design doc can never be a legitimate pointer
in a public file, and a tenant's product name can never be a legitimate literal in engine code.

Public cut: the export script is absent (only `bootstrap.sh` ships), so this exits 0 with a
note — the same detector `tests/conftest.py` uses for `private_tree`.

Usage: `python3 tests/lint/carve_surface_check.py [repo-root]` (stdlib for the surface;
`gtm_core.gating` for the stub list).
"""

from __future__ import annotations

import fnmatch
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT_REL = Path("scripts") / "oss-export.sh"

#: Any docs/*.md-shaped path — mirrors the export's own §2 grep verbatim. Bare directory
#: mentions ("the withheld design-note tree under docs/prds/") are deliberately NOT matched
#: — the sentence stands on its own without a file the reader lacks.
DOC_REF = re.compile(r"docs/[A-Za-z0-9/_.-]+\.md")
#: Always a hit, never allowlistable — same rule the export's strict sweep enforces first.
_WITHHELD_PREFIXES = ("docs/prds/", "docs/archive/")

_ARRAY = r"^{name}=\((.*?)^\)"
#: A line break plus whatever comment prefix opens the next line. Collapsing it to one space
#: is what lets a marker wrapped across comment lines match as the phrase it is.
_LINE_JOIN = re.compile(r"[ \t]*\n[ \t]*(?:#:?|//|\*|--)?[ \t]*")
#: Files the export's own marker sweep skips (`SWEEP_EXCLUDES`): the maintainer's handle in
#: CODEOWNERS is published on purpose.
_MARKER_SKIP_NAMES = frozenset({"CODEOWNERS"})


@dataclass(frozen=True)
class Surface:
    """What the carve copies, as parsed from the export script."""

    dirs: tuple[str, ...]
    files: tuple[str, ...]
    exclude_globs: tuple[str, ...]  # rsync --exclude basenames, applied anywhere under `dirs`
    overlay_root: str  # files here REPLACE the same relative path in the carve
    markers: str  # the export's MARKERS regex, verbatim
    stubbed_skill_dirs: tuple[str, ...] = field(default_factory=tuple)
    #: (citing-file, doc-path) pairs the export exempts — DOC_REF_ALLOW, verbatim.
    doc_ref_allow: frozenset[tuple[str, str]] = field(default_factory=frozenset)


def _array(script: str, name: str) -> list[str]:
    m = re.search(_ARRAY.format(name=name), script, re.M | re.S)
    if not m:
        raise SystemExit(
            f"✗ {SCRIPT_REL}: could not find the {name}=( … ) array — re-anchor this lint"
        )
    body = re.sub(r"#.*", "", m.group(1))
    return body.split()


def _one(script: str, pattern: str, what: str) -> list[str]:
    found = re.findall(pattern, script, re.M)
    if not found:
        raise SystemExit(f"✗ {SCRIPT_REL}: could not find {what} — re-anchor this lint")
    return found


def _quoted_array(script: str, name: str) -> list[str]:
    """Like ``_array``, for a bash array of double-quoted string literals."""
    m = re.search(_ARRAY.format(name=name), script, re.M | re.S)
    if not m:
        raise SystemExit(
            f"✗ {SCRIPT_REL}: could not find the {name}=( … ) array — re-anchor this lint"
        )
    body = re.sub(r"#.*", "", m.group(1))
    return re.findall(r'"([^"]+)"', body)


def parse_surface(root: Path, *, stubbed: Iterable[str] = ()) -> Surface:
    """Derive the shipped surface from the export script — never restate it."""
    script = (root / SCRIPT_REL).read_text(encoding="utf-8")
    dirs = list(_array(script, "ALLOW_DIRS"))
    dirs += _one(script, r'"\$ROOT/(profiles/[A-Za-z0-9_-]+)/"', "the profiles/_template rsync")
    dirs += _one(script, r'rsync -a[^\n]*"\$ROOT/(oss/github)/"', "the oss/github rsync")
    files = list(_array(script, "ALLOW_FILES"))
    files += [f"docs/{d}" for d in _array(script, "SHIPPING_DOCS")]
    files += _one(script, r'"\$ROOT/(scripts/[A-Za-z0-9_.-]+\.sh)"', "the bootstrap.sh copy")
    files += _one(script, r'"\$ROOT/(\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml)"', "the CI copies")
    gov = _one(
        script, r'^for f in ([^;]+); do\s*\n\s*cp "\$ROOT/oss/\$f"', "the governance copy loop"
    )
    files += [f"oss/{name}" for name in gov[0].split()]
    (overlay_root,) = _one(
        script, r'rsync -a[^\n]*"\$ROOT/(oss/overlays)/" "\$DEST/"', "the overlay rsync"
    )
    (markers,) = _one(script, r"^MARKERS='([^'\n]+)'$", "the MARKERS regex")
    excludes = tuple(dict.fromkeys(re.findall(r"--exclude='([^']+)'", script)))
    doc_ref_allow = frozenset(
        tuple(pair.split(":", 1)) for pair in _quoted_array(script, "DOC_REF_ALLOW")
    )
    return Surface(
        dirs=tuple(dict.fromkeys(dirs)),
        files=tuple(dict.fromkeys(files)),
        exclude_globs=excludes,
        overlay_root=overlay_root,
        markers=markers,
        stubbed_skill_dirs=tuple(f"plugin/skills/{name}" for name in sorted(stubbed)),
        doc_ref_allow=doc_ref_allow,
    )


def _excluded(rel: Path, globs: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(part, g) for part in rel.parts for g in globs)


def shipped_files(root: Path, surface: Surface) -> list[Path]:
    """Every file the carve would copy, as repo-relative paths, after excludes/stubs/overlays."""
    overlay = root / surface.overlay_root
    overlaid = {p.relative_to(overlay).as_posix() for p in overlay.rglob("*") if p.is_file()}
    out: list[Path] = []
    for d in surface.dirs:
        base = root / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file() or ".git" in p.parts:
                continue
            rel = p.relative_to(root)
            if _excluded(rel, surface.exclude_globs):
                continue
            if any(rel.as_posix().startswith(s + "/") for s in surface.stubbed_skill_dirs):
                continue  # stub-carve wipes the whole directory; only a generated SKILL.md ships
            if rel.as_posix() in overlaid:
                continue  # the overlay replaces this file; the overlay itself is scanned below
            out.append(rel)
    for f in surface.files:
        if (root / f).is_file():
            out.append(Path(f))
    out += sorted(p.relative_to(root) for p in overlay.rglob("*") if p.is_file())
    return out


def collapsed(text: str) -> tuple[str, list[int]]:
    """``text`` with line breaks (and the next line's comment prefix) folded to one space,
    plus a per-character map back to the 1-based source line."""
    lines = text.splitlines()
    buf: list[str] = []
    origin: list[int] = []
    for lineno, line in enumerate(lines, 1):
        seg = line if lineno == 1 else _LINE_JOIN.sub(" ", "\n" + line)
        buf.append(seg)
        origin.extend([lineno] * len(seg))
    return "".join(buf), origin


def marker_hits(text: str, markers: re.Pattern[str]) -> list[tuple[int, str]]:
    """``(line, matched text)`` for every marker in ``text``, wrapped or not."""
    flat, origin = collapsed(text)
    return [(origin[m.start()], m.group(0)) for m in markers.finditer(flat)]


def _resolvable_docs(root: Path, surface: Surface) -> frozenset[str]:
    """docs/*.md paths that exist in the carve with no SHIPPING_DOCS/ALLOW_FILES entry:
    an overlay ships at its path MINUS the oss/overlays/ prefix (the overlay tree itself is
    the source of truth, already read by ``shipped_files``), and ``docs/knowledge-usage.md``
    is regenerated fresh by the export's own post-overlay step every run — read from
    ``gtm_core.knowledge_usage`` so this cannot drift from what actually generates it."""
    overlay = root / surface.overlay_root
    extra = {p.relative_to(overlay).as_posix() for p in overlay.rglob("*") if p.is_file()}
    from gtm_core.knowledge_usage import usage_doc_path

    extra.add(usage_doc_path(root).relative_to(root).as_posix())
    return frozenset(extra)


def scan(root: Path, *, stubbed: Iterable[str] = ()) -> list[str]:
    """``rule path:line: <text>`` for every finding on the shipped surface."""
    surface = parse_surface(root, stubbed=stubbed)
    markers = re.compile(surface.markers)
    resolvable = frozenset(surface.files) | _resolvable_docs(root, surface)
    hits: list[str] = []
    for rel in shipped_files(root, surface):
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, ValueError):
            continue  # binary (fonts, PNGs, the onboarding PDF) — the export's -a sweep covers those
        for lineno, line in enumerate(text.splitlines(), 1):
            for m in DOC_REF.finditer(line):
                doc = m.group(0)
                if doc.startswith(_WITHHELD_PREFIXES):
                    hits.append(f"withheld-doc {rel.as_posix()}:{lineno}: {doc}")
                elif doc not in resolvable and (rel.as_posix(), doc) not in surface.doc_ref_allow:
                    hits.append(f"unresolved-doc {rel.as_posix()}:{lineno}: {doc}")
        if rel.name not in _MARKER_SKIP_NAMES:
            for lineno, found in marker_hits(text, markers):
                hits.append(f"tenant-marker {rel.as_posix()}:{lineno}: {found}")
    return hits


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(args[0]).resolve() if args else REPO
    if not (root / SCRIPT_REL).is_file():
        print("✓ carve-surface lint: not the private tree (no export script) — nothing to check")
        return 0
    from gtm_core.gating import stub_list  # the one non-stdlib import; the policy owns the stub set

    hits = scan(root, stubbed=stub_list())
    if hits:
        print("✗ the shipped surface carries what the export refuses:")
        for h in hits:
            print(f"    {h}")
        print(
            "  withheld-doc: reword the sentence to stand on its own — keep the reasoning, drop\n"
            "  the path (docs/prds/ and docs/archive/ never ship).\n"
            "  unresolved-doc: this docs/*.md path will not exist in the carve — reword to\n"
            "  drop the path, ship the doc (both SHIPPING_DOCS arrays), or add it to\n"
            "  DOC_REF_ALLOW in the export script if the shipped code already handles the\n"
            "  file being absent.\n"
            "  tenant-marker: a tenant's product name is profile data, never an engine literal;\n"
            "  pass it in (a scene `--label`, a kit field) or describe the role instead."
        )
        return 1
    print("✓ carve-surface lint: no withheld-doc citation, no tenant marker on the shipped surface")
    return 0


if __name__ == "__main__":
    sys.exit(main())
