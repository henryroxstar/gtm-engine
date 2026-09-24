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

A tenant whose `content/<tenant>/` is a SYMLINK to external storage is skipped — see
`_local_account_trees`. Storage is not source, and a lint that globs through the symlink makes
a commit hook depend on a file provider being mounted. The residual is that a name appearing
ONLY in such a tenant is not a derived key; the committed digest still carries it if it was
ever derived (`write_digest` is a union), and beyond that it falls to `carve-preflight` and
the human identity read.

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
  * a name's LEADING token is also a key when it is >= MIN_TOKEN_LEN and not ordinary
    English, because that is what a slug or a comment abbreviates a company to. "Ordinary"
    is `is_ordinary_word`, which stems before it consults the dictionary: the system list
    is a 1934 wordlist of BASE forms, so a bare membership test called `decisions`,
    `funding`, `circles` and `interactions` identities and fired on 409 lines of ordinary
    prose. The leading token of a multi-word name is often geography or a common noun
    instead ("<city> <bank>", "<plural noun> <suffix>"); those, and the British spellings
    and modern coinages no stemmer reaches (`cyber`), land in `[third-party-allowed]`
    under their own reason — measured here, that is ~20 entries and it buys the five
    companies whose folder name is multi-word;
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
    the identity read, all of which run where content/ and profiles/ exist. `pii_check`
    applies this ALONGSIDE the digests, never instead of them.
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


# English inflections the system wordlist does not carry. `web2` is a 1934 list of BASE
# forms: it has `decision`, `fund`, `circle` and `interaction` but none of their plurals or
# gerunds, so an account named with one keyed on an ordinary English word and the gate fired
# on prose 409 times — `agent/permissions.py`'s "returns one of three decisions", a dict key
# in the signal taxonomy, Venn-diagram guidance. Stripping a suffix before the dictionary
# check is what makes "is this an ordinary word?" mean what the docstring above says it
# means. Measured 2026-09-21 on 1,321 keys: 28 keys drop (2.1%), 13 accounts lose their only
# key — and all 13 are all-English names, which is the residual this module already documents
# and leaves to the human identity read, not new blindness.
#
# Deliberately crude, and deliberately over-generating: each candidate is only ever CHECKED
# against the wordlist, never emitted, so a wrong stem costs nothing and a missed one costs a
# false positive. No stemmer library — this file is stdlib-only because pre-commit and the
# export gate both run it.
def _stems(token: str) -> set[str]:
    """Candidate base forms of an inflected English token."""
    out: set[str] = set()
    if token.endswith("ies") and len(token) > 4:
        out.add(token[:-3] + "y")
    if token.endswith("es") and len(token) > 3:
        out |= {token[:-2], token[:-1]}
    if token.endswith("s") and not token.endswith("ss") and len(token) > 3:
        out.add(token[:-1])
    if token.endswith("ing") and len(token) > 5:
        out |= {token[:-3], token[:-3] + "e"}
        if len(token) > 6 and token[-4] == token[-5]:
            out.add(token[:-4])  # `running` -> `run`
    if token.endswith("ed") and len(token) > 4:
        out |= {token[:-2], token[:-1]}
        if len(token) > 5 and token[-3] == token[-4]:
            out.add(token[:-3])
    return out


def is_ordinary_word(token: str, words: set[str]) -> bool:
    """Whether `token` is ordinary English — directly, or as an inflection of a base form.

    The one predicate both filters consult. `derive_keys` drops a head token it calls True,
    and the residual test explains a missed account with it; if they ever disagree, the test
    reports accounts as unexplained that the filter deliberately dropped. Empty `words`
    (no wordlist installed) returns False for everything, which is the filter switched off —
    see `_dictionary`.
    """
    return token in words or any(s in words for s in _stems(token))


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
    return any(_local_account_trees(root))


def _local_account_trees(root: Path = ROOT) -> list[Path]:
    """Every ``content/<tenant>/accounts`` that is LOCAL to this checkout.

    A tenant's ``content/<tenant>/`` may be a symlink to external storage — the operator
    keeps one tenant's account and content data in a cloud-synced folder. Those bytes are
    storage, not source, and **no lint in this repo may reach into them**: doing so makes a
    commit hook depend on a file provider being mounted and on whatever OS permission that
    provider sits behind, which is not a property of the code being committed. Until
    2026-09-24 this globbed straight through the symlink and a commit died on a
    ``PermissionError`` from the cloud mount — a gate failing for a reason that had nothing
    to do with the diff.

    The consequence is stated rather than hidden: a name appearing ONLY in an external
    tenant's account folders is not a derived key, so it falls to the human identity read
    exactly like the all-English-name residual this module already documents. Every other
    tenant is local and still derives normally.
    """
    return [
        accounts
        for accounts in sorted(root.glob("content/*/accounts"))
        if not accounts.parent.is_symlink() and not accounts.is_symlink()
    ]


def _raw_names(root: Path, words: set[str] | None = None) -> set[str]:
    """Every third-party name the tenant data records, before any noise filtering."""
    words = _dictionary() if words is None else words
    names: set[str] = set()

    for accounts in _local_account_trees(root):
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
            and not is_ordinary_word(head, words)
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
    """Regenerate the committed digest as a UNION with what is already there.

    Append-only by construction, and that is the whole design. The digest is a list of
    names never to write into source, and a name does not stop being someone's the day its
    account folder is renamed, retired, or moved to storage this checkout cannot read.
    Three things shrink the live derivation without shrinking the truth: a folder
    consolidation (the 2026-09-23 merge retired ~29 alternate spellings), a tenant whose
    tree is a symlink to external storage (:func:`_local_account_trees`), and a machine that
    simply has fewer tenants checked out. A plain rewrite would silently hand all three back
    as permission to name those companies again — the leak this file exists to stop.

    The cost of keeping a stale key is that the gate also refuses a name nobody uses any
    more, which is the harmless direction. `--prune` is deliberately absent: removing a key
    is a decision to allow a name, and that belongs in `[third-party-allowed]` with a
    written reason, one name at a time.
    """
    _require_dictionary()
    keys = {digest(k) for k in derive_keys(root)} | load_digests()
    body = [
        "# Salted sha256 of every third-party name the tenant data records.",
        "# GENERATED by tests/lint/third_party_roster.py --write-digest — never hand-edit.",
        "#",
        "# The roster itself is PII and is not committed. CI has no content/ or profiles/,",
        "# so it enforces the rule against these digests; pre-commit and the export derive",
        "# the live roster instead. Regenerate whenever accounts or case studies change.",
        "#",
        "# UNION, never a replacement: this file only ever grows. A name that leaves the",
        "# live derivation — a renamed folder, a tenant on external storage, a smaller",
        "# checkout — has not stopped being a real company. See write_digest's docstring.",
        "",
    ]
    body.extend(sorted(keys))
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
