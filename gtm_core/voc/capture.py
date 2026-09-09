"""Capture customer-voice passages from LinkedIn replies and Syften digests into evidence.

LinkedIn reply drafts and Syften listening digests are both *read passages*: the agent that
authored them already retrieved and quoted the original source. This importer turns those
passages into verified ``customer-voice`` evidence records so they can count toward demand
breadth alongside SEC filings and other channels.

Claim assignment is deterministic and conservative. A small keyword map scores each passage
against the repo's known demand-claim vocabulary. If no claim scores above the threshold, the
record lands on a generic ``cross-channel-capture`` claim so it is preserved without inflating a
specific demand claim.

CLI::

    python -m gtm_core.voc.capture --profile P              # dry run
    python -m gtm_core.voc.capture --profile P --write      # append to evidence store
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from ..paths import PathConfig, _safe_segment
from . import evidence as ev
from .collect import CUSTOMER_VOICE

SOURCE_LINKEDIN = "linkedin_replies"
SOURCE_SYFTEN = "syften_market_signals"

#: Claim used when the keyword mapper cannot confidently assign a passage to a demand claim.
FALLBACK_CLAIM = "cross-channel-capture"

#: Minimum keyword hits before a claim assignment is considered confident.
MIN_CLAIM_SCORE = 2

#: Demand-claim vocabulary used for deterministic assignment. Stems are matched as substrings
#: so slight inflections ("identity"/"identities", "delegation"/"delegated") are covered.
CLAIM_KEYWORDS: dict[str, set[str]] = {
    "agents-act-autonomously-in-production": {
        "autonomous",
        "deploy",
        "production",
        "fleet",
        "live",
        "operate",
        "initiate",
        "execute",
    },
    "agent-access-control-is-a-disclosed-risk": {
        "risk",
        "control",
        "govern",
        "audit",
        "accountab",
        "supervis",
        "authori",
        "permit",
        "allow",
        "deny",
        "hold",
        "guardrail",
        "policy",
        "compliance",
        "regulat",
        "trust",
        "safety",
        "secure",
        "incident",
    },
    "agent-identity-frameworks-are-nascent": {
        "identity",
        "credential",
        "delegat",
        "framework",
        "did",
        "verifiable",
        "who",
        "whom",
        "behalf",
        "nascent",
        "guidance",
        "standard",
        "authority",
        "sender",
        "sent",
        "prove",
        "cross-org",
        "handoff",
        "chain",
    },
    "agentic-commerce-shifts-payment-liability": {
        "payment",
        "commerce",
        "x402",
        "transaction",
        "pay",
        "spend",
        "liability",
        "wallet",
    },
    "agentic-disintermediation-pressure": {
        "disintermediation",
        "platform",
        "lock-in",
        "lockin",
        "competitive",
        "booking",
        "travel",
        "intermediary",
        "substitute",
    },
    "attacker-side-agent-automation": {
        "attack",
        "breach",
        "adversar",
        "threat",
        "hacker",
        "malicious",
        "forensic",
    },
}

#: Link references in markdown: [text](url).
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\)]+)\)")

#: Date embedded in a filename or source line.
_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

#: Backticked handle/name used as a best-effort entity in Syften signals.
_BACKTICK_NAME_RE = re.compile(r"`([^`]{2,40})`")

#: A filename, not a speaker — the digest links in-repo artifacts the same way it cites people.
_FILENAME_RE = re.compile(r"\.(?:md|json|jsonl|csv|html?|py|toml|ya?ml|txt)$", re.IGNORECASE)

#: Attribution verb right after a backticked token — "`maishsk` published a series".
_ATTRIBUTION_RE = re.compile(
    r"^\W{0,3}(?:said|says|wrote|writes|published|publishes|notes|noted|posts|posted|asks|asked"
    r"|describes|described|reports|reported|commented|comments|observes|observed)\b",
    re.IGNORECASE,
)

#: Byline shape — "`jeffrschneider`, 2026-07-30" — the other way the digest attributes a handle.
_BYLINE_DATE_RE = re.compile(r"^\s*[,—-]\s*\d{4}-\d{2}-\d{2}")


def _normalise(text: str) -> str:
    """Lowercase, strip punctuation except hyphens, collapse whitespace."""
    text = re.sub(r"[^\w\s\-]", " ", text.lower())
    return " ".join(text.split())


def claim_for_text(*texts: str) -> str:
    """Return the best-matching demand claim_id for the joined *texts*, or the fallback.

    Scoring is a simple keyword overlap. It is deliberately conservative: a record with a weak
    or ambiguous match lands on ``FALLBACK_CLAIM`` rather than wrongly inflating a demand claim.
    """
    haystack = " " + _normalise(" ".join(t for t in texts if t)) + " "
    best_claim = FALLBACK_CLAIM
    best_score = 0
    for claim_id, stems in CLAIM_KEYWORDS.items():
        score = sum(1 for stem in stems if f" {stem}" in haystack)
        if score > best_score and score >= MIN_CLAIM_SCORE:
            best_score = score
            best_claim = claim_id
    return best_claim


def _extract_date(text: str) -> str:
    """Return the first ISO date found in *text*, or ``"unknown"``."""
    for m in _ISO_DATE_RE.finditer(text):
        try:
            from datetime import date

            date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return m.group(0)
        except ValueError:
            continue
    return "unknown"


def _file_url(path: Path) -> str:
    """A ``file://`` URL for *path* so LinkedIn drafts without a captured post URL still have
    honest, deterministic provenance."""
    return path.resolve().as_uri()


# ---------------------------------------------------------------------------
# LinkedIn reply parser
# ---------------------------------------------------------------------------

_CUSTOMER_BLOCK_RE = re.compile(
    r"<!--\s*voc:customer-voice\s*-->(.*?)"
    r"(?:<!--\s*voc:bd-focus\s*-->|\n##\s|\n---\s*$|$)",
    re.IGNORECASE | re.DOTALL,
)

# Field matchers inside a customer-voice block.
_WHO_RE = re.compile(r"-\s*Who:\s*(.+?)(?:\s*·|\n)", re.DOTALL)
_SOURCE_RE = re.compile(r"-\s*Source:\s*(.+?)$", re.MULTILINE | re.DOTALL)
_CORE_CLAIM_RE = re.compile(r"-\s*Core claim[^:]*:\s*(.+?)(?:\n-|\Z)", re.DOTALL)
_VERBATIM_RE = re.compile(r"-\s*Verbatim:\s*\"([\s\S]*?)\"", re.DOTALL)
_TOPIC_RE = re.compile(r"-\s*Topic / pillar:\s*(.+?)(?:\n-|\Z)", re.DOTALL)


def _first_field(block: str, pattern: re.Pattern) -> str:
    m = pattern.search(block)
    return m.group(1).strip() if m else ""


def _entity_from_who(who: str) -> str:
    """Entity is the poster's name, before the first ``·`` or em-dash."""
    who = who.strip()
    for sep in (" · ", " - ", " — ", "--", "-"):
        if sep in who:
            return who.split(sep)[0].strip()
    return who


def parse_linkedin(path: Path) -> list[ev.EvidenceRecord]:
    """Parse customer-voice blocks from one linkedin-reply markdown file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []

    records: list[ev.EvidenceRecord] = []
    for block in _CUSTOMER_BLOCK_RE.findall(text):
        who = _first_field(block, _WHO_RE)
        source = _first_field(block, _SOURCE_RE)
        core_claim = _first_field(block, _CORE_CLAIM_RE)
        verbatim = _first_field(block, _VERBATIM_RE)
        topic = _first_field(block, _TOPIC_RE)

        if not verbatim or not who:
            continue

        entity = _entity_from_who(who)
        date = _extract_date(source) if source else _extract_date(path.name)
        claim_id = claim_for_text(core_claim, topic)
        tags = ["linkedin-reply"]
        if topic:
            tags.append(re.sub(r"[^\w\-]+", "-", topic.lower()).strip("-"))

        records.append(
            ev.EvidenceRecord(
                claim_id=claim_id,
                verbatim=verbatim,
                url=_file_url(path),
                date=date,
                entity=entity,
                speaker=CUSTOMER_VOICE,
                source_id=SOURCE_LINKEDIN,
                verified=True,
                note="Customer-voice block extracted from a linkedin-reply draft. "
                f"Core claim: {core_claim or '(none)'}.",
                tags=tags,
            )
        )
    return records


# ---------------------------------------------------------------------------
# Syften digest parser
# ---------------------------------------------------------------------------

#: Section headings that have carried the per-signal findings across digest generations.
#: The digest is hand-written per issue and its heading has drifted three times since
#: 2026-07-28 ("Signals worth acting on" -> "Findings" -> "What practitioners actually
#: said"). Matching only the first name silently yielded zero Syften evidence for five
#: weeks while every pull reported success, so all three are accepted and a new name is a
#: one-line addition here rather than a silent regression.
_SIGNALS_SECTION_RE = re.compile(
    r"##\s*(?:Signals worth acting on|Findings|What practitioners actually said)\s*\n(.*?)"
    r"(?:^##\s|\Z)",
    re.DOTALL | re.IGNORECASE | re.MULTILINE,
)

#: The per-signal item header, in each style the digest has used. Order is irrelevant —
#: matches from every pattern are merged and sorted by position — but overlaps are
#: resolved first-wins so a single item never yields two records.
_SIGNAL_HEADER_PATTERNS = (
    # "### Signal 3 — H — MCP's own security posture is a numbered problem"
    re.compile(r"^###\s*Signal\s+\d+\s*[—\-]\s*[^—\-\n]+\s*[—\-]\s*(.+?)\s*$", re.MULTILINE),
    # "### 2. \"Acme Router\" is now a contested product name"
    re.compile(r"^###\s*\d+\.\s*(.+?)\s*$", re.MULTILINE),
    # "**1. Agents can't self-provision credentials — a named pain point.** From ..."
    re.compile(r"^\*\*\d+\.\s*(.+?)\*\*", re.MULTILINE),
)

#: Kept as the canonical single-style matcher for callers/tests that reference it.
_SIGNAL_HEADER_RE = _SIGNAL_HEADER_PATTERNS[0]


def _split_signals(section: str) -> list[tuple[str, str]]:
    """Split a findings section into ``(title, body)`` pairs across all header styles."""
    spans: list[tuple[int, int, str]] = []
    for pattern in _SIGNAL_HEADER_PATTERNS:
        for match in pattern.finditer(section):
            spans.append((match.start(), match.end(), match.group(1).strip()))
    if not spans:
        return []

    spans.sort(key=lambda span: span[0])
    # Two styles can match the same line (a bold-numbered item inside an "###" heading);
    # keep the first and drop anything that starts before the previous match ended.
    deduped: list[tuple[int, int, str]] = []
    last_end = -1
    for start, end, title in spans:
        if start >= last_end:
            deduped.append((start, end, title))
            last_end = end

    out: list[tuple[str, str]] = []
    for index, (_, end, title) in enumerate(deduped):
        body_end = deduped[index + 1][0] if index + 1 < len(deduped) else len(section)
        out.append((title, section[end:body_end]))
    return out


def _extract_quotes(signal_text: str) -> list[str]:
    """Group consecutive blockquote lines, then return quoted passages."""
    groups: list[list[str]] = []
    current: list[str] = []
    for line in signal_text.splitlines():
        if line.startswith(">"):
            current.append(line)
        else:
            if current:
                groups.append(current)
                current = []
    if current:
        groups.append(current)

    quotes: list[str] = []
    for group in groups:
        cleaned = " ".join(re.sub(r"^>\s?", "", line) for line in group).strip()
        if cleaned.startswith('"') and cleaned.endswith('"'):
            quotes.append(cleaned[1:-1].strip())
    return quotes


def _entity_from_signal(signal_text: str) -> str:
    """Best-effort speaker identity for one signal.

    Breadth counts distinct ``(source_id, entity)`` pairs and every Syften record shares one
    ``source_id``, so the entity is what separates two practitioners from one. A parser that
    read the quotes but always returned the same fallback would still report breadth 1 — the
    same practical failure as reading nothing — hence the link-label fallback below.
    """
    # Spans of every markdown link LABEL — "[`docdavkitty` — \"...\"](url)" is an attribution.
    label_spans = [(m.start(1), m.end(1)) for m in _LINK_RE.finditer(signal_text)]

    for m in _BACKTICK_NAME_RE.finditer(signal_text):
        candidate = m.group(1).strip()
        # Avoid multi-word phrases that are not handles.
        if " " in candidate or candidate.startswith("http"):
            continue
        # A backticked token is only an author when it sits in an ATTRIBUTION POSITION. The
        # digest also backticks house vocabulary (`direct`, `competitor`) and code identifiers
        # (`AccessDeniedException`); accepting those invents speakers and inflates breadth — a
        # worse failure than under-counting, because breadth sets the confidence band.
        if _FILENAME_RE.search(candidate):
            continue
        trailing = signal_text[m.end() : m.end() + 60]
        in_link_label = any(start <= m.start() and m.end() <= end for start, end in label_spans)
        if in_link_label or _ATTRIBUTION_RE.search(trailing) or _BYLINE_DATE_RE.search(trailing):
            return candidate
    # Later digests cite the speaker as a markdown link ("[r/thegraph](...)") rather than a
    # backticked handle. A subreddit or host identifies a speaker; an in-repo filename does not.
    for m in _LINK_RE.finditer(signal_text):
        label = m.group(1).strip().strip("`")
        if not label or " " in label or _FILENAME_RE.search(label):
            continue
        if label.startswith("r/") or "." in label:
            return label
    for m in _LINK_RE.finditer(signal_text):
        url = m.group(2)
        # A link whose PATH is a document is a citation of an artifact, not a speaker.
        if _FILENAME_RE.search(url.split("?")[0].split("#")[0]):
            continue
        host = re.sub(r"^https?://(?:www\.)?([^/]+).*$", r"\1", url).strip()
        if host:
            return host
    # Unidentifiable speakers deliberately collapse onto one shared entity: an anonymous
    # passage is evidence, but it is not an INDEPENDENT source, and breadth counts sources.
    return "Syften organic corpus"


def _first_url(signal_text: str) -> str | None:
    m = _LINK_RE.search(signal_text)
    return m.group(2) if m else None


def parse_syften_digest(path: Path) -> list[ev.EvidenceRecord]:
    """Parse blockquote passages from a Syften digest markdown file.

    Each blockquote becomes its own record. They share the signal's URL, entity, and date;
    distinctness for breadth is computed on ``(source_id, entity)``, so multiple quotes from
    one author count as one source.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []

    # A digest may carry more than one findings section (an issue that was amended in place
    # keeps both). Concatenating them is what makes an addendum's passages reachable.
    sections = [m.group(1) for m in _SIGNALS_SECTION_RE.finditer(text)]
    if not sections:
        return []

    section = "\n".join(sections)
    digest_date = _extract_date(text[:500]) or _extract_date(path.name) or "unknown"

    records: list[ev.EvidenceRecord] = []
    for title, body in _split_signals(section):
        signal_text = f"{title}\n{body}"
        quotes = _extract_quotes(signal_text)
        if not quotes:
            continue
        entity = _entity_from_signal(signal_text)
        url = _first_url(signal_text)
        claim_id = claim_for_text(title, body)
        for quote in quotes:
            records.append(
                ev.EvidenceRecord(
                    claim_id=claim_id,
                    verbatim=quote,
                    # A signal that cites no external link still has provenance: the digest
                    # itself. An empty url fails the store's own _coerce guard, so the record
                    # would be written, silently dropped on load, and re-imported every run.
                    url=url or _file_url(path),
                    date=digest_date,
                    entity=entity,
                    speaker=CUSTOMER_VOICE,
                    source_id=SOURCE_SYFTEN,
                    verified=True,
                    note=f"Syften digest signal: {title}. Quote taken from the curated digest.",
                    tags=["syften-digest", re.sub(r"[^\w\-]+", "-", title.lower()).strip("-")],
                )
            )
    return records


# ---------------------------------------------------------------------------
# Discovery + dedup
# ---------------------------------------------------------------------------


def find_linkedin_files(content_root: Path, profile: str) -> list[Path]:
    """All linkedin-reply markdown files for *profile*."""
    prof = _safe_segment(profile, "profile")
    croot = content_root / prof
    files: list[Path] = []
    files.extend(sorted((croot / "linkedin").glob("linkedin-reply-*.md")))
    files.extend(sorted((croot / "accounts").glob("*/linkedin-reply-*.md")))
    return files


def find_syften_digests(content_root: Path, profile: str) -> list[Path]:
    """Every ``syften-digest-*.md`` for *profile*, oldest first.

    Each digest holds the passages read in ITS window, so reading only the newest discards
    every earlier window — the same asymmetry that already made LinkedIn drafts read in full.
    Re-import is safe: ``_dedup_key`` drops passages the store already holds.
    """
    prof = _safe_segment(profile, "profile")
    return sorted((content_root / prof / "market-signals").glob("syften-digest-*.md"))


def latest_syften_digest(content_root: Path, profile: str) -> Path | None:
    """Newest ``syften-digest-*.md`` for *profile*, or None."""
    files = find_syften_digests(content_root, profile)
    return files[-1] if files else None


def collect_records(content_root: Path, profile: str) -> dict[str, list[ev.EvidenceRecord]]:
    """Parse all capture sources for *profile*."""
    linkedin = []
    for path in find_linkedin_files(content_root, profile):
        linkedin.extend(parse_linkedin(path))

    syften = []
    for digest in find_syften_digests(content_root, profile):
        syften.extend(parse_syften_digest(digest))

    return {"linkedin": linkedin, "syften": syften}


#: A local artifact's URL is a ``file://`` absolute path; only the content-root-relative part
#: is stable identity. The repo moving (gtm-engine -> gtm-engine) changed every absolute path and
#: so re-imported all four LinkedIn passages under new keys — silent duplication on every run.
_FILE_URL_ROOT_RE = re.compile(r"^file://.*?/content/", re.IGNORECASE)


def _stable_url(url: str) -> str:
    """Reduce a local ``file://`` URL to its content-root-relative path."""
    return _FILE_URL_ROOT_RE.sub("content/", url or "")


def _dedup_key(record: ev.EvidenceRecord) -> tuple[str, str, str]:
    """Key for deduplication. URL is the best anchor; fall back to entity + verbatim snippet."""
    url = _stable_url(record.url)
    verbatim_snip = record.verbatim[:120].strip()
    return (record.source_id, url, verbatim_snip)


def new_records(
    parsed: list[ev.EvidenceRecord], existing: list[ev.EvidenceRecord]
) -> list[ev.EvidenceRecord]:
    """Return *parsed* records that are not already in *existing*."""
    seen = {_dedup_key(r) for r in existing}
    out = []
    for r in parsed:
        key = _dedup_key(r)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.voc.capture",
        description="Capture LinkedIn + Syften customer-voice passages into the evidence store.",
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument("--write", action="store_true", help="Append; default is a dry run.")
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(argv)

    cfg = PathConfig.from_env(repo_root=args.repo_root)
    store = ev.store_path(cfg.content_root, args.profile)
    existing = ev.load(store)

    captured = collect_records(cfg.content_root, args.profile)
    all_parsed = captured["linkedin"] + captured["syften"]
    fresh = new_records(all_parsed, existing)

    written = ev.append(store, fresh) if args.write else 0

    by_claim: dict[str, int] = {}
    for r in fresh:
        by_claim[r.claim_id] = by_claim.get(r.claim_id, 0) + 1

    print(
        json.dumps(
            {
                "kind": "voc-capture-import",
                "store": str(store),
                "linkedin": len(captured["linkedin"]),
                "syften": len(captured["syften"]),
                "new": len(fresh),
                "written": written,
                "dry_run": not args.write,
                "by_claim": dict(sorted(by_claim.items())),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
