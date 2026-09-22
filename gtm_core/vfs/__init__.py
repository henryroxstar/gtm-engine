"""Virtual File System (VFS) package for GTM Content OS."""

from __future__ import annotations

from pathlib import Path

from .base import VFS, _validate_safe_path
from .cloud import CloudVFS
from .local import LocalVFS

__all__ = ["VFS", "LocalVFS", "CloudVFS", "get_vfs", "_validate_safe_path"]


def get_vfs(root: Path | str | None = None, backend: str = "local", **kwargs) -> VFS:
    """Factory for obtaining a VFS instance.

    Args:
        root: Base path for LocalVFS (defaults to current working directory).
        backend: "local" or "cloud".
        **kwargs: Additional parameters for CloudVFS (bucket, prefix, client).
    """
    if backend == "local":
        return LocalVFS(root=root or Path.cwd())
    if backend == "cloud":
        bucket = kwargs.get("bucket", "gtm-storage")
        prefix = kwargs.get("prefix", "")
        client = kwargs.get("client")
        return CloudVFS(bucket=bucket, prefix=prefix, client=client)
    raise ValueError(f"Unknown VFS backend: {backend!r}")
