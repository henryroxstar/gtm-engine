"""MM9 (first layer): a tenant run's file tools and read-only shell are confined to its own workspace.

On the multi-tenant backend every workspace's tree sits in the SAME container, under the same
``data/workspaces/``, and a run's cwd is the shared repo checkout. The brain's ``Read`` / ``Grep`` /
``Glob`` / ``LS`` / ``Write`` / ``Edit`` and its shell commands were not asked WHERE — so one tenant's
run (or an instruction smuggled into untrusted research text, §R5) could read or overwrite another's
files. With a :class:`~agent.path_confinement.Confinement` the policy decides by where a path RESOLVES
(symlinks followed), not by how it is spelled.

Every refusal below has a positive twin on the same tool, so a refusal can't be a policy that denies
everything. The VPS posture (no confinement) is pinned by ``test_permissions_golden.py``.

What this does NOT do, stated where it can be found: the shell is a denylist, so a run that WRITES a
script into its own tree and runs ``python script.py`` is not stopped by reading its command line — that
is what the second layer (an OS-level boundary per run) is for.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from agent.path_confinement import Confinement
from agent.permissions import classify_tool, make_headless_can_use_tool

WS_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
WS_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Two workspaces and a shared repo checkout, on real disk."""
    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(tmp_path / "data" / "workspaces"))
    repo = tmp_path / "repo"
    for d in ("plugin/skills/x", "packs", "schemas", "gtm_core", "tools"):
        (repo / d).mkdir(parents=True)
    (repo / "plugin/skills/x/SKILL.md").write_text("skill")
    (repo / "gtm_core/secret_logic.py").write_text("x")
    ws = {}
    for wid in (WS_A, WS_B):
        root = tmp_path / "data" / "workspaces" / wid
        (root / "content" / "acme").mkdir(parents=True)
        (root / "profiles" / "acme").mkdir(parents=True)
        (root / "content" / "acme" / "notes.md").write_text(f"{wid} notes")
        ws[wid] = root
    a = ws[WS_A]
    # a symlink INSIDE A that points at B's content, and one that points at A's own file
    os.symlink(ws[WS_B] / "content", a / "content" / "acme" / "link-to-b")
    os.symlink(a / "content" / "acme" / "notes.md", a / "content" / "acme" / "link-to-self")
    confine = Confinement.for_workspace(WS_A, repo)
    return type("W", (), {"repo": repo, "a": a, "b": ws[WS_B], "confine": confine})


def _decide(w, tool, inp):
    return classify_tool(tool, inp, confine=w.confine)


# ── the builder: what a workspace may touch, and nothing else ──────────────────


def test_a_workspace_may_read_its_own_trees_and_the_repos_read_only_dirs_and_write_only_its_content(
    world,
):
    c = world.confine
    resolved = lambda paths: {Path(p).resolve() for p in paths}  # noqa: E731
    assert resolved(c.write_roots) == {(world.a / "content").resolve()}
    assert {(world.a / "content").resolve(), (world.a / "profiles").resolve()} <= resolved(
        c.read_roots
    )
    assert {(world.repo / d).resolve() for d in ("plugin", "packs", "schemas")} <= resolved(
        c.read_roots
    )
    assert not resolved(c.read_roots) & {(world.b / "content").resolve(), world.repo.resolve()}
    assert c.workspace_id == WS_A


# ── reading: each tool, own vs another workspace ───────────────────────────────

READ_SHAPES = [
    ("Read", "file_path"),
    ("NotebookRead", "notebook_path"),
    ("LS", "path"),
    ("Grep", "path"),
    ("Glob", "path"),
]


@pytest.mark.parametrize(("tool", "key"), READ_SHAPES)
def test_a_read_of_its_own_tree_is_allowed_and_of_anothers_is_refused(world, tool, key):
    extra = {"pattern": "x"} if tool in ("Grep", "Glob") else {}
    own = str(world.a / "content" / "acme" / "notes.md")
    other = str(world.b / "content" / "acme" / "notes.md")
    assert _decide(world, tool, {key: own, **extra}) == "allow"  # the positive control
    assert _decide(world, tool, {key: other, **extra}) == "deny"


@pytest.mark.parametrize(("tool", "key"), READ_SHAPES)
@pytest.mark.parametrize(
    "spelling",
    [
        "{a}/content/../../{b_name}/content/acme/notes.md",  # .. up and across
        "/etc/passwd",  # absolute, anywhere else
        "~/notes.md",  # the home directory
        "{a}/content/acme/link-to-b/acme/notes.md",  # a symlink INSIDE the workspace pointing out
    ],
)
def test_an_escape_is_refused_whatever_its_spelling(world, tool, key, spelling):
    extra = {"pattern": "x"} if tool in ("Grep", "Glob") else {}
    path = spelling.format(a=world.a, b_name=WS_B)
    assert _decide(world, tool, {key: path, **extra}) == "deny"


def test_a_symlink_inside_the_workspace_to_its_own_file_is_still_fine(world):
    assert (
        _decide(world, "Read", {"file_path": str(world.a / "content/acme/link-to-self")}) == "allow"
    )


def test_a_relative_path_resolves_against_the_runs_cwd_the_shared_repo(world, tmp_path):
    inside = f"../data/workspaces/{WS_A}/content/acme/notes.md"
    outside = f"../data/workspaces/{WS_B}/content/acme/notes.md"
    c = Confinement.for_workspace(WS_A, world.repo)
    assert classify_tool("Read", {"file_path": inside}, confine=c) == "allow"
    assert classify_tool("Read", {"file_path": outside}, confine=c) == "deny"
    assert classify_tool("Read", {"file_path": "notes.md"}, confine=c) == "deny"  # cwd is the repo


def test_the_repos_read_only_dirs_are_readable_and_the_rest_of_the_repo_is_not(world):
    assert (
        _decide(world, "Read", {"file_path": str(world.repo / "plugin/skills/x/SKILL.md")})
        == "allow"
    )
    assert (
        _decide(world, "Read", {"file_path": str(world.repo / "gtm_core/secret_logic.py")})
        == "deny"
    )
    assert _decide(world, "Read", {"file_path": str(world.repo / "tools/x.py")}) == "deny"


@pytest.mark.parametrize("tool", ["Grep", "Glob", "LS"])
def test_a_search_with_no_path_would_search_the_whole_repo_so_it_is_refused(world, tool):
    inp = {"pattern": "x"} if tool != "LS" else {}
    assert _decide(world, tool, inp) == "deny"


@pytest.mark.parametrize(
    ("tool", "key", "pattern"),
    [
        ("Glob", "pattern", "../*/content/*.md"),
        ("Glob", "pattern", "/etc/*"),
        ("Grep", "glob", "../*"),
        ("Grep", "glob", "/etc/p*"),
    ],
)
def test_a_pattern_that_walks_out_of_the_path_is_refused(world, tool, key, pattern):
    inp = {
        "path": str(world.a / "content"),
        key: pattern,
        "pattern": pattern if tool == "Glob" else "x",
    }
    assert _decide(world, tool, inp) == "deny"


def test_a_pattern_that_stays_inside_the_path_is_allowed(world):
    own = str(world.a / "content")
    assert _decide(world, "Glob", {"path": own, "pattern": "**/*.md"}) == "allow"
    assert _decide(world, "Grep", {"path": own, "pattern": "x", "glob": "*.md"}) == "allow"


# ── writing: only the content tree ─────────────────────────────────────────────

WRITE_SHAPES = [
    ("Write", "file_path"),
    ("Edit", "file_path"),
    ("MultiEdit", "file_path"),
    ("NotebookEdit", "notebook_path"),
]


@pytest.mark.parametrize(("tool", "key"), WRITE_SHAPES)
def test_a_write_is_allowed_only_under_its_own_content_tree(world, tool, key):
    ok = str(world.a / "content" / "acme" / "new.md")
    assert _decide(world, tool, {key: ok}) == "allow"  # the positive control
    for bad in (
        str(world.b / "content" / "acme" / "new.md"),  # another workspace
        str(
            world.a / "profiles" / "acme" / "PROFILE.md"
        ),  # its own profile: readable, never writable
        str(world.repo / "plugin" / "skills" / "x" / "SKILL.md"),  # a repo dir
        str(world.a / "content" / "acme" / "link-to-b" / "acme" / "new.md"),  # through a symlink
        "/tmp/new.md",
        "new.md",  # relative -> the repo
    ):
        assert _decide(world, tool, {key: bad}) == "deny", bad


# ── the shell: read-only programs and redirects ────────────────────────────────


def _bash(world, command):
    return _decide(world, "Bash", {"command": command})


def test_a_shell_read_of_its_own_tree_is_allowed_and_of_anothers_is_refused(world):
    own, other = world.a / "content/acme/notes.md", world.b / "content/acme/notes.md"
    assert _bash(world, f"cat {own}") == "allow"
    assert _bash(world, f"cat {other}") == "deny"
    assert _bash(world, "cat /etc/passwd") == "deny"
    assert _bash(world, f"head -n 3 {own}") == "allow"  # `3` is a count, not a path
    assert _bash(world, f"head -n 3 {world.a}/content/../../{WS_B}/content/acme/notes.md") == "deny"
    assert _bash(world, f"wc -l {world.a}/content/acme/*.md") == "allow"  # a glob that stays inside
    assert _bash(world, f"wc -l {world.b}/content/acme/*.md") == "deny"
    assert _bash(world, f"cat {world.a}/content/../*") == "deny"


def test_a_listing_or_recursive_search_with_no_path_would_cover_the_whole_repo_so_it_is_refused(
    world,
):
    own = world.a / "content"
    assert _bash(world, f"ls {own}") == "allow"
    assert _bash(world, "ls") == "deny"
    assert _bash(world, "ls -la") == "deny"
    assert _bash(world, "find . -name '*.md'") == "deny"
    assert _bash(world, f"find {own} -name '*.md'") == "allow"
    assert _bash(world, f"grep -rn foo {own}") == "allow"
    assert _bash(world, "grep -rn foo") == "deny"
    assert _bash(world, "grep -rn foo .") == "deny"


def test_a_pattern_is_not_a_path_and_a_filter_reads_stdin(world):
    own = world.a / "content/acme/notes.md"
    assert _bash(world, f"grep foo {own}") == "allow"
    assert _bash(world, f"grep foo {world.b}/content/acme/notes.md") == "deny"
    assert _bash(world, "grep foo") == "allow"  # stdin
    assert _bash(world, f"cat {own} | grep foo | head -n 2") == "allow"
    assert _bash(world, f"cat {own} | grep foo {world.b}/content/acme/notes.md") == "deny"


@pytest.mark.parametrize(
    "command",
    ["cat $SECRET_PATH", "cat $(echo /etc/passwd)", "cat `echo /etc/passwd`", "cat ${HOME}/x"],
)
def test_a_path_the_shell_would_compute_cannot_be_checked_so_it_is_refused(world, command):
    assert _bash(world, command) == "deny"


@pytest.mark.parametrize("prefix", ["", "env "])
def test_a_path_in_a_variable_assignment_before_a_command_is_a_path_like_any_other(world, prefix):
    """``GTM_CONTENT_ROOT=/elsewhere python -m gtm_core.x`` makes every gtm_core tool read and write under
    /elsewhere. The assignment is not an operand of the program, so it used to be dropped unchecked."""
    prog = "python -m gtm_core.ledger_cli denials"
    own, other = world.a / "content", world.b / "content"
    assert _bash(world, f"{prefix}GTM_CONTENT_ROOT={own} {prog}") == "allow"
    assert _bash(world, f"{prefix}GTM_CONTENT_ROOT={other} {prog}") == "deny"
    assert _bash(world, f"{prefix}GTM_PROFILES_ROOT=/etc {prog}") == "deny"
    assert _bash(world, f"{prefix}GTM_CONTENT_ROOT={own}/../../{WS_B}/content {prog}") == "deny"
    assert _bash(world, f"{prefix}A=1 GTM_CONTENT_ROOT={other} {prog}") == "deny"
    # not a path: a flag, a number, a word
    assert _bash(world, f"{prefix}PYTHONUNBUFFERED=1 LANG=C {prog}") == "allow"


def test_a_redirect_is_a_write_and_only_the_content_tree_may_be_written(world):
    own = world.a / "content/acme/out.txt"
    assert _bash(world, f"echo x > {own}") == "allow"
    assert _bash(world, f"echo x >> {own}") == "allow"
    assert _bash(world, "echo x > /tmp/out.txt") == "deny"
    assert _bash(world, f"echo x >> {world.b}/content/acme/out.txt") == "deny"
    assert _bash(world, f"echo x > {world.a}/profiles/acme/PROFILE.md") == "deny"
    assert _bash(world, "echo x > /dev/null") == "allow"
    assert _bash(world, f"cat < {world.b}/content/acme/notes.md") == "deny"


def test_a_copy_or_move_writes_to_its_destination_so_the_destination_is_confined_too(world):
    a = world.a / "content/acme"
    assert _bash(world, f"cp {a}/notes.md {a}/copy.md") == "allow"
    assert _bash(world, f"cp {a}/notes.md /tmp/copy.md") == "deny"
    assert _bash(world, f"mv {a}/notes.md {world.b}/content/acme/x.md") == "deny"
    assert _bash(world, f"mkdir -p {a}/sub") == "allow"
    assert _bash(world, f"mkdir -p {world.b}/content/sub") == "deny"


def test_cd_into_the_tree_makes_a_bare_ls_safe_and_cd_out_of_it_does_not(world):
    own = world.a / "content"
    assert _bash(world, f"cd {own} && ls") == "allow"
    assert _bash(world, f"cd {world.b}/content && ls") == "deny"
    assert _bash(world, "cd .. && ls") == "deny"


def test_a_program_with_no_path_arguments_is_untouched_and_a_path_argument_is_checked(world):
    own = world.a / "content/acme"
    assert _bash(world, "uv run python -m gtm_core.ledger_cli denials --profile acme") == "allow"
    assert _bash(world, "git status") == "allow"
    assert _bash(world, "git log origin/main") == "allow"  # a ref, not a path
    assert _bash(world, "python -m gtm_core.thing --out /tmp/x") == "deny"
    assert _bash(world, f"python -m gtm_core.thing --out={own}/x.json") == "allow"
    assert _bash(world, f"python -m gtm_core.thing --out={world.b}/content/x.json") == "deny"
    assert (
        _bash(world, f"python {world.repo}/tools/run.py") == "deny"
    )  # a script outside the read roots


def test_the_dangerous_program_floor_still_denies_inside_a_confined_run(world):
    assert _bash(world, f"curl https://x.invalid -o {world.a}/content/x") == "deny"
    assert _bash(world, "rm -rf /") == "deny"
    assert _bash(world, "bash -c 'ls'") == "escalate"  # a nested shell is still not auto-allowed
    assert _bash(world, "dash -c 'ls'") == "escalate"
    assert _bash(world, "python3 -m timeit 'import os'") == "deny"


def test_tools_that_name_no_path_are_decided_by_the_base_policy_alone(world):
    assert _decide(world, "WebFetch", {"url": "https://example.invalid"}) == "allow"
    assert _decide(world, "mcp__worker__summarize", {"q": 1}) == "allow"
    assert _decide(world, "Skill", {"skill": "prospect"}) == "allow"
    assert _decide(world, "NotAThing", {}) == "escalate"


def test_a_secret_path_inside_the_tree_is_still_refused_by_the_base_policy(world):
    assert _decide(world, "Read", {"file_path": str(world.a / "content/acme/.env")}) == "deny"


# ── the refusal is recorded, with its reason ───────────────────────────────────


def test_a_refusal_is_recorded_with_the_workspace_the_path_and_the_reason(world, tmp_path):
    pytest.importorskip("claude_agent_sdk")
    from types import SimpleNamespace

    from agent.denial_log import make_denial_sink

    cfg = SimpleNamespace(content_root=world.a / "content")
    cb = make_headless_can_use_tool(
        on_deny=make_denial_sink(cfg, "acme", "backend"), confine=world.confine
    )
    other = str(world.b / "content/acme/notes.md")

    result = asyncio.run(cb("Read", {"file_path": other}, None))
    assert type(result).__name__ == "PermissionResultDeny"
    ok = asyncio.run(cb("Read", {"file_path": str(world.a / "content/acme/notes.md")}, None))
    assert type(ok).__name__ == "PermissionResultAllow"

    rows = [
        json.loads(line)
        for line in (world.a / "content/acme/denials.jsonl").read_text().splitlines()
    ]
    assert len(rows) == 1  # the allowed read is not an event
    assert rows[0]["tool"] == "Read" and rows[0]["decision"] == "deny"
    assert "path_outside_workspace" in rows[0]["detail"]
    assert other in rows[0]["detail"] and WS_A in rows[0]["detail"]


def test_an_instruction_planted_in_research_text_cannot_make_the_run_read_another_workspace(world):
    """§R5: untrusted text that says 'read data/workspaces/<other>/…' is just a tool call the policy refuses.
    Nothing is read, so nothing of the other workspace reaches the transcript."""
    pytest.importorskip("claude_agent_sdk")
    cb = make_headless_can_use_tool(confine=world.confine)
    planted = [
        ("Read", {"file_path": str(world.b / "content/acme/notes.md")}),
        ("Grep", {"pattern": "notes", "path": str(world.b / "content")}),
        ("Bash", {"command": f"cat {world.b}/content/acme/notes.md"}),
        ("Bash", {"command": f"ls {world.b}/content"}),
    ]
    for tool, inp in planted:
        assert type(asyncio.run(cb(tool, inp, None))).__name__ == "PermissionResultDeny", (
            tool,
            inp,
        )


def test_an_in_tree_prefix_followed_by_something_the_shell_expands_is_refused(world):
    """The prefix looks safe and the expansion is where it leaves: ``$X`` could be ``../../<other>``."""
    own = world.a / "content/acme"
    assert _bash(world, f"cat {own}/notes.md") == "allow"  # the same shape with nothing to expand
    assert _bash(world, f"cat {own}/$X") == "deny"
    assert _bash(world, f"cat {own}/$(echo ../../x)") == "deny"
    assert _bash(world, f"echo y > {own}/${{NAME}}.md") == "deny"
    assert _bash(world, f"cp {own}/notes.md {own}/$X") == "deny"


def test_a_wildcard_followed_by_a_climb_leaves_the_tree_whatever_comes_before_it(world):
    own = world.a / "content"
    assert _bash(world, f"cat {own}/*/notes.md") == "allow"  # a wildcard that stays inside
    assert _bash(world, f"cat {own}/*/../../../{WS_B}/content/acme/notes.md") == "deny"
    assert _bash(world, f"cat {own}/acme/*/../../x") == "deny"


# ── gaps an automated mutation sweep found (each line below killed a surviving mutant) ──


def test_a_path_that_cannot_be_resolved_is_refused_not_crashed_on(world):
    assert _decide(world, "Read", {"file_path": "a\x00b"}) == "deny"
    assert _decide(world, "Write", {"file_path": f"{world.a}/content/acme/x\x00.md"}) == "deny"


def test_a_read_that_names_no_path_is_left_to_the_tool_to_refuse(world):
    assert _decide(world, "Read", {}) == "allow"
    assert _decide(world, "NotebookRead", {}) == "allow"


def test_a_pattern_that_is_not_a_string_does_not_crash_the_policy(world):
    own = str(world.a / "content")
    assert _decide(world, "Glob", {"path": own, "pattern": 5}) == "allow"
    assert _decide(world, "Grep", {"path": own, "pattern": "x", "glob": ["*.md"]}) == "allow"


def test_a_program_we_do_not_model_is_still_stopped_by_a_climb_in_any_operand(world):
    assert _bash(world, "python tools.py sub/../../x") == "deny"  # a climb in the middle
    assert _bash(world, "python tools.py sub/..") == "deny"  # a climb at the end
    assert (
        _bash(world, "python tools.py sub/file") == "deny"
    )  # it names a place under the shared cwd


def test_an_input_redirect_reads_from_its_own_tree_in_either_spelling(world):
    own = world.a / "content/acme/notes.md"
    assert _bash(world, f"cat < {own}") == "allow"
    assert _bash(world, f"cat <{own}") == "allow"
    assert _bash(world, f"cat <{world.b}/content/acme/notes.md") == "deny"


def test_a_lone_dash_is_stdin_not_a_path(world):
    assert _bash(world, "echo hi | cat -") == "allow"
    assert (
        _bash(world, f"cat - {world.b}/content/acme/notes.md") == "deny"
    )  # the real operand is still checked


def test_an_option_value_is_only_a_place_when_it_looks_like_one(world):
    own = world.a / "content/acme"
    assert _bash(world, "python -m gtm_core.thing --profile=acme") == "allow"
    assert (
        _bash(world, f"python -m gtm_core.thing --template={world.repo}/plugin/skills/x/SKILL.md")
        == "allow"
    )
    assert (
        _bash(world, f"python -m gtm_core.thing --template={world.repo}/gtm_core/secret_logic.py")
        == "deny"
    )
    assert _bash(world, f"cp --target-directory={own} {own}/notes.md") == "allow"
    assert _bash(world, f"cp --target-directory=/tmp {own}/notes.md") == "deny"


@pytest.mark.parametrize("flag", ["-r", "-rn", "-R", "--recursive"])
def test_every_spelling_of_a_recursive_search_with_no_path_is_refused(world, flag):
    assert _bash(world, f"grep {flag} foo") == "deny"
    assert _bash(world, f"grep {flag} foo {world.a}/content") == "allow"


def test_a_command_that_runs_no_program_is_allowed(world):
    assert _bash(world, "X=1") == "allow"
    assert _bash(world, "X=1 Y=2") == "allow"


def test_a_path_the_filesystem_cannot_resolve_is_refused(world, monkeypatch):
    def broken(self, *_a, **_k):
        raise OSError("too many levels of symbolic links")

    monkeypatch.setattr(Path, "resolve", broken)
    assert _decide(world, "Read", {"file_path": str(world.a / "content/acme/notes.md")}) == "deny"


def test_the_letters_inside_a_long_option_are_not_a_recursive_flag(world):
    """`--regexp` and `--ignore-case` contain an `r`; neither makes a stdin filter recurse."""
    assert _bash(world, "echo x | grep --regexp=foo") == "allow"
    assert _bash(world, "echo x | grep --ignore-case foo") == "allow"
    assert _bash(world, "echo x | grep -i foo") == "allow"


# ── a second, adversarial review found these ways out (each was ALLOWED before the fix) ──


def _other(world):
    return f"{world.b}/content/acme/notes.md"


@pytest.mark.parametrize(
    "template",
    [
        "echo x>{other}",  # a redirect glued to its word
        "echo x >{other}",
        "echo x>>{other}",
        ">{other} echo x",  # a redirect BEFORE the command
        "cat<{other}",
        "cat <{other}",
        "echo x &>{other}",  # stdout+stderr
        "echo x 2>{other}",
        "echo x 1>{other}",
        "echo x 2>>{other}",
        "echo x >| {other}",
        "cat <<< {other}; cat < {other}",
    ],
)
def test_a_redirect_into_another_workspace_is_refused_however_it_is_spelled(world, template):
    assert _bash(world, template.format(other=_other(world))) == "deny"


@pytest.mark.parametrize(
    "template",
    [
        "echo x>{own}",
        ">{own} echo x",
        "cat<{own}",
        "echo x &>{own}",
        "echo x 2>{own}",
        "echo x >> {own}",
    ],
)
def test_the_same_redirect_forms_into_its_own_tree_are_allowed(world, template):
    assert _bash(world, template.format(own=world.a / "content/acme/out.txt")) == "allow"


@pytest.mark.parametrize(
    "command",
    [
        "ls 2>&1",
        "echo hi 2>&1",
        "echo hi >/dev/null 2>&1",
        "echo hi 1>&2",
        "echo hi &>/dev/null",
        "cat - 2>&1",
    ],
)
def test_the_usual_stream_plumbing_is_not_mistaken_for_a_path(world, command):
    own = world.a / "content"
    assert _bash(world, command.replace("ls", f"ls {own}")) == "allow"


def test_a_command_substitution_in_the_operand_of_a_file_program_is_refused(world):
    rel = f"data/workspaces/{WS_B}/content/acme/notes.md"
    assert _bash(world, f'cat "$(echo {rel})"') == "deny"
    assert _bash(world, f"cat `echo {rel}`") == "deny"
    assert _bash(world, f"ls $(echo {rel})") == "deny"
    # …but a substitution into a program with no path to check is not what is being refused
    assert _bash(world, 'echo "$(date +%F)"') == "allow"
    assert _bash(world, f'echo "$(cat {world.a}/content/acme/notes.md)"') == "allow"
    assert (
        _bash(world, f'echo "$(cat {_other(world)})"') == "deny"
    )  # the INNER command is still judged


@pytest.mark.parametrize(
    "template",
    [
        "env -C {b}/content cat acme/notes.md",
        "xargs -a {other} echo",
        "find {b}/content -exec cat {{}} \\;",
        "find {b}/content -name x -exec cat {{}} +",
        "nice -n 5 cat {other}",
        "timeout 5 cat {other}",
        "time cat {other}",
        "env cat {other}",
    ],
)
def test_a_wrapper_or_find_exec_cannot_smuggle_a_path_past_the_check(world, template):
    assert _bash(world, template.format(b=world.b, other=_other(world))) == "deny"


@pytest.mark.parametrize(
    "template",
    [
        "grep --regexp=. {other}",
        "grep -e. {other}",
        "grep -e foo {other}",
        "grep -f patterns {other}",
        "sed --expression=p {other}",
        "sed -n -e p {other}",
        "rg --files {b}/content",
        "rg -e x {other}",
        "sed -f {b}/content/script.sed {a}/content/acme/notes.md",
    ],
)
def test_a_pattern_option_does_not_hide_the_file_operand_that_follows_it(world, template):
    assert _bash(world, template.format(b=world.b, a=world.a, other=_other(world))) == "deny"


def test_the_same_pattern_options_over_its_own_file_are_allowed(world):
    own = world.a / "content/acme/notes.md"
    assert _bash(world, f"grep -e foo {own}") == "allow"
    assert _bash(world, f"grep --regexp=foo {own}") == "allow"
    assert _bash(world, f"sed -n -e p {own}") == "allow"
    assert _bash(world, f"grep -m 5 foo {own}") == "allow"


@pytest.mark.parametrize(
    "template",
    [
        "tar cf - {b}/content",
        "tar xf {other}",
        "zcat {other}",
        "unzip -p {other}",
        "perl -pe1 {other}",
        "python tools.py {b}/content/acme/notes.md",
        "python tools.py data/workspaces/{ws_b}/content/acme/notes.md",
    ],
)
def test_a_program_the_policy_does_not_model_is_still_stopped_by_a_path_operand(world, template):
    cmd = template.format(b=world.b, other=_other(world), ws_b=WS_B)
    assert _bash(world, cmd) == "deny"


def test_a_slash_in_an_operand_of_a_program_that_is_not_about_files_is_not_a_path(world):
    assert _bash(world, "git log origin/main") == "allow"
    assert _bash(world, "git diff main...feature/x") == "allow"
    assert _bash(world, 'echo "use A/B testing"') == "allow"
    assert _bash(world, "echo https://example.invalid/a/b") == "allow"
    assert _bash(world, "python -m gtm_core.thing --profile acme status") == "allow"


@pytest.mark.parametrize(
    "template",
    [
        "sed -i s/a/b/ {other}",
        "sed -i.bak s/a/b/ {other}",
        "sed --in-place s/a/b/ {other}",
        "sort -o {other} {a}/content/acme/notes.md",
        "sort --output={other} {a}/content/acme/notes.md",
        "cp -t {b}/content {a}/content/acme/notes.md",
        "find {b}/content -delete",
        "find {a}/content -fprint {other}",
        "unlink {other}",
        "shred {other}",
        "chmod 777 {other}",
        "truncate -s 0 {other}",
    ],
)
def test_a_form_that_writes_is_checked_against_where_a_run_may_write_not_only_read(world, template):
    assert _bash(world, template.format(b=world.b, a=world.a, other=_other(world))) == "deny"


def test_a_form_that_writes_into_a_read_only_repo_dir_or_its_own_profile_is_refused(world):
    own = world.a / "content/acme/notes.md"
    for target in (world.repo / "plugin/skills/x/SKILL.md", world.a / "profiles/acme/PROFILE.md"):
        assert _bash(world, f"sed -i s/a/b/ {target}") == "deny"
        assert _bash(world, f"cp {own} {target}") == "deny"
    assert _bash(world, f"sed -i s/a/b/ {own}") == "allow"  # its own content tree: fine
    assert _bash(world, f"sort -o {own} {own}") == "allow"


def test_monitor_cannot_be_used_to_run_a_shell_command_in_a_tenant_run(world):
    assert _decide(world, "Monitor", {"command": f"cat {_other(world)}"}) == "deny"
    assert _decide(world, "Monitor", {"command": "echo hi"}) == "deny"


@pytest.mark.parametrize("name", ["denials.jsonl", "history.jsonl", "costs.jsonl"])
def test_a_tenant_cannot_write_the_ledgers_that_audit_and_bound_it(world, name):
    ledger = world.a / "content/acme" / name
    assert _decide(world, "Write", {"file_path": str(ledger)}) == "deny"
    assert _decide(world, "Edit", {"file_path": str(ledger)}) == "deny"
    assert _bash(world, f"echo x > {ledger}") == "deny"
    assert _bash(world, f"echo x >> {ledger}") == "deny"
    assert _bash(world, f"sed -i s/a/b/ {ledger}") == "deny"
    assert (
        _decide(world, "Write", {"file_path": str(world.a / "content/acme/notes.md")}) == "allow"
    )  # twin
    assert (
        _decide(world, "Read", {"file_path": str(ledger)}) == "allow"
    )  # reading its own ledger is fine


def test_the_onboarding_spend_ledger_directory_is_not_writable_by_a_tenant(world):
    spend = world.a / "content/_system/costs.jsonl"
    assert _decide(world, "Write", {"file_path": str(spend)}) == "deny"
    assert (
        _decide(world, "Write", {"file_path": str(world.a / "content/_system/other.md")}) == "deny"
    )
    assert _bash(world, f"echo x > {world.a}/content/_system/x") == "deny"


def test_the_denial_detail_keeps_the_reason_and_workspace_even_when_the_path_is_long(
    world, tmp_path
):
    pytest.importorskip("claude_agent_sdk")
    from types import SimpleNamespace

    from agent.denial_log import make_denial_sink

    cfg = SimpleNamespace(content_root=world.a / "content")
    cb = make_headless_can_use_tool(
        on_deny=make_denial_sink(cfg, "acme", "backend"), confine=world.confine
    )
    long_path = str(world.b / "content" / ("d" * 400) / "x.md")
    asyncio.run(cb("Read", {"file_path": long_path}, None))
    row = json.loads((world.a / "content/acme/denials.jsonl").read_text().splitlines()[0])
    assert row["detail"].startswith("path_outside_workspace (workspace ")
    assert WS_A in row["detail"]  # the workspace survives the 300-character cut


def test_a_heredoc_body_is_data_not_commands_and_cannot_hide_the_command_after_it(world):
    own = world.a / "content/acme/x.md"
    body = "line with a/b and /usr/share and c/d\nanother ../ thing"
    assert _bash(world, f"cat > {own} <<'EOF'\n{body}\nEOF") == "allow"
    assert _bash(world, f"cat > {own} <<EOF\n{body}\nEOF") == "allow"
    assert _bash(world, f"cat > {own} <<-EOF\n{body}\nEOF") == "allow"
    # the delimiter ends the body: what follows is a command again, and is judged
    assert _bash(world, f"cat > {own} <<'EOF'\n{body}\nEOF\ncat {_other(world)}") == "deny"
    # a here-string is not a heredoc: nothing after it is swallowed
    assert _bash(world, f"cat <<< hello; cat {_other(world)}") == "deny"


def test_a_hash_in_the_middle_of_a_word_does_not_start_a_comment_that_hides_the_rest(world):
    own = world.a / "content/acme/notes.md"
    assert _bash(world, f"cat {own}#a {_other(world)}") == "deny"
    assert (
        _bash(world, f"cat {own} # {_other(world)}") == "deny"
    )  # hash with a space is a comment in bash
    # (and bash would treat the second as a comment, so refusing it is the conservative side)


# ── gaps the second round's own controls exposed ──────────────────────────────


def test_an_fd_number_before_a_redirect_is_not_the_destination_of_a_copy(world):
    a = world.a / "content/acme"
    assert _bash(world, f"cp {a}/notes.md {a}/copy.md 2>/dev/null") == "allow"
    assert _bash(world, f"cp {a}/notes.md {a}/copy.md 2>&1") == "allow"
    assert _bash(world, f"mkdir -p {a}/sub 2>/dev/null") == "allow"


def test_a_here_string_on_its_own_line_does_not_swallow_the_command_on_the_next(world):
    assert _bash(world, f"cat <<< hello\ncat {_other(world)}") == "deny"
    assert _bash(world, f"cat <<< hello\ncat {world.a}/content/acme/notes.md") == "allow"


@pytest.mark.parametrize(
    "command",
    [
        "xargs -a notes.txt echo",
        "xargs echo",
        "env -C acme cat notes.md",
        "nice -n 5 cat notes.md",
        "timeout -s KILL 5 cat notes.md",
    ],
)
def test_a_wrapper_option_that_changes_where_or_what_is_read_is_refused(world, command):
    assert _bash(world, command) == "deny"


def test_a_wrapper_with_no_options_runs_the_program_it_wraps_under_the_same_checks(world):
    own = world.a / "content/acme/notes.md"
    assert _bash(world, f"timeout 5 cat {own}") == "allow"
    assert _bash(world, f"env FOO=1 cat {own}") == "allow"
    assert _bash(world, f"time cat {own}") == "allow"
    assert _bash(world, f"timeout 5 cat {_other(world)}") == "deny"
    assert _bash(world, f"env FOO=1 cat {_other(world)}") == "deny"


def test_the_command_a_find_runs_is_checked_not_only_the_directories_it_walks(world):
    own = world.a / "content"
    assert _bash(world, f"find {own} -name x -exec cat {{}} \\;") == "allow"
    assert _bash(world, f"find {own} -exec cat {_other(world)} \\;") == "deny"
    assert _bash(world, f"find {own} -execdir cat {_other(world)} +") == "deny"


def test_a_find_that_deletes_or_prints_to_a_file_needs_somewhere_it_may_write(world):
    plugin = world.repo / "plugin"
    profile = world.a / "profiles/acme"
    assert _bash(world, f"find {plugin} -name x -delete") == "deny"  # readable, not writable
    assert _bash(world, f"find {profile} -delete") == "deny"
    assert _bash(world, f"find {world.a}/content -name x -delete") == "allow"
    assert _bash(world, f"find {world.a}/content -fprint {profile}/out.txt") == "deny"
    assert _bash(world, f"find {world.a}/content -fprint {world.a}/content/out.txt") == "allow"


def test_a_write_target_that_is_readable_but_not_writable_is_refused(world):
    """`profiles/` and the repo's read-only dirs are inside the READ roots, so a check that only asked
    'is it somewhere I may go' would let a run rewrite them."""
    src = world.a / "content/acme/notes.md"
    profile = world.a / "profiles/acme"
    assert _bash(world, f"sort -o {profile}/PROFILE.md {src}") == "deny"
    assert _bash(world, f"sort --output={profile}/PROFILE.md {src}") == "deny"
    assert _bash(world, f"cp -t {profile} {src}") == "deny"
    assert _bash(world, f"cp --target-directory={profile} {src}") == "deny"
    assert _bash(world, f"sort -o {world.a}/content/acme/sorted.md {src}") == "allow"
    assert _bash(world, f"cp -t {world.a}/content/acme {src}") == "allow"


# ── gaps the sweep over the rewritten shell checker found ────────────────────


def test_an_unbalanced_quote_or_unterminated_substitution_is_refused_not_crashed_on(world):
    assert _bash(world, 'cat "unterminated') == "deny"
    assert _bash(world, "echo `unterminated") in ("allow", "deny")  # must not raise
    assert _bash(world, "cat $(unterminated") in ("allow", "deny")


def test_what_counts_as_a_path_in_an_unmodelled_programs_operands(world):
    assert _bash(world, 'python x.py "use A/B testing"') == "allow"  # a sentence is not a path
    assert _bash(world, "python x.py https://example.invalid/a/b") == "allow"  # nor is a URL
    assert _bash(world, "python x.py ~") == "deny"  # the home directory is
    assert _bash(world, "python x.py -v") == "allow"  # nor is a flag
    assert _bash(world, "git diff -- a/../../x") == "deny"  # a climb in the middle…
    assert _bash(world, "git diff -- a/b/..") == "deny"  # …or at the end
    assert _bash(world, "git diff -- ../x") == "deny"
    assert _bash(world, "git log origin/main") == "allow"  # a ref is not a path


def test_a_glued_short_pattern_option_is_told_apart_from_a_glued_file_option(world):
    own = world.a / "content/acme/notes.md"
    assert (
        _bash(world, f"sed -es/a/b/ {own}") == "allow"
    )  # -e<script>: the script has slashes, it is not a file
    assert (
        _bash(world, f"sed -f{world.b}/content/s.sed {own}") == "deny"
    )  # -f<file>: that IS a file
    assert _bash(world, f"grep -f{world.a}/content/acme/notes.md {own}") == "allow"


def test_a_letter_that_means_something_else_to_another_program_is_not_sed_in_place_or_sort_output(
    world,
):
    profile = world.a / "profiles/acme/PROFILE.md"
    own = world.a / "content/acme/notes.md"
    assert _bash(world, f"grep -i foo {profile}") == "allow"  # -i is ignore-case here, not in-place
    assert (
        _bash(world, f"grep -o foo {profile}") == "allow"
    )  # -o is only-matching, not an output file
    assert _bash(world, f"ls -t {profile}") == "allow"  # -t is sort-by-time, not a target directory
    assert _bash(world, f"rg -t py foo {own}") == "allow"
    assert _bash(world, f"sed --in-place s/a/b/ {profile}") == "deny"  # but for sed it IS in place


def test_a_target_directory_option_takes_the_destination_from_the_option_not_the_last_operand(
    world,
):
    content = world.a / "content/acme"
    profile = world.a / "profiles/acme/PROFILE.md"
    assert (
        _bash(world, f"cp -t {content} {profile}") == "allow"
    )  # read a profile, write into content
    assert _bash(world, f"cp {profile} {content}/copy.md") == "allow"


def test_a_wrapper_still_lets_the_program_it_wraps_be_recognised_and_its_operands_checked(world):
    assert (
        _bash(world, "timeout 5 cat notes.md") == "deny"
    )  # `cat` is modelled: a bare name is the shared cwd
    assert _bash(world, f"timeout 5 cat {world.a}/content/acme/notes.md") == "allow"


def test_a_call_the_base_policy_already_refused_is_not_reported_as_a_confinement_refusal(world):
    pytest.importorskip("claude_agent_sdk")
    seen: list = []
    cb = make_headless_can_use_tool(
        on_deny=lambda tool, inp, decision: seen.append((tool, inp)), confine=world.confine
    )
    asyncio.run(
        cb("Read", {"file_path": str(world.a / "content/acme/.env")}, None)
    )  # a secret path: base policy
    asyncio.run(cb("NotAThing", {"x": 1}, None))  # an unknown tool: base policy
    assert [type(i).__name__ for _t, i in seen] == ["dict", "dict"]
    asyncio.run(cb("Read", {"file_path": _other(world)}, None))  # THIS one is the confinement's
    assert type(seen[-1][1]).__name__ == "ConfineDenial"
    plain = make_headless_can_use_tool(on_deny=lambda t, i, d: seen.append((t, i)))
    asyncio.run(plain("Read", {"file_path": "/etc/ssh/.env"}, None))
    assert type(seen[-1][1]).__name__ == "dict"


def test_an_unrelated_short_flag_does_not_turn_sed_into_in_place_or_sort_into_output(world):
    profile = world.a / "profiles/acme/PROFILE.md"
    assert _bash(world, f"sed -n p {profile}") == "allow"  # -n is quiet, not in-place
    assert _bash(world, f"sed -E s/a/b/ {profile}") == "allow"
    assert _bash(world, f"sort -r {profile}") == "allow"  # -r is reverse; the profile is only READ
    assert _bash(world, f"sort -n -u {profile}") == "allow"
    assert _bash(world, f"sed -i s/a/b/ {profile}") == "deny"  # the real in-place still is
    assert _bash(world, f"sort -o {profile} {profile}") == "deny"


@pytest.mark.parametrize("decl", ["export", "declare -x", "typeset -x", "readonly"])
def test_an_exported_assignment_is_checked_like_a_prefix_assignment(world, decl):
    """``export GTM_CONTENT_ROOT=/elsewhere; python -m gtm_core.x`` sets the same variable for the next command."""
    own, other = world.a / "content", world.b / "content"
    assert _bash(world, f"{decl} GTM_CONTENT_ROOT={own}") == "allow"
    assert _bash(world, f"{decl} GTM_CONTENT_ROOT={other}") == "deny"
    assert _bash(world, f"{decl} FOO=1 GTM_PROFILES_ROOT=/etc") == "deny"
    assert _bash(world, f"{decl} PYTHONUNBUFFERED=1") == "allow"


def test_the_content_root_a_run_points_the_tools_at_must_be_somewhere_it_may_write(world):
    """GTM_CONTENT_ROOT is where every gtm_core tool WRITES, so a place the run may only read is not enough."""
    assert (
        _bash(world, f"GTM_CONTENT_ROOT={world.a}/profiles python -m gtm_core.ledger_cli denials")
        == "deny"
    )
    assert (
        _bash(world, f"GTM_CONTENT_ROOT={world.a}/content python -m gtm_core.ledger_cli denials")
        == "allow"
    )
