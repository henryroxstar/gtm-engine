"""SKILL.md codegen — the manifest is canonical; SKILL.md is generated.

Stdlib-only (gtm_core is SDK-free and dependency-free): frontmatter is emitted by
hand (no PyYAML), and the prompt body is copied verbatim from
plugin/skills/<name>/body_template.md so migration can never mangle a prompt.
Cowork keeps reading the generated SKILL.md; code runtimes import the manifest.

CLI:
  python -m gtm_core.skills.codegen migrate <name>    # SKILL.md -> body_template.md (one-time)
  python -m gtm_core.skills.codegen generate <name>   # manifest + body_template -> SKILL.md
  python -m gtm_core.skills.codegen generate-all
  python -m gtm_core.skills.codegen check             # exit 1 if any committed SKILL.md is stale
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .base import GTMSkill

_OPEN = "---\n"
_CLOSE = "\n---\n"
_WRAP_WIDTH = 92


def _repo_root() -> Path:
    # gtm_core/skills/codegen.py -> parents[2] is the repo root in a dev/CI checkout.
    return Path(__file__).resolve().parents[2]


def default_plugin_root() -> Path:
    return _repo_root() / "plugin"


def skill_md_path(plugin_root: Path, name: str) -> Path:
    return plugin_root / "skills" / name / "SKILL.md"


def body_path(plugin_root: Path, name: str) -> Path:
    return plugin_root / "skills" / name / "body_template.md"


def split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_text, body_text). Raises ValueError if absent."""
    if not text.startswith(_OPEN):
        raise ValueError("file does not start with '---' frontmatter")
    end = text.find(_CLOSE, len(_OPEN))
    if end == -1:
        raise ValueError("unterminated frontmatter (no closing '---')")
    return text[len(_OPEN) : end], text[end + len(_CLOSE) :]


def _wrap(text: str, width: int = _WRAP_WIDTH) -> list[str]:
    """Deterministic greedy word-wrap for the folded description block."""
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def render_frontmatter(skill: GTMSkill) -> str:
    """Emit deterministic YAML frontmatter. `description` is a folded block scalar
    (`>-`) so newlines fold to spaces — the same value the prior hand-authored
    `>` block produced, minus the trailing newline."""
    out = ["---", f"name: {skill.name}", "description: >-"]
    out.extend(f"  {line}" for line in _wrap(skill.description))
    if skill.license:
        out.append(f"license: {skill.license}")
    out.append("metadata:")
    out.append(f'  version: "{skill.version}"')
    if skill.phase is not None:
        out.append(f'  phase: "{skill.phase}"')
    out.append(f"  capability_tier: {skill.capability_tier.value}")
    if skill.requires_capability:
        out.append(f"  requires_capability: [{', '.join(skill.requires_capability)}]")
    out.append("---")
    return "\n".join(out) + "\n"


def render(skill: GTMSkill, body: str) -> str:
    text = render_frontmatter(skill) + body
    if skill.fallback_note:
        text = text.rstrip("\n") + (
            "\n\n## Degraded mode (no paid connectors)\n\n" + skill.fallback_note + "\n"
        )
    return text


def generate(skill: GTMSkill, plugin_root: Path | None = None) -> Path:
    plugin_root = plugin_root or default_plugin_root()
    body = body_path(plugin_root, skill.name).read_text(encoding="utf-8")
    target = skill_md_path(plugin_root, skill.name)
    target.write_text(render(skill, body), encoding="utf-8")
    return target


def migrate(name: str, plugin_root: Path | None = None) -> Path:
    """One-time: split an existing SKILL.md and write its body verbatim to body_template.md."""
    plugin_root = plugin_root or default_plugin_root()
    text = skill_md_path(plugin_root, name).read_text(encoding="utf-8")
    _, body = split_frontmatter(text)
    target = body_path(plugin_root, name)
    target.write_text(body, encoding="utf-8")
    return target


def check(plugin_root: Path | None = None) -> list[str]:
    """Return names whose committed SKILL.md differs from the generated output."""
    from . import registry

    plugin_root = plugin_root or default_plugin_root()
    drift: list[str] = []
    for skill in registry.all_skills():
        body = body_path(plugin_root, skill.name).read_text(encoding="utf-8")
        if render(skill, body) != skill_md_path(plugin_root, skill.name).read_text(
            encoding="utf-8"
        ):
            drift.append(skill.name)
    return drift


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gtm_core.skills.codegen")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("migrate", "generate"):
        sp = sub.add_parser(name)
        sp.add_argument("name")
    sub.add_parser("generate-all")
    sub.add_parser("check")
    args = parser.parse_args(argv)

    from . import registry

    if args.cmd == "migrate":
        print(f"wrote {migrate(args.name)}")
        return 0
    if args.cmd == "generate":
        skill = next((s for s in registry.all_skills() if s.name == args.name), None)
        if skill is None:
            print(f"no manifest registered for {args.name!r}", file=sys.stderr)
            return 2
        print(f"wrote {generate(skill)}")
        return 0
    if args.cmd == "generate-all":
        for skill in registry.all_skills():
            print(f"wrote {generate(skill)}")
        return 0
    if args.cmd == "check":
        drift = check()
        if drift:
            print(
                "✗ codegen-sync: stale SKILL.md (run: generate-all): " + ", ".join(drift),
                file=sys.stderr,
            )
            return 1
        print("✓ codegen-sync: all generated SKILL.md in sync")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
