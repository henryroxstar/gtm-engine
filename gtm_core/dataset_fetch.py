"""Host-pinned dataset downloader for prospecting exports (§R6 egress exception).

WHY THIS EXISTS
---------------
Bulk-mode prospect runs materialise their candidate set by exporting a CSV from a
data provider and ingesting it (``gtm_core.prospects_import ingest``). Until now the
only way to land that file was an operator manually downloading it and dropping it on
disk — the shell egress floor in ``.claude/settings.json`` denies ``curl``/``wget``, so a
headless or autonomous run simply could not finish. That made the documented "bulk mode"
unreachable without a human in the loop, which is the opposite of what the skill promises.

This module is the narrow, reviewable fix: a **file download** from an explicit
allowlist of provider hosts, and nothing else.

WHAT THIS IS NOT
----------------
This is not a general HTTP client and must never become one:

* The host allowlist below is the security boundary. It is a hardcoded constant,
  never derived from a profile, a skill argument, a CLI flag, or brain output —
  a *destination* is not representable by the caller (same property as the publish gate).
* https only. A plain-http URL is refused, not upgraded.
* Redirects are followed **only** while every hop stays inside the allowlist. A
  cross-host redirect is a hard failure, not a follow — otherwise the allowlist would
  be trivially bypassable by a provider-side open redirect.
* No credentials, cookies, or auth headers are ever sent or read. These are
  pre-signed, short-lived share URLs; if a URL needs a secret to fetch, it does not
  belong here.
* GET only, response written straight to disk under the resolved content root, with a
  size cap. Nothing is executed, parsed, or evaluated.

ARCHITECTURE DEBT (accepted 2026-08-12, operator decision)
-----------------------------------------------------------
The clean shape is a provider MCP tool that returns the rows directly, so the brain
never touches a URL at all — Vibe's ``export-to-csv`` returns only a share link, and
``show-sample`` caps at 5 preview rows, so no such tool exists today. This module is a
deliberate, scoped stand-in for that missing tool. It is registered as a known egress
point in ``.semgrep/gtm-invariants.yml`` (§R6 allowlist) and in ``CLAUDE.md``
(§Egress). Retire it if/when the provider exposes a row-returning MCP tool.

USAGE
-----
Single file::

    uv run python -m gtm_core.dataset_fetch --profile <slug> \\
        --url https://share.explorium.ai/XXXXXX --name us_ent_intent_a

Batch (the bulk-mode path — one call, many exports)::

    uv run python -m gtm_core.dataset_fetch --profile <slug> \\
        --manifest content/<slug>/prospects/imports/manifest-<run-id>.json

where the manifest is ``{"downloads": [{"name": "...", "url": "..."}, ...]}``.
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
        # Vibe Prospecting / Explorium — bulk candidate-set exports.
        # share.explorium.ai issues a 302 to the storage bucket below; that exact
        # bucket host is pinned rather than a wildcard, so an open redirect on the
        # share host cannot reach arbitrary S3 (verified live 2026-08-12).
        "share.explorium.ai",
        "app.vibeprospecting.ai",
        "mcp-datasets-prod.s3.amazonaws.com",
        # RocketReach — bulk contact-export downloads. If RocketReach also redirects
        # to a storage host, the first run fails closed naming that host; pin it here
        # then (do not widen to a wildcard).
        "rocketreach.co",
        "api.rocketreach.co",
    }
)

MAX_BYTES = 64 * 1024 * 1024  # a candidate-set CSV is ~100KB; 64MB is a runaway guard
MAX_REDIRECTS = 5
TIMEOUT_SECONDS = 120


class EgressRefused(RuntimeError):
    """A URL failed the allowlist. Fail closed — never fall back to fetching it anyway."""


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


def _safe_name(name: str) -> str:
    """Reject anything that could escape the imports directory."""
    if not name or "/" in name or "\\" in name or "\x00" in name or name in (".", ".."):
        raise ValueError(f"unsafe --name: {name!r}")
    return name


def imports_dir(profile: str) -> Path:
    """The profile's prospecting imports directory (created if absent)."""
    target = resolve_content_root() / _safe_name(profile) / "prospects" / "imports"
    target.mkdir(parents=True, exist_ok=True)
    return target


def fetch(url: str, profile: str, name: str) -> Path:
    """Download one allowlisted URL to ``content/<profile>/prospects/imports/<name>.csv``.

    Returns the written path. Raises EgressRefused if the URL (or any redirect hop)
    is outside the allowlist.
    """
    _check_url(url)
    # A caller who already wrote "<name>.csv" gets one .csv, not "<name>.csv.csv".
    stem = _safe_name(name)
    if stem.lower().endswith(".csv"):
        stem = stem[:-4]
    dest = imports_dir(profile) / f"{stem}.csv"

    opener = urllib.request.build_opener(_AllowlistRedirectHandler)
    request = urllib.request.Request(url, method="GET")  # no auth headers, by design
    with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
        payload = response.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise RuntimeError(f"response exceeds {MAX_BYTES} byte cap: {url}")

    dest.write_bytes(payload)
    return dest


def _row_count(path: Path) -> int:
    """Data rows in the written CSV (header excluded); 0 if unreadable."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return max(0, sum(1 for line in text.splitlines() if line.strip()) - 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.dataset_fetch",
        description="Download a prospecting export from an allowlisted provider host.",
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument("--url", help="single download URL (with --name)")
    parser.add_argument("--name", help="output basename, no extension (with --url)")
    parser.add_argument("--manifest", help='JSON: {"downloads":[{"name":..,"url":..}]}')
    args = parser.parse_args(argv)

    if args.manifest:
        jobs = json.loads(Path(args.manifest).read_text(encoding="utf-8"))["downloads"]
    elif args.url and args.name:
        jobs = [{"url": args.url, "name": args.name}]
    else:
        parser.error("provide either --manifest, or both --url and --name")

    written, failed = [], []
    for job in jobs:
        try:
            path = fetch(job["url"], args.profile, job["name"])
        except (EgressRefused, urllib.error.URLError, OSError, RuntimeError) as exc:
            failed.append({"name": job.get("name"), "error": f"{type(exc).__name__}: {exc}"})
            print(f"FAIL {job.get('name')}: {exc}", file=sys.stderr)
            continue
        rows = _row_count(path)
        written.append({"name": job["name"], "path": str(path), "rows": rows})
        print(f"ok   {job['name']}: {rows} rows -> {path}")

    print(
        json.dumps(
            {
                "profile": args.profile,
                "downloaded": len(written),
                "failed": len(failed),
                "total_rows": sum(item["rows"] for item in written),
                "files": written,
                "errors": failed,
            },
            indent=2,
        )
    )
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
