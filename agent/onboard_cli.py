"""Deterministic CLI adapter for the onboarding engine — driven by the setup skill in
Claude Code via Bash.

Brain work (ingest a source, run the extraction LLM call, run the interview) stays in the
live agent conversation; this CLI does ONLY the mechanical, deterministic steps that
`agent/onboard.py` already implements — render a draft into files, stage them, diff
against a live profile, promote, cancel, and report status. There is deliberately no
"extract" subcommand: extraction never happens here (see the docstring in onboard.py and
the tenant-boundary rules in the repo's CLAUDE.md).

Each subcommand prints exactly one JSON object to stdout. Anything diagnostic goes to
stderr so the calling skill can safely `json.loads()` stdout.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from agent import onboard
from agent.config import Config

_STALE_DAYS = 3
_PRUNE_DAYS = 14


def _emit(obj: dict) -> None:
    print(json.dumps(obj))


def _load_draft(draft_path: str) -> dict:
    return json.loads(Path(draft_path).read_text(encoding="utf-8"))


def cmd_render_stage(args: argparse.Namespace, cfg: Config) -> None:
    draft = _load_draft(args.draft)
    slug = onboard.slugify(draft["company"]["name"])
    files = onboard.render(draft)
    draft_id, staged = onboard.stage(slug, files, cfg, company_name=draft["company"]["name"])
    # Persist the draft alongside the staged files so a resumed session (which only has a
    # draft_id from `status`, not the original temp draft file the live agent session wrote)
    # can still promote — see the resume gap surfaced by the Task 6 setup-skill review.
    (staged / ".draft.json").write_text(json.dumps(draft), encoding="utf-8")
    # Front-load the promote()-time "already exists" check here so the skill can ask the
    # founder to choose before ever attempting to promote — staging still proceeds either
    # way (there must be something to review/promote later); promote()'s hard-fail-if-exists
    # check remains the final safety net.
    collision = (cfg.profiles_root / slug).exists()
    _emit(
        {
            "draft_id": draft_id,
            "slug": slug,
            "staged_root": str(staged),
            "file_count": len(files),
            "gaps": draft.get("gaps", []),
            "confidence": draft.get("confidence"),
            "files": sorted(files.keys()),
            "collision": collision,
        }
    )


def cmd_promote(args: argparse.Namespace, cfg: Config) -> None:
    staged = onboard._staged_root_for_draft_id(args.draft_id, cfg)
    if args.draft:
        draft = _load_draft(args.draft)
    else:
        # Resume path: no original draft file (it was a temp file the live agent session
        # wrote and may be long gone). Fall back to the copy persisted at render-stage time.
        draft_copy = staged / ".draft.json"
        if not draft_copy.exists():
            raise FileNotFoundError(
                f"--draft not given and no persisted draft copy at {draft_copy} — "
                "this draft was staged before the resume fix; re-run render-stage."
            )
        draft = _load_draft(str(draft_copy))
    slug = staged.name
    live = onboard.promote(slug, args.draft_id, staged, draft, cfg)
    _emit({"status": "promoted", "slug": slug, "draft_id": args.draft_id, "live_root": str(live)})


def cmd_diff(args: argparse.Namespace, cfg: Config) -> None:
    staged = onboard._staged_root_for_draft_id(args.draft_id, cfg)
    slug = staged.name
    file_diffs = onboard.diff(slug, staged, cfg)
    _emit({"draft_id": args.draft_id, "slug": slug, "diff": file_diffs})


def cmd_cancel(args: argparse.Namespace, cfg: Config) -> None:
    staged = onboard._staged_root_for_draft_id(args.draft_id, cfg)
    slug = staged.name
    onboard.cancel(staged)
    _emit({"status": "cancelled", "draft_id": args.draft_id, "slug": slug})


def _age_days(iso_ts: str | None) -> float | None:
    """Age in days of an ISO 8601 timestamp against now, or None if unparseable."""
    if not iso_ts:
        return None
    try:
        ts = datetime.fromisoformat(iso_ts)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (datetime.now(UTC) - ts).total_seconds() / 86400


def cmd_status(args: argparse.Namespace, cfg: Config) -> None:
    staging_root = cfg.profiles_root / ".staging"
    drafts = []
    pruned = []
    if staging_root.exists():
        for slug_dir in sorted(staging_root.iterdir()):
            if not slug_dir.is_dir():
                continue
            meta_file = slug_dir / ".onboard-meta.json"
            if not meta_file.exists():
                continue
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            age_days = _age_days(meta.get("updated_at"))
            if args.prune and age_days is not None and age_days > _PRUNE_DAYS:
                onboard.cancel(slug_dir)
                pruned.append(meta.get("slug", slug_dir.name))
                continue
            meta["stale"] = age_days is not None and age_days > _STALE_DAYS
            drafts.append(meta)
    if args.prune:
        _emit({"pruned": pruned})
    else:
        _emit({"staged": drafts})


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="agent.onboard_cli")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("render-stage", help="Render a ProfileDraft JSON and stage it.")
    s.add_argument("--draft", required=True, help="Path to a ProfileDraft JSON file.")
    s.set_defaults(fn=cmd_render_stage)

    s = sub.add_parser("promote", help="Promote a staged draft to a live profile.")
    s.add_argument("--draft-id", required=True)
    s.add_argument(
        "--draft",
        required=False,
        default=None,
        help=(
            "Path to the same ProfileDraft JSON file. Optional — defaults to the copy "
            "persisted in the staged directory at render-stage time (needed to resume "
            "after the original temp draft file is gone)."
        ),
    )
    s.set_defaults(fn=cmd_promote)

    s = sub.add_parser("diff", help="Diff a staged draft against the live profile (if any).")
    s.add_argument("--draft-id", required=True)
    s.set_defaults(fn=cmd_diff)

    s = sub.add_parser("cancel", help="Discard a staged draft.")
    s.add_argument("--draft-id", required=True)
    s.set_defaults(fn=cmd_cancel)

    s = sub.add_parser("status", help="List all currently staged drafts.")
    s.add_argument(
        "--prune",
        action="store_true",
        default=False,
        help="Auto-cancel staged drafts older than 14 days (by updated_at) and report which were pruned.",
    )
    s.set_defaults(fn=cmd_status)

    return ap


def main(argv: list[str] | None = None) -> None:
    ap = _build_parser()
    args = ap.parse_args(argv)
    cfg = Config.from_env()
    args.fn(args, cfg)


if __name__ == "__main__":
    main()
