"""Host-pinned, same-turn-bound PUT uploader for provider pre-signed URLs — Reap Video Studio and
HeyGen assets (§R6 egress exception).

The module keeps its original name because the egress allowlists (semgrep, the urllib import
contract test, CLAUDE.md §Egress) key on the path. HeyGen was added 2026-09-14 (operator-approved):
its ``create_asset_upload`` MCP tool mints a pre-signed S3 PUT URL and, like Reap, has no tool
that performs the PUT itself, so screenshots meant as Video Agent inputs could not leave the
machine. Same containment, same module — not a second HTTP client.

WHY THIS EXISTS
---------------
``demo-capture`` (Phase C) stages a demo clip
from real product footage through Reap Video Studio. Reap's own upload step is a two-call
dance: ``request_upload_url`` (an MCP tool, already audited/pinned credentials) mints a
pre-signed S3-style PUT URL; something then has to PUT the local file's bytes to that URL.
Nothing in this system does that PUT — ``Bash(curl:*)``/``Bash(wget:*)`` are denied by the
shell egress floor (§R6), and there is no MCP tool that performs the PUT itself (the MCP tool
only mints the URL). Without this module, ``request_upload_url``'s output was a dead end.

WHAT THIS IS NOT
----------------
Same discipline as :mod:`gtm_core.dataset_fetch` (its precedent, the GET-side of this exact
problem) — a general HTTP client must never re-emerge here:

* ``ALLOWED_UPLOAD_HOSTS`` is a hardcoded module constant, never derived from a profile, a
  skill argument, a CLI flag, or brain output — same unrepresentable-destination property as
  the publish gate. It ships **empty on purpose**: the first live call always fails closed,
  naming exactly the host to pin, so the allowlist is never guessed ahead of a real
  observation (mirrors how ``dataset_fetch.py``'s storage-bucket host was pinned live, not
  wildcarded).
* https-only, **PUT-only** — GET/POST/DELETE are not this module's job.
* No auth/cookie/credential headers are ever sent — the signed URL itself IS the credential,
  a provider-issued, time-boxed grant. Only ``Content-Type`` is set, and it is read from the
  URL's OWN ``content-type`` query parameter when the provider signed one in — S3-style
  pre-signed PUT URLs bind the signature to a specific Content-Type, and sending a different
  one is a signature mismatch (a 403, not a content problem; confirmed live 2026-08-21 on a
  ``content-type=video%2Fmp4`` URL). Falls back to ``application/octet-stream`` only when the
  URL carries no such parameter, never a guess from the file's own extension.
* The source file must resolve under the profile's resolved content root — never an arbitrary
  filesystem path — and is size-capped.
* Redirects are a hard failure, **never** followed: a PUT redirect could re-send the bytes (and
  the still-attached signed query string) to a host the allowlist never approved.
* "Same-turn" is not representable to a process — the enforceable version is *a fresh,
  provider-signed, short-lived credential carried in a structurally intact tool result*. The
  caller passes the RAW JSON ``request_upload_url`` returned — on stdin, never a bare URL typed
  or reconstructed by hand — and this module refuses anything that does not parse to the
  documented ``{uploadUrl, id, fileName}`` shape, or whose signed URL is already stale by its
  own ``X-Amz-Date``/``X-Amz-Expires`` query params (not a wall-clock guess about "same turn").

ARCHITECTURE DEBT (mirrors DR-02/dataset_fetch.py — same shape, opposite direction)
-------------------------------------------------------------------------------------
The clean shape is an MCP tool that performs the PUT itself, so the brain never touches upload
bytes at all. Reap's surface does not offer one today. Registered in
``.semgrep/gtm-invariants.yml`` (§R6 allowlist) and ``CLAUDE.md`` (§Egress).
Retire this module if Reap ships a row-returning-equivalent upload tool.

USAGE
-----
    <raw request_upload_url tool result JSON> | uv run python -m gtm_core.reap_upload \\
        --profile <slug> --file content/<slug>/video/demo/raw/screen-recording.mp4
"""

from __future__ import annotations

import argparse
import calendar
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from gtm_core.paths import _safe_segment, resolve_content_root

# ── the security boundary ────────────────────────────────────────────────────────
# Hardcoded on purpose. Adding a host here is a boundary change: update CLAUDE.md (§Egress) in
# the same commit, per CLAUDE.md "When you change a boundary here" — never widen this from a
# guess, only from a live provider response naming the exact host.
#
# Confirmed live 2026-08-20 by calling Reap's own `request_upload_url` MCP tool and reading the
# host out of the `uploadUrl` it minted. Pinned as the EXACT bucket host, never `*.amazonaws.com`
# and never `*.s3-accelerate.amazonaws.com` — either wildcard would admit every S3 tenant on the
# internet, which is not an allowlist (same reasoning as media_fetch.py's CloudFront note).
#
# Until this was populated the module shipped empty and fail-closed, which meant the ONLY local
# ingest path in the repo raised EgressRefused on its first PUT — so the operator's own footage
# could never leave the machine. That was the deeper of the two blockers on the real-footage lane.
#
# HeyGen's bucket was confirmed live 2026-09-14 the same way: read out of the `upload_url` that
# HeyGen's own `create_asset_upload` MCP tool minted. Also bucket-scoped — on an S3 transfer-
# acceleration endpoint the leftmost label IS the bucket — and distinct from the READ-side
# `heygen-product` bucket pinned in media_fetch.py, so neither pin implies the other.
ALLOWED_UPLOAD_HOSTS: frozenset[str] = frozenset(
    {
        "reap-user-upload-bkt-prod.s3-accelerate.amazonaws.com",
        "heygen-resources-prod.s3-accelerate.amazonaws.com",
    }
)

#: Headers a provider's tool result may ask the PUT to carry. HeyGen signs the upload with
#: ``content-type`` and ``x-amz-server-side-encryption`` in ``X-Amz-SignedHeaders``, so omitting
#: either is a 403. Anything else — above all an auth, cookie or credential header — is refused
#: rather than forwarded: the signed URL stays the only credential this module ever sends.
PASSTHROUGH_UPLOAD_HEADERS: frozenset[str] = frozenset(
    {"content-type", "x-amz-server-side-encryption"}
)

MAX_BYTES = 2 * 1024 * 1024 * 1024  # 2GB — a raw screen recording, generously capped
TIMEOUT_SECONDS = 300
#: "Same-turn" enforced as: the provider's own signature is still fresh. A caller cannot claim
#: same-turn after the fact; this checks the credential itself, not the claim.
MAX_SIGNATURE_AGE_SECONDS = 300


class EgressRefused(RuntimeError):
    """A destination, file, or tool-result shape failed a confinement check. Fail closed —
    never fall back to uploading anyway."""


def _check_url(url: str) -> str:
    """Return ``url`` if it is https and inside ALLOWED_UPLOAD_HOSTS, else raise EgressRefused."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise EgressRefused(f"refusing non-https upload URL (scheme={parsed.scheme!r}): {url}")
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_UPLOAD_HOSTS:
        raise EgressRefused(
            f"refusing host {host!r} — not in ALLOWED_UPLOAD_HOSTS "
            f"({', '.join(sorted(ALLOWED_UPLOAD_HOSTS)) or '<empty>'}). This is fail-closed by "
            "design: pin the host in gtm_core/reap_upload.py only after confirming it live, "
            "then update CLAUDE.md + SECURITY-SELF-ASSESSMENT.md (DR-03) in the same commit."
        )
    return url


def _check_signature_freshness(url: str, *, now: float | None = None) -> None:
    """Refuse a pre-signed URL whose own ``X-Amz-Date``/``X-Amz-Expires`` query params show it
    is already stale or expired. Not every provider uses SigV4 query auth, so absence of these
    params is a no-op here (not proof of staleness) — the host allowlist + shape check remain
    the hard gate either way."""
    q = parse_qs(urlparse(url).query)
    amz_date = q.get("X-Amz-Date", [None])[0]
    amz_expires = q.get("X-Amz-Expires", [None])[0]
    if not amz_date or not amz_expires:
        return
    try:
        signed_ts = calendar.timegm(time.strptime(amz_date, "%Y%m%dT%H%M%SZ"))
        expires_s = int(amz_expires)
    except (ValueError, OverflowError) as exc:
        raise EgressRefused(f"unparseable signature timestamp on upload URL: {exc}") from exc
    current = now if now is not None else time.time()
    age = current - signed_ts
    if age > MAX_SIGNATURE_AGE_SECONDS:
        raise EgressRefused(
            f"upload URL was signed {age:.0f}s ago, over the {MAX_SIGNATURE_AGE_SECONDS}s "
            "freshness window — refusing a stale credential rather than trusting a caller's "
            "claim that this is 'the same turn'"
        )
    if current > signed_ts + expires_s:
        raise EgressRefused("upload URL's own X-Amz-Expires window has already elapsed")


def _signed_content_type(url: str) -> str:
    """Return the Content-Type the URL's own signature was computed against.

    A pre-signed S3-style PUT URL can bind its signature to a specific Content-Type via a
    ``content-type`` query parameter; sending anything else at PUT time is a signature mismatch
    (403), not a content-type quibble the server shrugs off. Observed live 2026-08-21: a URL
    carrying ``content-type=video%2Fmp4`` rejected a hardcoded ``application/octet-stream`` PUT.
    Falls back to ``application/octet-stream`` when the URL carries no such parameter — this
    module has no business guessing a type from the file's own extension.
    """
    q = parse_qs(urlparse(url).query)
    return q.get("content-type", ["application/octet-stream"])[0]


def _heygen_headers(raw_headers: object) -> dict[str, str]:
    """Validate HeyGen's ``upload_headers`` against PASSTHROUGH_UPLOAD_HEADERS. Refuses — never
    drops — an unexpected header name, because a silently dropped signed header is a 403 and a
    silently forwarded credential header is the leak this module exists to prevent."""
    if raw_headers is None:
        return {}
    if not isinstance(raw_headers, dict):
        raise EgressRefused("tool result 'upload_headers' must be a JSON object")
    headers: dict[str, str] = {}
    for name, value in raw_headers.items():
        if not isinstance(name, str) or name.lower() not in PASSTHROUGH_UPLOAD_HEADERS:
            raise EgressRefused(
                f"refusing upload header {name!r} — only "
                f"{', '.join(sorted(PASSTHROUGH_UPLOAD_HEADERS))} may be forwarded"
            )
        if not isinstance(value, str) or not value:
            raise EgressRefused(f"upload header {name!r} must be a non-empty string")
        headers[name.lower()] = value
    return headers


def parse_tool_result(raw: str) -> dict:
    """Parse the RAW JSON a provider's upload-minting tool returned. Two documented shapes:
    Reap ``request_upload_url`` → ``{uploadUrl, id, fileName}``, and HeyGen
    ``create_asset_upload`` → ``{asset_id, upload_url, upload_headers, ...}``. HeyGen's is
    normalized onto Reap's keys (plus ``headers``) so ``upload()`` has one path. Never accepts a
    bare URL string, which would strip the structural guarantee that this came from the tool
    call itself."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EgressRefused(f"tool result is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise EgressRefused("tool result must be a JSON object, not a bare URL or string")
    if "uploadUrl" not in data and "upload_url" in data:
        upload_url = data.get("upload_url")
        if not isinstance(upload_url, str) or not upload_url:
            raise EgressRefused("tool result is missing a non-empty 'upload_url' field")
        asset_id = data.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id:
            raise EgressRefused("tool result is missing a non-empty 'asset_id' field")
        return {
            "uploadUrl": upload_url,
            "id": asset_id,
            "fileName": None,
            "headers": _heygen_headers(data.get("upload_headers")),
        }
    upload_url = data.get("uploadUrl")
    if not isinstance(upload_url, str) or not upload_url:
        raise EgressRefused("tool result is missing a non-empty 'uploadUrl' field")
    upload_id = data.get("id")
    if not isinstance(upload_id, str) or not upload_id:
        raise EgressRefused("tool result is missing a non-empty 'id' field")
    return data


def _safe_source(file_path: Path, *, content_root: Path) -> Path:
    """Refuse a source file outside the resolved content root, missing, or oversized."""
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
    size = resolved.stat().st_size
    if size > MAX_BYTES:
        raise EgressRefused(f"source file is {size} bytes, over the {MAX_BYTES} byte cap")
    return resolved


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A PUT redirect could re-send the bytes (and the still-attached signed query string) to a
    host the allowlist never approved — refuse, never follow."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        raise EgressRefused(
            f"upload host attempted a redirect to {newurl!r} — refused, not followed"
        )


def upload(
    file_path: Path,
    tool_result_raw: str,
    *,
    profile: str,
    content_root: Path | None = None,
    now: float | None = None,
) -> dict:
    """PUT ``file_path``'s bytes to the URL in the raw ``request_upload_url`` tool result.

    Raises :class:`EgressRefused` on any confinement violation — host, shape, staleness, or
    source-file location/size — before a single byte is sent.
    """
    # Default root is the PROFILE's tree, resolved — the same shape media_fetch.py uses. Resolving
    # only `content/` broke on a profile directory that is itself a symlink (content/<p> → a
    # synced drive): the source resolved through the link and was refused as outside `content/`.
    # It is also the narrower boundary: one tenant's upload cannot read another tenant's files.
    if content_root is not None:
        root = content_root
    else:
        try:
            root = (resolve_content_root() / _safe_segment(profile, "profile")).resolve()
        except ValueError as exc:
            raise EgressRefused(str(exc)) from exc
    source = _safe_source(Path(file_path), content_root=root)
    result = parse_tool_result(tool_result_raw)
    url = _check_url(result["uploadUrl"])
    _check_signature_freshness(url, now=now)

    headers = result.get("headers") or {}
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, method="PUT", data=source.read_bytes())
    request.add_header("Content-Type", headers.get("content-type") or _signed_content_type(url))
    for name, value in headers.items():
        if name != "content-type":
            request.add_header(name, value)
    with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
        status = response.status

    return {
        "profile": profile,
        "upload_id": result["id"],
        "file_name": result.get("fileName"),
        "source": str(source),
        "bytes": source.stat().st_size,
        "status": status,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.reap_upload",
        description="PUT a local file to a Reap-issued pre-signed upload URL.",
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument("--file", required=True, type=Path, help="Path under the content root.")
    args = parser.parse_args(argv)

    raw = sys.stdin.read()
    if not raw.strip():
        parser.error("pass the raw request_upload_url tool result JSON on stdin")

    try:
        result = upload(args.file, raw, profile=args.profile)
    except (EgressRefused, urllib.error.URLError, OSError) as exc:
        print(f"[reap-upload] {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
