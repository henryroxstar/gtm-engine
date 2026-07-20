#!/usr/bin/env python3
"""Content linter (spec §13.1, build-first) — enforces the platform playbooks so every asset
passes before review. Stdlib-only (no deps), runnable as a CLI gate and importable by
content-studio. Rules from docs/{linkedin,x,facebook,instagram}-optimization.md + spec §13.

Two advisory layers ride alongside the structural checks, both WARN-only (they never change the
exit code or block review): the prose-quality (human-voice) checks catch the AI "tells" a regex
can see (docs/prose-craft.md); ``--ban-file`` adds the active profile's knowledge/voice-bans.txt.

Asset contract (dict):
  platform:   "linkedin" | "x" | "facebook" | "instagram"
  format:     "carousel" | "text" | "infographic" | "infographic-handwritten" | "thread" | "single" | "reel"
  hook:       str   (LinkedIn first line / cover hook)
  body:       str   (LinkedIn post body / caption text / infographic narrative)
  key_points: [str] (infographic only — data points or insights)
  tone:       str   (infographic only — style note)
  tweets:     [str] (X thread/single)
  slides:     [str] (carousel slides)
  caption:    str   (Instagram caption)
  duration_s: number (Instagram reel)
  hashtags:   [str] (optional; else parsed from body/caption)

ERROR = blocks review (CLI exits 1). WARN = advisory.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass

URL_RE = re.compile(r"https?://", re.IGNORECASE)
HASHTAG_RE = re.compile(r"(?<!\w)#\w+")

# LinkedIn
LI_HOOK_MAX = 140
LI_SEE_MORE = 210  # first 210 chars are above the "see more" fold (warn if hook spills past)
LI_BODY_MIN, LI_BODY_MAX = 1300, 2500
LI_CAROUSEL_MIN, LI_CAROUSEL_MAX = 8, 12
LI_HASHTAG_MAX = 2
# X
X_TWEET_MAX = 280
X_THREAD_MIN, X_THREAD_MAX = 2, 10
# Instagram
IG_REEL_MIN_S, IG_REEL_MAX_S = 30, 90
IG_CAROUSEL_MIN, IG_CAROUSEL_MAX = 7, 10
# Facebook
FB_SEE_MORE = 477  # chars before the "see more" fold
FB_BODY_MAX = 1000  # Facebook favors short; warn past this
FB_HASHTAG_MAX = 3

# ── prose-quality (human-voice) lint ──────────────────────────────────────────
# Advisory WARN layer catching the AI "tells" the structural lints can't see. Full judgment
# reference: docs/prose-craft.md. Curated in-house (not lifted from any list). Whole-word /
# whole-phrase, case-insensitive. Profile-specific additions come from voice-bans.txt (--ban-file).
_AI_TELLS: tuple[str, ...] = (
    "delve",
    "leverage",
    "foster",
    "seamless",
    "robust",
    "realm",
    "tapestry",
    "testament",
    "underscores",
    "boasts",
    "myriad",
    "plethora",
    "pivotal",
    "elevate",
    "it's worth noting",
    "when it comes to",
    "needless to say",
)
_GENERIC_OPENERS: tuple[str, ...] = (
    "in today's",
    "in a world",
    "in an era",
    "in the fast-paced",
    "in the world of",
)
_EM_DASH_RE = re.compile(r"—| -- ")
# Antithetical parallelism ("not X, it's Y" / "not just X but Y" / "don't X, instead Y").
_ANTITHESIS_RES: tuple[re.Pattern, ...] = (
    re.compile(r"\bit'?s not\b.{0,40}?\bit'?s\b", re.IGNORECASE),
    re.compile(r"\bnot just\b.{0,40}?\bbut\b", re.IGNORECASE),
    re.compile(r"\bisn'?t\b.{0,40}?\bit'?s\b", re.IGNORECASE),
    re.compile(r"\bdon'?t\b.{0,40}?\binstead\b", re.IGNORECASE),
)


# ── safe-to-share lint ────────────────────────────────────────────────────────
# Tenant tokens + internal hosts are TENANT DATA, not engine code: they live in
# tests/linter/safe_share_denylist.txt (single source, shared with debrand_check.sh),
# which the OSS export EXCLUDES — so the public cut ships an EMPTY tenant denylist
# (the secret-pattern rules below still apply). Override the file for tests via
# GTM_SAFE_SHARE_DENYLIST_FILE. Absent file → empty, by design.
def _load_denylist() -> tuple[tuple[str, ...], tuple[re.Pattern, ...]]:
    path = os.environ.get("GTM_SAFE_SHARE_DENYLIST_FILE") or os.path.join(
        os.path.dirname(__file__), "safe_share_denylist.txt"
    )
    tokens: list[str] = []
    hosts: list[re.Pattern] = []
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("host:"):
                    hosts.append(re.compile(line[5:].strip(), re.IGNORECASE))
                elif line.startswith("token:"):
                    tokens.append(line[6:].strip().lower())
                elif line.startswith("rtoken:"):
                    tokens.append(line[7:].strip().lower())
    except FileNotFoundError:
        pass
    return tuple(tokens), tuple(hosts)


_TENANT_TOKENS, _HOSTNAME_RES = _load_denylist()

# Credential / secret surface patterns that must not appear in public content.
_SECRET_RES: tuple[re.Pattern, ...] = (
    re.compile(r"\b\w+_TOKEN\b"),  # e.g. BACKEND_JWT_TOKEN
    re.compile(r"\b\w+_SECRET\b"),  # e.g. BACKEND_JWT_SECRET
    re.compile(r"\b\w+_API_KEY\b"),  # e.g. FIRECRAWL_API_KEY
    re.compile(r"\.env\b"),  # .env file reference
    re.compile(r"\bdoppler run\b", re.IGNORECASE),  # secret injection via a secret manager
)


@dataclass(frozen=True)
class Violation:
    severity: str  # "error" | "warn"
    rule: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.rule}: {self.message}"


def _hashtags(asset: dict) -> list[str]:
    if asset.get("hashtags") is not None:
        return list(asset["hashtags"])
    text = " ".join(str(asset.get(k, "")) for k in ("body", "caption"))
    return HASHTAG_RE.findall(text)


def _has_url(text: str | None) -> bool:
    return bool(URL_RE.search(text or ""))


def lint_linkedin(asset: dict) -> list[Violation]:
    v: list[Violation] = []
    hook = asset.get("hook", "") or ""
    if len(hook) > LI_HOOK_MAX:
        v.append(Violation("error", "li.hook", f"hook is {len(hook)} chars (>{LI_HOOK_MAX})"))
    elif len(hook) > LI_SEE_MORE:
        v.append(
            Violation(
                "warn",
                "li.hook",
                f"hook {len(hook)} chars spills past the {LI_SEE_MORE}-char 'see more' fold",
            )
        )
    body = asset.get("body", "") or ""
    if _has_url(body):
        v.append(
            Violation(
                "error",
                "li.body_url",
                "no outbound links in the post body (kills reach ~50-60%; put it in a comment)",
            )
        )
    ht = _hashtags(asset)
    if len(ht) > LI_HASHTAG_MAX:
        v.append(
            Violation(
                "error", "li.hashtags", f"{len(ht)} hashtags (>{LI_HASHTAG_MAX}; 0-2 in 2026)"
            )
        )
    if body and not (LI_BODY_MIN <= len(body) <= LI_BODY_MAX):
        v.append(
            Violation(
                "warn", "li.body_len", f"body {len(body)} chars outside {LI_BODY_MIN}-{LI_BODY_MAX}"
            )
        )
    if asset.get("format") == "carousel":
        n = len(asset.get("slides", []) or [])
        if not (LI_CAROUSEL_MIN <= n <= LI_CAROUSEL_MAX):
            v.append(
                Violation(
                    "error",
                    "li.carousel_slides",
                    f"{n} slides outside {LI_CAROUSEL_MIN}-{LI_CAROUSEL_MAX}",
                )
            )
    if asset.get("format") in ("infographic", "infographic-handwritten"):
        if not (asset.get("key_points") or []):
            v.append(
                Violation(
                    "warn",
                    "li.infographic_key_points",
                    "key_points missing or empty — visual brief needs at least one data point or insight",
                )
            )
    return v


def lint_x(asset: dict) -> list[Violation]:
    v: list[Violation] = []
    fmt = asset.get("format", "thread")
    tweets = asset.get("tweets") or ([asset["body"]] if asset.get("body") else [])
    if not tweets:
        return [Violation("error", "x.empty", "no tweets/body")]
    for i, t in enumerate(tweets):
        if len(t) > X_TWEET_MAX:
            v.append(
                Violation(
                    "error", "x.tweet_len", f"tweet {i + 1} is {len(t)} chars (>{X_TWEET_MAX})"
                )
            )
    if fmt == "thread":
        if _has_url(tweets[0]):
            v.append(
                Violation(
                    "error",
                    "x.first_link",
                    "tweet 1/ must stand alone — no link (put the link in a reply)",
                )
            )
        if not (X_THREAD_MIN <= len(tweets) <= X_THREAD_MAX):
            v.append(
                Violation(
                    "warn",
                    "x.thread_len",
                    f"{len(tweets)} tweets outside {X_THREAD_MIN}-{X_THREAD_MAX}",
                )
            )
    return v


def lint_instagram(asset: dict) -> list[Violation]:
    v: list[Violation] = []
    fmt = asset.get("format", "reel")
    if not (asset.get("caption", "") or "").strip():
        v.append(Violation("error", "ig.caption", "caption required (drives SEO/discovery)"))
    if fmt == "reel":
        d = asset.get("duration_s")
        if d is None or not (IG_REEL_MIN_S <= d <= IG_REEL_MAX_S):
            v.append(
                Violation(
                    "error",
                    "ig.reel_dur",
                    f"reel duration {d}s outside {IG_REEL_MIN_S}-{IG_REEL_MAX_S}s",
                )
            )
    if fmt == "carousel":
        n = len(asset.get("slides", []) or [])
        if not (IG_CAROUSEL_MIN <= n <= IG_CAROUSEL_MAX):
            v.append(
                Violation(
                    "error",
                    "ig.carousel_slides",
                    f"{n} slides outside {IG_CAROUSEL_MIN}-{IG_CAROUSEL_MAX}",
                )
            )
    return v


def lint_facebook(asset: dict) -> list[Violation]:
    v: list[Violation] = []
    body = asset.get("body", "") or ""
    if not body.strip():
        v.append(Violation("error", "fb.body", "body required"))
    if _has_url(body):
        v.append(
            Violation(
                "error",
                "fb.body_url",
                "no outbound links in the body — put the link in the first comment",
            )
        )
    first_line = (asset.get("hook", "") or "") or (body.splitlines()[0] if body else "")
    if len(first_line) > FB_SEE_MORE:
        v.append(
            Violation(
                "warn",
                "fb.hook",
                f"first line {len(first_line)} chars spills past the {FB_SEE_MORE}-char 'see more' fold",
            )
        )
    if body and len(body) > FB_BODY_MAX:
        v.append(
            Violation(
                "warn",
                "fb.body_len",
                f"body {len(body)} chars — Facebook favors short; say it in half",
            )
        )
    ht = _hashtags(asset)
    if len(ht) > FB_HASHTAG_MAX:
        v.append(
            Violation(
                "warn",
                "fb.hashtags",
                f"{len(ht)} hashtags (>{FB_HASHTAG_MAX}; hashtags are weak on Facebook)",
            )
        )
    return v


def _asset_text(asset: dict) -> str:
    """Assemble the human-readable copy of an asset for the prose-quality pass (excludes `tone`)."""
    parts: list[str] = []
    for k in ("hook", "body", "caption"):
        val = asset.get(k)
        if isinstance(val, str):
            parts.append(val)
    for k in ("tweets", "slides", "key_points"):
        val = asset.get(k)
        if isinstance(val, list):
            parts.extend(str(x) for x in val)
    return "\n".join(p for p in parts if p)


_ABSTRACT_OPEN_RE = re.compile(
    r"(\bis becoming\b|^\s*the (future|rise|power|age|era|dawn|world) of\b|^\s*in a world\b)",
    re.IGNORECASE,
)


def lint_prose_quality(text: str, extra_bans: tuple[str, ...] = ()) -> list[Violation]:
    """Advisory (WARN-only) human-voice checks — catch the AI 'tells' the structural lints miss.

    Never emits an ERROR, so it never changes the CLI exit code or blocks review; every finding is
    a revise-or-justify item. Full reference: docs/prose-craft.md. ``extra_bans`` is the active
    profile's knowledge/voice-bans.txt (passed via --ban-file).
    """
    text = text or ""
    v: list[Violation] = []
    if _EM_DASH_RE.search(text):
        v.append(
            Violation(
                "warn",
                "prose.em_dash",
                "em dash reads as AI — recast as two sentences, a comma, or a colon",
            )
        )
    _first = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0] if text.strip() else ""
    if _ABSTRACT_OPEN_RE.search(_first):
        v.append(
            Violation(
                "warn",
                "prose.abstract_open",
                "opens on a category abstraction ('X is becoming Y' / 'the future of…') — "
                "lead with a number, name, quote, or shipped artifact",
            )
        )
    stripped_low = text.lstrip().lower()
    for opener in _GENERIC_OPENERS:
        if stripped_low.startswith(opener):
            v.append(
                Violation(
                    "warn",
                    "prose.generic_opener",
                    f"generic opener {opener!r} — open on the concrete thing",
                )
            )
            break
    seen: set[str] = set()
    for term in tuple(_AI_TELLS) + tuple(extra_bans):
        key = term.lower()
        if key in seen:
            continue
        if re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.IGNORECASE):
            seen.add(key)
            v.append(
                Violation(
                    "warn",
                    "prose.banned_word",
                    f"banned / AI-tell phrase {term!r} — use a plain word",
                )
            )
    for pat in _ANTITHESIS_RES:
        if pat.search(text):
            v.append(
                Violation(
                    "warn",
                    "prose.antithesis",
                    "antithetical parallelism ('not X, it's Y') — state it positively, once",
                )
            )
            break
    # Anaphora: 3+ consecutive sentence-like lines opening with the same word.
    run_word: str | None = None
    run = 0
    flagged = False
    for ln in (s.strip() for s in text.splitlines()):
        if len(ln) <= 30:
            continue
        first = re.split(r"\W+", ln.lower(), maxsplit=1)[0]
        if first and first == run_word:
            run += 1
            if run >= 3 and not flagged:
                v.append(
                    Violation(
                        "warn",
                        "prose.anaphora",
                        f"3+ lines opening with {first!r} — vary the openings",
                    )
                )
                flagged = True
        else:
            run_word, run = first, 1
    return v


_DISPATCH = {
    "linkedin": lint_linkedin,
    "x": lint_x,
    "facebook": lint_facebook,
    "instagram": lint_instagram,
}


def lint(asset: dict, extra_bans: tuple[str, ...] = ()) -> list[Violation]:
    fn = _DISPATCH.get(asset.get("platform"))
    if fn is None:
        return [Violation("error", "platform", f"unknown platform {asset.get('platform')!r}")]
    violations = fn(asset)
    violations.extend(lint_prose_quality(_asset_text(asset), extra_bans))
    return violations


def passes(asset: dict) -> bool:
    """True if the asset has no ERROR-severity violations (it may still have warnings)."""
    return not any(x.severity == "error" for x in lint(asset))


def lint_safe_to_share(text: str, allow_tenants: tuple[str, ...] = ()) -> list[Violation]:
    """Scan free text for tenant names and credential patterns.

    ERROR severity (blocks Gate 2). Designed for builder-studio outputs.
    Tenant tokens mirror debrand_check.sh so the list is single-sourced.

    ``allow_tenants`` downgrades the named tenants to WARNING instead of ERROR —
    for the one legitimate case where the ACTIVE profile's own brand appears by
    design (e.g. a founder-byline post). It must name each tenant explicitly;
    the cross-tenant hard block is unaffected for every other token.
    """
    allowed = {t.lower() for t in allow_tenants}
    v: list[Violation] = []
    for token in _TENANT_TOKENS:
        if re.search(rf"\b{re.escape(token)}\b", text, re.IGNORECASE):
            if token.lower() in allowed:
                v.append(
                    Violation(
                        "warning",
                        "safe.tenant.allowed",
                        f"contains tenant name {token!r} — allowed via --allow-tenant "
                        "(active-profile brand; verify the byline is intentional)",
                    )
                )
            else:
                v.append(
                    Violation(
                        "error",
                        "safe.tenant",
                        f"contains tenant name {token!r} — remove before publishing",
                    )
                )
    for pattern in _SECRET_RES:
        if pattern.search(text):
            v.append(
                Violation(
                    "error", "safe.credential", f"credential pattern matched ({pattern.pattern!r})"
                )
            )
    for pattern in _HOSTNAME_RES:
        if pattern.search(text):
            v.append(
                Violation(
                    "error",
                    "safe.hostname",
                    f"internal hostname matched ({pattern.pattern!r}) — use a placeholder host",
                )
            )
    return v


def passes_safe_to_share(text: str) -> bool:
    """True if text has no ERROR-severity safe-to-share violations."""
    return not any(x.severity == "error" for x in lint_safe_to_share(text))


def _load_bans(path: str | None) -> tuple[str, ...]:
    """Read a newline-delimited ban list (voice-bans.txt); skip blanks and #-comments."""
    if not path:
        return ()
    out: list[str] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s and not s.startswith("#"):
                out.append(s)
    return tuple(out)


def _selftest() -> int:
    cases = [
        # (asset, expect_pass)
        (
            {
                "platform": "linkedin",
                "format": "carousel",
                "hook": "x" * 120,
                "body": "y" * 1500,
                "slides": ["s"] * 10,
                "hashtags": ["#ai", "#trust"],
            },
            True,
        ),
        (
            {
                "platform": "linkedin",
                "format": "carousel",
                "hook": "x" * 200,  # hook too long
                "body": "y" * 1500,
                "slides": ["s"] * 10,
            },
            False,
        ),
        (
            {
                "platform": "linkedin",
                "format": "text",
                "hook": "ok",
                "body": "see https://example.com",
            },
            False,
        ),  # body url
        (
            {
                "platform": "linkedin",
                "format": "carousel",
                "hook": "ok",
                "body": "y" * 1500,
                "slides": ["s"] * 5,
            },
            False,
        ),  # too few slides
        (
            {
                "platform": "linkedin",
                "format": "text",
                "hook": "ok",
                "body": "y" * 1500,
                "hashtags": ["#a", "#b", "#c"],
            },
            False,
        ),  # too many hashtags
        (
            {"platform": "x", "format": "thread", "tweets": ["hook stands alone", "more", "cta"]},
            True,
        ),
        (
            {"platform": "x", "format": "thread", "tweets": ["see https://x.com now", "b"]},
            False,
        ),  # link in 1/
        ({"platform": "instagram", "format": "reel", "caption": "c", "duration_s": 45}, True),
        (
            {"platform": "instagram", "format": "reel", "caption": "c", "duration_s": 120},
            False,
        ),  # too long
        (
            {"platform": "instagram", "format": "carousel", "caption": "", "slides": ["s"] * 8},
            False,
        ),  # no caption
        (
            {
                "platform": "facebook",
                "format": "text",
                "body": "shipped the thing. it earns a share.",
            },
            True,
        ),
        (
            {"platform": "facebook", "format": "text", "body": "read more https://x.com"},
            False,
        ),  # body url → link in first comment
        ({"platform": "facebook", "format": "text", "body": ""}, False),  # no body
        (
            # prose-quality is advisory: em dash + AI-tell word warn but never flip pass/fail
            {
                "platform": "linkedin",
                "format": "text",
                "hook": "ok",
                "body": "y" * 1490 + " we leverage this — now.",
            },
            True,
        ),
    ]
    failures = 0
    for i, (asset, expect) in enumerate(cases):
        got = passes(asset)
        if got != expect:
            failures += 1
            print(
                f"  ✗ case {i}: expected pass={expect}, got {got}; violations={[str(x) for x in lint(asset)]}"
            )
    if failures:
        print(f"SELFTEST FAILED: {failures}/{len(cases)} cases")
        return 1
    print(f"✓ content linter selftest: {len(cases)}/{len(cases)} cases pass")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Content linter (spec §13)")
    ap.add_argument("asset", nargs="?", help="path to a JSON asset file to lint")
    ap.add_argument("--selftest", action="store_true", help="run built-in cases")
    ap.add_argument(
        "--safe-to-share",
        metavar="TEXT",
        help="check free text for tenant names and credential patterns",
    )
    ap.add_argument(
        "--safe-to-share-file",
        metavar="PATH",
        help=(
            "check a plain-text/markdown FILE (e.g. an article or podcast script) for tenant "
            "names and credential patterns — the file form of --safe-to-share, so skills can run "
            "the linter via an approved command instead of inline `python -c`"
        ),
    )
    ap.add_argument(
        "--allow-tenant",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "downgrade this tenant name from ERROR to WARNING in the safe-to-share checks. "
            "Only for the ACTIVE profile's own brand appearing by design (founder-byline "
            "posts); never use it for another tenant's name. Repeatable."
        ),
    )
    ap.add_argument(
        "--ban-file",
        metavar="PATH",
        help=(
            "newline-delimited profile ban list (knowledge/voice-bans.txt) added to the advisory "
            "prose-quality checks; resolve it with `python -m gtm_core.resolve_knowledge voice-bans.txt`"
        ),
    )
    ap.add_argument(
        "--prose-file",
        metavar="PATH",
        help=(
            "run only the advisory prose-quality (human-voice) checks on a plain-text/markdown FILE "
            "(article, podcast script). Warnings only — never fails the build."
        ),
    )
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    safe_text: str | None = args.safe_to_share
    if args.safe_to_share_file:
        with open(args.safe_to_share_file, encoding="utf-8") as f:
            safe_text = f.read()
    if safe_text is not None:
        violations = lint_safe_to_share(safe_text, allow_tenants=tuple(args.allow_tenant))
        for x in violations:
            print(str(x))
        errors = [x for x in violations if x.severity == "error"]
        if errors:
            print(f"FAIL: {len(errors)} error(s)")
            return 1
        print("PASS")
        return 0
    bans = _load_bans(args.ban_file)
    if args.prose_file:
        with open(args.prose_file, encoding="utf-8") as f:
            prose_text = f.read()
        violations = lint_prose_quality(prose_text, bans)
        for x in violations:
            print(str(x))
        # Prose-quality is advisory — warnings only, never a build failure.
        print("PASS" + (f" ({len(violations)} prose warning(s))" if violations else ""))
        return 0
    if not args.asset:
        ap.error(
            "provide an asset JSON file, --selftest, --safe-to-share TEXT, "
            "--safe-to-share-file PATH, or --prose-file PATH"
        )
    with open(args.asset) as f:
        asset = json.load(f)
    violations = lint(asset, bans)
    for x in violations:
        print(str(x))
    errors = [x for x in violations if x.severity == "error"]
    if errors:
        print(f"FAIL: {len(errors)} error(s)")
        return 1
    print("PASS" + (f" ({len(violations)} warning(s))" if violations else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
