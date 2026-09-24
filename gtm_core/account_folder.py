"""CLI + shared function: the account folder a deliverable should be written to.

``gtm_core.slugify`` answers "what is the canonical slug for this name?". That was
never enough, because the same account arrives under different names: the ledger
says ``Kestrel Shipping Limited``, a campaign CSV says ``Kestrel Shipping``, an older
import minted the id ``tarnwick--co`` before the company field was cleaned to
``Tarnwick``. A skill that slugs whatever name it was handed and creates that folder
puts one account's dossier and its outreach pack in different directories. Confirmed real drift: on
2026-09-23 the dossier skill wrote to ``slug(company)`` while an outreach run wrote
next to the account's July pack or to its ledger id. Six accounts split in one run,
and about 100 more had split the same way earlier.

This module answers the question skills actually have: *which folder already
belongs to this account?* It is read-only. It resolves; the skill still creates
the folder when the answer is a new one.

Resolution, first match wins:

1. ``exact``: ``accounts/<slug(company)>/`` exists.
2. ``ledger``: ``latest.json`` rows for this account, matched by the slug of the
   name (or, failing that, by ``--domain``), name the folder. Every row carries
   two slugs for its account (the import-time ``id`` and ``slug(company)``), and
   either may be the folder that exists.
3. ``separator``: an existing folder that differs only in punctuation
   (``quorum-io`` vs ``quorumio``).
4. ``suffix``: an existing folder that differs only by a trailing legal suffix
   (``brindlecove`` vs ``brindlecove-limited``).
5. ``new``: nothing matches, so the answer is ``slug(company)``. The exception:
   when the ledger has no row for the account and an existing folder's name
   extends this one (``marrowgate`` vs ``marrowgate-technologies-inc``), the answer
   is ambiguous. Look-alikes that are different companies (``quillon`` vs
   ``quillon-leap-ai``) have exactly that shape, so no rung joins them silently.

Several candidates at any rung means ambiguity, and ambiguity exits 3 with the
candidates on stderr. Picking one would be the same silent split this module
exists to prevent. Passing ``--domain`` resolves most of it.

VPS invocation: python -m gtm_core.account_folder "<company>" --profile <p> [--domain <d>]

Prints the folder slug on stdout (a drop-in for ``gtm_core.slugify``) and the rung
that decided it on stderr.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .paths import _safe_segment
from .prospect_paths import accounts_dir
from .prospects_state import load_latest
from .slugify import slug

#: Trailing tokens that name a legal form, not a company. Deliberately closed and
#: short: ``bancorp``, ``bankshares``, ``financial`` and the like are real name
#: tokens that tell two companies apart, so they are not on it.
LEGAL_SUFFIXES = frozenset(
    {
        "inc",
        "incorporated",
        "corp",
        "corporation",
        "co",
        "company",
        "companies",
        "ltd",
        "limited",
        "plc",
        "llc",
        "lp",
        "llp",
        "holding",
        "holdings",
        "group",
        "pte",
        "pty",
        "gmbh",
        "ag",
        "sa",
        "nv",
        "bv",
    }
)

EXIT_AMBIGUOUS = 3


class AmbiguousFolder(Exception):
    """More than one existing folder could be this account."""

    def __init__(self, rung: str, candidates: list[str]):
        self.rung = rung
        self.candidates = candidates
        super().__init__(f"{rung}: {', '.join(candidates)}")


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s)


def _strip_suffix(s: str) -> str:
    parts = [p for p in s.split("-") if p]
    while len(parts) > 1 and parts[-1] in LEGAL_SUFFIXES:
        parts.pop()
    return "-".join(parts)


def _token_prefix(a: str, b: str) -> bool:
    """True when one hyphen-token sequence begins the other."""
    short, longer = sorted((a, b), key=len)
    return longer.startswith(short + "-")


def _existing(accounts: Path) -> dict[str, str]:
    """``{comparison key -> the folder name as it is on disk}``.

    The key is casefolded because every rung below compares against ``slug()`` output,
    which is lowercase, while a folder on disk need not be: a folder created by hand (or by
    a skill that predates this module) can carry capitals, and two such folders exist in the
    live tree. Comparing raw meant the account's OWN name missed its OWN folder and fell all
    the way through to the ``new`` rung — minting a second, lowercase folder for an account
    that already had one, which is the single failure this module exists to prevent. Found by
    `test_every_existing_folder_resolves_to_itself`, which is why that test runs on real data.

    The VALUE is the on-disk name, never the key: the caller is about to write a file into
    it, and a path that differs from the directory by case is a new directory on a
    case-sensitive filesystem.
    """
    if not accounts.is_dir():
        return {}
    out: dict[str, str] = {}
    for p in accounts.iterdir():
        if not p.is_dir():
            continue
        try:
            name = _safe_segment(p.name, "account folder")
        except ValueError:
            continue
        # First writer wins, and iteration is sorted, so the choice between two folders
        # differing only by case is deterministic rather than filesystem-order-dependent.
        out.setdefault(name.casefold(), name)
    return out


def _one(rung: str, found: set[str], names: dict[str, str] | None = None) -> str | None:
    """The single member of ``found``, or None. ``names`` maps a key to its on-disk folder,
    so an ambiguity is reported to the operator in the names they will actually see."""
    if len(found) > 1:
        raise AmbiguousFolder(rung, sorted((names or {}).get(f, f) for f in found))
    return next(iter(found), None)


def _ledger_slugs(items: list, canonical: str, domain: str) -> tuple[set[str], set[str]]:
    """Both slugs of every ledger row that is this account: ``(by_name, by_domain)``.

    Kept apart because a parent and a subsidiary can share a domain while being
    separate accounts, so a domain hit only counts when no row matched by name.
    """
    domain = domain.strip().lower()
    by_name: set[str] = set()
    by_domain: set[str] = set()
    for row in items:
        if not isinstance(row, dict):
            continue
        row_slugs = {s for s in (row.get("id") or "", slug(row.get("company") or "")) if s}
        if canonical in row_slugs:
            by_name |= row_slugs
        if domain and (row.get("domain") or "").strip().lower() == domain:
            by_domain |= row_slugs
    return by_name, by_domain


def resolve(
    company: str, profile: str, domain: str = "", content_root: Path | None = None
) -> tuple[str, str]:
    """Return ``(folder_slug, rung)`` for ``company``. Raises :class:`AmbiguousFolder`."""
    canonical = slug(company)
    if not canonical:
        raise ValueError("company name is empty")
    on_disk = _existing(accounts_dir(profile, content_root))
    existing = set(on_disk)

    if canonical in existing:
        return on_disk[canonical], "exact"

    items = load_latest(profile, content_root).get("items", [])
    by_name, by_domain = _ledger_slugs(items, canonical, domain)
    hit = _one("ledger", by_name & existing, on_disk)
    if hit:
        return on_disk[hit], "ledger"
    # A domain a parent shares with a subsidiary names several accounts. It decides alone
    # only when it names one; otherwise it narrows what the name rungs below may pick.
    domain_hits = by_domain & existing
    if len(domain_hits) == 1:
        return on_disk[next(iter(domain_hits))], "ledger"

    stem = _strip_suffix(canonical)
    for rung, found in (
        ("separator", {f for f in existing if _squash(f) == _squash(canonical)}),
        ("suffix", {f for f in existing if _strip_suffix(f) == stem}),
    ):
        hit = _one(rung, found, on_disk)
        if hit and domain_hits and hit not in domain_hits:
            raise AmbiguousFolder(rung, sorted(on_disk[f] for f in domain_hits | {hit}))
        if hit:
            return on_disk[hit], rung
    if domain_hits:
        raise AmbiguousFolder("ledger-domain", sorted(on_disk[f] for f in domain_hits))

    # When the ledger knows this account and none of its slugs has a folder, the account
    # is new. When the ledger does not know it, a folder whose name extends it token by
    # token (``marrowgate`` / ``marrowgate-technologies-inc``) is as likely the same
    # account as a different one (``quillon`` / ``quillon-leap-ai``), so a person decides.
    if not (by_name or by_domain):
        near = {f for f in existing if _token_prefix(stem, _strip_suffix(f))}
        if near:
            raise AmbiguousFolder("prefix", sorted(on_disk[f] for f in near))

    return canonical, "new"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m gtm_core.account_folder",
        description="Print the account folder slug a deliverable for COMPANY belongs in.",
    )
    ap.add_argument("company")
    ap.add_argument("--profile", required=True)
    ap.add_argument("--domain", default="", help="the account's web domain, when known")
    args = ap.parse_args(argv)
    try:
        folder, rung = resolve(args.company, args.profile, args.domain)
    except AmbiguousFolder as e:
        print(
            f"account_folder: ambiguous at {e.rung}: {', '.join(e.candidates)} — "
            "decide which folder is this account (or pass --domain); do not create a new one",
            file=sys.stderr,
        )
        return EXIT_AMBIGUOUS
    except ValueError as e:
        print(f"account_folder: {e}", file=sys.stderr)
        return 2
    print(folder)
    print(f"account_folder: {rung}: {folder}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
