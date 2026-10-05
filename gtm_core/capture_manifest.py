"""The URL manifest and allowlist for an unattended source-capture run (signal-first R0.3).

A capture run may scrape only what its manifest names, with exactly the options the manifest
pins, and no more pages than its cap. The manifest is written by Python (``signal_obs due
--write-manifest``) before the run starts; :class:`CaptureGate` is the PreToolUse check
``agent/permissions.py`` consults for a run that carries one. A model never edits either: a page
it fetched off-list would be evidence nobody asked for and money nobody approved.

The gate is a closed allowlist: the ``firecrawl_scrape`` leaf (checked against the manifest) and
a ``Read`` of the run's own manifest file. Every other tool is denied. A page the brain scraped is
untrusted text (§R5); if the web fetch, a shell, a file write or any other connector stayed open,
text on that page could steer the brain to fetch an arbitrary URL, write into ``content/`` or
forge a capture. Search, crawl, map, agent and interact fan out to pages the manifest cannot list.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .paths import _safe_segment, resolve_content_root
from .signal_sources import sources_dir_for

SCHEMA = 1
PINNED_FORMATS = ["markdown"]
PINNED_ONLY_MAIN_CONTENT = False
_OPTION_KEYS = ("formats", "onlyMainContent", "maxAge")
_SCRAPE_LEAF = "firecrawl_scrape"
#: The closed set of codes ``CaptureGate.check`` returns, so a ledger can tell a code from text.
REASONS = (
    "tool-not-allowed",
    "url-not-in-manifest",
    "options-differ",
    "cap-exceeded",
    "budget-exhausted",
)
#: A query on a listed page may only page through it. Anything else is a channel: a URL the
#: brain builds can carry data out in a query string, so only these names, with short plain values.
_CURSOR_PARAMS = frozenset({"cursor", "page", "offset", "next", "after", "start", "p"})
_CURSOR_VALUE = re.compile(r"[A-Za-z0-9._~=+-]{1,100}")
#: A URL is sent as written. ``urlsplit`` silently drops tab, CR and LF, so a check that reads the
#: parts would pass a string whose raw bytes differ: only printable ASCII, no space, is a URL here.
_PLAIN_URL = re.compile(r"[\x21-\x7e]{1,2000}")


class ManifestError(ValueError):
    """The manifest cannot be written or trusted. The message says what and why."""


@dataclass(frozen=True)
class CaptureManifest:
    run_id: str
    profile: str
    urls: list[str]
    max_age_ms: int
    cap: int
    options: dict = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "options",
            {
                "formats": list(PINNED_FORMATS),
                "onlyMainContent": PINNED_ONLY_MAIN_CONTENT,
                "maxAge": self.max_age_ms,
            },
        )


def _check_url(url: object) -> str:
    if not isinstance(url, str):
        raise ManifestError(f"manifest URL is not a string: {url!r}")
    if _PLAIN_URL.fullmatch(url) is None:
        raise ManifestError(
            f"manifest URL has a space, a control character or a non-ASCII one: {url!r}"
        )
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ManifestError(f"manifest URL must be an absolute http(s) URL: {url!r}")
    return url


def _validated(run_id: str, profile: str, urls: list, cap: int, max_age_ms: int) -> CaptureManifest:
    try:
        _safe_segment(run_id, "run_id")
        _safe_segment(profile, "profile")
    except ValueError as exc:
        raise ManifestError(str(exc)) from exc
    if not urls:
        raise ManifestError("a manifest with no URLs would allow nothing; refusing to write it")
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
        raise ManifestError(f"cap must be a positive integer, got {cap!r}")
    if isinstance(max_age_ms, bool) or not isinstance(max_age_ms, int) or max_age_ms < 0:
        raise ManifestError(f"max_age_ms must be a non-negative integer, got {max_age_ms!r}")
    return CaptureManifest(
        run_id=run_id,
        profile=profile,
        urls=[_check_url(u) for u in urls],
        max_age_ms=max_age_ms,
        cap=cap,
    )


def write_manifest(
    *,
    profile: str,
    run_id: str,
    urls: list[str],
    cap: int,
    max_age_ms: int,
    content_root: Path | None = None,
) -> Path:
    """Write ``content/<profile>/sources/manifest-<run_id>.json`` and return its path."""
    manifest = _validated(run_id, profile, urls, cap, max_age_ms)
    path = sources_dir_for(profile, content_root) / f"manifest-{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "schema": SCHEMA,
        "run_id": manifest.run_id,
        "profile": manifest.profile,
        "urls": manifest.urls,
        "options": manifest.options,
        "cap": manifest.cap,
    }
    path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_manifest(path: Path | str) -> CaptureManifest:
    """Read a manifest, refusing one whose pinned options are not the pinned ones."""
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestError(f"cannot read manifest {p}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ManifestError(f"manifest {p} is not schema {SCHEMA}")
    opts = data.get("options")
    if (
        not isinstance(opts, dict)
        or opts.get("formats") != PINNED_FORMATS
        or opts.get("onlyMainContent") is not PINNED_ONLY_MAIN_CONTENT
    ):
        raise ManifestError(f"manifest {p} pins options other than the fixed capture options")
    return _validated(
        str(data.get("run_id", "")),
        str(data.get("profile", "")),
        data.get("urls") if isinstance(data.get("urls"), list) else [],
        data.get("cap"),
        opts.get("maxAge"),
    )


def _authority(url: str) -> tuple[str, str, str] | None:
    parts = urllib.parse.urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    return parts.scheme.lower(), parts.netloc.lower(), parts.path.removesuffix("/")


def _is_cursor_query(url: str) -> bool:
    """No query, or one or two paging parameters with plain short values."""
    query = urllib.parse.urlsplit(url).query
    if not query:
        return True
    pairs = urllib.parse.parse_qsl(query, keep_blank_values=True, strict_parsing=True)
    return len(pairs) <= 2 and all(
        k in _CURSOR_PARAMS and _CURSOR_VALUE.fullmatch(v) is not None for k, v in pairs
    )


class CaptureGate:
    """PreToolUse check for one capture run. ``check`` returns None (allow) or a reason code.

    Stateful on purpose: the cap counts allowed calls across the run, and a denied call consumes
    nothing. ``budget_ok`` is the §R2 guard; if it says no, or raises, no paid call is made.
    """

    def __init__(
        self,
        manifest: CaptureManifest,
        budget_ok: Callable[[], bool] | None = None,
        manifest_path: Path | str | None = None,
    ):
        self._manifest = manifest
        self._budget_ok = budget_ok
        self._manifest_path = os.path.realpath(manifest_path) if manifest_path else None
        self._exact = {u.removesuffix("/") for u in manifest.urls}
        self._cursor_bases = {a for a in (_authority(u) for u in manifest.urls) if a}
        self.used = 0
        self.last_reason: str | None = None

    @property
    def manifest_path(self) -> str | None:
        """The one file a run may ``Read``: its own manifest, as an absolute real path."""
        return self._manifest_path

    def _url_listed(self, url: object) -> bool:
        if not isinstance(url, str) or _PLAIN_URL.fullmatch(url) is None:
            return False
        try:
            # Compared as written, never through ``url_norm``: that strips ``utm_*`` parameters
            # and the fragment, which would let a call carry data out in either one.
            if url.removesuffix("/") in self._exact:
                return True
            # A cursor is a query on a listed page: same scheme, host and path, nothing else.
            return (
                _authority(url) in self._cursor_bases
                and not urllib.parse.urlsplit(url).fragment
                and _is_cursor_query(url)
            )
        except ValueError:  # a malformed URL (an unclosed bracket, a bad port) is never listed
            return False

    def _options_pinned(self, tool_input: dict) -> bool:
        extra = set(tool_input) - {"url", *_OPTION_KEYS}
        if extra or any(k not in tool_input for k in _OPTION_KEYS):
            return False
        pinned = self._manifest.options
        return all(tool_input[k] == pinned[k] for k in _OPTION_KEYS) and (
            tool_input["onlyMainContent"] is False
        )

    def check(self, tool_name: str, tool_input: object) -> str | None:
        self.last_reason = self._decide(tool_name, tool_input)
        return self.last_reason

    def _may_read(self, tool_input: object) -> bool:
        """True only for an absolute path that is this run's own manifest file."""
        path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
        return (
            self._manifest_path is not None
            and isinstance(path, str)
            and os.path.isabs(path)
            and os.path.realpath(path) == self._manifest_path
        )

    def _decide(self, tool_name: str, tool_input: object) -> str | None:
        if tool_name == "Read":
            return None if self._may_read(tool_input) else "tool-not-allowed"
        if "firecrawl" not in (tool_name or "").lower():
            return "tool-not-allowed"
        if tool_name.rsplit("__", 1)[-1] != _SCRAPE_LEAF:
            return "tool-not-allowed"
        call = tool_input if isinstance(tool_input, dict) else {}
        if not self._url_listed(call.get("url")):
            return "url-not-in-manifest"
        if not self._options_pinned(call):
            return "options-differ"
        if self.used >= self._manifest.cap:
            return "cap-exceeded"
        if self._budget_ok is not None:
            try:
                ok = bool(self._budget_ok())
            except Exception:  # noqa: BLE001 - an unreadable guard must refuse, never pass
                ok = False
            if not ok:
                return "budget-exhausted"
        self.used += 1
        return None


def manifest_path_for(profile: str, run_id: str, content_root: Path | None = None) -> Path:
    root = content_root if content_root is not None else resolve_content_root()
    return sources_dir_for(profile, root) / f"manifest-{_safe_segment(run_id, 'run_id')}.json"
