"""Cloud / Object-storage implementation of VFS (R2/S3 compatible).

Maintains exact parity with LocalVFS while enabling stateless execution across
ephemeral runners.
"""

from __future__ import annotations

import fnmatch
from typing import Any

from .base import VFS, _validate_safe_path


class CloudVFS(VFS):
    """VFS adapter for S3/R2-compatible object storage with simulated or remote backend."""

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        client: Any | None = None,
    ) -> None:
        self.bucket = bucket.strip("/")
        self.prefix = prefix.strip("/")
        self._client = client
        # In-memory store when no remote client is supplied
        self._store: dict[str, bytes] = {}

    def _full_key(self, path: str, allow_root: bool = False) -> str:
        clean = _validate_safe_path(path, allow_root=allow_root)
        if not clean:
            return self.prefix
        if self.prefix:
            return f"{self.prefix}/{clean}"
        return clean

    def _strip_prefix(self, key: str) -> str:
        if self.prefix and key.startswith(f"{self.prefix}/"):
            return key[len(self.prefix) + 1 :]
        return key

    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        data = self.read_bytes(path)
        return data.decode(encoding)

    def write_text(self, path: str, content: str, encoding: str = "utf-8") -> None:
        self.write_bytes(path, content.encode(encoding))

    def read_bytes(self, path: str) -> bytes:
        key = self._full_key(path)
        if self._client is not None and hasattr(self._client, "get_object"):
            try:
                res = self._client.get_object(Bucket=self.bucket, Key=key)
                return res["Body"].read()
            except Exception as e:
                raise FileNotFoundError(f"File not found: {path}") from e

        if key not in self._store:
            raise FileNotFoundError(f"File not found: {path}")
        return self._store[key]

    def write_bytes(self, path: str, data: bytes) -> None:
        key = self._full_key(path)
        if self._client is not None and hasattr(self._client, "put_object"):
            self._client.put_object(Bucket=self.bucket, Key=key, Body=data)
            return

        self._store[key] = bytes(data)

    def exists(self, path: str) -> bool:
        clean = _validate_safe_path(path, allow_root=True)
        if not clean:
            return True
        key = self._full_key(clean, allow_root=True)

        if self._client is not None and hasattr(self._client, "head_object"):
            if self.is_file(path):
                return True
            if hasattr(self._client, "list_objects_v2"):
                res = self._client.list_objects_v2(Bucket=self.bucket, Prefix=f"{key}/", MaxKeys=1)
                if res.get("KeyCount", 0) > 0:
                    return True
            return False

        if key in self._store:
            return True
        prefix_key = f"{key}/"
        return any(k.startswith(prefix_key) for k in self._store)

    def is_file(self, path: str) -> bool:
        clean = _validate_safe_path(path, allow_root=True)
        if not clean:
            return False
        key = self._full_key(clean, allow_root=True)
        if self._client is not None and hasattr(self._client, "head_object"):
            try:
                self._client.head_object(Bucket=self.bucket, Key=key)
                return True
            except Exception:
                return False
        return key in self._store

    def is_dir(self, path: str) -> bool:
        clean = _validate_safe_path(path, allow_root=True)
        if not clean:
            return True
        key = self._full_key(clean, allow_root=True)
        prefix_key = f"{key}/"
        if self._client is not None and hasattr(self._client, "list_objects_v2"):
            res = self._client.list_objects_v2(Bucket=self.bucket, Prefix=prefix_key, MaxKeys=1)
            return res.get("KeyCount", 0) > 0
        return any(k.startswith(prefix_key) for k in self._store)

    def list_dir(self, path: str) -> list[str]:
        clean = _validate_safe_path(path, allow_root=True)
        if not self.is_dir(path):
            raise FileNotFoundError(f"Directory not found: {path}")

        key_dir = self._full_key(clean, allow_root=True)
        prefix_search = f"{key_dir}/" if key_dir else ""
        children: set[str] = set()

        if self._client is not None and hasattr(self._client, "list_objects_v2"):
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix_search, Delimiter="/"):
                for cp in page.get("CommonPrefixes", []):
                    p = cp.get("Prefix", "").removeprefix(prefix_search).strip("/")
                    if p:
                        children.add(p)
                for obj in page.get("Contents", []):
                    k = obj.get("Key", "").removeprefix(prefix_search)
                    if k and "/" not in k:
                        children.add(k)
            return sorted(children)

        for k in self._store:
            if k.startswith(prefix_search):
                remainder = k[len(prefix_search) :]
                parts = remainder.split("/")
                if parts[0]:
                    children.add(parts[0])

        return sorted(children)

    def glob(self, pattern: str) -> list[str]:
        if ".." in pattern or "\x00" in pattern:
            raise ValueError(f"Unsafe glob pattern: {pattern!r}")

        all_keys: list[str] = []
        if self._client is not None and hasattr(self._client, "list_objects_v2"):
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
                for obj in page.get("Contents", []):
                    all_keys.append(obj["Key"])
        else:
            all_keys = list(self._store.keys())

        matched: list[str] = []
        for k in all_keys:
            rel = self._strip_prefix(k)
            if fnmatch.fnmatch(rel, pattern):
                matched.append(rel)
        return sorted(matched)

    def delete(self, path: str) -> None:
        clean = _validate_safe_path(path)
        key = self._full_key(clean)

        if self._client is not None:
            if hasattr(self._client, "delete_object"):
                self._client.delete_object(Bucket=self.bucket, Key=key)
            if hasattr(self._client, "get_paginator"):
                paginator = self._client.get_paginator("list_objects_v2")
                for page in paginator.paginate(Bucket=self.bucket, Prefix=f"{key}/"):
                    for obj in page.get("Contents", []):
                        self._client.delete_object(Bucket=self.bucket, Key=obj["Key"])
            elif hasattr(self._client, "list_objects_v2"):
                res = self._client.list_objects_v2(Bucket=self.bucket, Prefix=f"{key}/")
                for obj in res.get("Contents", []):
                    self._client.delete_object(Bucket=self.bucket, Key=obj["Key"])
            return

        if key in self._store:
            del self._store[key]
        prefix_key = f"{key}/"
        to_del = [k for k in self._store if k.startswith(prefix_key)]
        for k in to_del:
            del self._store[k]
