"""Data-spine types and storage-adapter abstraction for multi-tenant operation.

Pure Python, zero DB/SDK imports — gtm_core is the foundation, never a consumer.
The backend (Phase D) injects a cloud StorageAdapter; the plugin and VPS always
use LocalStorageAdapter (wrapping the existing content/<profile>/ layout so that
no existing ledger/path code needs to change).

Type hierarchy:
  WorkspaceId      — validated string wrapper (rejects path-traversal chars).
  Workspace        — tenant unit: id, slug, owner_user_id, entitlement.
  TenantContext    — binds workspace + profile_name for one request/session;
                     every call touching content state is scoped to one of these.

Storage:
  StorageAdapter   (ABC) — workspace-scoped read / write / exists / list / delete.
  LocalStorageAdapter    — filesystem implementation (content/<profile>/).

Storage path mapping:
  Cloud-canonical (TenantContext.content_path()):
      workspace/<workspace_id>/profile/<profile_name>/<relative_path>
  Local (LocalStorageAdapter) maps this to:
      <content_root>/<profile_name>/<relative_path>
  The workspace-id prefix is dropped on local so the existing on-disk layout
  (content/<profile>/history.jsonl etc.) is unchanged.

Credential vault:
  The schema for per-workspace encrypted social/publish credentials lives in
  backend/schema/V003__credential_vault.sql (envelope encryption: AES-256-GCM
  with per-workspace DEK encrypted under the platform KEK in KMS/Doppler).
  Decrypted credentials are NEVER held in gtm_core; they flow only through the
  server-side vault accessor in the backend package (Phase D).
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .capabilities import Entitlement


# ── Path safety ───────────────────────────────────────────────────────────────


def _check_storage_path(path: str) -> None:
    """Raise ValueError if `path` could escape the storage root.

    Rejects: absolute paths, empty paths, and any segment that is '..', '.',
    contains backslash, or contains NUL.
    """
    if not path:
        raise ValueError("storage path must not be empty")
    if os.path.isabs(path):
        raise ValueError(f"absolute storage path not allowed: {path!r}")
    for part in Path(path).parts:
        if part in ("..", ".") or "\\" in part or "\x00" in part:
            raise ValueError(f"unsafe path segment {part!r} in {path!r}")


# ── WorkspaceId ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WorkspaceId:
    """Immutable, validated workspace identifier.

    The value must be a non-empty string with no path-traversal characters (no
    '/', '\\', '..', or NUL). UUIDs and short-slugs are both valid.
    """

    value: str

    def __post_init__(self) -> None:
        if (
            not self.value
            or "/" in self.value
            or "\\" in self.value
            or "\x00" in self.value
            or self.value in (".", "..")
        ):
            raise ValueError(f"unsafe workspace id: {self.value!r}")

    def __str__(self) -> str:
        return self.value


# ── Workspace ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Workspace:
    """Core tenant entity.

    V1: one workspace is auto-created per user at signup. V2 adds team
    membership (workspace_members) with no migration required because
    workspace_id is on every row from day one.

    `entitlement` is loaded from the `subscriptions` table in the backend
    (Phase D) and supplied by the calling runtime — gtm_core never reads it
    from storage itself.
    """

    id: WorkspaceId
    slug: str  # kebab-case human-readable identifier
    owner_user_id: str  # opaque user ID (backend-specific format)
    entitlement: Entitlement  # from gtm_core.capabilities.Entitlement

    def __post_init__(self) -> None:
        # slug must be path-safe (same rules as knowledge filenames)
        if (
            not self.slug
            or "/" in self.slug
            or "\\" in self.slug
            or "\x00" in self.slug
            or self.slug in (".", "..")
        ):
            raise ValueError(f"unsafe workspace slug: {self.slug!r}")


# ── TenantContext ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TenantContext:
    """Binds a workspace to a specific profile for one request / session.

    This is the runtime tenant token. Every call that touches content state
    (ledgers, paths, storage) should be scoped to a TenantContext. Never hold
    one across requests — create a fresh one per request in the backend.

    The profile_name corresponds to an entry in profiles/<profile_name>/
    in the skills repo. Its validity is enforced by the profiles table (Phase D)
    and by the per-request profile lookup in the session layer.
    """

    workspace: Workspace
    profile_name: str

    def __post_init__(self) -> None:
        if (
            not self.profile_name
            or "/" in self.profile_name
            or "\\" in self.profile_name
            or "\x00" in self.profile_name
            or self.profile_name in (".", "..")
        ):
            raise ValueError(f"unsafe profile name: {self.profile_name!r}")

    @property
    def workspace_id(self) -> WorkspaceId:
        return self.workspace.id

    def content_path(self, *parts: str) -> str:
        """Cloud-canonical content path for this tenant.

        Returns a relative key of the form:
            workspace/<id>/profile/<profile_name>[/<parts>...]

        LocalStorageAdapter maps this to the local content/<profile>/ layout.
        Cloud adapters use it as an object-storage key prefix.
        """
        segments = ["workspace", str(self.workspace_id), "profile", self.profile_name]
        for p in parts:
            _check_storage_path(p)
            segments.append(p)
        return "/".join(segments)


# ── StorageAdapter (ABC) ──────────────────────────────────────────────────────


class StorageAdapter(ABC):
    """Abstract workspace-scoped content storage.

    Local implementation (plugin/VPS): wraps content/<profile>/ filesystem via
    LocalStorageAdapter — no change to the existing on-disk layout.
    Backend implementation (Phase D): wraps cloud object storage or Postgres
    BYTEA columns, keyed on workspace_id.

    gtm_core.ledgers and gtm_core.paths do not import backend code; the adapter
    is injected by the runtime boundary (Phase D dependency injection).

    All path arguments are relative paths within the tenant's content namespace
    (e.g. "history.jsonl", "runs/abc.json", "accounts/sggc/dossier.docx").
    Absolute paths and traversal sequences are rejected by the implementations.
    """

    @abstractmethod
    def read(self, ctx: TenantContext, path: str) -> bytes:
        """Read the content of a file. Raises FileNotFoundError if absent."""

    @abstractmethod
    def write(self, ctx: TenantContext, path: str, data: bytes) -> None:
        """Write (or overwrite) a file. Creates parent directories as needed."""

    @abstractmethod
    def exists(self, ctx: TenantContext, path: str) -> bool:
        """Return True if the path exists."""

    @abstractmethod
    def list_prefix(self, ctx: TenantContext, prefix: str) -> Iterator[str]:
        """Yield all relative paths under the given prefix (empty = all files)."""

    @abstractmethod
    def delete(self, ctx: TenantContext, path: str) -> None:
        """Delete a single file. No-op if the file does not exist."""

    def delete_workspace(self, ctx: TenantContext) -> None:
        """Cascade-delete all content files for this workspace + profile.

        Implements the GDPR/lifecycle deletion obligation (plan fix #11).
        The default walks list_prefix("") + delete(); cloud implementations
        should override with a bulk prefix-delete operation for efficiency.

        Does NOT delete the DB rows (subscriptions, profiles, etc.) — that
        is handled by the backend's cascade-delete migration (V001 ON DELETE
        CASCADE) and the deletion endpoint (Phase D).
        """
        for path in list(self.list_prefix(ctx, "")):
            self.delete(ctx, path)


# ── LocalStorageAdapter ───────────────────────────────────────────────────────


class LocalStorageAdapter(StorageAdapter):
    """StorageAdapter backed by the local filesystem.

    Maps TenantContext paths to:
        <content_root>/<profile_name>/<path>

    This is a drop-in for the existing content/<profile>/ convention used by the
    personal VPS and the Cowork plugin. The workspace-id component of the
    cloud-canonical path is intentionally ignored here — local storage is always
    single-tenant (one owner, no namespace collision risk).

    Path traversal is guarded: no '..', no absolute paths, no NUL or backslash.
    """

    def __init__(self, content_root: Path) -> None:
        self._root = Path(content_root).resolve()

    def _resolve(self, ctx: TenantContext, path: str) -> Path:
        _check_storage_path(path)
        target = (self._root / ctx.profile_name / path).resolve()
        # Belt-and-suspenders: resolved path must stay inside the content root.
        try:
            target.relative_to(self._root)
        except ValueError:
            raise ValueError(f"path escaped content root: {path!r}") from None
        return target

    def read(self, ctx: TenantContext, path: str) -> bytes:
        return self._resolve(ctx, path).read_bytes()

    def write(self, ctx: TenantContext, path: str, data: bytes) -> None:
        target = self._resolve(ctx, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def exists(self, ctx: TenantContext, path: str) -> bool:
        try:
            return self._resolve(ctx, path).exists()
        except ValueError:
            return False

    def list_prefix(self, ctx: TenantContext, prefix: str) -> Iterator[str]:
        if prefix:
            base = self._resolve(ctx, prefix)
        else:
            base = self._root / ctx.profile_name
        if not base.exists():
            return
        for p in sorted(base.rglob("*")):
            if p.is_file():
                yield str(p.relative_to(self._root / ctx.profile_name))

    def delete(self, ctx: TenantContext, path: str) -> None:
        target = self._resolve(ctx, path)
        if target.exists():
            target.unlink()
