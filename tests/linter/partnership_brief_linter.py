#!/usr/bin/env python3
"""Partnership-brief linter — the deterministic half of the `product-partnership` quality gate.

Every rule here exists because a real brief shipped the mistake. The classes of error that
reached a partner-facing draft, in order of how badly each would have landed:

  1. standards mis-attribution  — "Verifiable W3C DID (did:tdw)" borrowed W3C's authority for
     a DIF specification. A reviewer who knows the specs reads this as proof the author doesn't.
  2. unsourced statistic        — a "~65% of agent failures" figure that traced back to a vendor
     asserting it with no study behind it.
  3. absolute claim             — "None of them publish a provenance model" was simply false; one
     competitor has a blog post titled exactly that.
  4. framework enumeration      — "ASI06 prescribes five defence layers" invented a taxonomy. The
     real document lists nine differently-named guidelines.
  5. internal voice             — process narration ("we tried to source it", "so this does not
     read as charity") that belongs in a working note, not a partner's inbox.
  6. untagged capability        — an integration row with no Shipped / Confirm-in-V1 / Roadmap tag.
  7. dead or unattributable link — a cited page whose own domain root 404s.

ERROR blocks delivery (CLI exits 1). WARN is advisory. Any rule can be suppressed on the
offending line with a trailing ``<!-- lint-ok: <rule-id> -->`` — deliberately visible, so
suppression is a decision someone made rather than a default.

Stdlib-only, no network by default. ``--check-links`` opts into HTTP HEAD verification.

  python -m tests.linter.partnership_brief_linter brief.md
  python -m tests.linter.partnership_brief_linter brief.md --check-links
  python -m tests.linter.partnership_brief_linter brief.html --mode html
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

# ── standards registry — artifact → the body that actually owns it ────────────
# The recurring failure is borrowing a *better-known* body's authority for a spec it does not
# own. W3C standardises the DID data model; it does not standardise any DID *method*.
STANDARDS_OWNERS: dict[str, str] = {
    "did:tdw": "DIF",
    "did:peer": "DIF",
    "did:webplus": "DIF",
    "didcomm": "DIF",
    "oid4vci": "OpenID Foundation",
    "oid4vp": "OpenID Foundation",
    "openid4vci": "OpenID Foundation",
    "openid4vp": "OpenID Foundation",
    "sd-jwt": "IETF",
    "verifiable credential": "W3C",
    "did core": "W3C",
    "decentralized identifier": "W3C",
    "model context protocol": "Anthropic",
}
BODIES: tuple[str, ...] = (
    "W3C",
    "DIF",
    "OpenID Foundation",
    "IETF",
    "OASIS",
    "ISO",
    "NIST",
    "OWASP",
    "Linux Foundation",
    "Decentralized Identity Foundation",
)
_BODY_ALIAS = {"Decentralized Identity Foundation": "DIF"}

# A body legitimately appears next to an artifact it does not own in these contexts.
_ATTRIBUTION_SAFE = re.compile(
    r"(registry|registries|registered|not (?:a|itself a)|rather than|"
    r"not endorsed|conforming to|conforms to|data model)",
    re.IGNORECASE,
)

# ── claim patterns ────────────────────────────────────────────────────────────
# Deliberately narrow. "never"/"always" are ordinary prose ("never on the hot path") and firing
# on them buries the real finding. Only forms that assert a fact about a *set of other parties*
# — the ones that actually get falsified — are caught.
ABSOLUTE_RE = re.compile(
    r"(?<![\w-])(none of (?:them|these|the\s+\w+)|nobody|no one|no other|"
    r"the only \w+ (?:that|who|which)|only vendor|unique(?:ly)?|"
    r"first (?:commercial|to|in the|vendor|company))(?![\w-])",
    re.IGNORECASE,
)
# An absolute is acceptable when its scope is stated — "in this set", "publicly", "that we
# found". Verified-and-bounded is the goal, not silence.
SCOPE_QUALIFIER_RE = re.compile(
    r"(in this set|in the set|publicly|published|that we (?:found|could find)|"
    r"we found no|as of|at the time of|among the|of the vendors|reviewed)",
    re.IGNORECASE,
)
STAT_RE = re.compile(
    r"(?<![\w.])(?:~|approximately\s*|roughly\s*|over\s*|under\s*)?"
    r"(\d[\d,]*\.?\d*)\s*(%|percent|x\b|×|k\b|m\b|bn\b|billion|million|"
    r"basis points)|(?:\$\s?\d[\d,]*\.?\d*\s*[kmb]?)",
    re.IGNORECASE,
)
ENUM_FRAMEWORK_RE = re.compile(
    r"\b(?:prescribes|defines|lists|specifies|has|comprises|names)\s+"
    r"(?:exactly\s+)?(two|three|four|five|six|seven|eight|nine|ten|\d+)\s+"
    # an adjective may sit between the count and the noun — "five *defence* layers" is the
    # exact phrasing that shipped, and an immediate-noun match misses it
    r"(?:[a-z-]+\s+){0,2}"
    r"(layers?|pillars?|controls?|steps?|stages?|principles?|guidelines?|"
    r"requirements?|categories|domains?|tiers?|phases?)",
    re.IGNORECASE,
)
CITATION_RE = re.compile(r"\]\(https?://|<https?://|https?://\S")
STATUS_TAG_RE = re.compile(
    r"(shipped|confirm in v1|roadmap|available now|coming soon|new .*code|preview|ga\b)",
    re.IGNORECASE,
)

# ── internal-voice tells ──────────────────────────────────────────────────────
# Process narration: true of how the document was made, irrelevant to the reader.
INTERNAL_VOICE: tuple[str, ...] = (
    "we tried to source",
    "does not read as charity",
    "naive version",
    "survives contact",
    "stated rather than glossed",
    "worth stating plainly so",
    "as noted earlier in this brief",
    "internal note",
    "internal only",
    "do not share",
    "for internal use",
    "todo",
    "tk ",
    "placeholder",
    "lorem ipsum",
)
# Anything that betrays the working environment rather than the argument.
LEAK_RE = re.compile(
    r"(profiles/[a-z0-9_-]+/|content/[a-z0-9_-]+/accounts/|plugin/skills/|"
    r"gtm_core\.|/Users/|~/\.claude)",
    re.IGNORECASE,
)
# A bare version string is ambiguous: a competitor's published version is a legitimate fact,
# an unreleased version of *our own* product is a leak. Advisory, so a human decides.
VERSION_RE = re.compile(r"(?<![\w.])v\d+\.\d+\.\d+(?![\w.])")
PLACEHOLDER_HREF_RE = re.compile(r"\]\(#\)|href=[\"']#[\"']")

# ── required structure (mirrors references/brief-structure.md) ────────────────
REQUIRED_SECTIONS: tuple[tuple[str, str], ...] = (
    ("executive summary", "the 4-6 sentence thesis"),
    ("opportunity", "market case: buyers, use cases, defensibility, commercial consequence"),
    ("what each side brings", "two-sided value — a one-sided list invalidates the brief"),
    ("competitive landscape", "where the category is and what is unclaimed"),
    ("why now", "dated external urgency"),
    ("commercial shape", "staged, reversible, no exclusivity"),
    ("risks", "our own maturity risk first"),
    ("sources", "public URLs only"),
)
# Sub-blocks the opportunity section must answer. A market section that asserts a market
# without naming who buys it is the most common weak section in a partnership brief.
OPPORTUNITY_BLOCKS: tuple[tuple[str, str], ...] = (
    ("buyer", "who buys this, and at what moment"),
    ("defensib|moat|compound", "why the position holds rather than being copied"),
    ("use case|required, not preferred", "where it is required rather than preferred"),
)
JARGON: tuple[str, ...] = (
    "did",
    "verifiable credential",
    "verifiable presentation",
    "hash chain",
    "hash-chained",
    "mcp",
    "opa",
    "rego",
    "bitemporal",
    "attestation",
    "zero-knowledge",
    "didcomm",
    "otel",
    "opentelemetry",
)


@dataclass(frozen=True)
class Finding:
    level: str  # "ERROR" | "WARN"
    rule: str
    line: int
    message: str

    def __str__(self) -> str:  # pragma: no cover - formatting only
        return f"{self.level:5}  L{self.line:<4} [{self.rule}] {self.message}"


def _suppressed(line: str, rule: str) -> bool:
    return f"lint-ok: {rule}" in line


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n", text) if s.strip()]


def _strip_code(md: str) -> str:
    """Fenced code and inline code are not prose — claims inside them are not claims."""
    md = re.sub(r"```.*?```", "", md, flags=re.DOTALL)
    return re.sub(r"`[^`]*`", "", md)


def _section_spans(md: str) -> list[tuple[int, int, str, str]]:
    """(start_line, end_line, heading, body) per ``##``+ section — the unit an editor actually
    reasons about. A statistic is sourced if its *section* carries the citation; demanding a
    link on every line would punish tables and force citation spam."""
    lines = md.splitlines()
    marks = [i for i, line in enumerate(lines) if re.match(r"^#{1,6}\s", line)]
    spans = []
    for n, start in enumerate(marks):
        end = marks[n + 1] if n + 1 < len(marks) else len(lines)
        spans.append(
            (start + 1, end, lines[start].strip("# ").strip(), "\n".join(lines[start:end]))
        )
    return spans


def lint_markdown(md: str) -> list[Finding]:
    out: list[Finding] = []
    lines = md.splitlines()
    prose = _strip_code(md)

    spans = _section_spans(md)

    def _context(lineno: int) -> tuple[str, str]:
        for s, e, head, body in spans:
            if s <= lineno <= e:
                return head.lower(), body
        return "", md

    for i, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("```"):
            continue
        low = line.lower()
        head, section = _context(i)
        # A Sources section is citations by definition — statistics quoted there are the
        # citation, not an unsourced claim.
        in_sources = "source" in head
        section_cited = bool(CITATION_RE.search(section))

        # 1 — standards attribution. Only fires when a body directly *modifies* the artifact
        # (appears just before it). "did:tdw … registered in the W3C registry" is correct
        # and must not fire; "Verifiable W3C DID (did:tdw)" must.
        if not _suppressed(raw, "standards-attribution"):
            for artifact, owner in STANDARDS_OWNERS.items():
                for am in re.finditer(re.escape(artifact), low):
                    window = line[max(0, am.start() - 45) : am.start()]
                    for body in BODIES:
                        if body.lower() not in window.lower():
                            continue
                        if _BODY_ALIAS.get(body, body) == owner:
                            continue
                        if _ATTRIBUTION_SAFE.search(line):
                            continue
                        out.append(
                            Finding(
                                "ERROR",
                                "standards-attribution",
                                i,
                                f"{body!r} modifies {artifact!r}, which is owned by {owner}. "
                                f"Do not borrow another body's authority.",
                            )
                        )
                        break
                    else:
                        continue
                    break

        # 2 — absolute claims: citation in section, or an explicit scope qualifier
        m = ABSOLUTE_RE.search(line)
        if (
            m
            and not section_cited
            and not SCOPE_QUALIFIER_RE.search(line)
            and not _suppressed(raw, "absolute-claim")
        ):
            out.append(
                Finding(
                    "ERROR",
                    "absolute-claim",
                    i,
                    f"absolute claim {m.group(0)!r} — no citation in this section and no scope "
                    f"qualifier. These are the claims that get falsified; bound it or cite it.",
                )
            )

        # 3 — statistics need a source somewhere in their section
        if not section_cited and not in_sources and not _suppressed(raw, "unsourced-statistic"):
            sm = STAT_RE.search(line)
            if sm:
                out.append(
                    Finding(
                        "ERROR",
                        "unsourced-statistic",
                        i,
                        f"statistic {sm.group(0)!r} — no source anywhere in this section. "
                        f"Every number a partner could repeat needs a primary source.",
                    )
                )

        # 4 — enumerated claims about someone else's framework
        em = ENUM_FRAMEWORK_RE.search(line)
        if em and not _suppressed(raw, "framework-enumeration"):
            out.append(
                Finding(
                    "WARN",
                    "framework-enumeration",
                    i,
                    f"enumerated claim {em.group(0)!r} about a third-party framework — verify "
                    f"the count AND the element names against the framework's own text.",
                )
            )

        # 5 — internal voice and environment leakage
        for tell in INTERNAL_VOICE:
            if tell in low and not _suppressed(raw, "internal-voice"):
                out.append(Finding("ERROR", "internal-voice", i, f"internal-voice tell {tell!r}"))
                break
        lm = LEAK_RE.search(line)
        if lm and not _suppressed(raw, "internal-leak"):
            out.append(
                Finding(
                    "ERROR",
                    "internal-leak",
                    i,
                    f"internal path {lm.group(0)!r} in a partner-facing document",
                )
            )
        vm = VERSION_RE.search(line)
        if vm and not _suppressed(raw, "version-string"):
            out.append(
                Finding(
                    "WARN",
                    "version-string",
                    i,
                    f"version {vm.group(0)!r} — fine if it is a published third-party version, "
                    f"a leak if it is our own unreleased build",
                )
            )

        # 6 — placeholder links
        if PLACEHOLDER_HREF_RE.search(line) and not _suppressed(raw, "placeholder-link"):
            out.append(Finding("ERROR", "placeholder-link", i, "placeholder '#' href"))

        # 7 — integration-table rows must carry a status tag
        if line.startswith("|") and re.match(r"^\|\s*\d+\s*\|", line):
            if not STATUS_TAG_RE.search(line) and not _suppressed(raw, "untagged-capability"):
                out.append(
                    Finding(
                        "ERROR",
                        "untagged-capability",
                        i,
                        "integration row has no Shipped / Confirm-in-V1 / Roadmap tag",
                    )
                )

    # ── document-level structure ──
    low_all = prose.lower()
    for needle, why in REQUIRED_SECTIONS:
        if not re.search(rf"^#+.*{needle}", low_all, re.MULTILINE):
            out.append(Finding("ERROR", "missing-section", 0, f"no '{needle}' section — {why}"))

    # Span to the next SAME-level heading, so `###` sub-blocks inside the section still count.
    opp = re.search(
        r"^(#+)[^\n]*opportunity(.*?)(?=^\1\s|\Z)", prose, re.DOTALL | re.MULTILINE | re.I
    )
    if opp:
        body = opp.group(2).lower()
        for pattern, why in OPPORTUNITY_BLOCKS:
            if not re.search(pattern, body):
                out.append(
                    Finding(
                        "WARN",
                        "thin-opportunity",
                        0,
                        f"opportunity section does not address: {why}",
                    )
                )

    if not re.search(r"^#+.*(glossary|plain-language|key terms)", low_all, re.MULTILINE):
        hits = {t for t in JARGON if re.search(rf"(?<![\w-]){re.escape(t)}(?![\w-])", low_all)}
        if len(hits) >= 4:
            out.append(
                Finding(
                    "WARN",
                    "missing-glossary",
                    0,
                    f"{len(hits)} technical terms used with no plain-language key — half the "
                    f"audience is a CEO ({', '.join(sorted(hits)[:6])}…)",
                )
            )

    # Diagram claims must be traceable to a status-tagged row. A diagram asserts more
    # confidently than prose and gets screenshotted out of context.
    if "```mermaid" in md and not re.search(r"accTitle:", md):
        out.append(Finding("WARN", "diagram-a11y", 0, "mermaid diagram has no accTitle/accDescr"))
    if "```mermaid" in md and not re.search(r"shape a|proposed|shipped|confirm", low_all):
        out.append(
            Finding(
                "WARN",
                "diagram-status",
                0,
                "diagram present but no status qualifier nearby — label which parts are shipped",
            )
        )
    return out


def lint_html(html: str) -> list[Finding]:
    """Structural a11y checks readable from the rendered file. Colour-contrast is measured in
    the browser (see references/quality-gates.md) — it cannot be computed from markup alone."""
    out: list[Finding] = []
    # Script and style bodies are not markup. The house companion template carries its whole
    # source inside a <script type="text/markdown"> block and builds the DOM at runtime, so
    # linting the raw file would measure the loader, not the document.
    stripped = re.sub(r"<script\b.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    stripped = re.sub(r"<style\b.*?</style>", "", stripped, flags=re.DOTALL | re.IGNORECASE)
    # Detect client-rendering by its marker, not by absence of headings — a fragment legitimately
    # has no headings and must still be checked.
    client_rendered = bool(
        re.search(r'type=["\']text/markdown["\']', html, re.IGNORECASE)
        or re.search(r'<div\s+id=["\']content["\']\s*>\s*</div>', html, re.IGNORECASE)
    )
    if client_rendered:
        out.append(
            Finding(
                "WARN",
                "client-rendered",
                0,
                "this document builds its DOM at runtime — static markup shows the loader, not "
                "the document. Run the structural and contrast checks in the browser "
                "(quality-gates.md §Gate 3).",
            )
        )
        return out
    html = stripped
    heads = [int(m.group(1)) for m in re.finditer(r"<h([1-6])\b", html, re.IGNORECASE)]
    prev = 1
    for n in heads:
        if n > prev + 1:
            out.append(Finding("WARN", "heading-skip", 0, f"heading level jumps h{prev}→h{n}"))
        prev = n
    n_th = len(re.findall(r"<th\b", html, re.IGNORECASE))
    n_scope = len(re.findall(r"<th[^>]*\bscope=", html, re.IGNORECASE))
    if n_th and n_scope < n_th:
        out.append(
            Finding("WARN", "th-scope", 0, f"{n_th - n_scope}/{n_th} <th> lack a scope attribute")
        )
    for m in re.finditer(r"<img\b(?![^>]*\balt=)[^>]*>", html, re.IGNORECASE):
        out.append(Finding("ERROR", "img-alt", 0, f"<img> without alt: {m.group(0)[:60]}"))
    if PLACEHOLDER_HREF_RE.search(html):
        out.append(Finding("ERROR", "placeholder-link", 0, "placeholder '#' href in rendered HTML"))
    if LEAK_RE.search(html):
        out.append(
            Finding("ERROR", "internal-leak", 0, "internal path/version string in rendered HTML")
        )
    return out


def check_links(md: str) -> list[Finding]:
    """Opt-in network pass. A page that loads is not automatically an attributable source —
    a cited spec whose own domain root 404s has no owner, and cannot back a claim."""
    import urllib.error
    import urllib.request
    from urllib.parse import urlsplit

    out: list[Finding] = []
    urls = sorted(set(re.findall(r"https?://[^\s)\]\"'>]+", md)))
    roots_checked: dict[str, bool] = {}

    def _ok(u: str) -> bool:
        if urlsplit(u).scheme not in ("http", "https"):
            # Belt-and-braces: the extraction regex above already anchors to http(s), so
            # this never fires today, but it keeps urlopen from ever seeing a file:// URL
            # if that regex is ever loosened.
            return False
        req = urllib.request.Request(u, method="HEAD", headers={"User-Agent": "brief-linter"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected -- scheme is validated to http(s) above, so file:// read is unreachable
                return r.status < 400
        except urllib.error.HTTPError as e:
            return e.code < 400
        except Exception:
            return False

    for u in urls:
        if not _ok(u):
            out.append(Finding("ERROR", "dead-link", 0, f"unreachable: {u}"))
            continue
        parts = urlsplit(u)
        root = f"{parts.scheme}://{parts.netloc}/"
        if root not in roots_checked:
            roots_checked[root] = _ok(root)
        if not roots_checked[root]:
            out.append(
                Finding(
                    "WARN",
                    "unattributable-source",
                    0,
                    f"page loads but its domain root 404s — no identifiable owner: {u}",
                )
            )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="partnership_brief_linter")
    ap.add_argument("path")
    ap.add_argument("--mode", choices=("md", "html", "auto"), default="auto")
    ap.add_argument(
        "--check-links", action="store_true", help="verify every external URL (network)"
    )
    ap.add_argument("--strict", action="store_true", help="treat WARN as blocking")
    args = ap.parse_args(argv)

    text = Path(args.path).read_text(encoding="utf-8")
    mode = args.mode
    if mode == "auto":
        mode = "html" if args.path.endswith((".html", ".htm")) else "md"

    findings = lint_html(text) if mode == "html" else lint_markdown(text)
    if args.check_links and mode == "md":
        findings += check_links(text)

    for f in sorted(findings, key=lambda x: (x.level != "ERROR", x.line)):
        print(f)

    errors = [f for f in findings if f.level == "ERROR"]
    warns = [f for f in findings if f.level == "WARN"]
    print(f"\n{len(errors)} error(s), {len(warns)} warning(s)")
    return 1 if errors or (args.strict and warns) else 0


if __name__ == "__main__":
    raise SystemExit(main())
