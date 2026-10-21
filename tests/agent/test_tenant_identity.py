"""MM9 second layer: each workspace's run is a different OS user, so the KERNEL refuses a cross-tenant read.

The first layer (``agent/path_confinement.py``) reads command lines. It cannot see what a script the run
wrote itself will open. This layer gives every workspace its own uid/gid from a fixed pool, recorded once
and never reused, and starts a tenant run's CLI through ``gtm-launch`` as that identity.

What is pinned here (the live proof — a real run as workspace A refused on workspace B's tree, including
by a script it wrote — is ``test_tenant_launcher_live.py``):

* the identity is stable per workspace, unique across workspaces, and inside the pool the launcher accepts;
* an exhausted pool REFUSES (a shared identity would be no boundary at all);
* the flag defaults off and then nothing changes; with it on and no launcher the run is REFUSED, never
  started unconfined;
* the launcher is wired only into a tenant run, and the run's HOME/config/tmp are inside its own tree.
"""

from __future__ import annotations

import dataclasses
import threading
from pathlib import Path

import pytest

from agent import tenant_identity as ti

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _tree_is_owned(monkeypatch):
    """Provisioning (the tree owned by its identity) is not what these tests are about; one test below takes it away."""
    monkeypatch.setattr(
        ti, "_tree_owner", lambda path: int(ti.identity_for(path.name, path.parent))
    )


def _ids(tmp_path, n, start=0):
    return [ti.identity_for(f"ws-{i}", tmp_path) for i in range(start, start + n)]


def test_an_identity_is_stable_per_workspace(tmp_path):
    first = ti.identity_for("ws-a", tmp_path)
    assert ti.identity_for("ws-a", tmp_path) == first


def test_two_workspaces_never_share_an_identity(tmp_path):
    ids = _ids(tmp_path, 50)
    assert len(set(ids)) == 50


def test_every_identity_is_inside_the_pool_the_launcher_accepts(tmp_path):
    assert all(ti.POOL_MIN <= i <= ti.POOL_MAX for i in _ids(tmp_path, 20))


def test_concurrent_allocation_never_hands_two_workspaces_the_same_identity(tmp_path):
    out: dict[str, int] = {}

    def take(name):
        out[name] = ti.identity_for(name, tmp_path)

    threads = [threading.Thread(target=take, args=(f"ws-{i}",)) for i in range(40)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(set(out.values())) == 40


def test_an_exhausted_pool_refuses_and_does_not_reuse(tmp_path, monkeypatch):
    monkeypatch.setattr(ti, "POOL_MAX", ti.POOL_MIN + 2)
    held = _ids(tmp_path, 3)
    with pytest.raises(ti.IdentityPoolExhausted):
        ti.identity_for("one-too-many", tmp_path)
    assert [ti.identity_for(f"ws-{i}", tmp_path) for i in range(3)] == held


@pytest.mark.parametrize("bad", ["", "../x", "a/b", "a b", "x" * 80, "ws\x00"])
def test_a_workspace_id_that_is_not_a_plain_name_is_refused(tmp_path, bad):
    with pytest.raises(ValueError):
        ti.identity_for(bad, tmp_path)


# ── the flag ─────────────────────────────────────────────────────────────────────────────────────


def test_with_the_flag_off_nothing_changes(tmp_path, monkeypatch):
    monkeypatch.delenv(ti.ENABLED_ENV, raising=False)
    assert ti.plan_for(WS, tmp_path, run_dir=tmp_path / "run") is None


WS = "aaaaaaaa-0000-0000-0000-aaaaaaaaaaaa"


def test_with_the_flag_on_and_no_launcher_the_run_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv(ti.ENABLED_ENV, "1")
    monkeypatch.setenv(ti.LAUNCHER_ENV, str(tmp_path / "missing-launcher"))
    with pytest.raises(ti.LauncherUnavailable):
        ti.plan_for(WS, tmp_path, run_dir=tmp_path / "run")


def test_with_the_flag_on_the_plan_names_the_launcher_and_the_workspaces_own_identity(
    tmp_path, monkeypatch
):
    launcher = tmp_path / "gtm-launch"
    launcher.write_text("#!/bin/sh\n")
    launcher.chmod(0o755)
    monkeypatch.setenv(ti.ENABLED_ENV, "1")
    monkeypatch.setenv(ti.LAUNCHER_ENV, str(launcher))
    run_dir = tmp_path / "run"
    plan = ti.plan_for(WS, tmp_path, run_dir=run_dir)
    ident = ti.identity_for(WS, tmp_path)
    assert plan.cli_path == str(launcher)
    assert plan.env["GTM_RUN_UID"] == plan.env["GTM_RUN_GID"] == str(ident)
    assert plan.env["GTM_REAL_CLI"]
    # the run's scratch is inside its own tree: no shared /tmp, no shared HOME
    for key in ("HOME", "CLAUDE_CONFIG_DIR", "TMPDIR"):
        assert Path(plan.env[key]).is_relative_to(run_dir), key


@pytest.mark.parametrize("value", ["", "0", "false", "no"])
def test_only_an_explicit_one_turns_it_on(tmp_path, monkeypatch, value):
    monkeypatch.setenv(ti.ENABLED_ENV, value)
    assert ti.plan_for(WS, tmp_path, run_dir=tmp_path / "run") is None


# ── wired into the tenant run only ───────────────────────────────────────────────────────────────


def _cfg(tmp_path, monkeypatch, *, tenant: bool):
    from agent.config import Config
    from backend.session import _workspace_scoped_config

    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(tmp_path / "workspaces"))
    cfg = Config.from_env(repo_root=REPO)
    if tenant:
        return _workspace_scoped_config(cfg, WS, REPO, credentials={})
    return dataclasses.replace(cfg, content_root=tmp_path / "content")


def _launcher(tmp_path, monkeypatch):
    launcher = tmp_path / "gtm-launch"
    launcher.write_text("#!/bin/sh\n")
    launcher.chmod(0o755)
    monkeypatch.setenv(ti.ENABLED_ENV, "1")
    monkeypatch.setenv(ti.LAUNCHER_ENV, str(launcher))
    return launcher


def test_a_tenant_run_starts_its_cli_through_the_launcher_when_the_flag_is_on(
    tmp_path, monkeypatch
):
    pytest.importorskip("claude_agent_sdk")
    from agent.session import build_agent_options
    from tests.agent.test_tenant_transcripts import _confined_callback

    launcher = _launcher(tmp_path, monkeypatch)
    options = build_agent_options(
        _cfg(tmp_path, monkeypatch, tenant=True), "acme", can_use_tool=_confined_callback()
    )
    assert str(options.cli_path) == str(launcher)
    assert options.env["GTM_RUN_UID"] == options.env["GTM_RUN_GID"]


def test_a_single_tenant_run_never_goes_through_the_launcher(tmp_path, monkeypatch):
    """The control: the flag is on and the same call path is used, and the operator's own run is untouched."""
    pytest.importorskip("claude_agent_sdk")
    from agent.session import build_agent_options

    _launcher(tmp_path, monkeypatch)
    options = build_agent_options(_cfg(tmp_path, monkeypatch, tenant=False), "acme")
    assert not options.cli_path
    assert "GTM_RUN_UID" not in options.env


def test_with_the_flag_on_and_no_launcher_building_the_tenant_options_refuses(
    tmp_path, monkeypatch
):
    pytest.importorskip("claude_agent_sdk")
    from agent.session import build_agent_options
    from tests.agent.test_tenant_transcripts import _confined_callback

    monkeypatch.setenv(ti.ENABLED_ENV, "1")
    monkeypatch.setenv(ti.LAUNCHER_ENV, str(tmp_path / "missing"))
    with pytest.raises(ti.LauncherUnavailable):
        build_agent_options(
            _cfg(tmp_path, monkeypatch, tenant=True), "acme", can_use_tool=_confined_callback()
        )


# ── the two halves agree ─────────────────────────────────────────────────────────────────────────


@pytest.mark.private_tree  # reads deploy/, which the public carve does not ship
def test_the_pool_here_is_the_pool_the_launcher_accepts():
    import re

    src = (REPO / "deploy" / "launcher" / "gtm_launch.c").read_text()
    low = int(re.search(r"#define UID_MIN (\d+)", src).group(1))
    high = int(re.search(r"#define UID_MAX (\d+)", src).group(1))
    assert (ti.POOL_MIN, ti.POOL_MAX) == (low, high)


@pytest.mark.private_tree
def test_the_flag_is_passed_to_the_services_that_start_runs_with_an_empty_default():
    import yaml

    compose = yaml.safe_load((REPO / "deploy" / "docker-compose.yml").read_text())
    for service in ("api", "worker"):
        env = compose["services"][service]["environment"]
        env = env if isinstance(env, dict) else dict(item.split("=", 1) for item in env)
        assert env.get(ti.ENABLED_ENV) == f"${{{ti.ENABLED_ENV}:-}}", service


@pytest.mark.private_tree
def test_the_backend_image_builds_the_launcher_for_the_backends_user_only():
    text = (REPO / "deploy" / "Dockerfile.backend").read_text()
    assert "gtm_launch.c" in text
    assert "chown root:gtm /usr/local/sbin/gtm-launch" in text and "chmod 0750" in text
    # these five and no others: kill/setuid/setgid to start and stop a run, chown/fowner to provision its tree
    assert "setcap cap_chown,cap_fowner,cap_kill,cap_setuid,cap_setgid+ep" in text
    assert text.index("setcap") < text.index("\nUSER gtm\n")


# ── fail closed when the books and the disk disagree ─────────────────────────────────────────────


def test_a_tree_not_owned_by_its_identity_refuses_the_run(tmp_path, monkeypatch):
    """The registry is the only record of who owns what. If a tree is not owned by the uid the registry says (an
    unprovisioned tree, a registry restored from a different backup than the trees), the run is refused: starting
    it would hand one workspace a uid that owns another's files."""
    _launcher(tmp_path, monkeypatch)
    monkeypatch.undo()  # drop the autouse fake owner; the flag and launcher are set again below
    _launcher(tmp_path, monkeypatch)
    tree = tmp_path / WS
    tree.mkdir()
    with pytest.raises(ti.TreeNotProvisioned):
        ti.plan_for(WS, tmp_path, run_dir=tree / ".run")


def test_a_corrupt_registry_is_refused_not_repaired(tmp_path):
    import json

    ti.identity_for("ws-a", tmp_path)
    (tmp_path / "identities.json").write_text(
        json.dumps({"ws-a": 20001, "ws-b": 20001})
    )  # one id, two owners
    with pytest.raises(ti.RegistryCorrupt):
        ti.identity_for("ws-c", tmp_path)
    (tmp_path / "identities.json").write_text(json.dumps({"ws-a": 5}))  # outside the pool
    with pytest.raises(ti.RegistryCorrupt):
        ti.identity_for("ws-c", tmp_path)


def test_a_missing_bundled_cli_refuses_with_a_clear_error(tmp_path, monkeypatch):
    _launcher(tmp_path, monkeypatch)
    monkeypatch.setattr(ti, "_bundled_cli", lambda: str(tmp_path / "no-such-cli"))
    with pytest.raises(ti.LauncherUnavailable, match="bundled CLI"):
        ti.plan_for(WS, tmp_path, run_dir=tmp_path / "run")


@pytest.mark.private_tree
def test_the_launchers_allowed_prefix_matches_where_the_sdk_puts_its_cli():
    """The Dockerfile bakes the prefix at build time; the SDK's own layout is what the launcher will be asked to run."""
    import re

    text = (REPO / "deploy" / "Dockerfile.backend").read_text()
    prefix = re.search(r"-DCLI_PREFIX='\"([^\"]+)\"'", text).group(1)
    cli = Path(ti._bundled_cli())
    assert prefix.endswith("/site-packages/claude_agent_sdk/")
    assert cli.parts[-3:] == ("claude_agent_sdk", "_bundled", "claude")
    assert str(cli).endswith(prefix.split("site-packages/", 1)[1] + "_bundled/claude")


# ── a secret never reaches the CLI's command line ────────────────────────────────────────────────


def _tenant_options(tmp_path, monkeypatch, *, launcher: bool):
    pytest.importorskip("claude_agent_sdk")
    from agent.config import Config
    from agent.session import build_agent_options
    from backend.session import _workspace_scoped_config
    from tests.agent.test_tenant_transcripts import _confined_callback

    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("SALESHANDY_API_KEY", "")
    if launcher:
        _launcher(tmp_path, monkeypatch)
    cfg = _workspace_scoped_config(
        Config.from_env(repo_root=REPO), WS, REPO, credentials={"saleshandy": "SECRET-TENANT-KEY"}
    )
    return build_agent_options(cfg, "acme", can_use_tool=_confined_callback()), cfg


def _command_line(options) -> list[str]:
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    transport = SubprocessCLITransport(prompt="hi", options=options)
    transport._cli_path = "/launcher"
    return transport._build_command()


def test_through_the_launcher_no_secret_is_on_the_command_line_and_each_travels_as_a_private_file(
    tmp_path, monkeypatch
):
    options, cfg = _tenant_options(tmp_path, monkeypatch, launcher=True)
    cmd = _command_line(options)
    flat = " ".join(cmd)
    assert "SECRET-TENANT-KEY" not in flat  # the tenant's own key (it is in the MCP config)
    assert (
        "--append-system-prompt " not in flat and "--system-prompt " not in flat
    )  # the prompt is a file too
    assert "--mcp-config" in cmd and "--append-system-prompt-file" in cmd

    mcp_path = Path(cmd[cmd.index("--mcp-config") + 1])
    prompt_path = Path(cmd[cmd.index("--append-system-prompt-file") + 1])
    launch_dir = tmp_path / "workspaces" / ".launch"
    for path in (mcp_path, prompt_path):
        assert path.parent == launch_dir and path.stat().st_mode & 0o777 == 0o600, path
    assert launch_dir.stat().st_mode & 0o777 == 0o700
    assert "SECRET-TENANT-KEY" in mcp_path.read_text()  # the CLI still gets the key, by file
    from agent import profiles

    assert prompt_path.read_text() == profiles.system_prompt_for(
        "acme", cfg
    )  # and the prompt, whole


def test_the_control_without_the_launcher_the_same_options_do_put_the_key_on_the_command_line(
    tmp_path, monkeypatch
):
    """The instrument: the probe above is not blind. Without the launcher the SDK inlines the configuration."""
    options, _cfg = _tenant_options(tmp_path, monkeypatch, launcher=False)
    flat = " ".join(_command_line(options))
    assert "SECRET-TENANT-KEY" in flat and "--append-system-prompt " in flat


def test_a_private_file_that_cannot_be_written_refuses_the_run(tmp_path, monkeypatch):
    _launcher(tmp_path, monkeypatch)
    plan = ti.plan_for(WS, tmp_path, run_dir=tmp_path / WS / ".run")
    blocker = tmp_path / ".launch"
    blocker.write_text("a file where the directory should be")
    with pytest.raises((ti.HandoffUnavailable, OSError)):
        plan.private_file("mcp.json", "x")


def test_a_stale_private_file_is_swept_when_the_next_one_is_written(tmp_path, monkeypatch):
    import os
    import time

    _launcher(tmp_path, monkeypatch)
    plan = ti.plan_for(WS, tmp_path, run_dir=tmp_path / WS / ".run")
    old = Path(plan.private_file("old.json", "stale"))
    past = time.time() - 7200
    os.utime(old, (past, past))
    fresh = Path(plan.private_file("new.json", "fresh"))
    assert not old.exists() and fresh.exists()


@pytest.mark.private_tree
def test_the_launch_directory_the_launcher_accepts_is_the_one_the_backend_writes():
    import re

    text = (REPO / "deploy" / "Dockerfile.backend").read_text()
    prefix = re.search(r"-DARGS_PREFIX='\"([^\"]+)\"'", text).group(1)
    assert prefix == f"/app/data/workspaces/{ti.LAUNCH_DIR}/"


def test_a_failed_options_build_leaves_no_private_file_behind(tmp_path, monkeypatch):
    pytest.importorskip("claude_agent_sdk")
    import claude_agent_sdk

    def boom(**_kw):
        raise RuntimeError("the SDK refused these options")

    monkeypatch.setattr(claude_agent_sdk, "ClaudeAgentOptions", boom)
    with pytest.raises(RuntimeError):
        _tenant_options(tmp_path, monkeypatch, launcher=True)
    launch_dir = tmp_path / "workspaces" / ".launch"
    assert not launch_dir.exists() or not list(launch_dir.iterdir())


# ── provisioning: the tree is handed to its identity before a run needs it ───────────────────────


def _recording_launcher(tmp_path, monkeypatch, *, rc=0):
    """A stand-in for `gtm-launch --provision`: records its argv and identity, then exits `rc`."""
    log = tmp_path / "launcher.log"
    launcher = tmp_path / "gtm-launch"
    launcher.write_text(
        # home= records whether the run's HOME already existed when the tree was handed over (so the walk claims it)
        f'#!/bin/sh\nh=no; [ -d "$2/.run/home" ] && h=yes\n'
        f'echo "$* uid=$GTM_RUN_UID gid=$GTM_RUN_GID home=$h" >> {log}\n'
        f'[ {rc} -eq 0 ] || echo "gtm-launch: refused: stand-in" >&2\nexit {rc}\n'
    )
    launcher.chmod(0o755)
    monkeypatch.setenv(ti.LAUNCHER_ENV, str(launcher))
    return log


def _unowned(monkeypatch):
    monkeypatch.setattr(ti, "_tree_owner", lambda path: None)


def test_with_the_flag_off_a_new_tree_is_not_provisioned(tmp_path, monkeypatch):
    monkeypatch.delenv(ti.ENABLED_ENV, raising=False)
    log = _recording_launcher(tmp_path, monkeypatch)
    _unowned(monkeypatch)
    (tmp_path / WS).mkdir()
    assert ti.ensure_provisioned(WS, tmp_path) is None
    assert not log.exists()


def test_with_the_flag_on_an_unprovisioned_tree_is_handed_to_its_identity(tmp_path, monkeypatch):
    monkeypatch.setenv(ti.ENABLED_ENV, "1")
    log = _recording_launcher(tmp_path, monkeypatch)
    _unowned(monkeypatch)
    (tmp_path / WS).mkdir()
    ident = ti.ensure_provisioned(WS, tmp_path)
    assert ident == ti.identity_for(WS, tmp_path)
    tree = (tmp_path / WS).resolve()
    assert log.read_text() == f"--provision {tree} uid={ident} gid={ident} home=yes\n"
    # the run's HOME and TMPDIR exist inside the tree before it is handed over (plan_for points the CLI there)
    assert (tree / ".run" / "home").is_dir() and (tree / ".run" / "tmp").is_dir()


def test_a_tree_already_its_identitys_is_not_walked_again(tmp_path, monkeypatch):
    """The ownership of the tree's root is the commit marker the launcher writes last; a run does not re-walk it."""
    monkeypatch.setenv(ti.ENABLED_ENV, "1")
    log = _recording_launcher(tmp_path, monkeypatch)  # the autouse fixture says the tree is owned
    (tmp_path / WS).mkdir()
    assert ti.ensure_provisioned(WS, tmp_path) == ti.identity_for(WS, tmp_path)
    assert not log.exists()


def test_a_refused_provisioning_refuses_the_run(tmp_path, monkeypatch):
    monkeypatch.setenv(ti.ENABLED_ENV, "1")
    _recording_launcher(tmp_path, monkeypatch, rc=126)
    _unowned(monkeypatch)
    (tmp_path / WS).mkdir()
    with pytest.raises(ti.ProvisionRefused, match="stand-in"):
        ti.ensure_provisioned(WS, tmp_path)


def test_with_the_flag_on_and_no_launcher_provisioning_refuses(tmp_path, monkeypatch):
    monkeypatch.setenv(ti.ENABLED_ENV, "1")
    monkeypatch.setenv(ti.LAUNCHER_ENV, str(tmp_path / "missing"))
    _unowned(monkeypatch)
    (tmp_path / WS).mkdir()
    with pytest.raises(ti.LauncherUnavailable):
        ti.ensure_provisioned(WS, tmp_path)


def test_provisioning_closes_the_workspaces_root_to_listing_and_the_registry_to_runs(
    tmp_path, monkeypatch
):
    """A run must traverse the root to reach its own tree, so the root keeps o+x; it must not LIST every other
    workspace, nor read the registry that names them all."""
    monkeypatch.setenv(ti.ENABLED_ENV, "1")
    _recording_launcher(tmp_path, monkeypatch)
    _unowned(monkeypatch)
    root = tmp_path / "ws-root"
    (root / WS).mkdir(parents=True)
    root.chmod(0o755)
    # a registry written before this change, already holding this workspace (so nothing rewrites it on the way)
    (root / "identities.json").write_text(f'{{"{WS}": {ti.POOL_MIN}}}')
    (root / "identities.json").chmod(0o644)
    ti.ensure_provisioned(WS, root)
    assert root.stat().st_mode & 0o777 == 0o711
    assert (root / "identities.json").stat().st_mode & 0o777 == 0o600


def test_a_registry_the_backend_writes_is_private_to_the_backend(tmp_path):
    ti.identity_for(WS, tmp_path)
    assert (tmp_path / "identities.json").stat().st_mode & 0o777 == 0o600


def test_starting_a_tenant_run_provisions_its_tree_before_the_plan_is_made(tmp_path, monkeypatch):
    pytest.importorskip("claude_agent_sdk")
    from agent.session import build_agent_options
    from tests.agent.test_tenant_transcripts import _confined_callback

    log = _recording_launcher(tmp_path, monkeypatch)
    monkeypatch.setenv(ti.ENABLED_ENV, "1")
    cfg = _cfg(tmp_path, monkeypatch, tenant=True)
    tree = Path(cfg.content_root).parent
    ident = ti.identity_for(tree.name, tree.parent)
    # unowned until the launcher has run, as on disk
    monkeypatch.setattr(ti, "_tree_owner", lambda path: ident if log.exists() else None)
    options = build_agent_options(cfg, "acme", can_use_tool=_confined_callback())
    assert log.read_text().startswith(f"--provision {tree.resolve()} ")
    assert options.env["GTM_RUN_UID"] == str(ident)


def test_building_a_tenant_config_never_provisions(tmp_path, monkeypatch):
    """The refusal-audit paths build a tenant config too (a refused service principal's denials row): they must not
    walk a tree, nor fail on one, so provisioning lives on the run-start path only."""
    log = _recording_launcher(tmp_path, monkeypatch)
    monkeypatch.setenv(ti.ENABLED_ENV, "1")
    _unowned(monkeypatch)
    _cfg(tmp_path, monkeypatch, tenant=True)
    assert not log.exists()


def test_the_repair_command_provisions_every_workspace_tree_and_nothing_else(
    tmp_path, monkeypatch, capsys
):
    """For a restored backup: every tree is walked again, whoever its root says owns it, whatever the flag."""
    monkeypatch.delenv(ti.ENABLED_ENV, raising=False)
    log = _recording_launcher(tmp_path, monkeypatch)
    root = tmp_path / "ws-root"
    for name in ("ws-1", "ws-2", ".launch"):
        (root / name).mkdir(parents=True)
    (root / "identities.json").write_text("{}")
    (root / "linked").symlink_to(root / "ws-1")
    assert ti.main(["provision", "--all", "--root", str(root)]) == 0
    calls = log.read_text().splitlines()
    assert [c.split()[1] for c in calls] == [
        str((root / "ws-1").resolve()),
        str((root / "ws-2").resolve()),
    ]
    assert "ws-1" in capsys.readouterr().out


def test_the_repair_command_names_one_workspace_and_reports_a_refusal(
    tmp_path, monkeypatch, capsys
):
    _recording_launcher(tmp_path, monkeypatch, rc=126)
    root = tmp_path / "ws-root"
    (root / "ws-1").mkdir(parents=True)
    assert ti.main(["provision", "ws-1", "--root", str(root)]) == 1
    assert "ws-1" in capsys.readouterr().err


def test_the_repair_command_refuses_a_workspace_with_no_tree(tmp_path, monkeypatch, capsys):
    log = _recording_launcher(tmp_path, monkeypatch)
    root = tmp_path / "ws-root"
    root.mkdir()
    assert ti.main(["provision", "ws-9", "--root", str(root)]) == 1
    assert not log.exists() and "ws-9" in capsys.readouterr().err


@pytest.mark.private_tree
def test_the_backend_image_compiles_provisioning_for_the_backends_tree_and_user():
    import re

    text = (REPO / "deploy" / "Dockerfile.backend").read_text()
    prefix = re.search(r"-DWORKSPACES_PREFIX='\"([^\"]+)\"'", text).group(1)
    backend_uid = re.search(r"-DBACKEND_UID=(\d+)", text).group(1)
    assert prefix == "/app/data/workspaces/"
    assert backend_uid == re.search(r"useradd [^\n]*--uid (\d+)", text).group(1)


def test_a_tree_restored_closed_to_the_backend_is_still_repaired_and_gets_its_run_dirs(
    tmp_path, monkeypatch
):
    """A backup restored as root leaves the tree unwritable by the backend until the launcher grants it; the run's
    HOME/TMPDIR cannot be made first, so they are made after the grant instead of failing the repair."""
    log = tmp_path / "launcher.log"
    launcher = tmp_path / "gtm-launch"
    # the stand-in grants what the real one's ACL would: the backend may write the tree again
    launcher.write_text(f'#!/bin/sh\necho "$*" >> {log}\nchmod 0700 "$2"\n')
    launcher.chmod(0o755)
    monkeypatch.setenv(ti.LAUNCHER_ENV, str(launcher))
    root = tmp_path / "ws-root"
    tree = root / "ws-1"
    tree.mkdir(parents=True)
    tree.chmod(0o500)
    try:
        assert ti.main(["provision", "ws-1", "--root", str(root)]) == 0
    finally:
        tree.chmod(0o700)
    assert log.exists()
    assert (tree / ".run" / "home").is_dir() and (tree / ".run" / "tmp").is_dir()


# ── T7: gtm_launch.c kernel rlimits & GTM_TENANT_LAUNCHER path error guard ────


@pytest.mark.private_tree
def test_t7a_gtm_launch_c_enforces_rlimits_after_setresuid_and_unsets_env():
    """T7a (R7): gtm_launch.c unsets GTM_RLIMIT_* before fork() and calls setrlimit(RLIMIT_NPROC/FSIZE/CPU) in become() after setresuid()."""
    c_src = (REPO / "deploy" / "launcher" / "gtm_launch.c").read_text(encoding="utf-8")
    for sym in (
        "RLIMIT_NPROC",
        "RLIMIT_FSIZE",
        "RLIMIT_CPU",
        "GTM_RLIMIT_NPROC",
        "GTM_RLIMIT_FSIZE",
        "GTM_RLIMIT_CPU",
        'unsetenv("GTM_RLIMIT_NPROC")',
        'unsetenv("GTM_RLIMIT_FSIZE")',
        'unsetenv("GTM_RLIMIT_CPU")',
    ):
        assert sym in c_src, f"gtm_launch.c missing {sym}"

    # Default RLIMIT_NPROC must be 1024 (safe for 3 concurrent runs per workspace UID)
    assert "1024" in c_src
    # setrlimit must occur AFTER setresuid inside become()
    become_body = c_src.split("static void become(", 1)[1].split("static void supervise(", 1)[0]
    assert become_body.index("setresuid(") < become_body.index("setrlimit(")


def test_t7b_setting_gtm_tenant_launcher_to_a_path_raises_actionable_error(tmp_path, monkeypatch):
    """T7b (R7): Setting GTM_TENANT_LAUNCHER=/usr/local/sbin/gtm-launch raises LauncherUnavailable naming GTM_TENANT_LAUNCHER=1 and GTM_TENANT_LAUNCHER_PATH."""
    monkeypatch.setenv(ti.ENABLED_ENV, "/usr/local/sbin/gtm-launch")
    with pytest.raises(ti.LauncherUnavailable) as exc_info:
        ti.plan_for(WS, tmp_path, run_dir=tmp_path / "run")
    msg = str(exc_info.value)
    assert "GTM_TENANT_LAUNCHER=1" in msg
    assert "GTM_TENANT_LAUNCHER_PATH" in msg
