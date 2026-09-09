"""CLI + shared function: turn a real identity into a fictional one of the same shape.

§R9 says "preserve the string shape the test needs, never the identity". Said only as
prose, that rule loses every time, because typing the real row is one keystroke cheaper
than inventing a company — and the real row is right there on screen, being the thing that
broke the parser. Nine identity leaks reached the public repo that way, six of them bare
company names no gate could see. `tests/lint/third_party_roster.py` now refuses them at
commit time; this module is the other half, and the more important one: it makes the
fictional value the cheap path rather than the disciplined one.

The output is DETERMINISTIC in the input, so the same real name always yields the same
fake. That matters more than it looks: a fixture built across two sessions stays
self-consistent, a diff stays reviewable, and a test that joins two records still joins.
It is a one-way function — the mapping is never written down, so nothing in the repo can
turn the fake back into the real name.

What "same shape" means is whatever the code under test actually asserts on, so each
generator preserves the property and nothing else:

    company   trailing sibilant, a leading article, an ampersand, a digit, a corporate
              suffix, CJK characters, word count, ALLCAPS-ness, lowercase-brand-ness
    email     local-part shape, on an RFC-2606 reserved domain
    domain    label count and TLD class (.com / .edu / .co.uk), on a reserved suffix
    person    given/family word count and capitalisation
    phone     E.164 vs NANP form, digit count, in the reserved 555-01xx range

Usage:
    python -m gtm_core.fictionalize company "Northwind Health Partners"
    python -m gtm_core.fictionalize email "someone@their-real-domain.invalid"
    python -m gtm_core.fictionalize --kind auto "Acme Corp"     # infer from the value
    python -m gtm_core.fictionalize --json person "Jane Smith"

Prints the fictional value to stdout and exits 0.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys

# Fictional word stock. Deliberately bland and obviously invented — a fake that reads like
# a real company invites the next reader to wonder whether it is one.
# Split by ENDING CLASS, because a trailing sibilant changes the possessive a renderer
# writes ("Acme's" vs "Atlas'") and is a real cased-on property. Selecting from the right
# pool keeps the fake a word; mutating a chosen word to force the ending produced
# "Logisticn" and "Healths", which is worse than the leak it replaces.
_LEAD_PLAIN = (
    "Cascade",
    "Northwind",
    "Summitline",
    "Brightpath",
    "Eastvale",
    "Riverbend",
    "Wavelet",
    "Wideloop",
    "Quarry",
    "Lantern",
    "Fernway",
    "Copperline",
    "Marlowe",
    "Tidewater",
)
_LEAD_SIB = ("Forgeworks", "Nimbus", "Atlas", "Cirrus", "Meridians", "Halyards")
_LEAD = _LEAD_PLAIN + _LEAD_SIB
_TAIL_PLAIN = ("Health", "Financial", "Digital", "Interactive", "Capital", "Medical", "Foundry")
_TAIL_SIB = ("Systems", "Labs", "Partners", "Networks", "Analytics", "Holdings", "Logistics")
_TAIL = _TAIL_PLAIN + _TAIL_SIB
_GIVEN = ("Robin", "Sam", "Alex", "Dana", "Jordan", "Casey", "Riley", "Morgan", "Quinn", "Avery")
_FAMILY = ("Kraft", "Ortega", "Nakamura", "Bello", "Halvorsen", "Okafor", "Renner", "Vasquez")
# The CJK stock exists for one property: a name that strips to empty under ASCII
# normalisation, which is the case gtm_core.slugify has a hash fallback for.
_CJK = ("環宇", "青嵐", "北辰", "宏遠")

_SIBILANT = ("s", "x", "z", "ch", "sh")
_SUFFIXES = (
    "Inc.",
    "Inc",
    "LLC",
    "Ltd",
    "Ltd.",
    "GmbH",
    "Pte Ltd",
    "Pty Ltd",
    "plc",
    "SA",
    "NV",
    "BV",
    "AG",
    "Co.",
    "Co",
    "Corp",
    "Corp.",
    "Corporation",
    "Company",
)
_ARTICLES = ("the", "a", "an")

_EMAIL_RE = re.compile(r"^([^@\s]+)@([^@\s]+)$")
_DOMAIN_RE = re.compile(r"^(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}$")
_PHONE_RE = re.compile(r"^\+?[\d\s().-]{7,}$")


def _rng(value: str, salt: str) -> int:
    """A stable integer for this (value, kind). Not a secret — determinism is the point."""
    return int(hashlib.sha256(f"{salt}\n{value.strip().lower()}".encode()).hexdigest()[:12], 16)


def _pick(pool: tuple[str, ...], value: str, salt: str, offset: int = 0) -> str:
    return pool[(_rng(value, salt) + offset) % len(pool)]


def company(name: str) -> str:
    """A fictional company preserving every property a name-handling test asserts on."""
    raw = name.strip()
    if not raw:
        return ""
    if any("　" <= ch <= "鿿" or "가" <= ch <= "힯" for ch in raw):
        return _pick(_CJK, raw, "company-cjk")

    tokens = raw.split()
    suffix = ""
    # A trailing corporate form is a separate property from the name itself. The ORIGINAL
    # spelling is kept — "Ltd" and "Ltd." are different strings to a parser.
    for cand in sorted(_SUFFIXES, key=lambda c: len(c.split()), reverse=True):
        n = len(cand.split())
        if len(tokens) > n and " ".join(tokens[-n:]).lower().rstrip(".") == cand.lower().rstrip(
            "."
        ):
            suffix = " ".join(tokens[-n:])
            tokens = tokens[:-n]
            break

    article = ""
    if tokens and tokens[0].lower() in _ARTICLES:
        article = tokens[0]
        tokens = tokens[1:]

    body = " ".join(tokens)
    n_words = max(len(tokens), 1)
    ends_sibilant = bool(body) and body.lower().rstrip(".").endswith(_SIBILANT)

    if n_words == 1:
        out = _pick(_LEAD_SIB if ends_sibilant else _LEAD_PLAIN, raw, "company")
    else:
        last = _pick(_TAIL_SIB if ends_sibilant else _TAIL_PLAIN, raw, "company-tail")
        words = [_pick(_LEAD_PLAIN, raw, "company")]
        for i in range(1, n_words - 1):
            mid = _pick(_TAIL, raw, "company-mid", offset=i)
            # A repeated word reads as a bug in the fixture rather than a company.
            while mid in words or mid == last:
                i += 1
                mid = _pick(_TAIL, raw, "company-mid", offset=i)
            words.append(mid)
        words.append(last)
        out = " ".join(words)

    # An ampersand and an embedded digit are each a distinct parser case.
    if "&" in body:
        out = out.replace(" ", " & ", 1) if " " in out else out + " & Co"
    digits = re.findall(r"\d+", body)
    if digits:
        out = re.sub(r"^(\w+)", lambda m: m.group(1) + digits[0], out, count=1)

    # Casing is itself the property in the lowercase-brand and ALLCAPS cases.
    if body and body.isupper():
        out = out.upper()
    elif body and body.islower():
        out = out.lower()

    if article:
        out = f"{article} {out}"
    if suffix:
        out = f"{out} {suffix}"
    return out


def domain(value: str) -> str:
    """A reserved-domain equivalent keeping label count and TLD class."""
    raw = value.strip().lower().rstrip(".")
    if not raw:
        return ""
    labels = raw.split(".")
    base = _pick(_LEAD, raw, "domain").lower()
    # An academic or government suffix is matched by real rules, so it must survive.
    if raw.endswith(".edu") or ".edu." in raw:
        return f"{base}.edu.example"
    if raw.endswith(".gov") or ".gov." in raw:
        return f"{base}.gov.example"
    sub = ".".join(
        _pick(_LEAD, raw, "domain-sub", offset=i).lower() for i in range(len(labels) - 2)
    )
    return f"{sub + '.' if sub else ''}{base}.example"


def email(value: str) -> str:
    """A reserved-domain address keeping the local-part's shape (dots, initials, digits)."""
    m = _EMAIL_RE.match(value.strip())
    if not m:
        return value
    local, host = m.group(1), m.group(2)
    given = _pick(_GIVEN, value, "email-given").lower()
    family = _pick(_FAMILY, value, "email-family").lower()
    if "." in local:
        new_local = f"{given[0]}.{family}" if len(local.split(".")[0]) == 1 else f"{given}.{family}"
    elif "_" in local:
        new_local = f"{given}_{family}"
    elif local.isdigit():
        new_local = str(_rng(value, "email-num") % 10 ** len(local)).zfill(len(local))
    else:
        new_local = given if len(local) <= len(given) + 2 else f"{given}{family[0]}"
    if re.search(r"\d$", local):
        new_local += re.search(r"\d+$", local).group(0)
    return f"{new_local}@{domain(host)}"


def person(name: str) -> str:
    """A fictional person keeping word count and capitalisation."""
    raw = name.strip()
    if not raw:
        return ""
    parts = raw.split()
    out = [_pick(_GIVEN, raw, "person-given")]
    if len(parts) > 1:
        out.append(_pick(_FAMILY, raw, "person-family"))
    for i in range(2, len(parts)):
        out.append(_pick(_FAMILY, raw, "person-family", offset=i))
    joined = " ".join(out)
    return joined.upper() if raw.isupper() else joined


def phone(value: str) -> str:
    """A reserved-range number keeping E.164-vs-NANP form and digit count."""
    raw = value.strip()
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return raw
    tail = str(_rng(raw, "phone") % 100).zfill(2)
    if raw.startswith("+"):
        # Keep the total digit count so length assertions still hold.
        head = digits[: max(len(digits) - 7, 1)]
        return f"+{head}55501{tail}"
    if "(" in raw:
        return f"(555) 555-01{tail}"
    return f"555-01{tail}"


KINDS = {
    "company": company,
    "email": email,
    "domain": domain,
    "person": person,
    "phone": phone,
}


def infer_kind(value: str) -> str:
    v = value.strip()
    if _EMAIL_RE.match(v):
        return "email"
    if _PHONE_RE.match(v) and sum(c.isdigit() for c in v) >= 7:
        return "phone"
    if _DOMAIN_RE.match(v):
        return "domain"
    # Two capitalised words with no corporate marker read as a person far more often
    # than as a company; anything else is treated as a company, the safer default
    # (a company generator applied to a person still removes the identity).
    tokens = v.split()
    if (
        len(tokens) == 2
        and all(t[:1].isupper() for t in tokens)
        and not any(
            t.lower().rstrip(".") in {s.lower().rstrip(".") for s in _SUFFIXES} for t in tokens
        )
        and not any(t in _TAIL for t in tokens)
    ):
        return "person"
    return "company"


def fictionalize(value: str, kind: str = "auto") -> str:
    return KINDS[infer_kind(value) if kind == "auto" else kind](value)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m gtm_core.fictionalize",
        description="Replace a real identity with a shape-preserving fictional one (§R9).",
    )
    ap.add_argument("kind", choices=[*KINDS, "auto"], help="what the value is")
    ap.add_argument("value", nargs="+", help="one or more values to fictionalize")
    ap.add_argument("--json", action="store_true", help="emit {real: fake} as JSON")
    args = ap.parse_args(argv)

    pairs = {v: fictionalize(v, args.kind) for v in args.value}
    if args.json:
        print(json.dumps(pairs, ensure_ascii=False, indent=2))
    else:
        for fake in pairs.values():
            print(fake)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
