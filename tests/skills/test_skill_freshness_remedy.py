"""T30 — the three skills that act on ``--check-fresh`` cite a remedy that can reach green.

WHY THIS EXISTS (2026-09-30, PRD F4). All three ran the check and none of them named the one
command that clears it. `status` told the reader to re-render the rollup — which refreshes the
page the old one-page check happened to ask about and leaves every scoped page exactly as stale.
`prospect` and `email-sequence` paired ``--scope open`` with ``--check-fresh``, so each asked
about the page it had just rendered a moment earlier: a check that cannot fail, run religiously.

Asserted over ``body_template.md``, because that is the source — ``SKILL.md`` is GENERATED
(CLAUDE.md) and the codegen-sync gate is what proves the two agree.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKILLS = ("status", "prospect", "email-sequence")


def _body(name: str) -> str:
    return (REPO / "plugin" / "skills" / name / "body_template.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", SKILLS)
def test_no_skill_asks_the_check_about_the_page_it_just_rendered(name):
    """``--scope`` and ``--check-fresh`` on ONE command line is the shape that cannot convict:
    the skill renders that scope, then asks whether that scope is current. The widened check has
    no scope precisely so the question is "is ANYTHING stale"."""
    for line in _body(name).splitlines():
        if "--check-fresh" not in line:
            continue
        assert "--scope" not in line, f"{name}: {line.strip()}"
        assert "--campaign" not in line, f"{name}: {line.strip()}"


@pytest.mark.parametrize("name", SKILLS)
def test_every_skill_names_refresh_all_as_the_remedy(name):
    """One remedy, named in the skill that reports the failure. A skill that says "it is stale"
    and stops has moved the work to whoever reads the output."""
    text = _body(name)
    assert "--check-fresh" in text, "fixture precondition: this skill runs the check"
    assert "--refresh-all" in text, f"{name} runs the check and names no remedy"


def _known_flags() -> set[str]:
    """Every option the real CLI accepts, read off the real parser's help.

    NOT by appending ``--help`` to the quoted argv and asserting exit 0: argparse acts on
    ``--help`` the moment it reaches it and exits 0 before it ever validates the tokens before
    it, so that check passes on a flag that does not exist. A check that cannot discriminate is
    not a check (§R18) — the negative control below is what proves this one can.
    """
    import contextlib
    import io

    from gtm_core.email_campaign_dashboard.cli import _cli

    out = io.StringIO()
    with contextlib.redirect_stdout(out), pytest.raises(SystemExit):
        _cli(["--help"])
    return set(re.findall(r"(--[a-z][a-z-]+)", out.getvalue()))


_QUOTED = re.compile(r"python -m gtm_core\.email_campaign_dashboard\s+(.+)")


@pytest.mark.parametrize("name", SKILLS)
def test_every_quoted_dashboard_command_uses_real_flags(name):
    """§4.6 — a command a skill quotes is a claim about the CLI. A renamed or invented flag is a
    dead end in an instruction the operator is told to run."""
    known = _known_flags()
    found = 0
    for raw in _body(name).splitlines():
        hit = _QUOTED.search(raw.strip())
        if not hit:
            continue
        found += 1
        for token in hit.group(1).split():
            if token.startswith("--"):
                assert token in known, f"{name} quotes {token}, which the CLI does not accept"
    assert found >= 2, f"{name}: expected the render and the check, found {found}"


def test_the_flag_check_catches_an_invented_flag():
    """NEGATIVE CONTROL for the two tests above — without it, `_known_flags()` returning an
    over-broad set (or the loop matching nothing) would pass on any wording forever."""
    known = _known_flags()
    assert {"--check-fresh", "--refresh-all", "--scope", "--profile"} <= known
    assert "--refresh-everything" not in known
    assert _QUOTED.search("uv run python -m gtm_core.email_campaign_dashboard --profile x")


def test_the_status_skill_no_longer_offers_a_bare_render_as_the_fix():
    """`status` renders only the rollup, so a bare render was never a remedy for the other
    pages. Pinned as an ABSENCE because that is what regressed: the wrong fix reads as a fix."""
    text = _body("status")
    fix = text.split("If it exits non-zero", 1)[1].split("Then run the status summary", 1)[0]
    assert "--refresh-all" in fix
    assert "Rendering one scope is **not** the remedy" in fix


# --- F2: the stats refresh covers every current sequence (T12) --------------------------------


def _flat(text: str) -> str:
    """The prose with its line wrapping removed, so a sentence is matched however it is wrapped."""
    return " ".join(text.split())


def _refresh_section(text: str) -> str:
    start = "Refresh the live sequencer stats first"
    return _flat(text.split(start, 1)[1].split("## Guardrails", 1)[0])


def _shipped_skill_md() -> str:
    return (REPO / "plugin" / "skills" / "email-sequence" / "SKILL.md").read_text(encoding="utf-8")


def _assert_every_current_sequence_instruction(refresh: str) -> None:
    """The instruction an agent reads before it refreshes the sending figures.

    The page's age is the age of the OLDEST current sequence behind it, so a refresh of a subset
    cannot make the page current — it can only change which old sequence sets the age. The skill
    therefore says: every current sequence, one pass, never a subset; ``status`` first, because
    it names every sequence whose figures are missing, old or unstamped; the writer owns the file
    and the date. This is procedure, not code: nothing proves an agent follows it (the plan's
    residual), but the instruction itself must not leave the gap.
    """
    assert "every current sequence in one pass — never a subset" in refresh
    assert "Run `status` first" in refresh
    assert "missing, old or unstamped" in refresh and "must cover all of them" in refresh
    assert "never write or edit `sequence-stats.json` by hand" in refresh
    assert "The writer, not you, owns the file" in refresh
    assert "stamps each sequence with its own fetch date" in refresh
    # The scoping that made the old text a subset instruction, and a typed limit (§R14).
    for stale in (
        "you want refreshed",
        "you touched",
        "A refresh of a few sequences is therefore safe",
    ):
        assert stale not in refresh, f"stale instruction still there: {stale!r}"
    assert "older than an hour" not in refresh and "60 minutes" not in refresh


def test_the_stats_refresh_is_every_current_sequence_in_one_pass():
    """T12 — against the SOURCE the skill is generated from."""
    text = _body("email-sequence")
    assert "Refresh the live sequencer stats first" in text
    _assert_every_current_sequence_instruction(_refresh_section(text))


def test_the_generated_skill_md_carries_the_same_instruction():
    """`SKILL.md` is GENERATED (CLAUDE.md). The codegen-sync gate proves they match in general;
    this asserts the specific instruction reached the file an agent actually loads."""
    _assert_every_current_sequence_instruction(_refresh_section(_shipped_skill_md()))


def test_the_instruction_check_can_fail():
    """NEGATIVE CONTROL: the old wording must fail the same assertions, or they pass on anything."""
    old = (
        "Refresh the live sequencer stats first (this is what powers the performance card). "
        "Call `get_sequence_stats` for the current sequences you want refreshed, save each reply "
        "to a file, and hand the files to the one writer. A refresh of a few sequences is "
        "therefore safe."
    )
    with pytest.raises(AssertionError):
        _assert_every_current_sequence_instruction(_flat(old))


def test_the_skill_tells_the_agent_to_hand_the_writer_rows_not_the_raw_reply():
    """The tool replies ``{message, payload}``; the writer refuses that wrapper, so the skill must
    say what goes in the file or the first honest refresh is refused."""
    refresh = _refresh_section(_body("email-sequence"))
    assert "the `payload` object of each reply" in refresh
    assert '{"sequences": [<payload>, …]}' in refresh


def test_the_skill_runs_status_before_and_after_the_write():
    section = _body("email-sequence").split("Refresh the live sequencer stats first", 1)[1]
    block = section.split("```bash", 1)[1].split("```", 1)[0]
    steps = [
        ln.split("sequencer_snapshot --profile <active> ", 1)[1].split()[0]
        for ln in block.splitlines()
        if "sequencer_snapshot" in ln
    ]
    assert steps == ["status", "write", "status"]
