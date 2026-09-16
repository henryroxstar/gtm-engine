"""Contract: the status block is one source, pasted, never composed by hand.

PS9 rewrote every operator-facing status report in the `prospect` and `email-sequence`
skills to run `gtm_core.prospects status` and paste its real output between
``<!-- operator --> ... <!-- /operator -->`` markers, rather than each reporting point
composing its own prose in its own vocabulary. This is checked against the GENERATED
``SKILL.md`` files, not ``body_template.md`` — the generated file is what a running
agent actually reads, and `tests/lint/skill_codegen_sync.sh` is what keeps the two in
sync, so a drift between them is that gate's job, not this one's.

Two properties, both real regressions this PRD's §2.6 and §4.3 found:

* The same finished run used to be reported in vocabularies that shared no words at
  different points (Step 12's lane split vs. Step 13's ready/verifying/enrich line).
  The fix is one command, invoked identically everywhere — checked here by requiring
  the exact same command string at every reporting point.
* Per [§R14](../../docs/RULES.md#r14--a-number-in-rendered-prose-is-derived-never-typed)
  a number in rendered prose is derived, never typed — so the pasted block itself must
  never carry a hand-typed digit standing in for a status count baked into the skill
  body. Counts may only ever come from actually running the command.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.lint.operator_vocabulary import _MARKER_RE

REPO = Path(__file__).resolve().parents[2]

#: The one command every reporting point must invoke, byte-identical.
_STATUS_CMD = "uv run python -m gtm_core.prospects status --profile <active>"

#: (skill name, generated SKILL.md path, minimum reporting points expected).
#: `prospect` has three (Steps 1, 12, 13); `email-sequence` has one (the closing
#: status/dashboard summary) — see PS9's §4.2 table row 11.
_TARGETS: tuple[tuple[str, Path, int], ...] = (
    ("prospect", REPO / "plugin/skills/prospect/SKILL.md", 3),
    ("email-sequence", REPO / "plugin/skills/email-sequence/SKILL.md", 1),
)


def test_every_reporting_point_invokes_the_identical_status_command() -> None:
    """Every reporting point runs the SAME `gtm_core.prospects status` invocation.

    Before PS9, Step 12 printed `N accounts · T Tier-A (packs) · lanes: ...` and Step 13
    printed `30 ready · 417 verifying · 1,231 accounts to enrich` for the same finished
    run — two vocabularies that share no words. One command string, repeated verbatim,
    is what makes progress readable as movement between identical snapshots.
    """
    for name, path, minimum in _TARGETS:
        assert path.is_file(), f"{name}: generated SKILL.md not found at {path}"
        text = path.read_text(encoding="utf-8")
        occurrences = text.count(_STATUS_CMD)
        assert occurrences >= minimum, (
            f"{name}/SKILL.md: expected >= {minimum} occurrence(s) of the exact command "
            f"{_STATUS_CMD!r}, found {occurrences}. Every reporting point must invoke the "
            "identical `gtm_core.prospects status` command, never compose its own summary."
        )


def test_operator_marked_blocks_carry_no_hand_typed_count() -> None:
    """The content between `<!-- operator -->` markers must be a pure paste target.

    A digit inside one of these blocks would be exactly the §R14 violation this PRD
    exists to remove: a status count baked into the skill body, going stale the moment
    the pool changes, and never re-derived before the next reader quotes it.
    """
    total_blocks = 0
    offenders: list[str] = []
    for name, path, _minimum in _TARGETS:
        text = path.read_text(encoding="utf-8")
        blocks = _MARKER_RE.findall(text)
        total_blocks += len(blocks)
        for block in blocks:
            digits = re.findall(r"\d", block)
            if digits:
                offenders.append(f"{name}/SKILL.md: {digits!r} in {block.strip()!r}")
    assert not offenders, (
        "hand-typed digit(s) found inside <!-- operator --> block(s) — a count must come "
        "only from running the command, never be baked into the skill body:\n  "
        + "\n  ".join(offenders)
    )
    # A check with nothing to check is not a check (§R18) — prove the markers actually
    # exist rather than passing vacuously if they were ever removed or renamed.
    assert total_blocks >= 4, (
        f"expected at least 4 <!-- operator --> blocks across "
        f"{[n for n, _, _ in _TARGETS]}, found {total_blocks} — "
        "markers may have been removed or renamed"
    )
