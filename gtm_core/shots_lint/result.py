"""Third result channel + optional craft hook (PRD 2026-10-19 R8). Craft logic: gtm_core/video_craft."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from .api import lint_shotlist


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: Literal["error", "warning"]
    message: str
    cut: int | None = None


@dataclass
class LintResult:
    errors: list[str]
    warnings: list[str]
    not_checked: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


class CraftHook(Protocol):
    """``video_craft.hook.craft_findings``: rule-id'd findings + names of checks that could not
    run. Under ``require_craft`` the hook reports an unrunnable check as an error finding."""

    def __call__(
        self, doc: object, *, require_craft: bool, base_dir: Path | None = None
    ) -> tuple[list[Finding], list[str]]: ...


def load_craft_hook() -> CraftHook | None:
    """None only when video_craft ITSELF is absent (public cut); a broken dependency raises."""
    try:
        from ..video_craft.hook import craft_findings

        return craft_findings
    except ModuleNotFoundError as exc:
        # The package (public cut) or its hook module is absent; any OTHER missing name inside
        # video_craft is a broken dependency and must surface, not read as a clean list.
        if exc.name in ("gtm_core.video_craft", "gtm_core.video_craft.hook"):
            return None
        raise


def lint_shotlist_detailed(
    doc: object, *, require_craft: bool = False, base_dir: Path | None = None, **kwargs
) -> LintResult:
    """:func:`lint_shotlist` unchanged, plus craft findings merged by severity (unknown → error)."""
    errors, warnings = lint_shotlist(doc, **kwargs)
    if (hook := load_craft_hook()) is None:
        return LintResult(errors, warnings)
    extra = (
        {} if base_dir is None else {"base_dir": base_dir}
    )  # the list's folder, for its own files
    findings, not_checked = hook(doc, require_craft=require_craft, **extra)
    for f in findings:
        (warnings if f.severity == "warning" else errors).append(f.message)
    return LintResult(errors, warnings, list(not_checked), list(findings))
