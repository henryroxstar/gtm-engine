"""MM9 second layer, LIVE: the kernel — not our parsing — refuses a tenant run's cross-workspace read.

Built and run in a real container, because a uid change and a file capability are not things a unit test can
fake. The image compiles ``deploy/launcher/gtm_launch.c`` exactly as the backend image does, gives it the same
file capabilities and runs it as the backend's own unprivileged user (uid 10001), the way the backend starts a
tenant run's CLI and provisions a workspace tree.

The stand-in "CLI" is a script that does what the first layer cannot see: it WRITES A PROGRAM into its own
tree and runs it against another workspace's tree.

Skipped unless ``GTM_TEST_DOCKER_LAUNCHER=1`` and Docker answers (it builds an image and, the first time,
pulls a base and installs a compiler: not something every `pytest` run should do).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LAUNCHER_C = REPO / "deploy" / "launcher" / "gtm_launch.c"
BASE = os.environ.get("GTM_TEST_LAUNCHER_BASE", "python:3.13-slim")
TAG = "gtm-launcher-live-test:local"
CLI_DIR = "/opt/sdk/"  # the allowed prefix this image compiles the launcher with

pytestmark = pytest.mark.skipif(
    os.environ.get("GTM_TEST_DOCKER_LAUNCHER") != "1" or shutil.which("docker") is None,
    reason="set GTM_TEST_DOCKER_LAUNCHER=1 with Docker running to build and run the launcher live",
)

DOCKERFILE = f"""
FROM {BASE}
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends gcc libc6-dev libcap2-bin \\
    && rm -rf /var/lib/apt/lists/*
RUN useradd --uid 10001 --create-home gtm
COPY gtm_launch.c /src/gtm_launch.c
RUN gcc -O2 -Wall -Werror -DCLI_PREFIX='"{CLI_DIR}"' -DARGS_PREFIX='"/w/launch/"' -DWORKSPACES_PREFIX='"/w/"' \\
        -o /usr/local/sbin/gtm-launch /src/gtm_launch.c \\
    && chown root:gtm /usr/local/sbin/gtm-launch && chmod 0750 /usr/local/sbin/gtm-launch \\
    && setcap cap_chown,cap_fowner,cap_kill,cap_setuid,cap_setgid+ep /usr/local/sbin/gtm-launch
# the stand-in CLI, under the allowed prefix; what it does is written by the test per scenario
RUN mkdir -p {CLI_DIR}_bundled
"""


def _docker(*args, check=True, timeout=180, **kw):
    # A hang here is a finding, not a wait: every scenario is bounded.
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=check, timeout=timeout, **kw
    )


@pytest.fixture(scope="module")
def image(tmp_path_factory):
    if _docker("info", check=False).returncode != 0:
        pytest.skip("Docker is not answering")
    ctx = tmp_path_factory.mktemp("ctx")
    (ctx / "Dockerfile").write_text(DOCKERFILE)
    shutil.copy(LAUNCHER_C, ctx / "gtm_launch.c")
    build = _docker("build", "-q", "-t", TAG, str(ctx), check=False)
    assert build.returncode == 0, build.stderr[-2000:]
    return TAG


# One shell script is the whole scenario: it sets up two workspace trees as root, then starts the launcher the way
# the backend does (as uid 10001) with a stand-in CLI that reads from both.
SETUP = textwrap.dedent(
    """
    set -e
    mkdir -p /w/a /w/b
    echo secret-a > /w/a/data.txt; echo secret-b > /w/b/data.txt
    chown -R 20001:20001 /w/a; chown -R 20002:20002 /w/b; chmod 0700 /w/a /w/b; chmod 0600 /w/a/data.txt /w/b/data.txt
    """
)

CLI = textwrap.dedent(
    """
    #!/bin/sh
    echo "uid=$(id -u) gid=$(id -g) groups=$(id -G)"
    echo "own=$(cat /w/a/data.txt 2>&1)"
    echo "umask=$(umask)"
    [ -e /proc/self/fd/9 ] && echo fd9=open || echo fd9=closed
    echo "nnp=$(grep NoNewPrivs /proc/self/status | tr -d '\t ')"
    echo "caps=$(grep CapEff /proc/self/status | tr -d '\t ')"
    echo "other=$(cat /w/b/data.txt 2>&1)"
    # the case the first layer cannot see: the run writes a program into its own tree, then runs it
    cat > /w/a/probe.py <<'PY'
    import os, sys
    try:
        print("script-other=" + open("/w/b/data.txt").read().strip())
    except OSError as e:
        print("script-other-refused=" + e.strerror)
    try:
        os.setuid(0); print("became-root")
    except OSError:
        print("cannot-become-root")
    PY
    python /w/a/probe.py
    # and the launcher itself is not reachable from inside a run
    /usr/local/sbin/gtm-launch 2>&1 | head -1 || true
    """
).lstrip()


def _run(
    image, *, env: dict[str, str], user="10001", cli_body=CLI, cli_name="_bundled/claude", pre=""
) -> str:
    script = (
        SETUP
        + f"cat > {CLI_DIR}{cli_name} <<'EOF'\n{cli_body}\nEOF\nchmod 0755 {CLI_DIR}{cli_name}\n"
        + pre
        + "\ncd /w && exec setpriv --reuid=10001 --regid=10001 --init-groups "
        + " ".join(f"env {k}={v}" for k, v in env.items())
        + " /usr/local/sbin/gtm-launch\n"
    )
    out = _docker(
        "run", "--rm", "--user", "root", "--entrypoint", "sh", image, "-c", script, check=False
    )
    return out.stdout + out.stderr


def test_a_run_as_workspace_a_is_refused_workspace_bs_tree_even_by_a_script_it_wrote(image):
    out = _run(
        image,
        env={
            "GTM_RUN_UID": "20001",
            "GTM_RUN_GID": "20001",
            "GTM_REAL_CLI": f"{CLI_DIR}_bundled/claude",
        },
    )
    assert "uid=20001 gid=20001 groups=20001\n" in out, (
        out
    )  # and no inherited group (the whole line)
    assert "own=secret-a" in out, (
        out
    )  # the run can use its own tree: the boundary is not "nothing works"
    assert "secret-b" not in out, out
    assert "other=cat: /w/b/data.txt: Permission denied" in out, out
    assert "script-other-refused=Permission denied" in out, out
    assert "cannot-become-root" in out and "became-root" not in out, out
    assert "nnp=NoNewPrivs:1" in out, out  # nothing it starts can regain a privilege
    assert "caps=CapEff:0000000000000000" in out, out  # and it holds none of its own


def test_it_closes_descriptors_the_backend_left_open_and_sets_a_private_umask(image):
    out = _run(
        image,
        env={
            "GTM_RUN_UID": "20001",
            "GTM_RUN_GID": "20001",
            "GTM_REAL_CLI": f"{CLI_DIR}_bundled/claude",
        },
        pre="exec 9</w/b/data.txt",  # an fd on workspace B's file, opened before the launcher starts
    )
    assert "fd9=closed" in out and "fd9=open" not in out, out
    assert "umask=0077" in out, out


def test_it_refuses_a_program_that_is_not_the_bundled_cli(image):
    out = _run(
        image,
        env={
            "GTM_RUN_UID": "20001",
            "GTM_RUN_GID": "20001",
            "GTM_REAL_CLI": f"{CLI_DIR}_bundled/other",
        },
        pre=f"cp {CLI_DIR}_bundled/claude {CLI_DIR}_bundled/other",
    )
    assert "refused: GTM_REAL_CLI is not the bundled CLI" in out, out
    assert "uid=" not in out, out


def test_it_refuses_a_bundled_cli_that_a_group_or_other_could_have_rewritten(image):
    out = _run(
        image,
        env={
            "GTM_RUN_UID": "20001",
            "GTM_RUN_GID": "20001",
            "GTM_REAL_CLI": f"{CLI_DIR}_bundled/claude",
        },
        pre=f"chmod 0775 {CLI_DIR}_bundled/claude",
    )
    assert "not root-owned and closed to group/other writes" in out, out
    assert "uid=" not in out, out


def test_control_without_the_launcher_the_same_scenario_reads_the_other_tree(image):
    """The probe is not blind: as one shared user, with both trees that user's, the script DOES read workspace B."""
    script = (
        SETUP
        + "chown -R 10001:10001 /w/a /w/b\n"
        + f"cat > {CLI_DIR}_bundled/claude <<'EOF'\n{CLI}\nEOF\nchmod 0755 {CLI_DIR}_bundled/claude\n"
        + f"cd /w && exec setpriv --reuid=10001 --regid=10001 --init-groups {CLI_DIR}_bundled/claude\n"
    )
    out = _docker(
        "run", "--rm", "--user", "root", "--entrypoint", "sh", image, "-c", script, check=False
    )
    assert "script-other=secret-b" in out.stdout + out.stderr


@pytest.mark.parametrize(
    "env,why",
    [
        ({"GTM_RUN_UID": "0", "GTM_RUN_GID": "0"}, "root"),
        ({"GTM_RUN_UID": "10001", "GTM_RUN_GID": "10001"}, "the backend's own user"),
        ({"GTM_RUN_UID": "19999", "GTM_RUN_GID": "20001"}, "just below the pool"),
        ({"GTM_RUN_UID": "30000", "GTM_RUN_GID": "20001"}, "just above the pool"),
        ({"GTM_RUN_UID": "20001", "GTM_RUN_GID": "0"}, "root group"),
        ({"GTM_RUN_UID": "20001"}, "no gid"),
        ({"GTM_RUN_UID": "2000x", "GTM_RUN_GID": "20001"}, "not a number"),
    ],
)
def test_it_refuses_an_identity_outside_the_pool(image, env, why):
    out = _run(image, env={**env, "GTM_REAL_CLI": f"{CLI_DIR}_bundled/claude"})
    assert "gtm-launch: refused" in out, f"{why}: {out}"
    assert "uid=" not in out, f"{why}: the program ran anyway: {out}"


def test_it_refuses_a_program_outside_the_allowed_prefix(image):
    out = _run(
        image,
        env={"GTM_RUN_UID": "20001", "GTM_RUN_GID": "20001", "GTM_REAL_CLI": "/bin/sh"},
    )
    assert "refused: GTM_REAL_CLI is not under the allowed prefix" in out, out


def test_it_refuses_a_prefix_escape_through_dotdot(image):
    out = _run(
        image,
        env={
            "GTM_RUN_UID": "20001",
            "GTM_RUN_GID": "20001",
            "GTM_REAL_CLI": f"{CLI_DIR}../../../bin/sh",
        },
    )
    assert "not under the allowed prefix" in out, out


def test_a_pool_user_cannot_run_the_launcher_at_all(image):
    script = (
        SETUP
        + "setpriv --reuid=20001 --regid=20001 --clear-groups sh -c /usr/local/sbin/gtm-launch\n"
    )
    out = _docker(
        "run", "--rm", "--user", "root", "--entrypoint", "sh", image, "-c", script, check=False
    )
    assert "Permission denied" in out.stdout + out.stderr


# ── what another workspace can see of a run, and whether the backend can stop it ─────────────────
#
# A different OS user does not by itself hide a run's COMMAND LINE (readable by every user in the container) and the SDK
# puts the MCP configuration — every provider key the run holds — and the tenant's system prompt on it. Nor can the
# backend signal a process that now belongs to another user. The launcher answers both: secret-bearing flags travel as a
# private file the backend wrote and arrive as an in-memory file, and it stays on as a supervisor holding only CAP_KILL.

ENV_A = f"GTM_RUN_UID=20001 GTM_RUN_GID=20001 GTM_REAL_CLI={CLI_DIR}_bundled/claude"
AS_BACKEND = "setpriv --reuid=10001 --regid=10001 --init-groups"
LAUNCH = f"{AS_BACKEND} env {ENV_A} /usr/local/sbin/gtm-launch"

PARKED_CLI = textwrap.dedent(
    """
    #!/bin/sh
    trap 'echo got-term > /w/pub/term; exit 0' TERM
    echo $$ > /w/pub/pid
    echo "$@" > /w/pub/args
    for a in "$@"; do case "$a" in /dev/fd/*) echo "via-file=$(cat "$a")" >> /w/pub/files;; esac; done
    while :; do sleep 0.1; done
    """
).lstrip()

PREP = textwrap.dedent(
    """
    mkdir -p /w/launch /w/pub; chmod 1777 /w/pub
    printf '%s' 'SECRET-MCP-CONTENT' > /w/launch/mcp.json
    printf '%s' 'SECRET-PROMPT-CONTENT' > /w/launch/sp.txt
    chown -R 10001:10001 /w/launch; chmod 0700 /w/launch; chmod 0600 /w/launch/*
    """
)
FILE_ARGS = (
    "--mcp-config /w/launch/mcp.json --append-system-prompt-file /w/launch/sp.txt --model keep-me"
)


def _scenario(image, body: str, *, cli: str = PARKED_CLI, pre: str = "") -> str:
    script = (
        SETUP
        + PREP
        + f"cat > {CLI_DIR}_bundled/claude <<'EOF'\n{cli}\nEOF\nchmod 0755 {CLI_DIR}_bundled/claude\n"
        + pre
        + "\nset +e\n"
        + body
    )
    out = _docker(
        "run", "--rm", "--user", "root", "--entrypoint", "sh", image, "-c", script, check=False
    )
    return out.stdout + out.stderr


WAIT_PID = (
    "for i in $(seq 1 50); do [ -s /w/pub/pid ] && break; sleep 0.1; done; CLI=$(cat /w/pub/pid)\n"
)
SIGNAL = f"{AS_BACKEND} sh -c 'kill -$1 $0 2>&1' "  # usage: SIGNAL <pid> <SIG>


def test_another_workspace_cannot_read_the_runs_keys_or_prompt_off_its_command_line(image):
    out = _scenario(
        image,
        f"cd /w && {LAUNCH} {FILE_ARGS} & SUP=$!\n"
        + WAIT_PID
        + "echo \"seen=$(setpriv --reuid=20002 --regid=20002 --clear-groups cat /proc/$CLI/cmdline | tr '\\0' ' ')\"\n"
        + 'echo "fd-open=$(setpriv --reuid=20002 --regid=20002 --clear-groups cat /proc/$CLI/fd/3 2>&1)"\n'
        + 'echo "launch-dir=$(setpriv --reuid=20002 --regid=20002 --clear-groups cat /w/launch/mcp.json 2>&1)"\n'
        + "echo \"sup-seen=$(setpriv --reuid=20002 --regid=20002 --clear-groups cat /proc/$SUP/cmdline | tr '\\0' ' ')\"\n"
        + 'sleep 0.3; cat /w/pub/files; echo "launch-files=$(ls /w/launch | wc -l)"; echo "args=$(cat /w/pub/args)"; kill -9 $SUP\n',
    )
    seen = next((ln for ln in out.splitlines() if ln.startswith("seen=")), "")
    sup_seen = next((ln for ln in out.splitlines() if ln.startswith("sup-seen=")), "")
    assert "/dev/fd/" in seen, out  # the observer did read the run's command line ...
    for line in (seen, sup_seen):
        assert "SECRET" not in line, out  # ... and neither it nor the supervisor's carries a secret
    assert "via-file=SECRET-MCP-CONTENT" in out and "via-file=SECRET-PROMPT-CONTENT" in out, (
        out
    )  # the CLI got both
    assert (
        "fd-open=cat" in out and "Permission denied" in out.split("fd-open=")[1].split("\n")[0]
    ), out
    assert "launch-dir=cat" in out and "SECRET" not in out.split("launch-dir=")[1].split("\n")[0], (
        out
    )
    assert "--model keep-me" in out.split("args=")[1], out  # an ordinary argument is passed through
    assert "launch-files=0" in out, (
        out
    )  # and the private files are gone from disk once the launcher has read them


@pytest.mark.parametrize(
    "flags",
    [
        "--mcp-config '{\"mcpServers\":{}}'",
        '--mcp-config={"mcpServers":{}}',
        "--settings '{\"a\":1}'",
        "--system-prompt hello",
        "--append-system-prompt hello",
        "--agents '{}'",
        "--append-system-prompt-file=/w/launch/sp.txt",
        "--mcp-config",
    ],
)
def test_a_secret_bearing_flag_given_inline_is_refused_and_nothing_runs(image, flags):
    out = _scenario(image, f"cd /w && {LAUNCH} {flags}; echo rc=$?; ls /w/pub\n")
    assert "gtm-launch: refused" in out and "rc=126" in out, out
    assert "pid" not in out.split("rc=126")[1], out  # the program never started


@pytest.mark.parametrize(
    ("setup", "path"),
    [
        ("", "/etc/passwd"),  # not under the launch directory
        ("chmod 0644 /w/launch/mcp.json", "/w/launch/mcp.json"),  # readable by others
        ("chown root:root /w/launch/mcp.json", "/w/launch/mcp.json"),  # not the backend's own
        ("ln -s /etc/passwd /w/launch/link.json", "/w/launch/link.json"),  # a link out
        (
            "mkdir -p /w/launch/../x; cp /w/launch/mcp.json /w/x/m.json; chown 10001 /w/x/m.json",
            "/w/launch/../x/m.json",
        ),
    ],
)
def test_a_file_that_is_not_the_backends_private_file_under_the_launch_directory_is_refused(
    image, setup, path
):
    out = _scenario(image, f"cd /w && {LAUNCH} --mcp-config {path}; echo rc=$?\n", pre=setup)
    assert "gtm-launch: refused" in out and "rc=126" in out, out


def test_the_backend_can_stop_the_run_and_a_hard_kill_of_the_supervisor_takes_the_run_with_it(
    image,
):
    out = _scenario(
        image,
        f"cd /w && {LAUNCH} {FILE_ARGS} & SUP=$!\n"
        + WAIT_PID
        # the instrument: signalling the run directly as the backend's user is denied, which is the defect
        + f"{SIGNAL.replace(' 2>&1', '')}$CLI TERM >/dev/null 2>&1 && echo direct=ok || echo direct=denied\n"
        + f'{SIGNAL}$SUP TERM; wait $SUP; echo "sup-exit=$?"\n'
        + "[ -s /w/pub/term ] && echo term=delivered || echo term=lost\n"
        + "rm -f /w/pub/pid /w/pub/term\n"
        + PREP  # the launcher deletes each private file once it has read it, so the second run needs new ones
        + f"{LAUNCH} {FILE_ARGS} & SUP=$!\n"
        + WAIT_PID
        + f"{SIGNAL}$SUP KILL; sleep 0.5\n"
        + "[ -d /proc/$CLI ] && echo after-kill=alive || echo after-kill=gone\n",
    )
    assert "direct=denied" in out, out
    assert "term=delivered" in out and "sup-exit=0" in out, out
    assert "after-kill=gone" in out, out


def test_the_supervisor_mirrors_the_runs_exit_status_and_leaves_nothing_running(image):
    cli = "#!/bin/sh\nsleep 300 &\necho $! > /w/pub/leftover\nexit 3\n"
    out = _scenario(
        image,
        f'cd /w && {LAUNCH} {FILE_ARGS}; echo "rc=$?"; sleep 0.3\n'
        + "[ -d /proc/$(cat /w/pub/leftover) ] && echo leftover=alive || echo leftover=gone\n",
        cli=cli,
    )
    assert "rc=3" in out and "leftover=gone" in out, out


def test_the_supervisor_stays_the_backends_user_and_holds_only_the_right_to_signal(image):
    out = _scenario(
        image,
        f"cd /w && {LAUNCH} {FILE_ARGS} & SUP=$!\n"
        + WAIT_PID
        + "echo \"sup=$(grep -E '^(Uid|CapEff|CapPrm|NoNewPrivs)' /proc/$SUP/status | tr -d '\\t' | tr '\\n' ' ')\"\n"
        + "kill -9 $SUP\n",
    )
    line = next(ln for ln in out.splitlines() if ln.startswith("sup="))
    assert "Uid:10001" in line, out  # never a pool user, never root
    assert "CapEff:0000000000000020" in line and "CapPrm:0000000000000020" in line, (
        out
    )  # CAP_KILL only
    assert "NoNewPrivs:1" in line, out


# ── the review's hardening findings ──────────────────────────────────────────────────────────────


def test_a_second_value_after_a_secret_bearing_flag_is_refused(image):
    """--mcp-config is variadic in the CLI: a bare second value would arrive as a second, inline configuration."""
    out = _scenario(
        image,
        f"cd /w && {LAUNCH} --mcp-config /w/launch/mcp.json '{{\"mcpServers\":{{}}}}'; echo rc=$?\n",
    )
    assert "refused" in out and "variadic" in out and "rc=126" in out, out


def test_descriptors_above_the_old_limit_and_closed_standard_streams_cannot_leak_a_secret(image):
    fd_cli = (
        "#!/bin/sh\n"
        "for n in 0 1 2 1500 3000; do [ -e /proc/self/fd/$n ] && "
        'echo "fd$n=$(readlink /proc/self/fd/$n)" || echo "fd$n=closed"; done\n'
    )
    script = f"cd /w && bash -c 'exec 1500</w/b/data.txt 3000</w/b/data.txt; exec 2>&-; {LAUNCH} {FILE_ARGS}'\n"
    out = _scenario(image, script, cli=fd_cli)
    assert "fd1500=closed" in out and "fd3000=closed" in out, (
        out
    )  # nothing past the old 1023 limit gets through
    # a closed stderr is re-opened on /dev/null, so a descriptor made later cannot land on a standard stream
    assert "fd2=/dev/null" in out, out


def test_an_inherited_ignored_sigchld_does_not_make_a_failed_run_look_successful(image):
    cli = "#!/bin/sh\nexit 3\n"
    out = _scenario(
        image,
        f"cd /w && bash -c \"trap '' CHLD; {LAUNCH} {FILE_ARGS}\"; echo rc=$?\n",
        cli=cli,
    )
    assert "rc=3" in out, out


# ── provisioning: the tree is its identity's, and the backend can still use it ───────────────────
#
# A run as uid 20003 can only write a tree 20003 owns, and the backend (10001) no longer owns that tree. The launcher's
# `--provision` mode hands the tree to the identity and grants the backend's user (and the identity) rwx through an
# access ACL on every entry plus a default ACL on every directory, so whatever either side creates later stays
# reachable by both and by nobody else. The tree's own owner is changed LAST: a refused provisioning leaves the root
# unowned by the identity, so `plan_for` keeps refusing the run.

PROV_SETUP = textwrap.dedent(
    """
    mkdir -p /w/c/content/sub/deep /w/c/profiles /w/launch
    chown 10001:10001 /w  # the workspaces root is the backend's, as in the image
    echo ledger-line > /w/c/content/ledger.jsonl; chmod 0644 /w/c/content/ledger.jsonl
    echo private-profile > /w/c/profiles/p.md; chmod 0600 /w/c/profiles/p.md
    echo deep-file > /w/c/content/sub/deep/x.txt
    chown -R 10001:10001 /w/c
    # a link the backend (or a run) left in the tree, pointing at another workspace's file
    setpriv --reuid=10001 --regid=10001 --init-groups ln -s /w/b/data.txt /w/c/content/link
    """
)
PROVISION = (
    f"{AS_BACKEND} env GTM_RUN_UID=20003 GTM_RUN_GID=20003 /usr/local/sbin/gtm-launch --provision"
)
AS_OTHER = "setpriv --reuid=20002 --regid=20002 --clear-groups"

RUN_C_CLI = textwrap.dedent(
    """
    #!/bin/sh
    echo run-wrote > /w/c/content/run.txt && echo run-write=ok || echo run-write=denied
    mkdir -p /w/c/content/newdir/runsub && echo run-mkdir=ok || echo run-mkdir=denied
    echo "run-reads-backend=$(cat /w/c/content/new.txt 2>&1)"
    echo "run-reads-old=$(cat /w/c/profiles/p.md 2>&1)"
    echo run-line >> /w/c/content/ledger.jsonl && echo run-append=ok || echo run-append=denied
    """
).lstrip()


def _prov(image, body: str, *, pre: str = "", cli: str = RUN_C_CLI, docker_args=()) -> str:
    script = (
        SETUP
        + PROV_SETUP
        + f"cat > {CLI_DIR}_bundled/claude <<'EOF'\n{cli}\nEOF\nchmod 0755 {CLI_DIR}_bundled/claude\n"
        + pre
        + "\nset +e\n"
        + body
    )
    out = _docker(
        "run",
        "--rm",
        "--user",
        "root",
        *docker_args,
        "--entrypoint",
        "sh",
        image,
        "-c",
        script,
        check=False,
    )
    return out.stdout + out.stderr


def _val(out: str, key: str) -> str:
    return next((ln.split("=", 1)[1] for ln in out.splitlines() if ln.startswith(key + "=")), "")


def test_a_provisioned_tree_is_its_identitys_the_backend_keeps_it_and_another_workspace_does_not(
    image,
):
    out = _prov(
        image,
        f"cd /w && {PROVISION} /w/c; echo prov-rc=$?\n"
        "echo \"root=$(stat -c '%u:%g %a' /w/c)\"\n"
        'echo "not-ident=$(find /w/c ! -user 20003 | wc -l)"\n'
        'echo "b-owner=$(stat -c %u /w/b/data.txt)"\n'
        f'echo "be-read=$({AS_BACKEND} cat /w/c/profiles/p.md 2>&1)"\n'
        f'echo "be-deep=$({AS_BACKEND} cat /w/c/content/sub/deep/x.txt 2>&1)"\n'
        f"{AS_BACKEND} sh -c 'echo from-backend > /w/c/content/new.txt && mkdir /w/c/content/newdir"
        " && echo more >> /w/c/content/ledger.jsonl' && echo be-write=ok || echo be-write=denied\n"
        f'echo "other-ls=$({AS_OTHER} ls /w/c 2>&1)"\n'
        f'echo "other-read=$({AS_OTHER} cat /w/c/content/ledger.jsonl 2>&1)"\n'
        # a run of workspace c, started through the launcher exactly as the backend starts one
        f"{AS_BACKEND} env GTM_RUN_UID=20003 GTM_RUN_GID=20003 GTM_REAL_CLI={CLI_DIR}_bundled/claude"
        " /usr/local/sbin/gtm-launch\n"
        f'echo "be-reads-run=$({AS_BACKEND} cat /w/c/content/run.txt 2>&1)"\n'
        f'echo "be-into-run-dir=$({AS_BACKEND} ls /w/c/content/newdir 2>&1)"\n'
        f'echo "other-reads-run=$({AS_OTHER} cat /w/c/content/run.txt 2>&1)"\n'
        'echo "run-file-owner=$(stat -c %u /w/c/content/run.txt)"\n',
    )
    assert _val(out, "prov-rc") == "0", out
    assert _val(out, "root") == "20003:20003 770", out  # the identity's, closed to other
    assert _val(out, "not-ident") == "0", out  # every entry, the link itself included
    assert _val(out, "b-owner") == "20002", out  # ... and the link was not followed
    # the backend keeps read and write on what was there and on what it adds
    assert _val(out, "be-read") == "private-profile", out
    assert _val(out, "be-deep") == "deep-file", out
    assert _val(out, "be-write") == "ok", out
    # another workspace's user can neither list nor read it
    assert "Permission denied" in _val(out, "other-ls"), out
    assert "Permission denied" in _val(out, "other-read"), out
    assert "ledger-line" not in _val(out, "other-read"), (
        out
    )  # the content, not the name in the error
    # a run launched as the identity writes its own tree and reads what the backend wrote, before and after
    assert _val(out, "run-write") == "ok" and _val(out, "run-mkdir") == "ok", out
    assert _val(out, "run-reads-backend") == "from-backend", out
    assert _val(out, "run-reads-old") == "private-profile", out
    assert _val(out, "run-append") == "ok", out
    # and what the run creates (under its private umask) is still the backend's to read, and nobody else's
    assert _val(out, "be-reads-run") == "run-wrote", out
    assert _val(out, "be-into-run-dir") == "runsub", out
    assert "Permission denied" in _val(out, "other-reads-run"), out
    assert _val(out, "run-file-owner") == "20003", out


def test_control_an_unprovisioned_tree_is_not_writable_by_its_run(image):
    """The probe is not blind: the same run against the backend-owned tree is refused its writes."""
    out = _prov(
        image,
        "chmod 0750 /w/c /w/c/content /w/c/profiles\n"
        f"cd /w && {AS_BACKEND} env GTM_RUN_UID=20003 GTM_RUN_GID=20003 GTM_REAL_CLI={CLI_DIR}_bundled/claude"
        " /usr/local/sbin/gtm-launch\n",
    )
    assert _val(out, "run-write") == "denied", out


@pytest.mark.parametrize(
    ("path", "pre"),
    [
        ("/w/c/", ""),  # not canonical
        ("/w", ""),  # the workspaces root itself
        ("/w/launch", ""),  # the private-file directory is not a workspace
        ("/w/c/content", ""),  # below a tree
        ("/w/evil", "ln -s /etc /w/evil"),  # a link out
        ("/w/alias", "ln -s /w/c /w/alias"),  # a link to another workspace's tree
        ("/w/../etc", ""),
        ("/etc", ""),
        ("/w/a.b", "mkdir /w/a.b; chown 10001 /w/a.b"),  # not a workspace id
        ("/w/nope", ""),  # missing
    ],
)
def test_provisioning_refuses_anything_but_one_canonical_workspace_tree(image, path, pre):
    out = _prov(
        image,
        f"cd /w && {PROVISION} {path}; echo rc=$?\n"
        "echo \"c=$(stat -c '%u %a' /w/c)\"\n"
        'echo "etc=$(stat -c %u /etc)"\n',
        pre=pre,
    )
    assert "gtm-launch: refused" in out and _val(out, "rc") == "126", out
    assert _val(out, "c") == "10001 755", out  # nothing was touched
    assert _val(out, "etc") == "0", out


def test_provisioning_refuses_a_workspaces_root_the_backend_does_not_own(image):
    """The tree is opened relative to a root reached without following a link and owned by the caller: a root that is
    someone else's (here root's) is not where the backend's trees live, so nothing below it is provisioned."""
    out = _prov(
        image,
        f'cd /w && {PROVISION} /w/c; echo rc=$?\necho "c=$(stat -c %u /w/c)"\n',
        pre="chown 0:0 /w",
    )
    assert "gtm-launch: refused" in out and _val(out, "rc") == "126", out
    assert "workspaces root is not the backend's own" in out, out
    assert _val(out, "c") == "10001", out


@pytest.mark.parametrize(
    "env",
    [
        "GTM_RUN_UID=0 GTM_RUN_GID=0",
        "GTM_RUN_UID=10001 GTM_RUN_GID=10001",
        "GTM_RUN_UID=30000 GTM_RUN_GID=20003",
        "GTM_RUN_UID=20003",
        "GTM_RUN_UID=20003 GTM_RUN_GID=20004",  # two pool ids: the tree would be one identity's and another's group
    ],
)
def test_provisioning_refuses_an_identity_outside_the_pool(image, env):
    out = _prov(
        image,
        f"cd /w && {AS_BACKEND} env {env} /usr/local/sbin/gtm-launch --provision /w/c; echo rc=$?\n"
        'echo "c=$(stat -c %u /w/c)"\n',
    )
    assert "gtm-launch: refused" in out and _val(out, "rc") == "126", out
    assert _val(out, "c") == "10001", out


@pytest.mark.parametrize(
    ("pre", "docker_args", "reason"),
    [
        ("ln /w/b/data.txt /w/c/content/sub/hard", (), "hard-linked file"),
        ("mknod /w/c/content/sub/dev c 1 3", (), "device node"),
        # a volume, not a tmpfs: a tmpfs may refuse the ACL itself, which would refuse for the wrong reason
        ("", ("-v", "/w/c/content/mnt"), "holds a mount"),
    ],
)
def test_provisioning_refuses_a_tree_holding_a_way_out_and_leaves_the_tree_unclaimed(
    image, pre, docker_args, reason
):
    why = reason
    out = _prov(
        image,
        f"cd /w && {PROVISION} /w/c; echo rc=$?\n"
        'echo "c=$(stat -c %u /w/c)"\n'
        'echo "b=$(stat -c %u /w/b/data.txt)"\n',
        pre=pre,
        docker_args=docker_args,
    )
    assert "gtm-launch: refused" in out and _val(out, "rc") == "126", f"{why}: {out}"
    assert reason in out, f"refused, but not for {reason}: {out}"
    assert _val(out, "b") == "20002", f"{why}: another workspace's file changed hands: {out}"
    # the root is claimed last, so a refusal leaves it unprovisioned and plan_for keeps refusing the run
    assert _val(out, "c") == "10001", f"{why}: {out}"


def test_a_tree_restored_as_root_with_private_modes_is_repaired_and_repairing_twice_is_harmless(
    image,
):
    out = _prov(
        image,
        f"cd /w && {PROVISION} /w/c; echo first=$?\n"
        f"{PROVISION} /w/c; echo second=$?\n"
        "echo \"root=$(stat -c '%u:%g %a' /w/c)\"\n"
        'echo "not-ident=$(find /w/c ! -user 20003 | wc -l)"\n'
        f'echo "be-read=$({AS_BACKEND} cat /w/c/content/ledger.jsonl 2>&1)"\n'
        f'echo "be-deep=$({AS_BACKEND} cat /w/c/content/sub/deep/x.txt 2>&1)"\n'
        f'echo "other-read=$({AS_OTHER} cat /w/c/content/ledger.jsonl 2>&1)"\n',
        # a backup restored by root: every entry root's or another pool user's, closed to everyone else
        pre="chown -R 0:0 /w/c; chmod -R go-rwx /w/c; chown 20002:20002 /w/c/content/ledger.jsonl",
    )
    assert _val(out, "first") == "0" and _val(out, "second") == "0", out
    assert _val(out, "root") == "20003:20003 770", out
    assert _val(out, "not-ident") == "0", out
    assert _val(out, "be-read") == "ledger-line" and _val(out, "be-deep") == "deep-file", out
    assert "Permission denied" in _val(out, "other-read"), out


@pytest.mark.parametrize(
    ("cmd", "reason"),
    [
        (
            "env GTM_RUN_UID=20003 GTM_RUN_GID=20003 /usr/local/sbin/gtm-launch --provision /w/c",
            "only the backend's user",  # root
        ),
        (f"{PROVISION} /w/c extra", "usage"),
        (f"{PROVISION}", "usage"),
    ],
)
def test_provisioning_refuses_any_caller_but_the_backend_and_any_other_shape(image, cmd, reason):
    why = reason
    out = _prov(image, f'cd /w && {cmd}; echo rc=$?\necho "c=$(stat -c %u /w/c)"\n')
    assert "gtm-launch: refused" in out and _val(out, "rc") == "126", f"{why}: {out}"
    assert reason in out, f"refused, but not for {reason}: {out}"
    assert _val(out, "c") == "10001", f"{why}: {out}"
