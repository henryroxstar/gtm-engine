from __future__ import annotations

from pathlib import Path

from ..paths import _safe_segment, resolve_content_root

# --- IO -----------------------------------------------------------------


def _prospects_dir(profile: str, content_root: Path | None = None) -> Path:
    root = content_root or resolve_content_root()
    # ``profile`` reaches here straight from --profile; guard it as a bare segment
    # before it is joined (CLAUDE.md tenant boundary).
    return root / _safe_segment(profile, "profile") / "prospects"


def _sequences_dir(profile: str, content_root: Path | None = None) -> Path:
    return _prospects_dir(profile, content_root) / "sequences"


def _pool_dir(profile: str, content_root: Path | None = None) -> Path:
    """Hidden home for everything that isn't the one human-facing load file:
    the full master-list audit, the needs-verification hold queue, snapshots,
    and the blocked log. Keeps ``sequences/`` down to a single visible CSV."""
    return _sequences_dir(profile, content_root) / ".pool"


def ready_to_load_path(profile: str, content_root: Path | None = None) -> Path:
    """The ONE file a human (or the email-sequence skill) loads from."""
    return _sequences_dir(profile, content_root) / "ready-to-load.csv"


def needs_verification_path(profile: str, content_root: Path | None = None) -> Path:
    return _pool_dir(profile, content_root) / "needs-verification.csv"


def dnc_cache_path(profile: str, content_root: Path | None = None) -> Path:
    """Where the skill/agent layer should dump the live Saleshandy DNC entries
    before calling :func:`consolidate` — this module never calls the Saleshandy MCP
    itself (plumbing vs. judgment split).

    Two accepted shapes. Preferred::

        {"emails": [...], "domains": [...], "fetched_at": "<ISO-8601 UTC>"}

    Legacy (still read, for caches written before domains were supported)::

        ["addr@example.com", ...]

    A legacy bare list carries no ``fetched_at``, so it can never satisfy a staleness
    check — under ``require_dnc`` it is rejected as un-datable rather than trusted.
    """
    return _prospects_dir(profile, content_root) / ".cache" / "dnc-emails.json"


def _accounts_dir(profile: str, content_root: Path | None = None) -> Path:
    root = content_root or resolve_content_root()
    return root / _safe_segment(profile, "profile") / "accounts"
