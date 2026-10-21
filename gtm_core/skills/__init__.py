"""Canonical skill manifests + SKILL.md codegen.

The Python `GTMSkill` manifest is the **source of truth** for a skill's metadata
and tier. `plugin/skills/<name>/SKILL.md` is a **generated artifact** — Cowork and
the Agent SDK read it, but it is rendered from the manifest + the human-authored
prompt body (`plugin/skills/<name>/body_template.md`). Code runtimes (backend API,
MCP server) import the manifest directly.

Stdlib-only, SDK-free — like the rest of gtm_core.

  base      — the GTMSkill dataclass (the manifest contract).
  registry  — single enumeration point all runtimes use.
  codegen   — render SKILL.md from a manifest; migrate/check helpers + CLI.
"""
