#!/usr/bin/env python3
"""Derive the roster of THIRD-PARTY names the shippable surface must never contain.

The de-brand lint knows OUR names. `pii_check`'s other rules know SHAPES — an email, a
phone, a LinkedIn slug, a domain. A bare company name has neither — a customer's name in
a docstring is just a word. Six of the nine identity leaks that reached the public repo
were exactly that, and each was fixed by hand because the only list of other people's
names lived in whoever ran the release identity read's memory. That list is ~1200
accounts long and grows daily, so the read found the ones a person happened to recall
and missed the rest — v0.15.0 shipped with at least eleven still in it.

So the roster is DERIVED from the tenant data that already names them, never typed:

    content/<tenant>/accounts/<slug>/                       one folder per real account
    profiles/<tenant>/knowledge/outreach-case-studies.txt   one name per line

Both are LISTS OF NAMES by construction. An earlier draft also scraped `### ` headings out
of `case-studies.md`, which produced keys like "reusable messaging hooks" from the
document's own structural headings — prose is not a roster, and the curated .txt already
covers every case study the headings did.

WHY A NAIVE ROSTER IS UNUSABLE, AND WHAT THIS DOES INSTEAD. The account slugs' leading
tokens include `first`, `state`, `direct`, `cross`, `level` — words no gate can fire on,
and a six-letter slug fragment sits inside a longer English word often enough to matter.
A gate that cries wolf is a gate people learn to skip (the same reasoning that narrowed
PHONE_RE). Noise is therefore removed when the key is GENERATED, not when a finding is
triaged:

  * a multi-word name becomes one n-gram key — distinctive by length, and its pattern
    spans separators, so it matches the slug form too;
  * a name's LEADING token is also a key when it is >= MIN_TOKEN_LEN and absent from the
    system dictionary, because that is what a slug or a comment abbreviates a company to.
    The leading token of a multi-word name is often geography or a common noun instead
    ("<city> <bank>", "<plural noun> <suffix>"), and the system dictionary is a 1934
    wordlist that has neither plurals nor British spellings, so those land in
    `[third-party-allowed]` under their own reason — measured here, that is ~15 entries
    and it buys the five companies whose folder name is multi-word;
  * an all-English phrase is dropped. A company genuinely named in plain English is a
    RESIDUAL this gate does not cover: keying on it cannot distinguish the company from
    the same words used as prose. Those stay with the human identity read;
  * `[third-party-allowed]` in pii_allowlist.txt removes names that are also legitimate
    vendors or ecosystem tools — a prospect can also be a thing we integrate with
    (`hubspot`, `vimeo`, `langchain`), and those citations are real content, not leaks.

Matching is word-bounded and case-insensitive, and that boundary does more work than any
filter: a six-letter key that is a prefix of an English word cannot match that word, and
a key like `archer` cannot match "researcher". Substring matching is what made a derived
roster look unusable; measured on this repo it cut the finding count by two thirds.

THE ROSTER IS ITSELF PII, so it is never committed in plaintext. Two modes:

  * derive (default) — read the live tenant data. Used by pre-commit, the export gate and
    the identity read, all of which run where content/ and profiles/ exist.
  * digest — the salted sha256 of each key, committed as tests/lint/third_party_digest.txt
    so CI (which has no tenant data) enforces the same rule. A digest of a short company
    name is brute-forceable by anyone holding a wordlist, which is why the salt is here
    and the file is private-repo-only: this protects against a name reaching the PUBLIC
    cut, not against an attacker who already has the private repo — that repo also holds
    the tenant denylist and content/ itself in plaintext.

NOTE TO WHOEVER EDITS THIS FILE. `pii_check` lists it as SELF_REFERENTIAL and therefore does
NOT scan it — the roster's own documentation has to be able to discuss the rule. That
exemption means a real name written here would ship. The first draft of this docstring used
three real customers as examples and reached a finished carve; use a described shape, or run
`python -m gtm_core.fictionalize company "<name>"`. Same for `pii_check.py` and the two test
files beside them.

Usage:
    python tests/lint/third_party_roster.py --list        # the derived keys
    python tests/lint/third_party_roster.py --write-digest
    python tests/lint/third_party_roster.py --stats
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST = Path(__file__).resolve().parent / "pii_allowlist.txt"
DIGEST_FILE = Path(__file__).resolve().parent / "third_party_digest.txt"

# Not a secret: it exists so the digest file is specific to this repo, not so it resists
# an attacker holding the repo. See the module docstring.
DIGEST_SALT = "gtm-engine-third-party-roster-v1"

# A single token shorter than this is a collision generator, not an identity: three-letter
# tickers and abbreviations (`anz`, `ema`, `zeb`) appear inside ordinary prose constantly.
# Four was measured, not assumed: on this repo it adds 61 keys and finds three real leaks
# that five missed, for four allowlist entries. Multi-word names have no such floor —
# their length makes them distinctive on their own.
MIN_TOKEN_LEN = 4

# Where the system wordlist lives, in preference order. macOS ships `words` (a symlink to the
# 1934 `web2` list this module's filters were measured against). Debian/Ubuntu ship the list
# itself as `american-english` and only create the `words` symlink via `dictionaries-common`,
# so a runner that installed `wamerican` may have the second path and not the first — checking
# both is what keeps the noise filter from silently no-opping on a package-layout detail.
_DICT_CANDIDATES = (
    Path("/usr/share/dict/words"),
    Path("/usr/share/dict/american-english"),
)
DICT_FILE = next((p for p in _DICT_CANDIDATES if p.exists()), _DICT_CANDIDATES[0])

# Slug tokens that are corporate-form noise, never the identity itself.
_CORP_SUFFIXES = frozenset(
    {
        "inc",
        "llc",
        "ltd",
        "limited",
        "corp",
        "corporation",
        "co",
        "company",
        "group",
        "holdings",
        "plc",
        "gmbh",
        "sa",
        "nv",
        "bv",
        "ag",
        "pte",
        "pty",
        "sdn",
        "bhd",
        "kk",
        "srl",
        "spa",
        "oy",
        "ab",
        "as",
        "the",
        "and",
    }
)


def _dictionary() -> set[str]:
    """Lowercased system dictionary; empty when the file is absent.

    An empty dictionary is NOT "stricter" — it is the noise filter switched off, and it
    fails in the one direction this roster cannot afford. Both filters that consult it are
    written as "drop the key when the words are ordinary", so with no words nothing is
    ordinary: an all-English name keys on every token. Measured: `the-first-state-bank`
    yields nothing on macOS (which ships `web2`) and `{'first', 'first state bank'}` on an
    ubuntu runner (which ships no `/usr/share/dict/words`) — `first` being exactly the kind
    of key the module docstring says no gate can fire on. A roster that cries wolf is one
    people learn to skip, so callers that PERSIST a roster must refuse to run without it;
    see `_require_dictionary`. Read-only callers degrade loudly rather than silently.
    """
    if not DICT_FILE.exists():
        print(
            f"warning: {DICT_FILE} is missing — the roster's English-word noise filter is "
            "OFF, so ordinary words will appear as keys. Install a wordlist "
            "(Debian/Ubuntu: `apt-get install wamerican`).",
            file=sys.stderr,
        )
        return set()
    return {
        w.strip().lower() for w in DICT_FILE.read_text(errors="ignore").splitlines() if w.strip()
    }


def load_third_party_allowed() -> set[str]:
    """`[third-party-allowed]` — names that are also legitimate vendors/ecosystem tools."""
    allowed: set[str] = set()
    section = None
    for raw in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section == "third-party-allowed":
            allowed.add(line.lower())
    return allowed


def has_tenant_data(root: Path = ROOT) -> bool:
    """Whether the account tree — the roster's main source — is present.

    Mode selection keys on THIS, never on "did derive_keys return anything". `content/` is
    gitignored and `profiles/` is tracked, so a CI checkout derives a handful of
    case-study names: non-empty, and a silently weaker gate than the committed digests.
    A gate that quietly degrades is worse than one that is absent.
    """
    return any(root.glob("content/*/accounts"))


def _raw_names(root: Path, words: set[str] | None = None) -> set[str]:
    """Every third-party name the tenant data records, before any noise filtering."""
    words = _dictionary() if words is None else words
    names: set[str] = set()

    for accounts in root.glob("content/*/accounts"):
        for folder in accounts.iterdir():
            if folder.is_dir() and not folder.name.startswith("."):
                names.add(folder.name.replace("-", " "))

    for path in root.glob("profiles/*/knowledge/outreach-case-studies.txt"):
        if "_template" in path.parts:
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                names.add(line)

    return names


def derive_keys(root: Path = ROOT) -> set[str]:
    """The match keys: lowercased, noise-filtered, allowlist-subtracted."""
    allowed = load_third_party_allowed()
    words = _dictionary()
    keys: set[str] = set()

    for name in _raw_names(root, words):
        tokens = [t for t in re.split(r"[^A-Za-z0-9]+", name.lower()) if t]
        tokens = [t for t in tokens if t not in _CORP_SUFFIXES]
        if not tokens:
            continue
        if len(tokens) > 1:
            # A multi-word name is distinctive as a phrase regardless of its parts, and
            # the phrase pattern spans separators, so it matches the slug form too
            # ("acme robotics" matches "acme-robotics").
            phrase = " ".join(tokens)
            if phrase not in allowed and not all(t in words for t in tokens):
                keys.add(phrase)
        head = tokens[0]
        if (
            len(head) >= MIN_TOKEN_LEN
            and head not in allowed
            and head not in words
            and not head.isdigit()
        ):
            keys.add(head)
    return keys


def digest(key: str) -> str:
    return hashlib.sha256((DIGEST_SALT + "\n" + key).encode()).hexdigest()[:32]


def load_digests() -> set[str]:
    if not DIGEST_FILE.exists():
        return set()
    return {
        line.strip()
        for line in DIGEST_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


def _require_dictionary() -> None:
    """Refuse to PERSIST a roster derived with the noise filter switched off.

    The digest is committed and CI enforces it, so a digest written on a machine with no
    `/usr/share/dict/words` would pin ordinary English words as third-party keys for
    everyone. Deriving without the dictionary stays allowed (it only warns) — writing the
    result down does not.
    """
    if not DICT_FILE.exists():
        raise SystemExit(
            f"refusing to write {DIGEST_FILE.name}: {DICT_FILE} is missing, so the "
            "English-word noise filter is off and ordinary words would be pinned as "
            "third-party keys. Install a wordlist (Debian/Ubuntu: `apt-get install "
            "wamerican`; macOS ships one) and re-run."
        )


def write_digest(root: Path = ROOT) -> int:
    _require_dictionary()
    keys = derive_keys(root)
    body = [
        "# Salted sha256 of every third-party name the tenant data records.",
        "# GENERATED by tests/lint/third_party_roster.py --write-digest — never hand-edit.",
        "#",
        "# The roster itself is PII and is not committed. CI has no content/ or profiles/,",
        "# so it enforces the rule against these digests; pre-commit and the export derive",
        "# the live roster instead. Regenerate whenever accounts or case studies change.",
        "",
    ]
    body.extend(sorted(digest(k) for k in keys))
    DIGEST_FILE.write_text("\n".join(body) + "\n", encoding="utf-8")
    return len(keys)


def matcher(keys: set[str]) -> re.Pattern[str] | None:
    """One word-bounded, case-insensitive alternation over every key.

    Word-bounded is the property that makes the roster safe to apply broadly: `archer`
    cannot match "researcher", `visa` cannot match "improvisation". Keys are sorted
    longest-first so a phrase wins over its own leading token in the reported finding.
    """
    if not keys:
        return None
    alts = "|".join(
        re.escape(k).replace(r"\ ", r"[\s_-]+") for k in sorted(keys, key=lambda k: (-len(k), k))
    )
    return re.compile(rf"(?<![\w-])({alts})(?![\w-])", re.IGNORECASE)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true", help="print the derived keys")
    ap.add_argument("--write-digest", action="store_true", help="regenerate the committed digest")
    ap.add_argument("--stats", action="store_true", help="how many names in, how many keys out")
    args = ap.parse_args(argv)

    if args.write_digest:
        n = write_digest()
        print(f"wrote {DIGEST_FILE.name}: {n} keys")
        return 0
    keys = derive_keys()
    if args.stats:
        raw = _raw_names(ROOT)
        print(f"raw names: {len(raw)}")
        print(f"match keys: {len(keys)}")
        print(f"allowlisted: {len(load_third_party_allowed())}")
        return 0
    if args.list:
        for k in sorted(keys):
            print(k)
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
