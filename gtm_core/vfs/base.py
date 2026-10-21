"""Abstract Base Class and safety validation for Virtual File System (VFS).

Enforces §R5/§R9 tenant isolation boundaries and prevents path traversal (OWASP ASI05)
across all VFS implementations.
"""

from __future__ import annotations

import posixpath
from abc import ABC, abstractmethod


def _validate_safe_path(path: str, allow_root: bool = False) -> str:
    """Normalize and validate that a relative path does not escape its root.

    Rejects:
    - Absolute paths (leading `/` or `\\` or drive letters)
    - Path traversal (`..`, empty segments unless allow_root=True)
    - Null bytes and unexpanded environment variable syntax (`$`, `%`)
    """
    if not isinstance(path, str):
        raise ValueError(f"Invalid path: {path!r}")

    # Check for forbidden characters
    if "\x00" in path or "$" in path or "%" in path:
        raise ValueError(f"Unsafe path characters in {path!r}")

    # Normalize backslashes to slashes
    clean = path.replace("\\", "/").strip()
    if clean.startswith("/"):
        raise ValueError(f"Absolute paths are forbidden in VFS: {path!r}")

    # Check Windows drive letters (e.g. C:)
    if len(clean) >= 2 and clean[1] == ":" and clean[0].isalpha():
        raise ValueError(f"Drive letters are forbidden in VFS: {path!r}")

    if not clean or clean == ".":
        if allow_root:
            return ""
        raise ValueError(f"Invalid path: {path!r}")

    # Normalize posixpath
    norm = posixpath.normpath(clean)
    if norm == ".":
        if allow_root:
            return ""
        raise ValueError(f"Path traversal detected in {path!r}")
    if norm.startswith("..") or "/../" in f"/{norm}/":
        raise ValueError(f"Path traversal detected in {path!r}")

    # Verify no segment contains dangerous tokens
    for part in norm.split("/"):
        if not part or part in (".", ".."):
            raise ValueError(f"Illegal segment {part!r} in path {path!r}")

    return norm


class VFS(ABC):
    """Abstract interface for file-bound state access in GTM pipelines."""

    @abstractmethod
    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        """Read text from path."""

    @abstractmethod
    def write_text(self, path: str, content: str, encoding: str = "utf-8") -> None:
        """Write text to path."""

    @abstractmethod
    def read_bytes(self, path: str) -> bytes:
        """Read raw bytes from path."""

    @abstractmethod
    def write_bytes(self, path: str, data: bytes) -> None:
        """Write raw bytes to path."""

    @abstractmethod
    def exists(self, path: str) -> bool:
        """Return True if file or directory exists."""

    @abstractmethod
    def is_file(self, path: str) -> bool:
        """Return True if path exists and is a regular file."""

    @abstractmethod
    def is_dir(self, path: str) -> bool:
        """Return True if path exists and is a directory."""

    @abstractmethod
    def list_dir(self, path: str) -> list[str]:
        """Return list of child names in directory."""

    @abstractmethod
    def glob(self, pattern: str) -> list[str]:
        """Return list of matching paths for glob pattern."""

    @abstractmethod
    def delete(self, path: str) -> None:
        """Delete a file if it exists."""
