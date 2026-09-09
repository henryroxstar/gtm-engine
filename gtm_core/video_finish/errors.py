from __future__ import annotations


class FfmpegUnavailable(RuntimeError):
    """ffmpeg is not on PATH. Never silently skipped — the caller must know encoding did not run."""


class PlanError(ValueError):
    """A FinishPlan would violate an invariant (e.g. more than one grade stage)."""


class LintError(RuntimeError):
    """A polish step was refused because the asset failed a shipped lint tier."""


class PolishError(RuntimeError):
    """A Reap polish call was refused or misconfigured."""


class SfxError(PlanError):
    """An SFX mix would ship a cue nobody can hear, or a cue file has no transient to align to.

    Subclasses :class:`PlanError` (itself a ValueError) so it inherits this module's existing
    CLI mapping to exit 4 rather than adding a fourth class to every ``except`` chain.
    """
