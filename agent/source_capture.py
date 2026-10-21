"""``python -m agent.source_capture --profile P [--product S]``: the operator's capture step.

Signal-first sourcing (capture step). One command, for
an operator on the VPS or a laptop, never a tenant:

1. opens only with ``GTM_SIGNAL_SOURCES_ENABLED`` (closed: prints why and exits 0, like every
   ``signal_obs`` command);
2. writes the manifest of the sources that are due (``signal_obs.due.write_due_manifest``);
3. runs the internal ``source-capture`` graph with that manifest named as its run input, so the
   runner builds the capture gate from it and the brain can scrape only what the manifest lists;
4. prints what it did.

It exists because the one other operator path, ``python -m agent --pack``, has no way to pass a run
input, and the graph is ``internal`` (the backend cannot run it). The runner, the locks, the §R2
guard and the gate are the ones every pack run uses; only the two steps around them are new.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import sys
from pathlib import Path

from gtm_core import capture_manifest, run_scope
from gtm_core.packs.loader import PackValidationError
from gtm_core.packs.tenant import load_pack_activation
from gtm_core.signal_obs import due, registry, switch
from gtm_core.signal_sources import get_captures, sources_dir_for

from .config import Config
from .egress_scope import EgressScopeError, valid_run_id
from .packs import load_engine_graph, make_executor_from_pack
from .pipeline import PipelineRunner, terminal_status

PACK = "prospecting"
VARIANT = "source-capture"


def _pack_is_active(cfg: Config, profile: str) -> str | None:
    """None when the profile activates the pack; otherwise the sentence saying why not."""
    packs_toml = cfg.profiles_root / profile / "packs.toml"
    if not packs_toml.is_file():
        return f"profile {profile!r} has no packs.toml, so no pack is active for it"
    try:
        activation = load_pack_activation(packs_toml)
    except PackValidationError as exc:
        return f"cannot read {packs_toml}: {exc}"
    if PACK not in activation.active:
        return f"pack {PACK!r} is not active for profile {profile!r}: add it to {packs_toml}"
    return None


def _stamp() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%S%f")


def _utc(stamp: str) -> datetime.datetime:
    t = datetime.datetime.fromisoformat(stamp)
    return t if t.tzinfo else t.replace(tzinfo=datetime.UTC)


def _unfiled(cfg: Config, profile: str, urls: list[str], since: datetime.datetime) -> list[str]:
    """The manifest URLs with no capture filed since ``since``: a run that reports ok can still
    have filed nothing, and a stale page from an earlier run must not stand in for this one."""
    sources_dir = sources_dir_for(profile, cfg.content_root)
    missing = []
    for url in urls:
        filed = (
            _utc(c.fetched_at) for c in get_captures(url, sources_dir=sources_dir) if c.fetched_at
        )
        if not any(t >= since for t in filed):
            missing.append(url)
    return missing


def _capture(cfg: Config, profile: str, manifest_run_id: str, urls: list[str]) -> int:
    pages = len(urls)
    graph_path = cfg.repo_root / "packs" / PACK / "graphs" / f"{VARIANT}.toml"
    pack_graph, engine_graph = load_engine_graph(graph_path)
    try:
        executor = make_executor_from_pack(
            cfg, profile, pack_graph, run_inputs={"manifest_run_id": manifest_run_id}
        )
    except EgressScopeError as exc:
        print(f"Refused: {exc}")
        return 2
    run_id = f"r-{_stamp()}-{VARIANT}"
    started = datetime.datetime.now(datetime.UTC)
    print(f"Capturing {pages} page{'s' if pages != 1 else ''} as run {run_id}.")
    manifest = asyncio.run(
        PipelineRunner(cfg, profile, graph=engine_graph).run(run_id, "cli", executor)
    )
    for stage in manifest["stages"]:
        error = f": {stage['error']}" if stage.get("error") else ""
        print(f"- {stage['name']}: {stage['status']}{error}")
    status = terminal_status(manifest)
    print(f"Capture run {run_id} finished: {status}.")
    if status != "ok":
        return 1
    if missing := _unfiled(cfg, profile, urls, started):
        for url in missing:
            print(f"Page not filed: {url}")
        print(
            "The run reported ok but these pages were not filed, so nothing was captured for them."
        )
        return 1
    return 0


def run(cfg: Config, profile: str, product: str | None = None, *, run_id: str | None = None) -> int:
    """The whole command. Returns the process exit code (0 done or nothing to do, 1 failed, 2 refused)."""
    if not switch.sources_enabled():
        print(switch.DISABLED_MESSAGE)
        return 0
    manifest_run_id = run_id if run_id is not None else f"cap-{_stamp()}"
    if not valid_run_id(manifest_run_id):
        print(
            f"Refused: unsafe run id {manifest_run_id!r}; use letters, digits, dot, dash, underscore."
        )
        return 2
    if (why := _pack_is_active(cfg, profile)) is not None:
        print(f"Refused: {why}.")
        return 2
    try:
        due_report = due.due_sources(
            profile, product, content_root=cfg.content_root, profiles_root=cfg.profiles_root
        )
        path = due.write_due_manifest(
            profile,
            product,
            run_id=manifest_run_id,
            content_root=cfg.content_root,
            profiles_root=cfg.profiles_root,
        )
    except (run_scope.ScopeError, registry.RegistryError, capture_manifest.ManifestError) as exc:
        print(f"Refused: {exc}")
        return 2
    if path is None:
        print("Nothing is due, so no manifest was written and no page was fetched.")
        return 0
    manifest = capture_manifest.load_manifest(path)
    print(f"Manifest: {path} ({len(manifest.urls)} page{'s' if len(manifest.urls) != 1 else ''}).")
    waiting = len(due_report.due) - len(manifest.urls)
    if waiting > 0:
        print(
            f"{waiting} more {'is' if waiting == 1 else 'are'} due and wait for the next run "
            f"(the page budget per run is {len(manifest.urls)}; change it with "
            "signal_monitor_max_captures in the profile's settings.json)."
        )
    return _capture(cfg, profile, manifest_run_id, manifest.urls)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent.source_capture", description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--product", default=None)
    parser.add_argument(
        "--run-id", default=None, help="Name the manifest (default: a fresh cap-<timestamp>)."
    )
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(argv)
    cfg = Config.from_env(repo_root=args.repo_root)
    return run(cfg, args.profile, args.product, run_id=args.run_id)


if __name__ == "__main__":
    sys.exit(main())
