"""Local POSIX filesystem implementation of VFS."""

from __future__ import annotations

from pathlib import Path

from .base import VFS, _validate_safe_path


class LocalVFS(VFS):
    """VFS adapter wrapping a local directory on POSIX filesystem."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def _resolve(self, path: str, allow_root: bool = False) -> Path:
        clean = _validate_safe_path(path, allow_root=allow_root)
        target = (self._root / clean).resolve() if clean else self._root
        # Enforce containment within root
        if not target.is_relative_to(self._root):
            raise ValueError(f"Path escapes root: {path!r}")
        return target

    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        target = self._resolve(path)
        if not target.is_file():
            raise FileNotFoundError(f"File not found: {path}")
        return target.read_text(encoding=encoding)

    def write_text(self, path: str, content: str, encoding: str = "utf-8") -> None:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding=encoding)

    def read_bytes(self, path: str) -> bytes:
        target = self._resolve(path)
        if not target.is_file():
            raise FileNotFoundError(f"File not found: {path}")
        return target.read_bytes()

    def write_bytes(self, path: str, data: bytes) -> None:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def exists(self, path: str) -> bool:
        target = self._resolve(path, allow_root=True)
        return target.exists()

    def is_file(self, path: str) -> bool:
        target = self._resolve(path, allow_root=True)
        return target.is_file()

    def is_dir(self, path: str) -> bool:
        target = self._resolve(path, allow_root=True)
        return target.is_dir()

    def list_dir(self, path: str) -> list[str]:
        target = self._resolve(path, allow_root=True)
        if not target.is_dir():
            raise FileNotFoundError(f"Directory not found: {path}")
        return [child.name for child in target.iterdir()]

    def glob(self, pattern: str) -> list[str]:
        # Validate that the pattern itself doesn't contain escape sequences
        if ".." in pattern or "\x00" in pattern:
            raise ValueError(f"Unsafe glob pattern: {pattern!r}")
        matches = []
        for p in self._root.glob(pattern):
            if p.is_file():
                rel = p.relative_to(self._root).as_posix()
                matches.append(rel)
        return matches

    def delete(self, path: str) -> None:
        target = self._resolve(path)
        if target.is_file():
            target.unlink()
        elif target.is_dir():
            import shutil

            shutil.rmtree(target)
