"""Workspace-first path resolution for the MCP server (R2).

Resolves `(profiles_root, content_root)` for a validated `workspace_id` and `profile`
slug so that multi-tenant workspaces read from `/app/data/workspaces/<workspace_id>/`
first while preserving single-tenant / legacy fallback only when a workspace has no
provisioned profiles on disk.
"""

from __future__ import annotations

from pathlib import Path

from gtm_core.paths import (
    PathConfig,
    _safe_segment,
    workspace_content_root,
    workspace_profiles_root,
)


def resolve_mcp_roots(
    workspace_id: str,
    profile: str,
    paths: PathConfig | None = None,
    repo_root: Path | None = None,
) -> tuple[Path, Path]:
    """Return `(profiles_root, content_root)` for an authenticated MCP caller.

    Resolution rules (R2):
    1. If the caller's workspace tree already has `profiles/<profile>` or
       `content/<profile>` on disk, always return `(ws_profiles, ws_content)`.
    2. If the workspace directory `<workspaces_root>/<workspace_id>` exists on disk:
       - When `ws_profiles` already contains at least one tenant profile directory,
         stay strictly confined to `(ws_profiles, ws_content)` so querying an
         unprovisioned profile slug never falls back to global `paths.content_root`.
       - When `ws_profiles` has no profile directories yet (legacy pre-onboarding
         workspace) and `(paths.profiles_root / profile)` exists, fall back to
         `(paths.profiles_root, paths.content_root)`.
    3. If `<workspaces_root>/<workspace_id>` does not exist on disk at all and
       `(paths.profiles_root / profile)` exists (single-tenant / local dev mode),
       fall back to `(paths.profiles_root, paths.content_root)`; otherwise return
       `(ws_profiles, ws_content)`.
    """
    profile_seg = _safe_segment(profile, "profile")
    effective_paths = paths if paths is not None else PathConfig.from_env(repo_root)
    ws_profiles = workspace_profiles_root(workspace_id, repo_root)
    ws_content = workspace_content_root(workspace_id, repo_root)

    if (ws_profiles / profile_seg).is_dir() or (ws_content / profile_seg).is_dir():
        return ws_profiles, ws_content

    ws_tree = ws_profiles.parent
    if ws_tree.is_dir():
        has_any_ws_profile = ws_profiles.is_dir() and any(
            child.is_dir() for child in ws_profiles.iterdir()
        )
        if not has_any_ws_profile and (effective_paths.profiles_root / profile_seg).is_dir():
            return effective_paths.profiles_root, effective_paths.content_root
        return ws_profiles, ws_content

    if (effective_paths.profiles_root / profile_seg).is_dir():
        return effective_paths.profiles_root, effective_paths.content_root

    return ws_profiles, ws_content
