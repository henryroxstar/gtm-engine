"""Contract: the deploy fires on everything that ships, and ships everything it fires on.

Two defects, measured 2026-08-27, in opposite directions.

FORWARD — a shipped surface that triggers no deploy. ``deploy.yml``'s ``paths:`` filter
omits ``gtm_core/**`` (baked into the image), ``packs/**``, ``backend/**``,
``mcp_server/**``, ``tests/linter/**`` and ``docs/**``. A change to any of them merges to
main and the box never learns: the baked ones need a rebuild that never fires, and the
bind-mounted ones come from the box's ``git pull``, which only runs during a deploy — so
the mounted tree silently goes stale against the repo.

REVERSE — a filter entry that ships nothing. ``.claude/**`` IS in the filter, carrying the
comment *"runtime-loaded permission backstop + skills/commands"* — and ``.claude/`` is
neither ``COPY``'d nor mounted, so the entry deploys nothing at all. A path filter entry
whose comment asserts a runtime role it does not have is the same failure shape as a
registry role with no consumer, pointed the other way. Hence: both directions, one rule.

Plus T5 — the deploy script pulls and rebuilds but never runs ``systemd/install.sh``, so
repo unit files never reach ``/etc/systemd/system``. That is the verbatim 2026-06-26
incident that ``install.sh``'s own header documents ("the box ran Jun-16 units while the
repo was Jun-22"), still open.

Fixtures: every synthetic workflow/Dockerfile below is invented (fictional directory names
only), per the third-party-PII rule (docs/RULES.md §R9).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.contracts._deploy_surface import (
    copy_targets,
    deploy_path_filter,
    mount_sources,
    undelivered,
)

REPO = Path(__file__).resolve().parents[2]
DEPLOY_WORKFLOW = REPO / ".github" / "workflows" / "deploy.yml"

#: The BACKEND stack — postgres/api/redis/mcp/cloudflared, a second Compose project on the
#: same box, built from its own Dockerfiles and deployed by its own workflow. It shares no
#: image with the agent stack, so its shipped surface has to be derived separately: before
#: 2026-08-29 nothing asserted it at all and it had no automated deploy of any kind.
BACKEND_WORKFLOW = REPO / ".github" / "workflows" / "deploy-staging.yml"
BACKEND_COMPOSE = REPO / "deploy" / "docker-compose.yml"
BACKEND_DOCKERFILES = ("deploy/Dockerfile.backend", "deploy/Dockerfile.mcp")

if not DEPLOY_WORKFLOW.exists() or not (REPO / "docker-compose.yml").exists():
    # Both stacks' deploy surfaces (deploy.yml, deploy-staging.yml, docker-compose.yml and
    # the whole deploy/ tree) are excluded from the OSS carve by design.
    pytest.skip("deploy workflow/compose not present (private surface)", allow_module_level=True)

#: FORWARD exemptions — delivered, but deliberately not deploy triggers.
#: `.git`      — VCS metadata mounted for the journey gitscan; not a committable source path.
#: `content`   — gitignored runtime state; never in git, so it can never trigger a path filter.
#: `README.md` — COPY'd by both backend Dockerfiles so `pip install .` can resolve the
#:               project metadata. It carries no runtime behaviour, so rebuilding the API on
#:               a README edit would be pure noise. Unlike the two above this one IS in git,
#:               which is exactly why it needs stating: it would otherwise read as an
#:               oversight rather than a decision.
_NOT_A_TRIGGER = frozenset({".git", "content", "README.md"})

#: REVERSE exemptions — legitimate filter entries that are build inputs rather than
#: paths delivered INTO the container. Changing any of them must rebuild.
_BUILD_INPUTS = frozenset({"Dockerfile", "docker-compose.yml", "pyproject.toml"})

#: REVERSE exemptions — shipped to the HOST rather than into the container. `systemd/`
#: is installed to /etc/systemd/system by the deploy's install.sh step, so "does a
#: container receive it?" is the wrong question for it. This exemption is not a free
#: pass: `test_the_host_installed_exemption_is_earned` below asserts the deploy really
#: does install it, so the entry cannot become orphaned again behind this comment.
_HOST_INSTALLED = frozenset({"systemd"})

#: REVERSE exemption for the BACKEND filter — `deploy/**` names that stack's own build
#: inputs (its two Dockerfiles and its compose files), so nothing COPYs "deploy" itself.
#: Earned the same way `_HOST_INSTALLED` is: `test_the_second_stack_exemption_is_earned`
#: asserts the backend workflow really does build from `deploy/docker-compose.yml`.
_SECOND_STACK = frozenset({"deploy"})

#: REVERSE exemption — the deck-renderer SIDECAR's build context. It is a second image in
#: the SAME Compose project and the SAME deploy, but built from its own Dockerfile with
#: `docker/deck-renderer` as its context root. So its COPY paths (`deck-theme`, `server.js`,
#: `package.json`, `make_pptx.py`) are context-relative and mean nothing as repo-root paths
#: — the honest unit is the context directory itself, exactly as `Dockerfile` is for the
#: agent image. Added 2026-08-29 after the theme baked into that image sat 15 days behind
#: `main`: `docker/**` was in no filter, so a theme change triggered no deploy at all and
#: the box kept rendering with the stale copy. Earned by
#: `test_the_sidecar_build_context_exemption_is_earned`.
_SIDECAR_BUILD_CONTEXT = frozenset({"docker/deck-renderer"})


def _filter_entries() -> set[str]:
    return deploy_path_filter(DEPLOY_WORKFLOW.read_text(encoding="utf-8"))


def _baked() -> set[str]:
    return copy_targets((REPO / "Dockerfile").read_text(encoding="utf-8"))


def _mounted() -> set[str]:
    return mount_sources((REPO / "docker-compose.yml").read_text(encoding="utf-8"))


def _deploy_script() -> str:
    """The workflow's inline deploy `script:` body."""
    return DEPLOY_WORKFLOW.read_text(encoding="utf-8")


def _backend_workflow_text() -> str:
    return BACKEND_WORKFLOW.read_text(encoding="utf-8")


def _backend_filter_entries() -> set[str]:
    return deploy_path_filter(_backend_workflow_text())


def _backend_baked() -> set[str]:
    """Union of both backend images' COPY lists.

    `api` and `mcp` are separate services built from separate Dockerfiles, but they deploy
    as one Compose project from one workflow — so a change to anything either image bakes
    has to trigger that one workflow. Unioning is the honest question to ask of it.
    """
    out: set[str] = set()
    for rel in BACKEND_DOCKERFILES:
        out |= copy_targets((REPO / rel).read_text(encoding="utf-8"))
    return out


# ── non-vacuity: a broken parser must fail LOUD, not pass empty ───────────────


def test_the_deploy_path_filter_parse_is_non_vacuous():
    entries = _filter_entries()
    assert {"agent", "cockpit", "plugin", "profiles"} <= entries, (
        f"deploy.yml paths: parse looks broken — got {sorted(entries)}. "
        "Every assertion in this file is vacuous if this set is wrong."
    )


def test_the_baked_and_mounted_extraction_is_non_vacuous():
    assert {"agent", "gtm_core"} <= _baked(), "Dockerfile COPY parse drifted"
    assert {"plugin", "profiles"} <= _mounted(), "compose source: parse drifted"


# ── T2: the filter is closed in BOTH directions ──────────────────────────────


def test_every_baked_or_mounted_path_triggers_a_deploy():
    """Forward: a surface that ships must be able to trigger the deploy that ships it."""
    shipped = (_baked() | _mounted()) - _NOT_A_TRIGGER
    missing = undelivered(shipped, _filter_entries())
    assert not missing, (
        f"{sorted(missing)} is baked into the image or bind-mounted, but is absent from "
        "deploy.yml's paths: filter — so changing it triggers no deploy and the box keeps "
        "running the old copy. Add it to the filter, or add it to _NOT_A_TRIGGER with a "
        "comment saying why it is deliberately not a trigger."
    )


def test_the_host_installed_exemption_is_earned():
    """`systemd/` may skip container delivery only because the deploy installs it to /etc.

    Without this, `_HOST_INSTALLED` would be a comment that permanently excuses an
    orphaned filter entry — the exact shape (a claim in prose, nothing behind it) that
    this file exists to catch.
    """
    assert "systemd/install.sh" in _deploy_script(), (
        "systemd/ is exempted from container delivery on the grounds that the deploy "
        "installs it to the host — but the deploy no longer runs systemd/install.sh, so "
        "the exemption is now excusing a filter entry that ships nowhere at all."
    )


def test_the_sidecar_build_context_exemption_is_earned():
    """`docker/deck-renderer/**` may skip the COPY/mount derivation only because it IS a
    build context this deploy builds from.

    Without this the exemption would be a comment permanently excusing a filter entry —
    the same shape the rest of this file exists to catch. Two things have to hold: compose
    really builds a service from that context, and that context really contains the theme
    the sidecar bakes.
    """
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    assert "./docker/deck-renderer" in compose, (
        "docker/deck-renderer/** is exempted from the COPY/mount derivation on the grounds "
        "that it is a build context this stack builds from — but docker-compose.yml no "
        "longer builds anything from it, so the entry now rebuilds nothing."
    )
    theme = REPO / "docker" / "deck-renderer" / "deck-theme"
    assert theme.is_dir() and any(theme.rglob("*.vue")), (
        f"{theme.relative_to(REPO)} is the deck-theme's home (see its REFRESH.md) and is "
        "baked into the sidecar image. It is empty or gone, so the filter entry that "
        "exists to redeploy it is guarding nothing."
    )


def test_every_deploy_path_filter_entry_reaches_the_container():
    """Reverse: a filter entry that ships nothing is a comment pretending to be wiring."""
    delivered = _baked() | _mounted() | _BUILD_INPUTS | _HOST_INSTALLED | _SIDECAR_BUILD_CONTEXT
    orphaned = undelivered(_filter_entries(), delivered)
    assert not orphaned, (
        f"deploy.yml's paths: filter names {sorted(orphaned)}, but nothing COPYs or mounts "
        "it, so a change there rebuilds an image that never receives it. Either deliver the "
        "path (a read-only mount, per the plugin/ pattern) or drop the filter entry — and "
        "drop any comment claiming it is runtime-loaded."
    )


# ── the SECOND stack: the backend API gets the same rule, not an exemption ───


def test_the_backend_stack_has_a_deploy_workflow():
    """The API stack must ship by an automated path, not by an operator remembering.

    Until 2026-08-29 `deploy/docker-compose.yml` was brought up only by hand
    (`deploy/setup.sh`), so a backend change merged and reached nothing. That is the same
    defect this file already catches WITHIN a stack — a surface whose deploy never fires —
    one level up: an entire stack outside the filter's reach. `deploy.yml` cannot cover it
    (different branch, different Compose project, different health container), so the
    guard is that a workflow for it exists at all.
    """
    assert BACKEND_WORKFLOW.exists(), (
        f"{BACKEND_WORKFLOW.relative_to(REPO)} is missing, so the backend stack "
        "(deploy/docker-compose.yml — postgres/api/redis/mcp/cloudflared) has no automated "
        "deploy: a change to backend/ merges and the running API keeps the old code until "
        "someone SSHes in. Add the workflow, or delete deploy/ if the stack is retired."
    )


def test_the_backend_path_filter_parse_is_non_vacuous():
    """Every backend assertion below is vacuous if this parse is wrong."""
    entries = _backend_filter_entries()
    assert {"backend", "gtm_core", "schemas"} <= entries, (
        f"deploy-staging.yml paths: parse looks broken — got {sorted(entries)}."
    )


def test_the_backend_baked_extraction_is_non_vacuous():
    assert {"backend", "mcp_server", "schemas"} <= _backend_baked(), (
        "deploy/Dockerfile.{backend,mcp} COPY parse drifted"
    )


def test_every_backend_baked_path_triggers_the_backend_deploy():
    """Forward, for the API stack.

    `schemas/` is the one this exists to catch: those files are the client contract a client
    app vendors, they are COPY'd into the backend image, and before this test
    a change to them triggered no deploy of any kind — neither stack's filter named them.
    """
    shipped = _backend_baked() - _NOT_A_TRIGGER
    missing = undelivered(shipped, _backend_filter_entries())
    assert not missing, (
        f"{sorted(missing)} is baked into the api/mcp image but is absent from "
        "deploy-staging.yml's paths: filter — so changing it triggers no deploy and the "
        "running API keeps the old copy. Add it to the filter, or to _NOT_A_TRIGGER with a "
        "comment saying why it is deliberately not a trigger."
    )


def test_every_backend_deploy_path_filter_entry_reaches_the_backend_container():
    """Reverse, for the API stack — a filter entry that ships nothing is a comment."""
    delivered = _backend_baked() | _BUILD_INPUTS | _SECOND_STACK
    orphaned = undelivered(_backend_filter_entries(), delivered)
    assert not orphaned, (
        f"deploy-staging.yml's paths: filter names {sorted(orphaned)}, but neither backend "
        "Dockerfile COPYs it, so a change there rebuilds an image that never receives it. "
        "Either deliver the path or drop the filter entry."
    )


def test_the_second_stack_exemption_is_earned():
    """`deploy/` may skip container delivery only because it IS the stack's build input.

    Without this, `_SECOND_STACK` would be a comment permanently excusing an orphaned
    filter entry — the exact shape this file exists to catch.
    """
    assert "-f deploy/docker-compose.yml" in _backend_workflow_text(), (
        "deploy/ is exempted from container delivery on the grounds that it is the backend "
        "stack's build input — but the workflow no longer builds from "
        "deploy/docker-compose.yml, so the exemption excuses a filter entry that ships "
        "nothing at all."
    )


def test_the_backend_deploy_is_isolated_by_compose_project():
    """`-p gtm_infra_stg` is what keeps staging off prod's containers/network/volumes.

    A Compose project name is the ONLY isolation between the two stacks on this box; drop
    it and `up -d` adopts the default project's containers and named volumes — staging
    would recreate prod's database. deploy/docker-compose.stg.yml's own header says the
    isolation comes from the project name, not from that file, so it is asserted here.
    """
    text = _backend_workflow_text()
    assert "gtm_infra_stg" in text, (
        "the backend deploy does not pass -p gtm_infra_stg, so it runs in the DEFAULT "
        "Compose project and would adopt prod's containers and named volumes."
    )
    assert "-f deploy/docker-compose.stg.yml" in text, (
        "the backend deploy does not layer deploy/docker-compose.stg.yml, so staging runs "
        "without its memory/cpu caps and can starve prod on the shared box."
    )


# ── both stacks: the deploy must refuse a wrong-branch checkout ──────────────


@pytest.mark.parametrize(
    "workflow",
    [DEPLOY_WORKFLOW, BACKEND_WORKFLOW],
    ids=["agent", "backend"],
)
def test_the_deploy_refuses_a_checkout_on_the_wrong_branch(workflow: Path):
    """The box deploys whatever branch is checked out — so the branch must be asserted.

    `deploy.yml` runs `git pull --ff-only` against a checkout it never inspects, while
    the staging bring-up runbook instructs an operator to `git checkout dev` on the
    box for a staging bring-up. Nothing reconciled those two facts, so a staging session
    silently left the agent stacks armed to build from `dev` on their next deploy. Each
    workflow must name the branch it expects and stop if the checkout disagrees.
    """
    if not workflow.exists():
        pytest.fail(f"{workflow.relative_to(REPO)} is missing — see the workflow-exists test")
    text = workflow.read_text(encoding="utf-8")
    assert "EXPECT_BRANCH" in text, (
        f"{workflow.relative_to(REPO)} never checks which branch the deploy directory is "
        "on, so it deploys whatever happens to be checked out there."
    )
    guard_at = text.index("EXPECT_BRANCH")
    pull_at = text.index("git pull --ff-only")
    assert guard_at < pull_at, (
        "the branch guard runs AFTER git pull --ff-only, so it fast-forwards the wrong "
        "branch before noticing — the pull is the side effect it exists to prevent."
    )


# ── both stacks: the job must bind to the environment that holds the secrets ──


@pytest.mark.parametrize(
    "workflow",
    [DEPLOY_WORKFLOW, BACKEND_WORKFLOW],
    ids=["agent", "backend"],
)
def test_the_deploy_job_binds_to_the_environment_holding_its_secrets(workflow: Path):
    """A deploy job that never names an environment reads its secrets as empty strings.

    Every deploy credential in this repo (VPS_HOST, VPS_SSH_KEY, VPS_HOST_FINGERPRINT,
    TG_BOT_TOKEN, TG_CHAT_ID) is an ENVIRONMENT secret on `production`, not a repo-level
    Actions secret. GitHub does not error on an unresolvable `secrets.X` — it substitutes
    the empty string — so a job missing `environment:` fails deep inside the action with a
    message that names no secret at all. `deploy-staging.yml`'s first run on 2026-08-29
    died as `error: missing server host`, and its Telegram notifier answered 404, because
    the job omitted this one line while using five secrets that only exist there.
    """
    if not workflow.exists():
        pytest.fail(f"{workflow.relative_to(REPO)} is missing — see the workflow-exists test")
    text = workflow.read_text(encoding="utf-8")
    uses_env_secrets = [
        name
        for name in (
            "VPS_HOST",
            "VPS_SSH_KEY",
            "VPS_HOST_FINGERPRINT",
            "TG_BOT_TOKEN",
            "TG_CHAT_ID",
        )
        if f"secrets.{name}" in text
    ]
    if not uses_env_secrets:
        pytest.skip(f"{workflow.relative_to(REPO)} consumes no environment-scoped secret")
    assert "environment: production" in text or "environment: staging" in text, (
        f"{workflow.relative_to(REPO)} reads {sorted(uses_env_secrets)}, which live on the "
        "`production` or `staging` GitHub Environment, but the job never declares "
        "`environment: production` or `environment: staging` — so every one of them resolves to an empty string and "
        "the deploy fails with an error that names no secret."
    )


def test_the_deploy_job_is_behind_the_gitops_kill_switch(workflow=BACKEND_WORKFLOW):
    """Turning GitOps deploys off must silence BOTH stacks, not just the agent one.

    `vars.GTM_DEPLOY_ENABLED` exists so main stays green when the VPS cannot be deployed
    to. A second deploy workflow that ignores it turns that switch into a half-switch:
    deploy.yml skips while deploy-staging.yml keeps SSHing and going red on every push.
    """
    text = workflow.read_text(encoding="utf-8")
    assert "vars.GTM_DEPLOY_ENABLED" in text, (
        f"{workflow.relative_to(REPO)} ignores the GTM_DEPLOY_ENABLED kill switch, so "
        "disabling GitOps deploys would still leave this workflow failing on every push."
    )


# ── T5: the deploy actually installs the units it pulls ──────────────────────


def test_the_deploy_script_installs_units_after_pulling():
    """`git pull` updates systemd/*.service on disk; only install.sh copies them to /etc."""
    script = _deploy_script()
    assert "systemd/install.sh" in script, (
        "the deploy script never runs systemd/install.sh, so repo unit files never reach "
        "/etc/systemd/system. This is the 2026-06-26 incident that install.sh's own header "
        "documents — the box ran Jun-16 units while the repo was Jun-22."
    )
    pull_at = script.index("git pull --ff-only")
    install_at = script.index("systemd/install.sh")
    assert install_at > pull_at, (
        "systemd/install.sh runs BEFORE git pull --ff-only, so it installs the previous "
        "revision's units — the same staleness the step exists to prevent."
    )


def test_the_deploy_script_runs_the_drift_check_unsuppressed():
    """A drift check whose failure is swallowed reports nothing.

    `check-drift.sh` also catches the case `install.sh` cannot: a missing gitignored
    `docker-compose.override.yml`, which once left the news radar resolving nothing for
    ~13 days.
    """
    script = _deploy_script()
    assert "check-drift.sh" in script, (
        "the deploy never runs systemd/check-drift.sh. systemd/README.md says to wire it "
        "into cron/CI; it never was, which is why the drift it guards went unnoticed."
    )
    for line in script.splitlines():
        if "check-drift.sh" not in line:
            continue
        stripped = line.strip()
        assert not stripped.startswith("-"), f"drift check failure is suppressed: {stripped!r}"
        assert "|| true" not in stripped, f"drift check failure is swallowed: {stripped!r}"


# ── checker self-tests: prove the checkers fire on a known-bad workflow ──────


def test_the_forward_checker_detects_a_synthetic_unshipped_surface():
    workflow = "on:\n  push:\n    paths:\n      - 'widgetsvc/**'\n\njobs:\n  deploy:\n"
    baked = copy_targets("COPY widgetsvc/ ./widgetsvc/\nCOPY sprockets/ ./sprockets/\n")
    assert undelivered(baked, deploy_path_filter(workflow)) == {"sprockets"}


def test_the_reverse_checker_detects_a_synthetic_orphaned_filter_entry():
    workflow = "on:\n  push:\n    paths:\n      - 'widgetsvc/**'\n      - 'gadgets/**'\n\njobs:\n"
    delivered = copy_targets("COPY widgetsvc/ ./widgetsvc/\n")
    assert undelivered(deploy_path_filter(workflow), delivered) == {"gadgets"}


def test_the_filter_parser_stops_at_the_end_of_the_paths_block():
    """A following key must not be swallowed as a path entry."""
    workflow = (
        "on:\n  push:\n    paths:\n      - 'widgetsvc/**'\n"
        "\nconcurrency:\n  group: deploy\n      - 'not-a-path'\n"
    )
    assert deploy_path_filter(workflow) == {"widgetsvc"}
