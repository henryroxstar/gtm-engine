"""Withhold a private skill's manifest PROSE from the OSS carve, keeping its manifest.

The distribution counterpart to ``gtm_core.gating.stub_carve``, closing the half that one
cannot reach.

``stub_carve`` wipes ``plugin/skills/<name>/`` — body, references, everything — and writes a
short "hosted product" stub in its place. It deliberately leaves ``gtm_core/skills/<name>.py``
alone, and must: the skill registry, the pack loader and ``docs/SKILLS.md`` all read that
manifest, so a carve without it cannot load the very pack graphs it ships.

That exemption was once described in ``scripts/oss-export.sh`` as "~20 lines of name/tier/
tools, the interface, not the asset". It stopped being true. Design history and dated provider
measurements accumulate in the module DOCSTRING, because that is where they naturally get
written: by 2026-09-07 ``video_render.py``'s docstring had reached ~11 KB across eighteen
``Phase N`` sections, most of it retired-approach post-mortems, and it shipped in full while
the skill's own body was stubbed.

The docstring is the one part of a manifest with **no runtime consumer at all**:
``codegen.render_frontmatter`` reads only the dataclass fields, and ``registry.all_skills()``
does ``getattr(module, "SKILL")``. So it can be replaced with a stub and nothing observes the
difference — which is what makes this safe to do mechanically, where the ``description`` field
(rendered into the stub's own SKILL.md frontmatter, and what an agent reads to decide whether
to invoke the skill) is kept short editorially and held there by
``tests/lint/manifest_prose_check.py`` instead.

Lives in its own module rather than in ``gating.py`` because that file is at its §R10
complexity ceiling; the ratchet comes down, never up.
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

from .gating import GatingPolicy, GatingPolicyError, stub_list

#: What replaces a private skill's module docstring in the carve.
_DOCSTRING_STUB = '''"""Canonical manifest for the `{name}` skill.

Design notes, build history and provider measurements for this skill are part of the
hosted product and are not included in this distribution. The declared interface is the
``GTMSkill(...)`` call below, and the prompt interface is the generated
``plugin/skills/{name}/SKILL.md``.
"""'''


def module_docstring_span(source: str) -> tuple[int, int] | None:
    """``(start_line, end_line)``, 1-indexed inclusive, of the module docstring — or None.

    AST-located rather than regex-matched: a module docstring is by definition the first
    statement, so this cannot mistake a triple-quoted string elsewhere in the file — a
    ``description=`` value, a nested helper's docstring — for the one being replaced.
    """
    tree = ast.parse(source)
    if not tree.body:
        return None
    first = tree.body[0]
    if not isinstance(first, ast.Expr) or not isinstance(first.value, ast.Constant):
        return None
    if not isinstance(first.value.value, str) or first.end_lineno is None:
        return None
    return first.lineno, first.end_lineno


def strip_manifest_docstrings(
    carved_root: Path, *, policy: GatingPolicy | None = None
) -> list[str]:
    """Replace every private skill's manifest module docstring under ``<carved_root>/
    gtm_core/skills/`` with a stub, leaving the ``SKILL = GTMSkill(...)`` call — and every
    other byte of the file — untouched.

    Keyed off the same ``oss = "private"`` resolution ``stub_carve`` uses, so one
    ``gating.toml`` edit still moves both halves.

    Raises :class:`GatingPolicyError` if a private skill has no carved manifest, or if that
    manifest has no module docstring to replace — either means the carve is not the shape
    this was written against, which is a reason to stop rather than to skip.
    """
    skills_dir = Path(carved_root) / "gtm_core" / "skills"
    stripped: list[str] = []
    for name in sorted(stub_list(policy=policy)):
        manifest = skills_dir / f"{name.replace('-', '_')}.py"
        if not manifest.is_file():
            raise GatingPolicyError(
                "missing_manifest",
                f"private skill {name!r} has no carved manifest at {manifest}",
            )
        source = manifest.read_text(encoding="utf-8")
        span = module_docstring_span(source)
        if span is None:
            raise GatingPolicyError(
                "missing_docstring",
                f"private skill manifest {manifest} has no module docstring to strip",
            )
        start, end = span
        lines = source.splitlines(keepends=True)
        replacement = _DOCSTRING_STUB.format(name=name) + "\n"
        manifest.write_text(
            "".join(lines[: start - 1]) + replacement + "".join(lines[end:]), encoding="utf-8"
        )
        stripped.append(name)
    return stripped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gtm_core.carve_manifest")
    parser.add_argument(
        "carved_root",
        help="root of the carved tree whose private skill manifests should be stripped",
    )
    args = parser.parse_args(argv)
    for name in strip_manifest_docstrings(Path(args.carved_root)):
        print(f"stripped manifest docstring: {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
