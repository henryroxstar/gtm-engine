"""Host-pinned media downloader for provider render results (§R6 egress exception).

WHY THIS EXISTS
---------------
The creator pack's video stages finish their work locally: ``video-render`` muxes a
voice-over onto each shot, stitches the shots, and grades the joined asset; ``video-finish``
burns captions and the Article 50 disclosure line. Every one of those is an ``ffmpeg`` pass
over **local files**. But the assets themselves are produced by an MCP generation tool that
returns a provider CDN URL, and no MCP tool hands back bytes or a local path — so something
has to bridge URL → disk.

Until this module existed, that bridge was a shell ``ffmpeg -i https://…`` invocation. That
worked, and it was an **unregistered egress path**: the §R6 semgrep rule
(``gtm-no-raw-egress-in-brain``) is ``languages: [python]`` and matches import statements, so
a subprocess never trips it; ``ffmpeg`` is not in ``agent/permissions.py``'s
``_DANGEROUS_PROGRAMS`` and is unlisted in ``.claude/settings.json``, so the Bash denylist
allows it; and ``tests/contracts/test_overlay_egress_contract.py`` derives its file list from
the semgrep allowlist, so a path that never trips the rule is invisible to it too. Net: an
arbitrary URL could be fetched with no allowlist, no scheme check, and no size cap, and no
automated gate would have seen it.

This module is the narrow, reviewable fix: a **file download** from an explicit allowlist of
provider hosts, and nothing else. Skills call this, then run ``ffmpeg`` on local files only.

WHAT THIS IS NOT
----------------
This is not a general HTTP client and must never become one:

* The host allowlist below is the security boundary. It is a hardcoded constant, never
  derived from a profile, a skill argument, a CLI flag, or brain output — a *destination* is
  not representable by the caller (same property as the publish gate).
* https only. A plain-http URL is refused, not upgraded.
* Redirects are followed **only** while every hop stays inside the allowlist. A cross-host
  redirect is a hard failure, not a follow — otherwise the allowlist would be trivially
  bypassable by a provider-side open redirect.
* No credentials, cookies, or auth headers are ever sent or read. These are public,
  short-lived CDN result URLs; if a URL needs a secret to fetch, it does not belong here.
* GET only, response written straight to disk under the resolved content root, with a size
  cap. Nothing is executed, parsed, or evaluated.
* It does not discover URLs. The caller passes a URL that came from an MCP tool's own result
  (``result_url`` on a completed generation job), never one it composed or guessed.

ARCHITECTURE DEBT (accepted 2026-08-17, operator decision)
----------------------------------------------------------
The clean shape is a provider MCP tool that returns the rendered bytes, or a signed local
path, so the brain never touches a URL at all. Higgsfield's MCP surface returns only a
CloudFront ``result_url`` on a completed job, so no such tool exists today. This module is a
deliberate, scoped stand-in for that missing tool. It is registered as a known egress point
in ``.semgrep/gtm-invariants.yml`` (§R6 allowlist) and in ``CLAUDE.md`` (§Egress).
Retire it if/when the provider exposes a bytes-returning or local-path MCP tool.

USAGE
-----
Single file::

    uv run python -m gtm_core.media_fetch --profile example \\
        --url https://d8j0ntlcm91z4.cloudfront.net/user_XXX/hf_2026_abc.mp4 \\
        --dest video/2026-08-17-my-slug/raw/shot1.mp4

Batch (one call, many assets)::

    uv run python -m gtm_core.media_fetch --profile example \\
        --manifest content/example/video/<slug>/raw/fetch-manifest.json

where the manifest is ``{"downloads": [{"dest": "...", "url": "..."}, ...]}`` and every
``dest`` is relative to ``content/<profile>/``.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from gtm_core.paths import resolve_content_root

# ── the security boundary ────────────────────────────────────────────────────────
# Hardcoded on purpose. Adding a host here is a boundary change: update CLAUDE.md
# (§Egress) in the same commit, per CLAUDE.md "When you change a boundary here".
ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        # Higgsfield — generation results (video/audio/image) and reference-element
        # source media. Two distinct CloudFront distributions, both observed live
        # 2026-08-17: results land on d8j0…, element/thumbnail media on d2ol….
        # Pinned exactly rather than a *.cloudfront.net wildcard — a wildcard would
        # admit every CloudFront tenant on the internet, which is not an allowlist.
        "d8j0ntlcm91z4.cloudfront.net",
        "d2ol7oe51mr4n9.cloudfront.net",
        # HeyGen — completed avatar renders. Pinned 2026-08-20 from the `video_url` of a
        # live `get_video` response on a completed job. Same short-lived-signed-CDN shape
        # as the Higgsfield entries above: the URL carries its own Expires/Signature and
        # needs no auth header from us.
        "files2.heygen.ai",
        # HeyGen — avatar-look preview images/video and brand-kit logo. Pinned 2026-08-20
        # from live `list_avatar_looks`/`list_brand_kits` responses naming this exact host
        # in `preview_image_url`/`preview_video_url`/`logo_url` on real records (the
        # trained digital twin's own preview, and the account's brand-kit logo) — never
        # guessed. Distinct from `files2.heygen.ai` above (finished renders) on purpose:
        # each pin is scoped to the ONE HeyGen surface it was observed serving, never
        # widened to a `*.heygen.ai` wildcard, which would admit every surface the vendor
        # hosts under that domain rather than the two actually named by a live response.
        # Operator-requested 2026-08-20 so a look can be visually confirmed (wardrobe,
        # framing) before an avatar render is spent, rather than choosing blind.
        "resource2.heygen.ai",
    }
)

# A 9:16 720p shot is ~2-5MB; a stitched long-form asset with audio can reach ~15MB.
# 256MB is a runaway guard, not a working limit.
MAX_BYTES = 256 * 1024 * 1024
TIMEOUT_SECONDS = 300


class EgressRefused(RuntimeError):
    """A URL or destination failed a confinement check. Fail closed — never fetch anyway."""


def _check_url(url: str) -> str:
    """Return ``url`` if it is https and inside ALLOWED_HOSTS, else raise EgressRefused."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise EgressRefused(f"refusing non-https URL (scheme={parsed.scheme!r}): {url}")
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise EgressRefused(
            f"refusing host {host!r} — not in the §R6 allowlist "
            f"({', '.join(sorted(ALLOWED_HOSTS))}). This is fail-closed by design; "
            f"widening it is a documented boundary change, not a config tweak."
        )
    return url


class _AllowlistRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow redirects only while every hop stays inside the allowlist."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        _check_url(newurl)  # raises EgressRefused on an off-allowlist hop
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _safe_segment(segment: str, label: str) -> str:
    """Reject a path segment that could escape the content root."""
    if (
        not segment
        or "/" in segment
        or "\\" in segment
        or "\x00" in segment
        or segment in (".", "..")
    ):
        raise EgressRefused(f"unsafe {label}: {segment!r}")
    return segment


def resolve_dest(profile: str, dest: str) -> Path:
    """Resolve ``dest`` under ``content/<profile>/``, refusing anything that escapes it.

    ``dest`` is a relative path (it may contain ``/`` for subdirectories); the *resolved*
    result must still sit inside the profile's content root. This is the write-side mirror
    of the read-side allowlist: a fetch can neither read an arbitrary host nor write to an
    arbitrary path.
    """
    root = (resolve_content_root() / _safe_segment(profile, "profile")).resolve()
    if Path(dest).is_absolute():
        raise EgressRefused(f"--dest must be relative to content/<profile>/: {dest!r}")
    if "\x00" in dest:
        raise EgressRefused(f"unsafe --dest: {dest!r}")
    target = (root / dest).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise EgressRefused(
            f"refusing a destination outside the resolved content root: {target} (root: {root})"
        ) from exc
    return target


def fetch(url: str, profile: str, dest: str) -> Path:
    """Download one allowlisted URL to ``content/<profile>/<dest>``.

    Returns the written path. Raises EgressRefused if the URL (or any redirect hop) is
    outside the host allowlist, or if the destination escapes the content root.
    """
    _check_url(url)
    target = resolve_dest(profile, dest)

    opener = urllib.request.build_opener(_AllowlistRedirectHandler)
    request = urllib.request.Request(url, method="GET")  # no auth headers, by design
    with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
        payload = response.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise EgressRefused(f"response exceeds {MAX_BYTES} byte cap: {url}")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.media_fetch",
        description="Download a generation result from an allowlisted provider CDN host.",
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument("--url", help="single download URL (with --dest)")
    parser.add_argument("--dest", help="path relative to content/<profile>/ (with --url)")
    parser.add_argument("--manifest", help='JSON: {"downloads":[{"dest":..,"url":..}]}')
    args = parser.parse_args(argv)

    if args.manifest:
        jobs = json.loads(Path(args.manifest).read_text(encoding="utf-8"))["downloads"]
    elif args.url and args.dest:
        jobs = [{"url": args.url, "dest": args.dest}]
    else:
        parser.error("provide either --manifest, or both --url and --dest")

    written, failed = [], []
    for job in jobs:
        try:
            path = fetch(job["url"], args.profile, job["dest"])
        except (EgressRefused, urllib.error.URLError, OSError, ValueError) as exc:
            failed.append({"dest": job.get("dest"), "error": f"{type(exc).__name__}: {exc}"})
            print(f"FAIL {job.get('dest')}: {exc}", file=sys.stderr)
            continue
        size = path.stat().st_size
        written.append({"dest": job["dest"], "path": str(path), "bytes": size})
        print(f"ok   {job['dest']}: {size} bytes -> {path}")

    print(
        json.dumps(
            {
                "profile": args.profile,
                "downloaded": len(written),
                "failed": len(failed),
                "total_bytes": sum(item["bytes"] for item in written),
                "files": written,
                "errors": failed,
            },
            indent=2,
        )
    )
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
