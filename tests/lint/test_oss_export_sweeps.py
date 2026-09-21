"""Contract: every whole-carve sweep in `scripts/oss-export.sh` survives a ZERO-match carve.

`grep` exits 1 when it finds nothing, and the script runs under `set -euo pipefail`. `set -e` is
suspended for an `if` condition but NOT for a bare `V="$(grep …)"` assignment — so a sweep written
in the bare form aborts the entire export with a bare exit 1 and no message, on exactly the clean
carve it exists to pass. The failure surfaces as whatever step printed last, which is why it can
sit latent.

The token sweep held that form until 2026-08-28. It never fired, because one of the swept tokens is
the maintainer's own handle and their published clone URL legitimately appears in two carved files —
so the sweep always matched, and the `KNOWN_GOOD_TOKEN_LINES` filter then discarded those very
lines. It depended on a match it threw away; dropping the clone URL (or renaming the public repo)
would have started killing the export silently.

Two guards: `test_no_bare_grep_assignment_sweeps` covers the whole file, so a FUTURE sweep cannot
reintroduce the form, and the executable tests run the real extracted token-sweep block against a
synthetic carve.

Every token this module needs is read out of the script at runtime rather than written here — a
copy of the denylist in a carved test file would be swept by the very gate under test.
"""

from __future__ import annotations

import re
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "oss-export.sh"

#: The fail-open-looking form: capture a grep's output into a variable, then inspect `$?`.
#: Under `set -e` the assignment itself aborts first, so the `rc` check below it is dead code
#: on the zero-match path.
_BARE_GREP_ASSIGN = re.compile(r'^\s*\w+="\$\(\s*grep\b[^\n]*\)"\s*;\s*rc=\$\?', re.M)

#: The token sweep, lifted verbatim from the script: everything between the
#: `KNOWN_GOOD_TOKEN_LINES` knob and the binary-sweep comment that follows it. Deliberately
#: anchored on its NEIGHBOURS rather than on its own `if …` shape, so extraction still succeeds
#: if someone reintroduces the bare-assignment form — the executable tests below then reproduce
#: the silent abort instead of sailing past it on a failed match.
_TOKEN_SWEEP = re.compile(r"^KNOWN_GOOD_TOKEN_LINES=.*?\n(.*?)^# The sweep above uses", re.M | re.S)


def _script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _knob(name: str) -> str:
    """Read a single-quoted top-level assignment (`NAME='…'`) out of the script."""
    m = re.search(rf"^{name}='([^']*)'", _script(), re.M)
    assert m, f"knob {name} not found in {SCRIPT.name}"
    return m.group(1)


def _token_sweep_block() -> str:
    m = _TOKEN_SWEEP.search(_script())
    assert m, (
        "could not locate the token-sweep block in scripts/oss-export.sh. If it was "
        "restructured, re-anchor this test — do not delete it; it guards a silent-abort class."
    )
    return m.group(1)


def _a_swept_token() -> str:
    """A plain-word alternative from the script's own denylist, for the positive-control fixture."""
    for alt in _knob("TOKENS").split("|"):
        if alt.isalpha():
            return alt
    pytest.fail("no plain-word token in the script's TOKENS denylist")


def _known_good_line() -> str:
    """The one allowed match, un-escaped from its filter regex into literal text."""
    line = _knob("KNOWN_GOOD_TOKEN_LINES")
    line = re.sub(r"\([^)]*?(/)[^)]*?\)", r"\1", line)
    return line.replace("\\", "")


def _run_sweep(carve: Path) -> subprocess.CompletedProcess:
    """Run the REAL extracted sweep block under the script's own shell options."""
    harness = (
        textwrap.dedent(
            """\
        set -euo pipefail
        DEST="$1"
        TOKENS="$SWEEP_TOKENS"
        KNOWN_GOOD_TOKEN_LINES="$SWEEP_KNOWN_GOOD"
        SWEEP_EXCLUDES=(--exclude-dir=.git --exclude=CODEOWNERS)
        """
        )
        + _token_sweep_block()
        + '\necho "SWEEP-SURVIVED"\n'
    )
    return subprocess.run(
        ["bash", "-c", harness, "sweep", str(carve)],
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "SWEEP_TOKENS": _knob("TOKENS"),
            "SWEEP_KNOWN_GOOD": _knob("KNOWN_GOOD_TOKEN_LINES"),
        },
    )


@pytest.mark.private_tree  # reads the un-carved scripts/oss-export.sh
def test_no_bare_grep_assignment_sweeps():
    """No sweep anywhere in the script may use the `V="$(grep …)"; rc=$?` form."""
    src = _script()
    offenders = [
        f"{SCRIPT.name}:{src.count(chr(10), 0, m.start()) + 1}: {m.group(0).strip()}"
        for m in _BARE_GREP_ASSIGN.finditer(src)
    ]
    assert not offenders, (
        "bare command-substitution assignment from grep under `set -e` — aborts the export "
        'silently when grep finds nothing (rc=1). Use the `if HITS="$(grep …)"; then … else '
        "rc=$?; …; fi` form instead:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.private_tree  # executes a block extracted from the un-carved scripts/oss-export.sh
def test_token_sweep_survives_a_clean_carve(tmp_path):
    """The regression: zero matches is the DESIRED outcome and must not abort the export."""
    (tmp_path / "README.md").write_text("A generic engine with no tenant identity.\n")
    r = _run_sweep(tmp_path)
    assert r.returncode == 0, (
        f"clean carve aborted the sweep: rc={r.returncode}\n{r.stdout}{r.stderr}"
    )
    assert "SWEEP-SURVIVED" in r.stdout


@pytest.mark.private_tree  # executes a block extracted from the un-carved scripts/oss-export.sh
def test_token_sweep_still_fails_on_a_real_token(tmp_path):
    """Fail-closed is preserved — the fix must not turn the gate off."""
    (tmp_path / "leak.md").write_text(f"Deployed for {_a_swept_token().title()} last quarter.\n")
    r = _run_sweep(tmp_path)
    assert r.returncode == 1
    assert "company token in carve" in r.stdout
    assert "SWEEP-SURVIVED" not in r.stdout


@pytest.mark.private_tree  # executes a block extracted from the un-carved scripts/oss-export.sh
def test_token_sweep_passes_when_only_the_known_good_line_matches(tmp_path):
    """The published clone URL is the one allowed match — and is no longer load-bearing."""
    (tmp_path / "END-USER-ONBOARDING.md").write_text(
        f"git clone https://{_known_good_line()}.git\n"
    )
    r = _run_sweep(tmp_path)
    assert r.returncode == 0, f"{r.stdout}{r.stderr}"
    assert "SWEEP-SURVIVED" in r.stdout
