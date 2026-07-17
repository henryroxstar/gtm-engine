#!/usr/bin/env python3
"""Outreach pack linter — deterministic gate for 1:1 cold-email packs (Tier-A manual
sends and sequencer step-1 bodies). Stdlib-only, CLI + importable, mirroring
content_linter conventions (ERROR blocks / WARN advises, --selftest, --ban-file).

Born from the 2026-07-16 Tier-A pack review: the drafting spec already mandated
hedged gaps, gift CTAs, and per-seat pain — and the pack violated all three while
passing the word-level lint. These checks are the mechanical subset with teeth:

  Pack-wide
    - Rules-Version header present and == RULES_VERSION (staleness gate: packs
      drafted under older rules fail closed and must be regenerated/re-reviewed)
    - template-share ceiling: no normalized 6-gram may appear in > MAX_NGRAM_EMAILS
      distinct emails (hedge-cue phrases whitelisted)
    - same-company divergence: recipients sharing a To: domain must differ in
      subject and keep pairwise content-word Jaccard <= JACCARD_MAX
    - no duplicate To: addresses
  Per-email
    - subject 1-4 words, lowercase, no placeholders/merge tags
    - greeting `Hi <First>,` matching the block header's first name
    - body ends with the bare sign-off line (default "Henry")
    - word count (hard 50-110, advisory 60-95)
    - no links, no em-dash, no spintax braces, no [INSERT-style placeholders
    - banned fluff words (built-in + optional --ban-file voice-bans.txt)
    - no antithesis constructions ("not X, it's Y")
    - banned stems (from --stem-file): a pack's signature mail-merge sentences may not
      recur; empty unless the operator supplies a stem file
    - named case studies (from --case-study-file): proof is company-TYPE, never the
      case-study company name (voice.md "Common mistakes"; an unknown logo confuses more
      than it credits, and Early-Access claims invite verification they may not survive;
      empty unless the operator supplies a case-study file). Exception lives
      in voice.md's FORMAL register (SG regulated, logo naming with permission) —
      that register is not linter-encoded; override manually for those sends. Note:
      the subject-lowercase rule below likewise assumes the informal/standard
      registers; formal-register SG sends (proper-cased subjects) need manual review.
    - hedge cue required (the "tell me if you've got this covered" family)
    - CTA: ends on a question, never "teardown", never a time-ask
    - specificity anchors: >= MIN_ANCHORS digit-bearing or mid-sentence proper
      tokens (the mechanical proxy for "used real dossier facts")

Pack format (tier-a-manual-pack-*.md):
    Rules-Version: 2026-07-16          <- anywhere in the pack header
    ### 1. First Last · Title, Company
    **To:** a@b.com
    **Subject:** two words

    Hi First,
    ...body...
    Henry
    ---
"""

from __future__ import annotations

import argparse
import csv as _csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path

RULES_VERSION = "2026-07-16"

WORDS_HARD_MIN, WORDS_HARD_MAX = 50, 110
WORDS_SOFT_MIN, WORDS_SOFT_MAX = 60, 95
SUBJECT_MAX_WORDS = 4
MAX_NGRAM_EMAILS = 3  # a 6-gram may appear in at most this many emails
NGRAM_N = 6
JACCARD_MAX = 0.45
MIN_ANCHORS = 2
SOFT_ANCHORS = 4

EM_DASH = "—"
URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
SPINTAX_RE = re.compile(r"\{[^{}]*\|[^{}]*\}")
PLACEHOLDER_RE = re.compile(r"\[(INSERT|TBD|TODO|PLACEHOLDER|LINK|ONE LINK)", re.IGNORECASE)
MERGE_TAG_RE = re.compile(r"\{\{[^}]+\}\}")
ANTITHESIS_RES = (
    re.compile(r"\bnot just\b.{0,40}?\bbut\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bisn'?t\b.{0,40}?\bit'?s\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bit'?s not\b.{0,40}?\bit'?s\b", re.IGNORECASE | re.DOTALL),
)

BANNED_WORDS = (
    "excited to",
    "thrilled to",
    "reach out",
    "touch base",
    "synergy",
    "circle back",
    "i hope this finds you well",
    "i came across your profile",
    "i'd love to connect",
    "delve",
    "seamless",
    "robust",
    "elevate",
    "pivotal",
    "foster",
)
# "leverage" only as a verb-ish use; crude but effective: flag "leverage " + noun-phrase
LEVERAGE_RE = re.compile(r"\bleverag(e|ing|es|ed)\b", re.IGNORECASE)

# Mail-merge "stems" (a pack's signature sentences — recurrence means the draft fell back
# into the skeleton) and case-study company names are TENANT-SPECIFIC. They are supplied at
# call time via --stem-file / --case-study-file (one entry per line, `#` comments allowed;
# see _load_bans), so the shipped defaults are EMPTY and the linter enforces neither until an
# operator provides their own — mirroring content_linter's tenant denylist. A case study must
# be cited by company TYPE ("a regulated-FI compliance team"), never the logo (an unknown name
# confuses more than it credits) — the named-case-study rule flags any file-listed name.
DEFAULT_BANNED_STEMS: tuple[str, ...] = ()
DEFAULT_CASE_STUDY_NAMES: tuple[str, ...] = ()

HEDGE_CUES = (
    "tell me if you've got this covered",
    "tell me if this is already handled",
    "if you've already solved this",
    "my hunch",
    "my read",
    "my bet",
    "correct me if",
    "am i wrong",
    "you may well have this covered",
)

TIME_ASK_TOKENS = (
    "15 minutes",
    "20 minutes",
    "quick call",
    "hop on a call",
    "jump on a call",
    "calendly",
    "calendar link",
    "grab time",
    "find time",
    "book a",
    "schedule a",
    "meet next week",
    "chat this week",
    "minutes this week",
    "a call this",
)

# Tokens that never count as specificity anchors even when capitalized mid-sentence.
ANCHOR_STOPLIST = {
    "Hi",
    "Henry",
    "I",
    "AI",
    "IAM",
    "MCP",
    "PHI",
    "HIPAA",
    "API",
    "APIs",
    "IT",
    "OK",
    "CTA",
    "CEO",
    "CTO",
    "CISO",
    "CFO",
    "CMO",
    "GC",
    "SVP",
    "EVP",
    "VP",
    "The",
    "A",
    "An",
    "And",
    "But",
    "So",
    "My",
    "Your",
    "Their",
    "That",
    "This",
    "Want",
    "Worth",
    "When",
    "Once",
    "With",
    "For",
    "If",
    "It",
    "No",
    "Not",
}

STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "but",
    "so",
    "of",
    "to",
    "in",
    "on",
    "for",
    "with",
    "at",
    "by",
    "from",
    "as",
    "is",
    "are",
    "was",
    "be",
    "been",
    "it",
    "its",
    "that",
    "this",
    "those",
    "these",
    "your",
    "you",
    "their",
    "they",
    "them",
    "one",
    "no",
    "not",
    "can",
    "cant",
    "cannot",
    "do",
    "does",
    "did",
    "i",
    "my",
    "me",
    "we",
    "our",
    "us",
    "he",
    "she",
    "his",
    "her",
    "than",
    "then",
    "when",
    "once",
    "into",
    "over",
    "under",
    "out",
    "up",
    "down",
    "what",
    "which",
    "who",
    "whose",
    "how",
    "why",
    "where",
    "any",
    "all",
    "each",
    "every",
    "some",
    "most",
    "more",
    "less",
    "own",
    "same",
    "other",
    "hi",
    "henry",
    "want",
    "worth",
    "sending",
    "send",
    "plus",
    "short",
}


@dataclass
class Violation:
    level: str  # "ERROR" | "WARN"
    email: str  # recipient label or "PACK"
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.email}: {self.rule} — {self.detail}"


@dataclass
class EmailBlock:
    index: int
    header: str  # "First Last · Title, Company"
    first: str
    company: str
    to: str
    subject: str
    body: str  # plain text, ends with sign-off line

    @property
    def label(self) -> str:
        return f"#{self.index} {self.to}"


_BLOCK_RE = re.compile(
    r"^###\s+(?P<idx>\d+)\.\s+(?P<header>.+?)\s*\n"
    r"\*\*To:\*\*\s*(?P<to>\S+)\s*\n"
    r"\*\*Subject:\*\*\s*(?P<subject>.+?)\s*\n"
    r"(?P<body>.*?)(?=\n---\s*\n|\n###\s+\d+\.|\Z)",
    re.DOTALL | re.MULTILINE,
)


def parse_pack(text: str) -> tuple[str | None, list[EmailBlock]]:
    """Return (rules_version, blocks)."""
    mv = re.search(r"^Rules-Version:\s*(\S+)", text, re.MULTILINE)
    version = mv.group(1) if mv else None
    blocks: list[EmailBlock] = []
    for m in _BLOCK_RE.finditer(text):
        header = m.group("header").strip()
        first = header.split()[0] if header.split() else ""
        company = header.split(",")[-1].strip() if "," in header else ""
        blocks.append(
            EmailBlock(
                index=int(m.group("idx")),
                header=header,
                first=first,
                company=company,
                to=m.group("to").strip().lower(),
                subject=m.group("subject").strip(),
                body=m.group("body").strip(),
            )
        )
    return version, blocks


def _norm_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9$%.']+", text.lower())


def _content_words(text: str) -> set[str]:
    return {t for t in _norm_tokens(text) if t not in STOPWORDS and len(t) > 2}


def _ngrams(tokens: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def _hedge_ngram_whitelist() -> set[tuple[str, ...]]:
    wl: set[tuple[str, ...]] = set()
    for cue in HEDGE_CUES:
        toks = _norm_tokens(cue)
        if len(toks) >= NGRAM_N:
            wl |= _ngrams(toks, NGRAM_N)
    return wl


def _anchors(body: str) -> set[str]:
    """Digit-bearing tokens + mid-sentence capitalized tokens (proxy for dossier facts)."""
    found: set[str] = set()
    for tok in re.findall(r"[\w$%.']+", body):
        if any(ch.isdigit() for ch in tok):
            found.add(tok)
    for m in re.finditer(r"(?<![.!?]\s)(?<!^)(?<!\n)\b([A-Z][A-Za-z0-9'-]+)", body):
        tok = m.group(1)
        if (
            tok not in ANCHOR_STOPLIST
            and not tok.isupper()
            or (tok.isupper() and len(tok) > 4 and tok not in ANCHOR_STOPLIST)
        ):
            if tok not in ANCHOR_STOPLIST:
                found.add(tok)
    return found


def lint_email(
    b: EmailBlock,
    extra_bans: tuple[str, ...] = (),
    signoff: str = "Henry",
    case_studies: tuple[str, ...] = DEFAULT_CASE_STUDY_NAMES,
    banned_stems: tuple[str, ...] = DEFAULT_BANNED_STEMS,
) -> list[Violation]:
    v: list[Violation] = []
    low = b.body.lower()
    plain = re.sub(r"\s+", " ", b.body)

    # Subject
    subj_words = b.subject.split()
    if not (1 <= len(subj_words) <= SUBJECT_MAX_WORDS):
        v.append(
            Violation("ERROR", b.label, "subject-length", f"{len(subj_words)} words: {b.subject!r}")
        )
    if b.subject != b.subject.lower():
        v.append(Violation("ERROR", b.label, "subject-lowercase", repr(b.subject)))
    if MERGE_TAG_RE.search(b.subject) or PLACEHOLDER_RE.search(b.subject):
        v.append(Violation("ERROR", b.label, "subject-placeholder", repr(b.subject)))

    # Greeting / sign-off
    if not b.body.startswith(f"Hi {b.first},"):
        v.append(Violation("ERROR", b.label, "greeting", f"must start 'Hi {b.first},'"))
    lines = [ln.strip() for ln in b.body.splitlines() if ln.strip()]
    if not lines or lines[-1] != signoff:
        v.append(Violation("ERROR", b.label, "sign-off", f"last line must be bare {signoff!r}"))

    # Length
    wc = len(plain.split())
    if not (WORDS_HARD_MIN <= wc <= WORDS_HARD_MAX):
        v.append(
            Violation(
                "ERROR",
                b.label,
                "word-count",
                f"{wc} words (hard {WORDS_HARD_MIN}-{WORDS_HARD_MAX})",
            )
        )
    elif not (WORDS_SOFT_MIN <= wc <= WORDS_SOFT_MAX):
        v.append(
            Violation(
                "WARN",
                b.label,
                "word-count",
                f"{wc} words (target {WORDS_SOFT_MIN}-{WORDS_SOFT_MAX})",
            )
        )

    # Mechanical hygiene
    if URL_RE.search(b.body):
        v.append(Violation("ERROR", b.label, "no-links", "first touch is link-free"))
    if EM_DASH in b.body:
        v.append(Violation("ERROR", b.label, "em-dash", "em dash present"))
    if SPINTAX_RE.search(b.body):
        v.append(Violation("ERROR", b.label, "spintax", SPINTAX_RE.search(b.body).group(0)))
    if PLACEHOLDER_RE.search(b.body) or MERGE_TAG_RE.search(b.body):
        v.append(Violation("ERROR", b.label, "placeholder", "unresolved placeholder/merge tag"))

    # Voice bans
    for ban in BANNED_WORDS + tuple(x.lower() for x in extra_bans):
        if ban and re.search(r"(?<!\w)" + re.escape(ban) + r"(?!\w)", low):
            v.append(Violation("ERROR", b.label, "banned-word", ban))
    if LEVERAGE_RE.search(b.body):
        v.append(Violation("ERROR", b.label, "banned-word", "leverage (verb)"))
    for rx in ANTITHESIS_RES:
        if rx.search(plain):
            v.append(Violation("ERROR", b.label, "antithesis", rx.pattern))

    # Mail-merge stems
    for stem in banned_stems:
        if stem in low:
            v.append(Violation("ERROR", b.label, "banned-stem", stem))

    # Named case studies (proof by company type, never the logo)
    for name in case_studies:
        if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", low):
            v.append(
                Violation(
                    "ERROR",
                    b.label,
                    "named-case-study",
                    f"{name}: name the company type + outcome, not the case-study logo",
                )
            )

    # Hedge
    if not any(c in low for c in HEDGE_CUES):
        v.append(
            Violation(
                "ERROR",
                b.label,
                "hedge-missing",
                'gap must be hedged (e.g. "my hunch, tell me if you\'ve got this covered")',
            )
        )

    # CTA
    body_wo_signoff = "\n".join(lines[:-1]) if lines and lines[-1] == signoff else b.body
    sentences = re.split(r"(?<=[.!?])\s+", body_wo_signoff.strip())
    last = sentences[-1].strip() if sentences else ""
    if not last.endswith("?"):
        v.append(
            Violation(
                "ERROR",
                b.label,
                "cta-question",
                f"last sentence must be the offer question: {last!r}",
            )
        )
    if "teardown" in low:
        v.append(
            Violation(
                "ERROR", b.label, "cta-teardown", 'name the actual gift artifact, never "teardown"'
            )
        )
    for t in TIME_ASK_TOKENS:
        if t in low:
            v.append(Violation("ERROR", b.label, "time-ask", t))

    # Specificity anchors (dossier-fact proxy)
    anchors = _anchors(b.body)
    if len(anchors) < MIN_ANCHORS:
        v.append(
            Violation(
                "ERROR",
                b.label,
                "specificity",
                f"only {len(anchors)} anchor(s) {sorted(anchors)}; need >= {MIN_ANCHORS} dated/named facts",
            )
        )
    elif len(anchors) < SOFT_ANCHORS:
        v.append(
            Violation(
                "WARN", b.label, "specificity", f"{len(anchors)} anchors; aim >= {SOFT_ANCHORS}"
            )
        )

    return v


def lint_pack(
    text: str,
    extra_bans: tuple[str, ...] = (),
    signoff: str = "Henry",
    case_studies: tuple[str, ...] = DEFAULT_CASE_STUDY_NAMES,
    banned_stems: tuple[str, ...] = DEFAULT_BANNED_STEMS,
) -> list[Violation]:
    v: list[Violation] = []
    version, blocks = parse_pack(text)

    # Staleness gate
    if version is None:
        v.append(
            Violation(
                "ERROR",
                "PACK",
                "rules-version-missing",
                f"add 'Rules-Version: {RULES_VERSION}' to the pack header",
            )
        )
    elif version != RULES_VERSION:
        v.append(
            Violation(
                "ERROR",
                "PACK",
                "rules-version-stale",
                f"pack drafted under {version}, current is {RULES_VERSION} — regenerate/re-review before sending",
            )
        )

    if not blocks:
        v.append(
            Violation("ERROR", "PACK", "parse", "no email blocks found (### N. header format)")
        )
        return v

    # Per-email
    for b in blocks:
        v.extend(
            lint_email(
                b,
                extra_bans=extra_bans,
                signoff=signoff,
                case_studies=case_studies,
                banned_stems=banned_stems,
            )
        )

    # Duplicate recipients
    seen: dict[str, int] = {}
    for b in blocks:
        if b.to in seen:
            v.append(Violation("ERROR", b.label, "duplicate-to", f"also block #{seen[b.to]}"))
        seen[b.to] = b.index

    # Template-share ceiling (6-grams across emails)
    wl = _hedge_ngram_whitelist()
    ngram_emails: dict[tuple[str, ...], set[int]] = {}
    for b in blocks:
        toks = _norm_tokens(b.body)
        for g in _ngrams(toks, NGRAM_N):
            if g in wl:
                continue
            ngram_emails.setdefault(g, set()).add(b.index)
    flagged: set[tuple[str, ...]] = {
        g for g, e in ngram_emails.items() if len(e) > MAX_NGRAM_EMAILS
    }
    # Report the longest offenders only (merge overlapping grams by picking top few)
    if flagged:
        samples = sorted(flagged, key=lambda g: -len(ngram_emails[g]))[:5]
        for g in samples:
            v.append(
                Violation(
                    "ERROR",
                    "PACK",
                    "template-share",
                    f'"{" ".join(g)}" appears in {len(ngram_emails[g])} emails (max {MAX_NGRAM_EMAILS})',
                )
            )

    # Same-company divergence
    by_domain: dict[str, list[EmailBlock]] = {}
    for b in blocks:
        dom = b.to.split("@")[-1]
        by_domain.setdefault(dom, []).append(b)
    for dom, group in by_domain.items():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, c = group[i], group[j]
                if a.subject.strip().lower() == c.subject.strip().lower():
                    v.append(
                        Violation(
                            "ERROR",
                            "PACK",
                            "same-company-subject",
                            f"{a.label} and {c.label} share subject {a.subject!r}",
                        )
                    )
                wa, wc_ = _content_words(a.body), _content_words(c.body)
                if wa and wc_:
                    jac = len(wa & wc_) / len(wa | wc_)
                    if jac > JACCARD_MAX:
                        v.append(
                            Violation(
                                "ERROR",
                                "PACK",
                                "same-company-overlap",
                                f"{a.label} vs {c.label}: Jaccard {jac:.2f} > {JACCARD_MAX} — diverge angle/signal",
                            )
                        )
    return v


def lint_tracker_csv(path: Path) -> list[Violation]:
    """Staleness check for a tracker CSV: DRAFTED rows must carry the current rules_version."""
    v: list[Violation] = []
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(_csv.DictReader(f))
    if not rows:
        return [Violation("WARN", "CSV", "empty", str(path))]
    has_col = "rules_version" in rows[0]
    for i, r in enumerate(rows, start=2):
        status = (r.get("status") or "").upper()
        if status.startswith("DRAFTED"):
            rv = (r.get("rules_version") or "").strip() if has_col else ""
            if rv != RULES_VERSION:
                v.append(
                    Violation(
                        "ERROR",
                        f"csv row {i} ({r.get('email', '?')})",
                        "rules-version-stale",
                        f"drafted under {rv or 'unversioned'}; re-validate under {RULES_VERSION} before sending",
                    )
                )
    return v


def _load_bans(path: str | None) -> tuple[str, ...]:
    if not path:
        return ()
    p = Path(path)
    if not p.exists():
        return ()
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line.lower())
    return tuple(out)


_GOOD = """Rules-Version: 2026-07-16

### 1. Dana Rivera · Head of Platform, Acme Robotics
**To:** dana@acmerobotics.dev
**Subject:** agent audit trail

Hi Dana,

Shipping autonomous agents into production, right after the Series B raise, reads like a trust bet your enterprise buyers will test in review. Your 12 agents already call internal tools directly, and every run is logged. My bet on the open piece: none of those logs prove which agent took which action once one hands off to another. EU AI Act Article 12 lands that on your customers by 2027. Want a one-pager plus a short recording of per-run attribution at the tool boundary?

Henry

---
"""

# Fixture tenant lists the selftest passes explicitly (the shipped defaults are empty).
_SELFTEST_CASE_STUDIES = ("exampleco",)
_SELFTEST_STEMS = ("service account with no per-call proof",)

_BAD = """Rules-Version: 2026-07-01

### 1. Jane Doe · CEO, Acme
**To:** jane@acme.com
**Subject:** Quick Question About Your Platform Strategy

Hey Jane,

I hope this finds you well. Your agents share a service account with no per-call proof, so no one can tell which agent acted. ExampleCo hit the same wall last year. Worth me sending the teardown?

Best,
Jane's Friend

---
"""


def _selftest() -> int:
    good = lint_pack(_GOOD)
    good_errors = [x for x in good if x.level == "ERROR"]
    bad = lint_pack(_BAD, case_studies=_SELFTEST_CASE_STUDIES, banned_stems=_SELFTEST_STEMS)
    bad_rules = {x.rule for x in bad}
    expect = {
        "rules-version-stale",
        "subject-length",
        "greeting",
        "sign-off",
        "banned-word",
        "banned-stem",
        "named-case-study",
        "cta-teardown",
        "hedge-missing",
    }
    ok = not good_errors and expect.issubset(bad_rules)
    print(
        f"selftest: good-pack errors={len(good_errors)} (want 0); bad-pack rules hit={sorted(bad_rules)}"
    )
    if not ok:
        for x in good_errors:
            print("  unexpected:", x)
        print("  missing:", expect - bad_rules)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=f"Outreach pack linter (rules {RULES_VERSION})")
    ap.add_argument("pack", nargs="?", help="path to the pack .md to lint")
    ap.add_argument("--csv", help="tracker CSV to staleness-check (rules_version column)")
    ap.add_argument("--ban-file", help="profile voice-bans.txt for extra banned words")
    ap.add_argument(
        "--case-study-file",
        help="profile file of case-study company names to flag (one per line)",
    )
    ap.add_argument(
        "--stem-file",
        help="profile file of banned mail-merge stems (one per line)",
    )
    ap.add_argument("--signoff", default="Henry", help="expected bare sign-off line")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()
    if not args.pack and not args.csv:
        ap.error("pack path or --csv required")

    violations: list[Violation] = []
    if args.pack:
        violations += lint_pack(
            Path(args.pack).read_text(encoding="utf-8"),
            extra_bans=_load_bans(args.ban_file),
            signoff=args.signoff,
            case_studies=_load_bans(args.case_study_file),
            banned_stems=_load_bans(args.stem_file),
        )
    if args.csv:
        violations += lint_tracker_csv(Path(args.csv))

    errors = [x for x in violations if x.level == "ERROR"]
    for x in violations:
        print(x)
    print(
        f"\n{len(errors)} error(s), {len(violations) - len(errors)} warning(s) — rules {RULES_VERSION}"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
