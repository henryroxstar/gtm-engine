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

_SIGNALS_SECTION_RE = re.compile(
    r"##\s*Signals worth acting on\s*\n(.*?)"
    r"(?:^##\s|^##\s*⚠\s*Name-collision|\Z)",
    re.DOTALL | re.IGNORECASE | re.MULTILINE,
)
_SIGNAL_HEADER_RE = re.compile(
    r"###\s*Signal\s+\d+\s*[—\-]\s*[^—\-\n]+\s*[—\-]\s*(.+?)$", re.MULTILINE
)


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
    """Best-effort author name from backticks or the first short token before a link."""
    for m in _BACKTICK_NAME_RE.finditer(signal_text):
        candidate = m.group(1).strip()
        # Avoid multi-word phrases that are not handles.
        if " " not in candidate and not candidate.startswith("http"):
            return candidate
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

    section_match = _SIGNALS_SECTION_RE.search(text)
    if not section_match:
        return []

    section = section_match.group(1)
    digest_date = _extract_date(text[:500]) or _extract_date(path.name) or "unknown"

    records: list[ev.EvidenceRecord] = []
    # Split the section on signal headers, keeping the header text with each chunk.
    parts = _SIGNAL_HEADER_RE.split(section)
    # parts[0] is preamble before the first signal; subsequent pairs are (header, body).
    for i in range(1, len(parts), 2):
        title = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
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
                    url=url or "",
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


def latest_syften_digest(content_root: Path, profile: str) -> Path | None:
    """Newest ``syften-digest-*.md`` for *profile*, or None."""
    prof = _safe_segment(profile, "profile")
    files = sorted((content_root / prof / "market-signals").glob("syften-digest-*.md"))
    return files[-1] if files else None


def collect_records(content_root: Path, profile: str) -> dict[str, list[ev.EvidenceRecord]]:
    """Parse all capture sources for *profile*."""
    linkedin = []
    for path in find_linkedin_files(content_root, profile):
        linkedin.extend(parse_linkedin(path))

    syften = []
    digest = latest_syften_digest(content_root, profile)
    if digest:
        syften.extend(parse_syften_digest(digest))

    return {"linkedin": linkedin, "syften": syften}


def _dedup_key(record: ev.EvidenceRecord) -> tuple[str, str, str]:
    """Key for deduplication. URL is the best anchor; fall back to entity + verbatim snippet."""
    url = record.url or ""
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
