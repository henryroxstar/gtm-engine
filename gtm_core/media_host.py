"""Media-host bridge: local finish file → hosted `https://` URL(s).

Every asset, regardless of modality, produces a `finish.json`. After lint passes and
before Gate 2, the approved local asset is uploaded to a tenant-pinned host via an MCP
tool chosen by server configuration. The returned URL(s) are written back to
`finish.json` under the key `hosted_media_urls`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path

from .paths import resolve_content_root


class MediaHostError(RuntimeError):
    """Upload failed or was refused."""


class EgressRefused(MediaHostError):
    """A path or destination failed a confinement check. Fail closed."""


_Uploader = Callable[[str, str], list[str]]


def _safe_source(file_path: Path, *, content_root: Path) -> Path:
    """Refuse a source file outside the resolved content root, missing, or non-file."""
    resolved = file_path.expanduser().resolve()
    root = content_root.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise EgressRefused(
            f"refusing to upload a file outside the resolved content root: {resolved} "
            f"(root: {root})"
        ) from exc
    if not resolved.is_file():
        raise EgressRefused(f"source file does not exist: {resolved}")
    return resolved


def _read_finish(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise MediaHostError(f"finish.json is not valid JSON: {exc}") from exc
    except FileNotFoundError as exc:
        raise MediaHostError(f"finish.json not found: {path}") from exc
    if not isinstance(data, dict):
        raise MediaHostError("finish.json must contain a JSON object")
    return data


def _write_finish(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def write_hosted_urls(finish_path: Path, urls: list[str]) -> None:
    """Persist ``hosted_media_urls`` into ``finish.json``.

    Raises :class:`MediaHostError` if the file is missing or malformed.
    """
    data = _read_finish(finish_path)
    data["hosted_media_urls"] = urls
    _write_finish(finish_path, data)


def upload(
    asset_path: Path,
    profile: str,
    *,
    content_root: Path | None = None,
    uploader: _Uploader | None = None,
) -> list[str]:
    """Upload a local finish file and return one or more hosted `https://` URLs.

    The file must resolve under the active profile's content root; absolute paths,
    ``..`` segments, or paths outside the root are refused.

    ``uploader`` may be injected for testing. If ``None``, the function calls the
    configured MCP upload tool. When no media-host MCP is configured (the provider
    is still being pinned), it raises :class:`MediaHostError` — fail closed, never
    silently skip the upload.
    """
    root = content_root if content_root is not None else resolve_content_root()
    source = _safe_source(asset_path, content_root=root)

    if uploader is not None:
        return uploader(str(source), profile)

    from . import r2_client

    if r2_client.is_configured() or os.getenv("GTM_MEDIA_HOST_PROVIDER") == "r2":
        return r2_client.r2_media_uploader(str(source), profile, content_root=root)

    raise MediaHostError(
        "No media-host MCP tool is configured. "
        "Set R2 credentials (R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY) "
        "or GTM_MEDIA_HOST_SERVER / GTM_MEDIA_HOST_TOOL env vars, or pass uploader=."
    )


def upload_and_record(
    asset_path: Path,
    finish_path: Path,
    profile: str,
    *,
    content_root: Path | None = None,
    uploader: _Uploader | None = None,
) -> list[str]:
    """Upload ``asset_path`` and write the returned URL(s) into ``finish_path``."""
    urls = upload(asset_path, profile, content_root=content_root, uploader=uploader)
    write_hosted_urls(finish_path, urls)
    return urls


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.media_host",
        description="Upload a local finish file to a configured host and record the hosted URL(s).",
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument("--asset", required=True, type=Path, help="Path under the content root.")
    parser.add_argument("--finish", required=True, type=Path, help="finish.json to update.")
    parser.add_argument(
        "--urls",
        nargs="+",
        help="Pre-computed hosted URL(s) to record instead of calling an MCP upload tool.",
    )
    args = parser.parse_args(argv)

    try:
        if args.urls:
            write_hosted_urls(args.finish, args.urls)
            print(json.dumps({"hosted_media_urls": args.urls}, indent=2))
            return 0

        urls = upload_and_record(args.asset, args.finish, args.profile)
        print(json.dumps({"hosted_media_urls": urls}, indent=2))
        return 0
    except MediaHostError as exc:
        print(f"[media-host] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
