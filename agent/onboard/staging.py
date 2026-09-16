from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # annotations only — no runtime import
    from ..config import Config

import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .slug import _STAGING_DIR


class ProfileAlreadyExistsError(ValueError):
    """promote() refused: a live profile with this slug already exists."""


class DraftNotStagedError(ValueError):
    """promote() refused: the staging dir is gone or was never a staged draft."""


# ── staging ───────────────────────────────────────────────────────────────────


def stage(
    slug: str, files: dict[str, str], cfg: Config, company_name: str = ""
) -> tuple[str, Path]:
    """Write rendered files to profiles/.staging/<slug>/ and return (draft_id, staged_root).

    Args:
        slug: Validated profile slug (output of slugify()).
        files: dict[relative_path, content] from render().
        cfg: Runtime config.
        company_name: Extracted company name stored in meta for confirm-step verification.

    Returns:
        (draft_id, staged_root)
    """
    from gtm_core.confine import confined_output_path
    from gtm_core.paths import _safe_segment

    _safe_segment(slug, "staging slug")
    staging_root = cfg.profiles_root / _STAGING_DIR / slug
    staging_root.mkdir(parents=True, exist_ok=True)

    for rel_path, content in files.items():
        # Confine BEFORE writing. render() normalises the slugs it controls, but stage()
        # writes whatever dict it is handed and `rel_path` is a multi-segment relative path
        # — which _safe_segment cannot validate (it rejects "/" outright). confined_output_path
        # resolves first, so "a/../../x" and a symlinked parent are both caught, which a
        # lexical check cannot do.
        dest = staging_root / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest = confined_output_path(dest, content_root=staging_root)
        dest.write_text(content, encoding="utf-8")

    draft_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    meta = {
        "draft_id": draft_id,
        "slug": slug,
        "status": "staged",
        "step": "staged",
        "file_count": len(files),
        "company_name": company_name,
        "created_at": now,
        "updated_at": now,
    }
    (staging_root / ".onboard-meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return draft_id, staging_root


def diff(slug: str, staged_root: Path, cfg: Config) -> dict[str, dict]:
    """Return per-file diffs between staged files and the live profile (if any).

    Returns:
        dict[relative_path, {"old": str | None, "new": str}]
        old is None for files that don't exist in the live profile.
    """
    live_root = cfg.profiles_root / slug
    result: dict[str, dict] = {}

    for staged_file in staged_root.rglob("*"):
        if staged_file.is_dir() or staged_file.name in (".onboard-meta.json", ".draft.json"):
            continue
        rel = staged_file.relative_to(staged_root).as_posix()
        new_content = staged_file.read_text(encoding="utf-8")
        live_file = live_root / rel
        old_content = live_file.read_text(encoding="utf-8") if live_file.exists() else None
        result[rel] = {"old": old_content, "new": new_content}

    return result


def promote(slug: str, draft_id: str, staged_root: Path, draft: dict, cfg: Config) -> Path:
    """Atomically rename staged profile to profiles/<slug>/; append audit record.

    INVARIANT: Raises ValueError if profiles/<slug>/ already exists. Never overwrites
    a live tenant. The operator must choose a new slug or explicitly delete the existing
    profile directory before promoting.

    Uses staged_root.rename(live_root) — POSIX rename(2), single syscall, no partial state.

    Returns:
        Path to the live profile directory.

    Raises:
        ValueError: If staged_root has no .onboard-meta.json, or if live profile already exists.
    """
    from agent.ledgers import Ledgers

    meta_file = staged_root / ".onboard-meta.json"
    if not meta_file.exists():
        raise DraftNotStagedError(
            f"No .onboard-meta.json in {staged_root} — not a valid staging dir"
        )

    live_root = cfg.profiles_root / slug
    if live_root.exists():
        raise ProfileAlreadyExistsError(
            f"Profile '{slug}' already exists at {live_root}. "
            "Pick a new slug or manually remove the existing profile directory."
        )

    # Remove the meta file (and the resume-support draft copy, if present) so the live
    # profile directory is clean — see onboard_cli.py cmd_render_stage's ".draft.json".
    meta_file.unlink(missing_ok=True)
    (staged_root / ".draft.json").unlink(missing_ok=True)

    # Atomic rename — staging and profiles/ are siblings under the same profiles_root,
    # so they share the same filesystem. rename() is a single syscall (POSIX rename(2))
    # — either succeeds or fails, no partial state. Matches PRD §7 step 4.
    staged_root.rename(live_root)

    # Default pack activation (A1 reachability bootstrap): a profile without a
    # packs.toml activates NOTHING (fail-closed), which would leave a freshly
    # onboarded workspace with an empty pack catalog and every skill unreachable
    # in pack mode. Activate the full shipped roster by default — still a
    # narrowing scope (only pack-declared skills are reachable; the long tail of
    # other skills is denied), and the tenant can narrow further by editing the
    # file. Never overwrites an activation the staging tree already carried.
    packs_toml = live_root / "packs.toml"
    if not packs_toml.exists():
        packs_dir = cfg.repo_root / "packs"
        shipped = sorted(p.name for p in packs_dir.iterdir() if (p / "graphs").is_dir())
        if shipped:
            active_line = ", ".join(f'"{name}"' for name in shipped)
            packs_toml.write_text(
                "# Default activation written by onboarding (A1): full shipped roster.\n"
                "# Narrow by removing packs; overrides are monotone-stricter only.\n"
                f"active = [{active_line}]\n",
                encoding="utf-8",
            )

    # Audit record — §R2 / NIST AU-12
    system_ledger = Ledgers(cfg, "_system")
    system_ledger.append_history(
        {
            "event": "onboard.promote",
            "slug": slug,
            "draft_id": draft_id,
            "source_type": draft.get("source", {}).get("type"),
            "confidence": draft.get("confidence"),
            "product_count": len(draft.get("products", [])),
            "gaps": draft.get("gaps", []),
        }
    )

    return live_root


def cancel(staged_root: Path) -> None:
    """Remove the staging directory for a cancelled onboarding run."""
    if staged_root.exists():
        shutil.rmtree(staged_root)


def _staged_root_for_draft_id(draft_id: str, cfg: Config) -> Path:
    """Find the staging directory that contains the given draft_id.

    Searches profiles/.staging/*/. Used by the Telegram cockpit to look up
    a staged draft by the id emitted in the confirmation message.

    Raises FileNotFoundError if no match.
    """
    staging_root = cfg.profiles_root / _STAGING_DIR
    if not staging_root.exists():
        raise FileNotFoundError(f"No staging directory at {staging_root}")

    for slug_dir in staging_root.iterdir():
        if not slug_dir.is_dir():
            continue
        meta_file = slug_dir / ".onboard-meta.json"
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if meta.get("draft_id") == draft_id:
                return slug_dir

    raise FileNotFoundError(f"No staged draft with id {draft_id!r}")
