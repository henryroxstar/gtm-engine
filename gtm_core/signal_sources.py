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

from .capture_index import fetched_at_key, filed_just_now, read_tolerant
from .merge_hygiene import Finding
from .paths import _safe_segment, resolve_content_root

try:  # POSIX advisory file lock, as in gtm_core.ledgers
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

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


def sources_dir_for(profile: str, content_root: Path | None = None) -> Path:
    """The one place a profile's capture folder is spelled: ``<content>/<profile>/sources``.

    ``content_root`` is the top-level content root (the one holding every profile), not a
    profile's own folder — passing it as ``content_root / "sources"`` is what once made the
    daily preflight read a tenant-less folder the hook never wrote to.
    """
    root = content_root if content_root is not None else resolve_content_root()
    return root / _safe_segment(profile, "profile") / "sources"


def _resolve_sources_dir(sources_dir: Path | str | None = None, profile: str | None = None) -> Path:
    """Explicit dir, else the named profile, else the active one, else the flat root.

    The flat-root fallback (``<content>/sources``, no profile segment) stays — plenty of
    callers (tests, a single-tenant flat content root) legitimately never bind a profile,
    and both sides of a read/write pair land there consistently. What must NOT happen is a
    malformed active-profile marker being swallowed into this same fallback: that bare
    ``except Exception`` is gone, so ``_safe_segment``'s ``ValueError`` on a bad marker now
    propagates instead of silently writing a tenant-less capture nobody warned about.
    """
    if sources_dir is not None:
        return Path(sources_dir)
    if profile:
        return sources_dir_for(profile)
    from .active_profile import show

    act = show()
    if act:
        return sources_dir_for(act)
    return resolve_content_root() / "sources"


#: Firecrawl response fields that are the page itself. `summary`, `json` and `query` are
#: Firecrawl's own LLM output about the page — a clause quoting them is a paraphrase of the
#: source, so they are never page text.
_PAGE_TEXT_KEYS = ("markdown", "content", "text")


def page_text(raw: object) -> str:
    """The page text inside a Firecrawl tool response or a stored capture.

    Accepts the shapes the tool boundary actually delivers: the response as a JSON *string*
    (how Claude Code hands an MCP result to a PostToolUse hook), MCP content blocks, or a
    parsed dict. Returns ``""`` for an HTTP error page and for a response that carries no
    page text. A plain-text capture comes back unchanged.
    """
    if isinstance(raw, str):
        s = raw.strip()
        parsed = None
        if s[:1] in "{[":
            try:
                parsed = json.loads(s)
            except ValueError:
                parsed = None
        return page_text(parsed) if parsed is not None else s
    if isinstance(raw, list):
        parts = [page_text(b) for b in raw if isinstance(b, dict | str)]
        return "\n\n".join(p for p in parts if p)
    if not isinstance(raw, dict):
        return ""
    meta = raw.get("metadata")
    status = meta.get("statusCode") if isinstance(meta, dict) else None
    if isinstance(status, int) and status >= 400:
        return ""
    data = raw.get("data")
    if isinstance(data, dict):
        return page_text(data)
    for key in _PAGE_TEXT_KEYS:
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            return page_text(val)
        if isinstance(val, list):
            return page_text(val)
    return ""


_MD_IMAGE = re.compile(r"!\[[^\]]*\]\((?:[^()\s]|\([^()]*\))*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\((?:[^()\s]|\([^()]*\))*(?:\s+\"[^\"]*\")?\)")
_MD_HARD_BREAK = re.compile(r"\\\n")
_MD_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|>~])")
_MD_STRONG = re.compile(r"\*\*|__")
_MD_EM = re.compile(r"(?<![\w*_])([*_])(?=\S)([^*_\n]+?)(?<=\S)\1(?![\w*_])")
_MD_LINE_MARK = re.compile(r"^[ \t]*(?:#{1,6}[ \t]+|>[ \t]?|[-*+][ \t]+)", re.MULTILINE)


def _markdown_to_text(text: str) -> str:
    """Delete markdown *syntax* so a quote of the rendered page can match its markdown.

    Every rule removes markup characters only — link targets, image lines, emphasis
    markers, backslash escapes, heading/list/quote markers. None adds, reorders, case-folds
    or drops a word or a punctuation mark of the prose, so a paraphrase stays a paraphrase.
    """
    t = _MD_IMAGE.sub(" ", text)
    t = _MD_LINK.sub(r"\1", t)
    t = _MD_HARD_BREAK.sub("\n", t)
    t = _MD_ESCAPE.sub(r"\1", t)
    t = _MD_STRONG.sub("", t)
    t = _MD_EM.sub(r"\2", t)
    return _MD_LINE_MARK.sub("", t)


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

    with open(dir_path / "index.jsonl", "a+", encoding="utf-8") as idx:
        if fcntl is not None:
            fcntl.flock(idx.fileno(), fcntl.LOCK_EX)
        try:
            # The same page filed twice in a moment is one paid call seen by two hooks (a headless
            # capture run has the run's hook and the project's), so it is one row. A later fetch
            # is a new row: cadence reads the date of the newest one.
            if filed_just_now(dir_path / "index.jsonl", norm, sha, ts):
                return sha
            for fname in ("index.jsonl", "sources.jsonl"):
                with open(dir_path / fname, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        finally:
            if fcntl is not None:
                fcntl.flock(idx.fileno(), fcntl.LOCK_UN)

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


def index_problems(
    *, sources_dir: Path | str | None = None, profile: str | None = None
) -> list[dict]:
    """Lines of the capture index a reader had to skip, as ``{line, kind, text}``."""
    dir_path = _resolve_sources_dir(sources_dir, profile)
    if not dir_path.is_dir():
        return []
    return read_tolerant(dir_path)[1]


def get_captures(
    url: str,
    *,
    sources_dir: Path | str | None = None,
    profile: str | None = None,
) -> list[Capture]:
    """Every capture whose text is on disk for ``url``, oldest first by ``fetched_at``.

    Order is by time, never by line position (R0.2): a union merge of two writers' indexes
    interleaves lines. Ties break by sha so the order is the same on every machine.
    """
    dir_path = _resolve_sources_dir(sources_dir, profile)
    if not dir_path.is_dir():
        return []
    norm = url_norm(url)
    entries, _ = read_tolerant(dir_path)
    matching = sorted((e for e in entries if e.get("url_norm") == norm), key=fetched_at_key)
    out: list[Capture] = []
    for e in matching:
        sha = e.get("sha256", "")
        # The sha names a file: only a full lowercase hex digest may, never a path.
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
            continue
        text_file = dir_path / f"{sha}.txt"
        if not text_file.is_file():
            continue
        out.append(
            Capture(
                url_norm=norm,
                sha256=sha,
                fetched_at=e.get("fetched_at", ""),
                tool=e.get("tool", ""),
                text=text_file.read_text(encoding="utf-8"),
            )
        )
    return out


def get_latest_capture(
    url: str,
    *,
    sources_dir: Path | str | None = None,
    profile: str | None = None,
) -> Capture | None:
    """The capture with the maximum ``fetched_at`` for ``url`` (ties by sha), or None.

    Raises on a damaged index, as BASE did: the evidence gate reads this, and a gate that
    quietly skips a bad line could pass on a capture it never saw (``get_captures`` tolerates).
    """
    if problems := index_problems(sources_dir=sources_dir, profile=profile):
        raise ValueError(
            f"Corrupt capture index: {len(problems)} unreadable line(s), first at {problems[0]['line']}"
        )
    captures = get_captures(url, sources_dir=sources_dir, profile=profile)
    if not captures:
        return None
    # The newest row wins even when its page is gone: BASE said "no capture" then, and quoting
    # evidence from an older page instead would pass a check the newest page may not.
    rows = read_tolerant(_resolve_sources_dir(sources_dir, profile))[0]
    newest = max((e for e in rows if e.get("url_norm") == url_norm(url)), key=fetched_at_key)
    return captures[-1] if newest.get("sha256") == captures[-1].sha256 else None


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

    try:
        capture = get_latest_capture(url, sources_dir=sources_dir, profile=profile)
    except ValueError as exc:
        out.append(Finding(severity, "signal_evidence", "source-folder-unresolved", str(exc)))
        return out
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

    # page_text unwraps a capture stored as the raw response envelope (every capture the
    # hook made before it parsed one), so its `\n` and `\"` are real characters again.
    norm_ev = normalise_quote_whitespace(_markdown_to_text(stripped_ev))
    norm_cap = normalise_quote_whitespace(_markdown_to_text(page_text(capture.text)))
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

    from .signal_obs.state import referenced_shas

    entries = _read_index_entries(dir_path)
    today = as_of or datetime.date.today()
    # A capture a member-set state file still points at is not ours to delete (R1.4).
    try:
        protected = referenced_shas(dir_path.parent / "prospects" / "observations")
    except ValueError as exc:
        raise ValueError(f"Refusing to prune, nothing deleted: {exc}") from exc

    active_entries: list[dict] = []
    expired_entries: list[dict] = []
    for e in entries:
        dt = _parse_iso_date(e.get("fetched_at", ""))
        if older_than_days is not None and dt is not None and e.get("sha256") not in protected:
            age = (today - dt).days
            if age > older_than_days:
                expired_entries.append(e)
                continue
        active_entries.append(e)

    active_hashes = {e.get("sha256") for e in active_entries if e.get("sha256")} | protected
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
        try:
            removed = prune(
                sources_dir=args.sources_dir,
                profile=args.profile,
                older_than_days=args.older_than,
                apply=args.apply,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
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
