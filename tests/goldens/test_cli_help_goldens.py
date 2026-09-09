"""``--help`` transcripts of the two Phase-2 CLIs (plus one Phase-3B parser), pinned byte-for-byte (PRD 2026-09-01 §6.2 V2).

``--help`` is the CLI contract the generated skills quote — that is why both parsers pin
``prog=`` — so these ARE permanent goldens, not per-PR artifacts. Captured with ``COLUMNS=80``
(argparse wraps to the terminal) and Python 3.13's argparse formatter (CI's version; a different
minor version may wrap differently — regenerate on the pinned one).

Two things are pinned here that are NOT transcripts:

* ``python -m gtm_core.screen_ui --help`` CRASHES today: the ``--min-tolerance`` help string
  carries a bare ``%`` ("~20% in advance width"), which argparse reads as a ``%i`` directive and
  raises ``TypeError``. A pure-motion split must reproduce that crash exactly; the fix (``%%``)
  is a separate, deliberate change that flips ``test_screen_ui_help_crashes_on_an_unescaped_percent``
  and adds a real transcript. Until then the usage line (which never expands help strings) is
  the screen_ui contract on file.
* the set of video_finish subcommands, cross-checked against the golden inventory so a new
  subcommand cannot ship without a transcript.

Regenerate deliberately, in the same PR as an intentional CLI change — never to green a red run:

    uv run python tests/goldens/test_cli_help_goldens.py --write
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import golden_matrix as gm
import pytest

GOLDEN_DIR = Path(__file__).resolve().parent / "cli"
SUBCOMMANDS = (
    "run",
    "predictor-trim",
    "grade",
    "frames-to-video",
    "mux",
    "split",
    "stitch",
    "burn-captions",
    "find-transient",
    "mix-sfx",
    "room-tone",
    "narration-track",
    "contact-sheet",
    "reframe",
)
#: (golden file, module, argv, expected exit, which stream is the transcript)
CASES: tuple[tuple[str, str, tuple[str, ...], int, str], ...] = (
    ("video_finish.help.txt", gm.VIDEO_FINISH, ("--help",), 0, "stdout"),
    *(
        (f"video_finish.{sub}.help.txt", gm.VIDEO_FINISH, (sub, "--help"), 0, "stdout")
        for sub in SUBCOMMANDS
    ),
    # No help transcript exists for screen_ui (see the module docstring); the usage block is
    # printed on the argparse error path and pins scene choices, ratio choices and every flag.
    ("screen_ui.usage.txt", gm.SCREEN_UI, (), 2, "stderr"),
    # Phase 3B: email_campaign_dashboard had no explicit prog= at the merge-base, so argparse
    # derived `email_campaign_dashboard.py` from argv[0] — and the package move silently turned
    # that into `__main__.py`. Now pinned to the form every skill cites (its 3B siblings'
    # convention) and captured here so the usage line cannot drift again.
    (
        "email_campaign_dashboard.help.txt",
        "gtm_core.email_campaign_dashboard",
        ("--help",),
        0,
        "stdout",
    ),
)
_CHOICES_RE = re.compile(r"\{([a-z0-9,-]+)\} \.\.\.")


def _run(module: str, argv: tuple[str, ...], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", module, *argv],
        cwd=cwd,
        env=gm.cli_env(cwd),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_transcript_matches_the_committed_golden(case, tmp_path):
    golden_name, module, argv, exit_code, stream = case
    golden = GOLDEN_DIR / golden_name
    assert golden.is_file(), f"missing golden {golden.name} — see the module docstring"
    proc = _run(module, argv, tmp_path)
    assert proc.returncode == exit_code, proc.stderr
    live = getattr(proc, stream)
    expected = golden.read_text(encoding="utf-8")
    assert live == expected, (
        f"{golden.name} drifted. If the CLI change is intentional, regenerate in the same PR:\n"
        "  uv run python tests/goldens/test_cli_help_goldens.py --write"
    )


def test_every_subcommand_in_the_live_help_has_a_golden(tmp_path):
    """The top-level help's ``{a,b,c} ...`` choices line is the subcommand inventory; a new verb
    added without a transcript fails here rather than shipping unpinned."""
    proc = _run(gm.VIDEO_FINISH, ("--help",), tmp_path)
    match = _CHOICES_RE.search(proc.stdout)
    assert match, proc.stdout
    assert match.group(1).split(",") == list(SUBCOMMANDS)


def test_video_finish_prog_is_pinned_under_module_execution(tmp_path):
    """Under ``-m`` of a PACKAGE the default prog degrades to ``__main__.py``; the pinned prog is
    what keeps every skill's quoted invocation true after the split (PRD §5 step 3)."""
    top = _run(gm.VIDEO_FINISH, ("--help",), tmp_path).stdout
    assert top.startswith("usage: python -m gtm_core.video_finish ")
    sub = _run(gm.VIDEO_FINISH, ("stitch", "--help"), tmp_path).stdout
    assert sub.startswith("usage: python -m gtm_core.video_finish stitch ")
    usage = _run(gm.SCREEN_UI, (), tmp_path).stderr
    assert usage.startswith("usage: gtm_core.screen_ui ")
    assert "__main__" not in top + sub + usage


def test_screen_ui_help_renders_now_that_the_percent_is_escaped(tmp_path):
    """Was a pinned DEFECT: an unescaped ``%`` in a help string made argparse raise
    ``TypeError: %i format`` on ``--help``, so the one command a new user runs first was the one
    command that crashed. Flipped 2026-09-03 when the escape landed — kept, inverted, so the
    regression cannot return silently."""
    proc = _run(gm.SCREEN_UI, ("--help",), tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("usage: gtm_core.screen_ui ")
    assert "TypeError" not in proc.stderr


def test_screen_ui_usage_lists_every_scene_and_ratio(tmp_path):
    usage = _run(gm.SCREEN_UI, (), tmp_path).stderr
    assert "{" + ",".join((*gm.SCENES, "audit-fit")) + "}" in usage
    assert "--ratio {16:9,1:1,4:5,9:16}" in usage
    assert usage.rstrip().endswith(
        "error: the following arguments are required: scene, --kit-json, --ratio"
    )


def write_goldens() -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    scratch = GOLDEN_DIR.parent
    for golden_name, module, argv, exit_code, stream in CASES:
        proc = _run(module, argv, scratch)
        if proc.returncode != exit_code:
            raise SystemExit(f"{golden_name}: exit {proc.returncode}, expected {exit_code}")
        (GOLDEN_DIR / golden_name).write_text(getattr(proc, stream), encoding="utf-8")
        print(f"wrote {GOLDEN_DIR / golden_name}")


if __name__ == "__main__":
    if "--write" in sys.argv:
        write_goldens()
    else:
        print(__doc__)
