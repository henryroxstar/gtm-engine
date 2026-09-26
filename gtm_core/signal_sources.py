"""Captured source pages for research verification (W6 R6.1, R6.4).

Content-addressed storage of fetched web pages, index tracking, and retention pruning.
Does not import any HTTP client — capture happens at the tool boundary.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from .merge_hygiene import Finding
from .paths import _safe_segment, resolve_content_root

__all__ = [
    "Capture",
    "url_norm",
    "store_capture",
    "get_latest_capture",
    "get_capture_text",
    "normalise_quote_whitespace",
    "validate_source_evidence",
    "prune",
]

_SMART_SINGLE_QUOTES = re.compile(r"[‘’‚‛]")
_SMART_DOUBLE_QUOTES = re.compile(r"[“”„‟«»]")
_WHITESPACE_RE = re.compile(r"\s+")

PERSONALISED_LANES = frozenset({"personalised", "personalized", "signal"})
GENERIC_LANES = frozenset({"generic", "premise-only"})
OTHER_LANES = frozenset({"repair", "hold", "excluded"})
KNOWN_LANES = PERSONALISED_LANES | GENERIC_LANES | OTHER_LANES


@dataclass(frozen=True)
class Capture:
    """One captured source page."""

    url_norm: str
    sha256: str
    fetched_at: str
    tool: str
    text: str


def url_norm(url: str) -> str:
    """Normalise a URL for source index lookup.

    - Case-fold host.
    - Strip fragment.
    - Strip utm_* query parameters.
    - Normalize trailing slash on path (path '/' or '/foo/' -> '' or '/foo').
    - Scheme (http vs https) stays distinct.
    """
    if not url:
        return ""
    parts = urllib.parse.urlsplit(url.strip())
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/")
    if parts.query:
        pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        filtered = [(k, v) for k, v in pairs if not k.lower().startswith("utm_")]
        query = urllib.parse.urlencode(filtered)
    else:
        query = ""
    return urllib.parse.urlunsplit((scheme, netloc, path, query, ""))


def _resolve_sources_dir(sources_dir: Path | str | None = None, profile: str | None = None) -> Path:
    if sources_dir is not None:
        return Path(sources_dir)
    if profile:
        return resolve_content_root() / _safe_segment(profile, "profile") / "sources"
    try:
        from .active_profile import show

        act = show()
        if act:
            return resolve_content_root() / _safe_segment(act, "profile") / "sources"
    except Exception:
        return resolve_content_root() / "sources"
    return resolve_content_root() / "sources"


def store_capture(
    url: str,
    text: str,
    *,
    sources_dir: Path | str | None = None,
    profile: str | None = None,
    tool: str = "firecrawl",
    fetched_at: str | None = None,
) -> str:
    """Store text content-addressed by sha256 and append row to sources.jsonl."""
    dir_path = _resolve_sources_dir(sources_dir, profile)
    dir_path.mkdir(parents=True, exist_ok=True)

    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    (dir_path / f"{sha}.txt").write_text(text, encoding="utf-8")

    ts = fetched_at or datetime.datetime.now(datetime.UTC).isoformat()
    norm = url_norm(url)
    entry = {"url_norm": norm, "fetched_at": ts, "sha256": sha, "tool": tool}

    for fname in ("index.jsonl", "sources.jsonl"):
        with open(dir_path / fname, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return sha


def _read_index_entries(dir_path: Path) -> list[dict]:
    entries: list[dict] = []
    p = (
        dir_path / "index.jsonl"
        if (dir_path / "index.jsonl").is_file()
        else dir_path / "sources.jsonl"
    )
    if not p.is_file():
        return []
    try:
        content = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Unreadable capture index {p}: {exc}") from exc
    for lineno, line in enumerate(content.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Corrupt capture index {p}:{lineno}: {exc}") from exc
    return entries


def get_latest_capture(
    url: str,
    *,
    sources_dir: Path | str | None = None,
    profile: str | None = None,
) -> Capture | None:
    """Return the latest Capture matching url_norm(url), or None."""
    dir_path = _resolve_sources_dir(sources_dir, profile)
    if not dir_path.is_dir():
        return None

    norm = url_norm(url)
    entries = _read_index_entries(dir_path)
    matching = [e for e in entries if e.get("url_norm") == norm]
    if not matching:
        return None

    # Latest row wins (order of capture / fetched_at)
    latest_entry = matching[-1]
    sha = latest_entry.get("sha256", "")
    text_file = dir_path / f"{sha}.txt"
    if not text_file.is_file():
        return None

    text = text_file.read_text(encoding="utf-8")
    return Capture(
        url_norm=norm,
        sha256=sha,
        fetched_at=latest_entry.get("fetched_at", ""),
        tool=latest_entry.get("tool", ""),
        text=text,
    )


def get_capture_text(
    url: str,
    *,
    sources_dir: Path | str | None = None,
    profile: str | None = None,
) -> str | None:
    """Return raw text of latest capture for url, or None."""
    cap = get_latest_capture(url, sources_dir=sources_dir, profile=profile)
    return cap.text if cap is not None else None


def normalise_quote_whitespace(text: str) -> str:
    """Fold smart quotes and collapse whitespace for verbatim comparison."""
    if not text:
        return ""
    t = _SMART_SINGLE_QUOTES.sub("'", text)
    t = _SMART_DOUBLE_QUOTES.sub('"', t)
    return _WHITESPACE_RE.sub(" ", t).strip()


def validate_source_evidence(
    evidence: str,
    url: str,
    *,
    lane: str | None = None,
    sources_dir: Path | str | None = None,
    profile: str | None = None,
) -> list[Finding]:
    """Validate signal_evidence against latest capture of url."""
    out: list[Finding] = []
    stripped_ev = (evidence or "").strip()

    if len(stripped_ev) < 20:
        out.append(
            Finding(
                "block",
                "signal_evidence",
                "evidence-too-short",
                f"evidence is {len(stripped_ev)} characters — under the 20-character minimum",
            )
        )
        return out

    severity = "block"
    if not lane or not str(lane).strip():
        severity = "block"
    else:
        lane_str = str(lane).strip().lower()
        if lane_str not in KNOWN_LANES:
            out.append(
                Finding("block", "lane", "lane-unknown", f"{lane!r} is not a recognised lane")
            )
            severity = "block"
        elif lane_str in GENERIC_LANES:
            severity = "warn"
        else:
            severity = "block"

    capture = get_latest_capture(url, sources_dir=sources_dir, profile=profile)
    if capture is None:
        out.append(
            Finding(
                severity,
                "signal_evidence",
                "no-source-capture",
                f"no capture on file for source URL {url!r}",
            )
        )
        return out

    norm_ev = normalise_quote_whitespace(stripped_ev)
    norm_cap = normalise_quote_whitespace(capture.text)
    if norm_ev not in norm_cap:
        out.append(
            Finding(
                severity,
                "signal_evidence",
                "signal-evidence-not-in-source",
                f"evidence is not a verbatim substring of the captured source for {url!r}",
            )
        )

    return out


def _parse_iso_date(val: str) -> datetime.date | None:
    raw = (val or "").strip()[:10]
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        return None


def prune(
    sources_dir: Path | str | None = None,
    *,
    profile: str | None = None,
    older_than_days: int | None = None,
    as_of: datetime.date | None = None,
    apply: bool = False,
) -> list[Path]:
    """Prune unreferenced and/or expired source captures."""
    dir_path = _resolve_sources_dir(sources_dir, profile)
    if not dir_path.is_dir():
        return []

    entries = _read_index_entries(dir_path)
    today = as_of or datetime.date.today()

    active_entries: list[dict] = []
    expired_entries: list[dict] = []
    for e in entries:
        dt = _parse_iso_date(e.get("fetched_at", ""))
        if older_than_days is not None and dt is not None:
            age = (today - dt).days
            if age > older_than_days:
                expired_entries.append(e)
                continue
        active_entries.append(e)

    active_hashes = {e.get("sha256") for e in active_entries if e.get("sha256")}
    all_files = sorted(dir_path.glob("*.txt"))
    to_remove = [f for f in all_files if f.stem not in active_hashes]

    if apply:
        for f in to_remove:
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass
        if older_than_days is not None:
            lines = [json.dumps(e, ensure_ascii=False) + "\n" for e in active_entries]
            for fname in ("index.jsonl", "sources.jsonl"):
                (dir_path / fname).write_text("".join(lines), encoding="utf-8")

    return to_remove


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage captured signal sources")
    sub = parser.add_subparsers(dest="cmd", required=True)

    prune_p = sub.add_parser("prune", help="Prune unreferenced or stale source captures")
    prune_p.add_argument("--sources-dir", type=Path, default=None)
    prune_p.add_argument("--profile", type=str, default=None)
    prune_p.add_argument("--older-than", type=int, default=None, dest="older_than")
    prune_p.add_argument("--apply", action="store_true", default=False)

    capture_p = sub.add_parser("capture", help="Capture a source page text")
    capture_p.add_argument("--url", required=True, type=str)
    capture_p.add_argument("--text", type=str, default=None)
    capture_p.add_argument("--file", type=Path, default=None)
    capture_p.add_argument("--sources-dir", type=Path, default=None)
    capture_p.add_argument("--profile", type=str, default=None)
    capture_p.add_argument("--tool", type=str, default="firecrawl")
    capture_p.add_argument("--fetched-at", type=str, default=None)

    args = parser.parse_args(argv)
    if args.cmd == "prune":
        removed = prune(
            sources_dir=args.sources_dir,
            profile=args.profile,
            older_than_days=args.older_than,
            apply=args.apply,
        )
        action = "Removed" if args.apply else "Would remove"
        for p in removed:
            print(f"{action}: {p}")
        print(f"Total: {len(removed)} file(s)")
        return 0

    if args.cmd == "capture":
        text = ""
        if args.file is not None:
            text = args.file.read_text(encoding="utf-8")
        elif args.text is not None:
            text = args.text
        else:
            text = sys.stdin.read()

        if not text:
            print("Error: no text provided to capture", file=sys.stderr)
            return 1

        sha = store_capture(
            url=args.url,
            text=text,
            sources_dir=args.sources_dir,
            profile=args.profile,
            tool=args.tool,
            fetched_at=args.fetched_at,
        )
        print(sha)
        return 0


if __name__ == "__main__":
    sys.exit(main())
