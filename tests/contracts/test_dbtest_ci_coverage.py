"""Contract: every dbtest-marked (or live-fixture-using) test file actually runs in CI.

Live-Postgres tests are marked ``@pytest.mark.dbtest`` (a decorator, or a module-level
``pytestmark``), but the marker itself does nothing — the SKIP actually comes from the
``live_db``/``clean_db`` fixtures in ``tests/conftest.py`` calling ``pytest.skip()`` when
``GTM_TEST_PG_ADMIN_DSN`` is unset. So a test that takes either fixture needs a real Postgres
to run for real, marker or not. The only place that ever sets that DSN is the ``backend-db``
job in ``.github/workflows/ci.yml``, which names its pytest files EXPLICITLY rather than
collecting ``tests/``. A file left off that list is therefore not "slow" or "flaky" — it is
DEAD: no CI job, and no documented local recipe, ever executes it.

Found 2026-09-15: ``tests/backend/test_publish_settings.py`` grew
``test_a_partial_put_preserves_the_workspaces_existing_schedule_settings`` under
``@pytest.mark.dbtest`` but was never added to ``ci.yml``'s file list, so it had never run
anywhere since it was written. That test has since moved to its own
``tests/backend/test_publish_settings_live.py`` so the backend-db job's own header comment — it
targets dbtest files directly, never collecting a mocked suite — stays true; this module's
contract does not depend on that split (it would still catch the original mixed-file shape).

Detection is AST-based, not regex/text-based: a text scan either misses real marker shapes
(``dbtest`` not first in a ``pytestmark`` list, a multi-line list, a tuple, ``from pytest
import mark`` + ``@mark.dbtest``, ``pytest.param(..., marks=pytest.mark.dbtest)``) or
false-positives on a docstring/comment that merely *mentions* the marker as prose. Walking the
parsed AST for an ``Attribute(attr="dbtest")`` whose value chain ends in ``.mark`` sees through
all of the above by construction — a string literal or a `#` comment is never an Attribute node.
The same walk also catches ``pytest.mark.usefixtures("live_db")``/``"clean_db"`` (a decorator or
``pytestmark``), which requests a fixture by name rather than by parameter.

Known limit: an INDIRECT fixture chain is invisible to a static, per-file scan. A test taking
some other fixture ``pool`` whose own definition depends on ``clean_db`` (e.g. ``@pytest.fixture
def pool(clean_db): ...`` elsewhere, then ``def test_x(pool): ...``) needs a real Postgres too,
but nothing here can see through that chain — this module only recognises a DIRECT
``live_db``/``clean_db`` parameter, marker, or ``usefixtures`` call.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CI = REPO / ".github" / "workflows" / "ci.yml"

_LIVE_DB_FIXTURES = frozenset({"live_db", "clean_db"})


def _is_mark_ref(value: ast.AST) -> bool:
    """True for a value chain ending in `.mark` — `pytest.mark` or a bare `mark` (as in
    `from pytest import mark`)."""
    if isinstance(value, ast.Name):
        return value.id == "mark"
    if isinstance(value, ast.Attribute):
        return value.attr == "mark"
    return False


def _fixture_names(args: ast.arguments) -> set[str]:
    return {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}


def _is_usefixtures_live_db_call(node: ast.AST) -> bool:
    """True for `<...>.mark.usefixtures("live_db"|"clean_db")` — a decorator or a `pytestmark`
    assignment requesting the fixture by NAME rather than by parameter."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not (
        isinstance(func, ast.Attribute) and func.attr == "usefixtures" and _is_mark_ref(func.value)
    ):
        return False
    return any(
        isinstance(arg, ast.Constant) and arg.value in _LIVE_DB_FIXTURES for arg in node.args
    )


def _classify_source(text: str) -> tuple[bool, bool]:
    """One AST pass -> (uses_dbtest_marker, uses_live_db_fixture) for a test module's source.

    The parameter-based fixture check only looks at ``test_*`` function defs, not every
    function — a fixture DEFINITION can itself take a same-named parameter
    (``tests/conftest.py``'s own ``clean_db`` fixture takes ``live_db``, since one depends on
    the other) without that file containing a single live-DB *test*; matching on any function
    would false-positive conftest.py itself. A ``usefixtures(...)`` call carries no such risk
    (a fixture definition is never itself decorated with `usefixtures` naming its own
    dependency), so it is recognised anywhere in the file.

    Raises SyntaxError on unparseable source — callers that scan real files turn that (and a
    decode failure) into a loud, path-naming AssertionError rather than a bare traceback.
    """
    tree = ast.parse(text)
    has_marker = False
    has_fixture = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "dbtest" and _is_mark_ref(node.value):
            has_marker = True
        elif _is_usefixtures_live_db_call(node):
            has_fixture = True
        elif (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
            and _fixture_names(node.args) & _LIVE_DB_FIXTURES
        ):
            has_fixture = True
    return has_marker, has_fixture


def uses_dbtest_marker(text: str) -> bool:
    """True if `text` (a test module's source) applies the dbtest marker to something.

    Deliberately AST-based, not a text/regex scan — see the module docstring for the shapes
    a regex misses or false-positives on.
    """
    return _classify_source(text)[0]


def uses_live_db_fixture(text: str) -> bool:
    """True if `text` requests `live_db`/`clean_db` — as a `test_*` function's parameter (sync
    or async), or via `pytest.mark.usefixtures("live_db"|"clean_db")` (decorator or pytestmark).

    These fixtures (`tests/conftest.py`) are what actually SKIPs without a live Postgres — a
    test can use one without ever carrying the dbtest marker, and still needs CI coverage.
    The parameter form is restricted to `test_*`-named functions so a FIXTURE definition that
    merely depends on another same-named fixture (`clean_db(live_db)` in conftest.py) doesn't
    count. See the module docstring for the known limit: an indirect fixture chain (a test
    taking some OTHER fixture that itself depends on `clean_db`) is invisible to this scan.
    """
    return _classify_source(text)[1]


def _is_live_db_test_source(text: str) -> bool:
    marker, fixture = _classify_source(text)
    return marker or fixture


def _backend_db_job_text(ci_text: str) -> str:
    start = ci_text.index("\n  backend-db:\n")
    end = ci_text.index("\n  actionlint:\n", start)
    return ci_text[start:end]


_RUN_HEADER_RE = re.compile(r"^([ \t]*)(?:-[ \t]+)?run:[ \t]*([|>][+-]?)?[ \t]*(.*)$")


def _flatten_run_blocks(job_text: str) -> str:
    """Best-effort normalization of a workflow job's `run:` steps, one OUTPUT LINE per
    step (or per original statement inside a literal block — see below), so a downstream
    per-line scan never crosses from one step's command into another's.

    Not a full YAML parser — just enough to keep `python -m pytest ...` findable regardless of
    how the step's `run:` value is spelled:
      - drops full-line comments (a `#`-led line, YAML or bash, never counts as an invocation)
      - joins bash `\\`-continued lines
      - recognises both `run:` and the dash-prefixed list-item form `- run:`, basing the block's
        indent on the `-` column when present (so a sibling `- name: ...`/`- run: ...` step,
        which aligns on that same dash column, correctly ends the block)
      - a FOLDED scalar (`run: >-`, `run: >`) really is one logical command wrapped across
        display lines, so its body is joined onto the header line with spaces
      - a LITERAL scalar (`run: |`) is a bash script of separate STATEMENTS, one per physical
        line — folding those together with a bare space silently merged two unrelated commands
        onto one line (a downstream per-line scan would then treat a later, unrelated command's
        `.py`-looking argument as part of the pytest invocation on the same line). Its body
        lines are therefore kept as separate output lines instead of being joined.
    """
    lines = [ln for ln in job_text.splitlines() if not ln.strip().startswith("#")]

    joined: list[str] = []
    buf = ""
    for ln in lines:
        stripped = ln.rstrip()
        if stripped.endswith("\\"):
            buf += stripped[:-1] + " "
            continue
        joined.append(buf + ln)
        buf = ""
    if buf:
        joined.append(buf)

    out: list[str] = []
    i = 0
    while i < len(joined):
        line = joined[i]
        m = _RUN_HEADER_RE.match(line)
        if not m:
            out.append(line)
            i += 1
            continue
        indent, indicator, inline = m.groups()
        if indicator is None and inline.strip():
            out.append(line)  # single-line `run: <command>` (dash prefix or not)
            i += 1
            continue
        base_indent = len(indent)  # the `-` column when present, else `run:`'s own column
        body: list[str] = []
        j = i + 1
        while j < len(joined):
            nxt = joined[j]
            if nxt.strip() == "":
                j += 1
                continue
            if len(nxt) - len(nxt.lstrip(" \t")) <= base_indent:
                break
            body.append(nxt.strip())
            j += 1
        if indicator is not None and indicator.startswith(">"):
            out.append(f"{indent}run: " + " ".join(body))  # folded: one logical command
        else:
            out.append(f"{indent}run:")  # literal (or a bare `run:` with a body): keep separate
            out.extend(body)
        i = j
    return "\n".join(out)


_STOP_TOKEN_RE = re.compile(r"&&|;|\||#")
_ARG_EXCLUDING_FLAGS = ("--ignore", "--deselect")


def _pytest_invocation_segments(line: str) -> list[str]:
    """Every `python -m pytest ...` invocation within ONE line, each bounded at the next
    invocation on the same line, a shell control operator (`&&`, `;`, `|`), an inline `#`
    comment, or end of line — never at a later, unrelated LINE (a different step's `run:`,
    `name:`, or `with:` value). `_flatten_run_blocks` guarantees one step (or, inside a literal
    block, one original statement) per line, so bounding to the line is bounding to the step.
    """
    segments: list[str] = []
    pos = 0
    while True:
        m = re.search(r"python -m pytest\b", line[pos:])
        if not m:
            break
        start = pos + m.start()
        after = pos + m.end()
        stop = _STOP_TOKEN_RE.search(line, after)
        end = stop.start() if stop else len(line)
        segments.append(line[start:end])
        pos = end
    return segments


def _pytest_file_args(segment: str) -> set[str]:
    """`.py` file args in one invocation segment. `path.py::node_id` normalises to `path.py`;
    the argument immediately after `--ignore`/`--deselect` (bare or `=`-joined) is excluded —
    it names a file pytest is told NOT to run, not one that is listed."""
    files: set[str] = set()
    skip_next = False
    for tok in segment.split():
        if skip_next:
            skip_next = False
            continue
        if tok in _ARG_EXCLUDING_FLAGS:
            skip_next = True
            continue
        if any(tok.startswith(f"{flag}=") for flag in _ARG_EXCLUDING_FLAGS):
            continue
        path = tok.split("::", 1)[0]
        if path.endswith(".py"):
            files.add(path)
    return files


def backend_db_pytest_files(ci_text: str) -> set[str]:
    """Union of every `python -m pytest ...` invocation's `.py` file args in the backend-db job.

    Each invocation is scanned within its own line/segment only (see `_pytest_invocation_segments`)
    — a `.py`-looking token anywhere else in the job (a later step's command, `name:`, or `with:`
    value) is never mistaken for a listed file.
    """
    job = _flatten_run_blocks(_backend_db_job_text(ci_text))
    files: set[str] = set()
    saw_invocation = False
    for line in job.splitlines():
        for segment in _pytest_invocation_segments(line):
            saw_invocation = True
            files |= _pytest_file_args(segment)
    assert saw_invocation, (
        "backend-db job has no `python -m pytest ...` invocation on any run: line — the parse "
        "is broken, and every assertion in this module would otherwise pass vacuously against "
        "an empty set."
    )
    return files


def _live_db_files_on_disk() -> set[str]:
    found: set[str] = set()
    for p in sorted((REPO / "tests").rglob("*.py")):
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            raise AssertionError(f"{p}: cannot read as utf-8 ({exc})") from exc
        try:
            live = _is_live_db_test_source(text)
        except SyntaxError as exc:
            raise AssertionError(f"{p}: failed to parse as Python ({exc})") from exc
        if live:
            found.add(p.relative_to(REPO).as_posix())
    return found


# ── non-vacuity: a broken parser must fail LOUD, not pass empty ───────────────


def test_the_backend_db_job_is_found_and_its_file_list_is_non_empty():
    files = backend_db_pytest_files(CI.read_text(encoding="utf-8"))
    assert files, "parsed an empty pytest file list from ci.yml's backend-db job"
    assert "tests/backend/test_rls_live.py" in files, f"parse looks broken — got {sorted(files)}"


def test_the_live_db_scan_finds_the_known_files():
    found = _live_db_files_on_disk()
    assert found, "found no dbtest-marker / live-fixture test files at all — the scan is broken"
    assert "tests/backend/test_rls_live.py" in found, f"scan looks broken — got {sorted(found)}"


def test_the_marker_detection_handles_real_world_marker_shapes():
    # forms a naive regex already handled
    assert uses_dbtest_marker("@pytest.mark.dbtest\ndef test_x(): ...\n")
    assert uses_dbtest_marker("pytestmark = pytest.mark.dbtest\n")
    assert uses_dbtest_marker("pytestmark = [pytest.mark.dbtest]\n")
    assert not uses_dbtest_marker("# mentions dbtest in a comment, applies no marker\n")

    # dbtest not first in a pytestmark list
    assert uses_dbtest_marker("pytestmark = [pytest.mark.asyncio, pytest.mark.dbtest]\n")

    # multi-line pytestmark list, dbtest not first
    assert uses_dbtest_marker(
        "pytestmark = [\n    pytest.mark.asyncio,\n    pytest.mark.dbtest,\n]\n"
    )

    # tuple form
    assert uses_dbtest_marker("pytestmark = (pytest.mark.dbtest,)\n")

    # `from pytest import mark` + a bare-`mark` decorator. Built via `"\n".join(...)` rather
    # than one escaped-newline literal, so the source bytes never place a bare word character
    # directly against an `@` sign — a shape the repo's PII lint reads as an email address.
    assert uses_dbtest_marker(
        "\n".join(["from pytest import mark", "", "", "@mark.dbtest", "def test_x(): ..."]) + "\n"
    )

    # `marks=` on pytest.param
    assert uses_dbtest_marker(
        "import pytest\n"
        "@pytest.mark.parametrize('x', [pytest.param(1, marks=pytest.mark.dbtest)])\n"
        "def test_x(x): ...\n"
    )

    # a docstring/comment MENTION must not count — the old text-based regex false-positived here
    assert not uses_dbtest_marker(
        '"""\nSome docs mention pytestmark = pytest.mark.dbtest as prose, never applied.\n"""\n'
        "def test_x(): ...\n"
    )


def test_uses_live_db_fixture_detects_fixture_params_without_a_marker():
    assert uses_live_db_fixture("def test_x(live_db): ...\n")
    assert uses_live_db_fixture("async def test_y(clean_db): ...\n")
    assert not uses_live_db_fixture("def test_z(tmp_path): ...\n")
    # a marker-only file with no fixture param must not trip the fixture check
    assert not uses_live_db_fixture("@pytest.mark.dbtest\ndef test_x(): ...\n")
    # a FIXTURE definition depending on another fixture of the same name is not a test —
    # this is conftest.py's own `def clean_db(live_db): ...` shape. Built via `"\n".join(...)`
    # for the same PII-lint reason as the `@mark.dbtest` case above.
    assert not uses_live_db_fixture(
        "\n".join(["import pytest", "", "", "@pytest.fixture", "def clean_db(live_db): ..."]) + "\n"
    )


def test_usefixtures_with_a_live_db_name_counts_as_a_live_db_file():
    assert uses_live_db_fixture("@pytest.mark.usefixtures('clean_db')\ndef test_x(): ...\n")
    assert uses_live_db_fixture("pytestmark = pytest.mark.usefixtures('live_db')\n")
    # a different fixture name must not trip it
    assert not uses_live_db_fixture("@pytest.mark.usefixtures('tmp_path')\ndef test_x(): ...\n")


def test_backend_db_pytest_files_handles_a_folded_multiline_run_block_and_ignores_comments():
    ci_text = (
        "jobs:\n"
        "  backend-db:\n"
        "    steps:\n"
        "      # a comment mentioning python -m pytest must never count as an invocation\n"
        "      - name: pytest (dbtest tier)\n"
        "        run: >-\n"
        "          python -m pytest\n"
        "          tests/backend/test_a.py\n"
        "          tests/backend/test_b.py::test_one\n"
        "          -q\n"
        "  actionlint:\n"
        "    steps: []\n"
    )
    assert backend_db_pytest_files(ci_text) == {
        "tests/backend/test_a.py",
        "tests/backend/test_b.py",
    }


def test_backend_db_pytest_files_unions_multiple_invocations_in_the_same_job():
    ci_text = (
        "jobs:\n"
        "  backend-db:\n"
        "    steps:\n"
        "      - name: first tier\n"
        "        run: python -m pytest tests/backend/test_a.py -q\n"
        "      - name: second tier\n"
        "        run: python -m pytest tests/backend/test_b.py -q\n"
        "  actionlint:\n"
        "    steps: []\n"
    )
    assert backend_db_pytest_files(ci_text) == {
        "tests/backend/test_a.py",
        "tests/backend/test_b.py",
    }


# ── TDD-red probes (code review, second pass): the span must not over-reach ──────
#
# The prior fix unioned every `python -m pytest ...` invocation, but each invocation's
# span ran to the NEXT invocation (or end of job) — so a `.py`-looking token in LATER,
# unrelated job text (a different step's `run:`, `with:`, or `name:`) was wrongly counted
# as "listed", a silent false negative in what is supposed to be a §R18-discriminating
# check. Probes A/B/H/I below reproduce that; each must report ONLY test_a.py.


def test_probe_a_a_later_unrelated_run_line_does_not_leak_its_py_looking_arg():
    ci_text = (
        "jobs:\n"
        "  backend-db:\n"
        "    steps:\n"
        "      - name: pytest (dbtest tier)\n"
        "        run: python -m pytest tests/backend/test_a.py -q\n"
        "      - name: dump something\n"
        "        run: python scripts/dump.py tests/backend/test_b_live.py\n"
        "  actionlint:\n"
        "    steps: []\n"
    )
    assert backend_db_pytest_files(ci_text) == {"tests/backend/test_a.py"}


def test_probe_b_a_second_command_in_a_literal_run_block_does_not_leak():
    ci_text = (
        "jobs:\n"
        "  backend-db:\n"
        "    steps:\n"
        "      - name: pytest (dbtest tier)\n"
        "        run: |\n"
        "          python -m pytest tests/backend/test_a.py -q\n"
        "          cat tests/backend/test_b_live.py\n"
        "  actionlint:\n"
        "    steps: []\n"
    )
    assert backend_db_pytest_files(ci_text) == {"tests/backend/test_a.py"}


def test_probe_h_a_later_with_path_value_does_not_leak():
    ci_text = (
        "jobs:\n"
        "  backend-db:\n"
        "    steps:\n"
        "      - name: pytest (dbtest tier)\n"
        "        run: python -m pytest tests/backend/test_a.py -q\n"
        "      - name: upload artifact\n"
        "        uses: actions/upload-artifact@v4\n"
        "        with:\n"
        "          path: tests/backend/test_b_live.py\n"
        "  actionlint:\n"
        "    steps: []\n"
    )
    assert backend_db_pytest_files(ci_text) == {"tests/backend/test_a.py"}


def test_probe_i_a_later_step_name_mentioning_the_path_does_not_leak():
    ci_text = (
        "jobs:\n"
        "  backend-db:\n"
        "    steps:\n"
        "      - name: pytest (dbtest tier)\n"
        "        run: python -m pytest tests/backend/test_a.py -q\n"
        "      - name: reference tests/backend/test_b_live.py in a log line\n"
        "        run: echo done\n"
        "  actionlint:\n"
        "    steps: []\n"
    )
    assert backend_db_pytest_files(ci_text) == {"tests/backend/test_a.py"}


def test_a_list_item_folded_run_block_is_still_recognized():
    """`- run: >-` (the dash-prefixed list-item form of a folded scalar) must still fold its
    body onto one line — `header_re` needs to accept the leading `- ` too, using the dash's
    own column as the block's base indent."""
    ci_text = (
        "jobs:\n"
        "  backend-db:\n"
        "    steps:\n"
        "      - run: >-\n"
        "          python -m pytest\n"
        "          tests/backend/test_a.py\n"
        "          -q\n"
        "  actionlint:\n"
        "    steps: []\n"
    )
    assert backend_db_pytest_files(ci_text) == {"tests/backend/test_a.py"}


def test_a_chained_command_on_the_same_line_does_not_leak():
    ci_text = (
        "jobs:\n"
        "  backend-db:\n"
        "    steps:\n"
        "      - name: pytest (dbtest tier)\n"
        "        run: python -m pytest tests/backend/test_a.py -q"
        " && echo tests/backend/test_d.py\n"
        "  actionlint:\n"
        "    steps: []\n"
    )
    assert backend_db_pytest_files(ci_text) == {"tests/backend/test_a.py"}


def test_an_inline_comment_after_the_invocation_does_not_leak():
    ci_text = (
        "jobs:\n"
        "  backend-db:\n"
        "    steps:\n"
        "      - name: pytest (dbtest tier)\n"
        "        run: python -m pytest tests/backend/test_a.py -q"
        " # touches tests/backend/test_e.py\n"
        "  actionlint:\n"
        "    steps: []\n"
    )
    assert backend_db_pytest_files(ci_text) == {"tests/backend/test_a.py"}


def test_an_ignore_or_deselect_argument_is_not_counted_as_listed():
    ci_text = (
        "jobs:\n"
        "  backend-db:\n"
        "    steps:\n"
        "      - name: pytest (dbtest tier)\n"
        "        run: python -m pytest tests/backend/test_a.py"
        " --ignore tests/backend/test_c.py --deselect=tests/backend/test_f.py::test_x -q\n"
        "  actionlint:\n"
        "    steps: []\n"
    )
    assert backend_db_pytest_files(ci_text) == {"tests/backend/test_a.py"}


# ── the actual contract ─────────────────────────────────────────────────────────


def test_every_live_db_test_file_runs_in_the_backend_db_ci_job():
    marked = _live_db_files_on_disk()
    listed = backend_db_pytest_files(CI.read_text(encoding="utf-8"))
    missing = marked - listed
    assert not missing, (
        f"{sorted(missing)} apply @pytest.mark.dbtest (directly or via a module pytestmark) or "
        "take the live_db/clean_db fixture, but are absent from ci.yml's backend-db job pytest "
        "file list. GTM_TEST_PG_ADMIN_DSN is only set there, so these tests SKIP everywhere "
        "else — add the file(s) to the job's `python -m pytest ...` list (see the dev docs for "
        "the local live-DB recipe)."
    )
