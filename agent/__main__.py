"""Headless CLI entrypoint for the brain — for cron skill runs and one-shots.

Usage:
    python -m agent --profile <name> "<prompt>"     # one-shot, streams to stdout
    python -m agent "<prompt>"                       # uses ACTIVE_PROFILE default
    python -m agent --list-profiles                  # print available profiles
    python -m agent --pipeline --profile <name>      # full radar→plan pipeline (cron mode)
    python -m agent --pack <p> --variant <v> --profile <name>          # run a pack graph
    python -m agent --pack <p> --variant <v> --from-node <id> ...      # re-run from a node

This path drives a one-shot turn over a persistent ``ClaudeSDKClient`` (via
:func:`agent.session.stream_brain_messages`) with the SAME options
:class:`agent.session.AgentSession` builds, so a cron run (e.g. ``--profile acme
"run the market-scan skill"``) behaves identically to an interactive turn — same
plugin, same MCP servers, same system prompt, model defaulting to Claude. The
persistent client (not the module-level ``query()``) keeps the permission
control stream open so ``can_use_tool``-gated tools work headless (see
``stream_brain_messages``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from gtm_core.locks import profile_lock

from . import permissions, profiles
from .config import Config
from .session import build_agent_options, stream_brain_messages

logger = logging.getLogger("agent.__main__")


async def _run_once(cfg: Config, profile: str, prompt: str) -> int:
    """Run a single one-shot query and stream assistant text to stdout.

    Returns a process exit code: 0 on success, 1 if the SDK raised.
    Holds profile_lock for the duration so cron runs and Telegram runs do not
    interleave on the same profile's content/ state tree.
    """
    # Imported here so --list-profiles works without claude-agent-sdk installed.
    from claude_agent_sdk import AssistantMessage, TextBlock

    # Unattended path: there is no operator to ask, so the policy is fail-closed
    # (allow known-safe, deny everything else). Log denials to stderr so a cron run
    # that hit the policy is visible in the journal, AND to the profile's
    # denials.jsonl so it is a queryable record, not just a grep (P0-1).
    from .denial_log import make_denial_sink

    _ledger_sink = make_denial_sink(cfg, profile, "one-shot")

    def _log_deny(tool_name: str, tool_input: dict, decision: str) -> None:
        print(f"[agent] permission policy {decision}: tool={tool_name}", file=sys.stderr)
        _ledger_sink(tool_name, tool_input, decision)

    options = build_agent_options(
        cfg,
        profile,
        can_use_tool=permissions.make_headless_can_use_tool(on_deny=_log_deny),
    )
    try:
        # Serialize against cockpit runs on the same profile's state tree.
        with profile_lock(cfg.content_root, profile, blocking=True):
            async for msg in stream_brain_messages(options, prompt):
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            # Stream incrementally; flush so cron logs show progress.
                            sys.stdout.write(block.text)
                            sys.stdout.flush()
        sys.stdout.write("\n")
        return 0
    except Exception as exc:  # surface, don't swallow — cron needs a nonzero exit
        print(f"\n[agent] run failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent",
        description="Headless Content OS brain (Claude Code via the Agent SDK).",
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        help="The prompt / skill invocation to run (omit when using --list-profiles or --pipeline).",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Company profile to bind for this run (default: ACTIVE_PROFILE env / 'template').",
    )
    parser.add_argument(
        "--list-profiles",
        action="store_true",
        help="List available profiles and exit.",
    )
    parser.add_argument(
        "--pipeline",
        action="store_true",
        help="Run the full radar→plan pipeline (cron mode). Reads pipeline_cron_day from settings.json.",
    )
    parser.add_argument(
        "--source",
        choices=["news", "journey"],
        default="news",
        help="Content source for --pipeline mode: 'news' (default) or 'journey' (reads build history).",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help=(
            "When used with --pipeline --source journey: ignore the watermark and scan "
            "from the first commit. Sets since_sha=None in builder-radar."
        ),
    )
    parser.add_argument(
        "--pack",
        default=None,
        help="Run a pack graph (e.g. 'prospecting') instead of the built-in DEFAULT_GRAPH.",
    )
    parser.add_argument(
        "--variant",
        default=None,
        help="Which variant of --pack to run (e.g. 'prospect-outreach'). Required with --pack.",
    )
    parser.add_argument(
        "--from-node",
        default=None,
        metavar="NODE_ID",
        help=(
            "Re-enter an existing run at NODE_ID: that node and everything downstream of it "
            "are marked re-runnable, upstream work is kept. Requires --run-id."
        ),
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Reuse an existing run id (required by --from-node; otherwise auto-generated).",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Override the repo root (defaults to the parent of the agent/ package).",
    )
    return parser


async def _run_pipeline(
    cfg: Config, profile: str, source: str = "news", backfill: bool = False
) -> int:
    """Run the radar → plan pipeline in cron mode.

    1. Reads the **source-specific** cron day from ``content/<profile>/settings.json``:
       ``journey_cron_day`` (default ``"friday"``) for the journey source, else
       ``pipeline_cron_day`` (default ``"monday"``) for news. The two sources run on
       different weekly cadences (their systemd timers fire on different days), so a single
       shared key would make one of them silently skip every week.
    2. Exits 0 ("not today") if today's weekday doesn't match — unless ``backfill`` is set,
       which is an explicit operator override that always runs.
    3. Runs each stage via the executor (which acquires the per-profile lock) and stops at
       ``AWAITING_APPROVAL`` (Gate 1) — the operator handles the gate via Telegram.
    """
    from .pipeline import AWAITING_APPROVAL as GATE
    from .pipeline import FAILED, PipelineRunner, terminal_status
    from .pipeline_executor import make_executor

    # The two sources have independent weekly cadences (see systemd/gtm-*.timer), so each
    # reads its own cron-day key. A single shared key would make the off-day source skip
    # forever (e.g. journey timer fires Friday but a Monday default would always "not today").
    cron_day_key, default_day = (
        ("journey_cron_day", "friday") if source == "journey" else ("pipeline_cron_day", "monday")
    )
    settings_path = cfg.content_root / profile / "settings.json"
    cron_day = default_day
    if settings_path.exists():
        try:
            with settings_path.open() as f:
                settings = json.load(f)
            cron_day = settings.get(cron_day_key, default_day).lower()
        except Exception:  # noqa: BLE001
            pass  # nosec B110 — corrupt settings → keep default; never blocks the cron gate

    today = datetime.now().strftime("%A").lower()
    if today != cron_day and not backfill:
        print(
            f"[pipeline] not today (source={source}, today={today}, cron_day={cron_day}) — skipping",
            flush=True,
        )
        return 0

    run_id = f"r-{datetime.now():%Y%m%d-%H%M}-{source}"
    print(f"[pipeline] starting run_id={run_id} profile={profile}", flush=True)

    runner = PipelineRunner(cfg, profile)
    executor = make_executor(cfg, profile, source=source)
    trigger = "backfill" if (source == "journey" and backfill) else "cron"

    manifest = await runner.run(run_id, trigger=trigger, executor=executor)
    # runner.run returns the *manifest*; reduce it to a terminal status. Comparing the dict to a
    # status string directly never matches — it silently skipped the Gate 1 push and let a FAILED
    # run exit 0 (systemd saw success). See agent.pipeline.terminal_status.
    status = terminal_status(manifest)

    if status == GATE:
        print(f"[pipeline] run_id={run_id} paused at Gate 1 (plan awaiting approval)", flush=True)
        try:
            from agent.gate_notify import push_gate1

            await push_gate1(cfg, cfg.profiles_root, profile, run_id)
        except Exception:  # noqa: BLE001
            logger.warning("Gate 1 Telegram push failed", exc_info=True)
        return 0

    if status == FAILED:
        print(f"[pipeline] run_id={run_id} FAILED — check logs", file=sys.stderr)
        return 1

    print(f"[pipeline] run_id={run_id} complete: {status}", flush=True)
    return 0


async def _run_pack(
    cfg: Config,
    profile: str,
    pack: str,
    variant: str,
    *,
    from_node: str | None = None,
    run_id: str | None = None,
) -> int:
    """Run a pack graph from the CLI — the laptop/VPS twin of the backend's pack path.

    Mirrors ``backend/services/runs/pack_executor.py:_execute_pack_run``, minus the workspace scoping and
    entitlement resolution that only exist server-side. Three deliberate differences from
    :func:`_run_pipeline`:

    * **No cron-day guard.** That guard is keyed on ``pipeline_cron_day``/``journey_cron_day``,
      which describe the news and journey sources. A pack run is operator-initiated and has
      no weekly cadence to gate on.
    * **The run id carries pack/variant.** ``_run_pipeline``'s id is minute-granular, so two
      runs started in the same minute resume each other's manifest. Including pack/variant
      keeps concurrent runs of different graphs apart.
    * **Tenant activation is enforced.** A pack absent from ``profiles/<p>/packs.toml`` is
      refused here, matching ``backend/pack_catalog.resolve_variant``. Without this the CLI
      would be a way around the activation the backend enforces.
    """
    from gtm_core.packs.loader import PackValidationError
    from gtm_core.packs.tenant import load_pack_activation

    from .packs import load_engine_graph, make_executor_from_pack
    from .pipeline import AWAITING_APPROVAL as GATE
    from .pipeline import FAILED, PipelineRunner, reset_from_node, terminal_status

    packs_toml = cfg.profiles_root / profile / "packs.toml"
    if not packs_toml.is_file():
        print(
            f"[pack] profile {profile!r} has no packs.toml, so no pack is active for it — "
            f"fail-closed, exactly as backend pack-mode runs behave",
            file=sys.stderr,
        )
        return 1
    try:
        activation = load_pack_activation(packs_toml)
    except PackValidationError as exc:
        print(f"[pack] cannot read {packs_toml}: {exc}", file=sys.stderr)
        return 1
    if pack not in activation.active:
        active = ", ".join(sorted(activation.active)) or "(none)"
        print(
            f"[pack] pack {pack!r} is not active for profile {profile!r} (active: {active}) — "
            f"add it to profiles/{profile}/packs.toml",
            file=sys.stderr,
        )
        return 1

    graph_path = cfg.repo_root / "packs" / pack / "graphs" / f"{variant}.toml"
    if not graph_path.is_file():
        print(f"[pack] no such variant: {graph_path}", file=sys.stderr)
        return 1
    try:
        pack_graph, engine_graph = load_engine_graph(graph_path)
    except PackValidationError as exc:
        print(f"[pack] {graph_path.name} failed validation: {exc}", file=sys.stderr)
        return 1

    run_id = run_id or f"r-{datetime.now():%Y%m%d-%H%M}-{pack}-{variant}"
    runner = PipelineRunner(cfg, profile, graph=engine_graph)
    executor = make_executor_from_pack(cfg, profile, pack_graph)

    manifest: dict | None = None
    if from_node is not None:
        manifest_path = cfg.content_root / profile / "runs" / f"{run_id}.json"
        if not manifest_path.is_file():
            print(
                f"[pack] --from-node needs an existing run to re-enter, and there is no "
                f"manifest at {manifest_path}. Pass the --run-id of a previous run.",
                file=sys.stderr,
            )
            return 1
        with manifest_path.open() as f:
            manifest = json.load(f)
        try:
            reset = reset_from_node(engine_graph, manifest, from_node)
        except KeyError:
            ids = ", ".join(engine_graph.ids)
            print(f"[pack] no node {from_node!r} in {variant} (nodes: {ids})", file=sys.stderr)
            return 1
        print(f"[pack] re-entering {run_id} at {from_node!r}: resetting {', '.join(reset)}")

    print(f"[pack] starting run_id={run_id} profile={profile} graph={pack}/{variant}", flush=True)
    manifest = await runner.run(run_id, trigger="cli", executor=executor, manifest=manifest)
    status = terminal_status(manifest)

    if status == GATE:
        gated = [s["name"] for s in manifest["stages"] if s.get("status") == GATE]
        print(f"[pack] run_id={run_id} paused at gate: {', '.join(gated)}", flush=True)
        return 0
    if status == FAILED:
        print(f"[pack] run_id={run_id} FAILED — check logs", file=sys.stderr)
        return 1
    print(f"[pack] run_id={run_id} complete: {status}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint. Returns a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    cfg = Config.from_env(repo_root=args.repo_root)

    # --list-profiles short-circuits before touching the SDK.
    if args.list_profiles:
        names = profiles.list_profiles(cfg.profiles_root)
        if not names:
            print("(no profiles found under profiles/)", file=sys.stderr)
            return 1
        for name in names:
            marker = " (default)" if name == cfg.default_profile else ""
            print(f"{name}{marker}")
        return 0

    profile = args.profile or cfg.default_profile
    # Validate the profile up front so a typo fails fast with a clear message.
    try:
        profiles.profile_dir(cfg.profiles_root, profile)
    except ValueError as exc:
        print(f"[agent] {exc}", file=sys.stderr)
        return 1

    if args.pack or args.variant:
        if not (args.pack and args.variant):
            parser.error("--pack and --variant must be given together")
        if args.from_node and not args.run_id:
            parser.error("--from-node re-enters an existing run, so it requires --run-id")
        return asyncio.run(
            _run_pack(
                cfg,
                profile,
                args.pack,
                args.variant,
                from_node=args.from_node,
                run_id=args.run_id,
            )
        )

    if args.from_node:
        parser.error("--from-node only applies to a pack run (--pack/--variant)")

    if args.pipeline:
        return asyncio.run(_run_pipeline(cfg, profile, source=args.source, backfill=args.backfill))

    if not args.prompt:
        parser.error("a prompt is required (or pass --list-profiles / --pipeline)")

    return asyncio.run(_run_once(cfg, profile, args.prompt))


if __name__ == "__main__":
    raise SystemExit(main())
