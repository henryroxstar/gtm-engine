from __future__ import annotations

import re
import unicodedata

# --- first name ----------------------------------------------------------

# Honorifics that arrive glued to the given name ("Dr.rani") or space-separated.
_TITLES = (
    "dr",
    "mr",
    "mrs",
    "ms",
    "miss",
    "prof",
    "professor",
    "sir",
    "rev",
    "capt",
    "lt",
    "sgt",
)
_TITLE_PREFIX_RE = re.compile(
    r"^(?:" + "|".join(_TITLES) + r")\.?\s*(?=[A-Za-zÀ-ÖØ-öø-ÿ])",
    re.IGNORECASE,
)
# Post-nominals that ride along in a name field ("Rothstein, Mba, Pmp").
_CREDENTIAL_RE = re.compile(
    r"[,\s]+(?:mba|md|phd|ph\.d|cpa|pmp|rn|msn|aprn|fnp-?c|cissp|cfa|jd|esq|shrm\s*-?\s*scp|"
    r"cism|cisa|pe|do|dds|mph|mha|bsn|scp|sphr)\b\.?",
    re.IGNORECASE,
)
# A name is letters plus the punctuation real names actually contain.
#: A given name, allowing the INTERNAL SPACE that romanised Chinese, Malay and Indian names
#: routinely carry: "Hui Jie", "Wei Ming", "Siti Nurhaliza", "Mary Anne", "Jean Luc". Until
#: 2026-09-04 this pattern had no space in it, so every such name was blocked as
#: `first-name-unrenderable` — "'Hui Jie' is not a name". On a Singapore list that is not an
#: edge case; the rule rejected the market the campaign was aimed at, and the message it
#: printed while doing so was wrong about a real person's name. Two spaces max: past three
#: tokens the field is carrying a title or a note, not a given name.
_NAME_TOKEN = r"[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'\-’]*"  # nosec B105 - name-shape regex, not a secret
_NAME_CHARS_RE = re.compile(rf"^{_NAME_TOKEN}(?: {_NAME_TOKEN}){{0,2}}$")
# Values that are placeholders, not people.
_NON_NAMES = frozenset(
    {
        "n/a",
        "na",
        "none",
        "null",
        "unknown",
        "test",
        "team",
        "info",
        "admin",
        "hr",
        "sales",
        "support",
        "contact",
        "hello",
        "there",
        "sir",
        "madam",
        "friend",
        "customer",
        "user",
        "-",
        "--",
        ".",
    }
)


def _has_letters(s: str) -> bool:
    return any(c.isalpha() for c in s)


def _strip_symbols(s: str) -> str:
    """Drop emoji, pictographs, and stray symbol runs, keeping letters/marks/punctuation."""
    return "".join(
        c for c in s if not unicodedata.category(c).startswith(("So", "Sk", "Cs", "Co"))
    ).strip()


def _titlecase(token: str) -> str:
    """Title-case a single name token, preserving interior capitals people actually use
    (McAfee, O'Brien, Jean-Luc) rather than flattening them to Mcafee."""
    if not token:
        return token
    # A two-letter all-caps token is initials (AJ, TJ, JR), not a shouted name. Without
    # this, clean_first_name is NOT idempotent: "A.j." -> "AJ" on the first sweep, then
    # "Aj" on the next one, and consolidate runs on a schedule. A genuinely shouted name
    # ("CHRIS") is 3+ characters and is still normalized.
    if token.isupper() and len(token) <= 2:
        return token
    if token.isupper() or token.islower():
        out = token[:1].upper() + token[1:].lower()
        # Re-capitalize after an internal hyphen or apostrophe.
        return re.sub(r"([\-'’])([a-z])", lambda m: m.group(1) + m.group(2).upper(), out)
    return token


def clean_first_name(first: str, last: str = "", email: str = "") -> str:
    """Return a sendable given name for ``Hi <first>,`` — or ``""`` when none can be
    derived without guessing.

    Repairs, in order: strip symbols/emoji, strip post-nominals, strip an honorific
    prefix (``Dr.rani`` -> ``Jaya``), collapse a glued single-initial prefix
    (``M.omar`` -> ``Ahmer``), normalize a dotted initial pair (``A.j.`` -> ``AJ``),
    take the first token of a multi-word value, and fix casing.

    Falls back to ``last`` then the email local-part **only** when the incoming value
    carries no usable letters at all — the 🍦-in-the-first-name case, where ``last``
    held the real full name. Never invents a name from nothing.
    """
    raw = (first or "").strip()
    cleaned = _strip_symbols(raw)
    cleaned = _CREDENTIAL_RE.sub("", cleaned).strip(" ,.")

    # Nothing usable left. Recover from ``last`` only — that is the swapped-field case,
    # where the real full name sits in the adjacent column ("🍦" / "Marcus Webb").
    #
    # Deliberately NOT falling back to the email local-part: "abrooke@medipath.example" would
    # yield "Hi Abrooke," (an initial glued to a surname), and "j.smith@" would yield
    # "Hi J,". Deriving a human's name from an address is guessing, and this module's
    # contract is that an unrepairable row is flagged for a person, never invented.
    if not _has_letters(cleaned):
        fallback = _strip_symbols((last or "").strip())
        cleaned = fallback if _has_letters(fallback) else ""
    if not cleaned:
        return ""

    cleaned = _TITLE_PREFIX_RE.sub("", cleaned).strip(" .,")

    # "M.omar" -> "Omar": a single initial glued to a real name. Prefer the name.
    # Checked BEFORE the dotted-initials rule below, which would otherwise read the
    # trailing name as a run of initials and return "MAHMER".
    m = re.fullmatch(r"([A-Za-z])\.\s*([A-Za-zÀ-ÖØ-öø-ÿ'\-’]{2,})", cleaned)
    if m:
        cleaned = m.group(2)
    # "A.j." / "A.J" -> "AJ" (initials only). A dot is required, so real two-letter
    # names like "Al" or "Bo" are never uppercased into initials.
    elif re.fullmatch(r"[A-Za-z]\.(?:[A-Za-z]\.?)+", cleaned):
        return re.sub(r"[^A-Za-z]", "", cleaned).upper()

    # Multi-word ("Mary Jane", "Marcus Webb") -> the given name only.
    cleaned = cleaned.split()[0] if cleaned.split() else cleaned
    cleaned = cleaned.strip(" .,")

    if not _has_letters(cleaned):
        return ""
    return _titlecase(cleaned)


def clean_last_name(last: str) -> str:
    """Return a surname safe to render through ``{{Last Name}}``.

    Strips emoji/symbol runs ("Reece 🥇✨") and trailing post-nominals ("Rothstein, Mba,
    Pmp" -> "Rothstein"), then tidies stray punctuation. Accented letters are preserved —
    "Pérez Trufero" is a correct name, not a defect, and must survive untouched.

    ``last`` is not in the current sequence copy, but ``{{Last Name}}`` is a valid provider
    field one template edit away, and ``clean_first_name`` falls back to this column when
    first/last are swapped. Cleaning it closes both paths.
    """
    raw = (last or "").strip()
    if not raw:
        return ""
    out = _strip_symbols(raw)
    prev = None
    while prev != out:  # "Warrix, Cpa, Mba" needs more than one pass
        prev = out
        out = _CREDENTIAL_RE.sub("", out).strip()
    out = re.sub(r"\s+", " ", out).strip(" ,.")
    return out if _has_letters(out) else raw
