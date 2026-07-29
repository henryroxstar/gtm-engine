"""Path resolution for gtm_core — stdlib-only, env-overridable.

The content and profiles roots can be overridden via environment variables so the
same code runs on the VPS (defaults to repo-relative paths) and locally (operator
sets GTM_CONTENT_ROOT / GTM_PROFILES_ROOT to a home directory path).

  GTM_CONTENT_ROOT   — absolute path to the content/ state tree (default: <repo>/content)
  GTM_PROFILES_ROOT  — absolute path to profiles/ (default: <repo>/profiles)

PathConfig is the minimal config duck-type consumed by gtm_core.ledger_cli and
gtm_core.radar. agent.config.Config is a strict superset — any code that accepts
PathConfig also accepts Config.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _repo_root_default() -> Path:
    """Parent of the gtm_core/ package directory = repo root."""
    return Path(__file__).resolve().parents[1]


def resolve_content_root(repo_root: Path | None = None) -> Path:
    """Return the content root, honouring GTM_CONTENT_ROOT env override."""
    override = os.getenv("GTM_CONTENT_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return (_repo_root_default() if repo_root is None else repo_root).resolve() / "content"


def resolve_profiles_root(repo_root: Path | None = None) -> Path:
    """Return the profiles root, honouring GTM_PROFILES_ROOT env override."""
    override = os.getenv("GTM_PROFILES_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return (_repo_root_default() if repo_root is None else repo_root).resolve() / "profiles"


def resolve_workspaces_root(repo_root: Path | None = None) -> Path:
    """Base dir for per-workspace, isolated ``{profiles,content}`` trees (the
    multi-tenant backend). Honours ``GTM_WORKSPACES_ROOT``; defaults to
    ``<repo>/data/workspaces``.

    The single-tenant VPS/cockpit path does NOT use this — it keeps the shared
    ``GTM_CONTENT_ROOT`` / ``GTM_PROFILES_ROOT``. Only the backend binds a run to
    one workspace's subtree here so a tenant can never read another's files.
    """
    override = os.getenv("GTM_WORKSPACES_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    base = (_repo_root_default() if repo_root is None else repo_root).resolve()
    return base / "data" / "workspaces"


def workspace_tree(workspace_id: str, repo_root: Path | None = None) -> Path:
    """Isolated filesystem root for one workspace: ``<workspaces_root>/<workspace_id>``.

    ``workspace_id`` is guarded as a bare path segment (no separators, ``..`` or
    NUL) — it is a server-issued UUID, never free-form tenant input, but the guard
    is the tenant-boundary backstop.
    """
    return resolve_workspaces_root(repo_root) / _safe_segment(workspace_id, "workspace_id")


def workspace_profiles_root(workspace_id: str, repo_root: Path | None = None) -> Path:
    """This workspace's isolated profiles/ tree (brand/ICP/knowledge)."""
    return workspace_tree(workspace_id, repo_root) / "profiles"


def workspace_content_root(workspace_id: str, repo_root: Path | None = None) -> Path:
    """This workspace's isolated content/ state tree (ledgers, accounts, outputs)."""
    return workspace_tree(workspace_id, repo_root) / "content"


def _safe_segment(value: str, label: str) -> str:
    """Reject path segments that could escape the profile directory.

    Knowledge filenames and product slugs flow in from PROFILE.md and skill
    arguments. The tenant boundary (CLAUDE.md) makes directory traversal the
    highest-risk error, so a segment must be a bare name — no separators, no
    ``..``, no NUL. Raises ValueError otherwise.
    """
    if not value or "/" in value or "\\" in value or "\x00" in value or value in (".", ".."):
        raise ValueError(f"unsafe {label}: {value!r}")
    return value


def resolve_knowledge_file(
    profiles_root: Path,
    profile: str,
    filename: str,
    product: str | None = None,
) -> Path:
    """Resolve a profile knowledge file, honouring a product-level override.

    Resolution order:
      1. ``profiles/<profile>/products/<product>/<filename>`` — if ``product`` is
         given AND that file exists (the product overrides the profile default).
      2. ``profiles/<profile>/knowledge/<filename>`` — the profile-level fallback,
         returned whether or not it exists so callers get a stable path to read
         or to report as missing.

    Pass ``product`` only when the run is bound to a specific product (e.g.
    Product A vs. Product B, which have different ICPs and scan configs). Omit
    it for profiles whose products share one knowledge pack — those always resolve
    to the profile level, so no per-product files are needed.

    This is the single source of truth for the product→profile fallback; skills
    reach it via ``python -m gtm_core.resolve_knowledge`` and Python callers
    import it directly.
    """
    base = profiles_root / _safe_segment(profile, "profile")
    name = _safe_segment(filename, "filename")
    if product is not None:
        candidate = base / "products" / _safe_segment(product, "product") / name
        if candidate.is_file():
            return candidate
    return base / "knowledge" / name


@dataclass(frozen=True)
class PathConfig:
    """Minimal config duck-type for gtm_core CLI tools (ledger_cli, radar).

    Contains only the path/profile fields needed by stdlib-only modules.
    agent.config.Config is a superset of this contract and is accepted wherever
    PathConfig is documented.
    """

    content_root: Path
    profiles_root: Path
    default_profile: str

    @classmethod
    def from_env(cls, repo_root: Path | None = None) -> PathConfig:
        """Build from environment, honouring GTM_CONTENT_ROOT / GTM_PROFILES_ROOT."""
        root = None if repo_root is None else repo_root.resolve()
        return cls(
            content_root=resolve_content_root(root),
            profiles_root=resolve_profiles_root(root),
            default_profile=os.getenv("ACTIVE_PROFILE", "template").strip() or "template",
        )
