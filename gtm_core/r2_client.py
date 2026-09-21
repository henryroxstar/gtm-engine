"""Cloudflare R2 storage client (S3-compatible API via boto3).

Implements Phase 1 (media host uploader) and Phase 2 (artifact mirror / presigned URLs).

Containment & security invariants:
  - Credentials from Doppler / env, never echoed to logs or ledgers.
  - Endpoints pinned to Cloudflare R2 storage domain.
  - Presigning is local cryptographic signing (SigV4) with zero network call.
"""

from __future__ import annotations

import mimetypes
import os
import secrets
from pathlib import Path
from typing import Any

from .media_host import EgressRefused, MediaHostError, _safe_source
from .paths import _safe_segment, resolve_content_root

_DEFAULT_PRIVATE_BUCKET = "gtm-content-prd"
_DEFAULT_PUBLIC_BUCKET = "gtm-public-prd"


def is_configured() -> bool:
    """Check if Cloudflare R2 credentials are set in the environment."""
    return bool(
        os.getenv("R2_ACCOUNT_ID")
        and os.getenv("R2_ACCESS_KEY_ID")
        and os.getenv("R2_SECRET_ACCESS_KEY")
    )


def get_s3_client(
    account_id: str | None = None,
    access_key: str | None = None,
    secret_key: str | None = None,
) -> Any:
    """Create a boto3 S3 client configured for Cloudflare R2."""
    import boto3
    from botocore.config import Config

    acc_id = account_id or os.getenv("R2_ACCOUNT_ID")
    key_id = access_key or os.getenv("R2_ACCESS_KEY_ID")
    secret = secret_key or os.getenv("R2_SECRET_ACCESS_KEY")

    if not acc_id or not key_id or not secret:
        raise MediaHostError(
            "R2 credentials not configured (R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY)"
        )

    endpoint_url = f"https://{acc_id}.r2.cloudflarestorage.com"

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        region_name="auto",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def upload_file(
    file_path: Path,
    key: str,
    *,
    bucket: str | None = None,
    content_type: str | None = None,
    s3_client: Any = None,
) -> str:
    """Upload a file to an R2 bucket and return its object key."""
    if not file_path.is_file():
        raise MediaHostError(f"Cannot upload non-existent file: {file_path}")

    target_bucket = (
        bucket
        or os.getenv("R2_BUCKET_NAME")
        or os.getenv("R2_PRIVATE_BUCKET")
        or _DEFAULT_PRIVATE_BUCKET
    )
    client = s3_client or get_s3_client()

    extra_args: dict[str, str] = {}
    ctype = content_type or mimetypes.guess_type(str(file_path))[0]
    if ctype:
        extra_args["ContentType"] = ctype

    client.upload_file(
        str(file_path),
        target_bucket,
        key,
        ExtraArgs=extra_args if extra_args else None,
    )
    return key


def generate_presigned_url(
    key: str,
    *,
    bucket: str | None = None,
    expires_in: int = 3600,
    client_method: str = "get_object",
    s3_client: Any = None,
) -> str:
    """Generate a presigned SigV4 URL for direct browser access."""
    target_bucket = (
        bucket
        or os.getenv("R2_BUCKET_NAME")
        or os.getenv("R2_PRIVATE_BUCKET")
        or _DEFAULT_PRIVATE_BUCKET
    )
    client = s3_client or get_s3_client()

    params: dict[str, Any] = {"Bucket": target_bucket, "Key": key}

    url: str = client.generate_presigned_url(
        ClientMethod=client_method,
        Params=params,
        ExpiresIn=expires_in,
    )
    return url


def r2_media_uploader(
    asset_path: str,
    profile: str,
    *,
    content_root: Path | None = None,
    s3_client: Any = None,
) -> list[str]:
    """Upload an approved render to the public R2 bucket, satisfying the media_host uploader seam.

    Enforces containment per PRD §5:
      - Source must resolve under the content root.
      - Source cannot be customer PII (e.g. accounts/ directory).
      - Destination key uses an unguessable 128-bit random token.
    """
    _safe_segment(profile, "profile")
    root = content_root if content_root is not None else resolve_content_root()
    source = _safe_source(Path(asset_path), content_root=root)

    # Refuse PII paths from entering the public bucket (PRD §5 containment #4)
    rel = source.relative_to(root.resolve())
    if "accounts" in rel.parts:
        raise EgressRefused(f"Refusing to upload customer account data to public bucket: {rel}")

    public_bucket = os.getenv("R2_PUBLIC_BUCKET", _DEFAULT_PUBLIC_BUCKET)
    token = secrets.token_hex(16)
    filename = source.name
    key = f"{profile}/{token}/{filename}"

    upload_file(source, key, bucket=public_bucket, s3_client=s3_client)

    # Construct public URL
    base_url = os.getenv("R2_PUBLIC_BASE_URL", "").rstrip("/")
    if base_url:
        public_url = f"{base_url}/{key}"
    else:
        acc_id = os.getenv("R2_ACCOUNT_ID", "account")
        public_url = f"https://{public_bucket}.{acc_id}.r2.cloudflarestorage.com/{key}"

    return [public_url]
