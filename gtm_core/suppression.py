"""The durable local suppression ledger.

Why this exists
---------------
``ready-to-load.csv`` and ``.pool/master-list.csv`` are **build outputs** of
:mod:`gtm_core.prospects_consolidate`. Annotating them with a ``suppression`` column looks like
it works and silently loses the annotation the next time the pool is rebuilt -- which is exactly
what happened on 2026-08-11: 24 already-contacted rows, 3 opt-outs and 31 wrong-role rows were
marked, and a rebuild an hour later returned every one of them to the sendable pool.

The provider-side Global DNC list is the right home for a genuine **opt-out**, and only for that:
it is global, permanent, and the MCP surface exposes no removal tool, so putting a merely
already-contacted person on it forfeits them forever. Everything that is "exclude from the next
send, but not forever" needs a home that survives a rebuild without being a life sentence.

This module is that home. ``suppression.csv`` is hand-editable, append-only in practice, and is
the **source of truth**; the ``suppression`` column on any derived CSV is a cache of it that
:func:`apply` re-derives and :func:`verify` proves. A gate that only lives in a regenerated file
is not a gate.

Reasons are open strings, but two are load-bearing:

``dnc-optout``
    A real opt-out. MUST also be on the provider DNC list. :func:`reconcile_dnc` proves that
    against a payload piped in from the provider's own DNC read tools -- so this is a check,
    not a promise. Until 2026-09-21 this docstring said the connector had no read-back and
    left it to "the operator confirms once"; that was wrong on both counts. The read-back
    existed (``list_dnc_lists`` -> ``get_dnc_items_by_id``, which
    ``gtm_core.prospects_consolidate.paths.dnc_cache_path`` had already been documenting as
    the cache's source), and the once-confirmation did not happen: three real opt-outs sat on
    the provider list for six weeks with no ``dnc-optout`` row here at all. A rule whose
    enforcement is "someone remembers" is not enforced.
``contacted-*`` / ``role-mismatch``
    Local-only exclusions. These must NOT be pushed to the provider DNC list.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import fsio
from .fsio import snapshot_file

__all__ = [
    "Suppression",
    "LedgerIndex",
    "load",
    "load_index",
    "apply",
    "append",
    "verify",
    "reconcile_dnc",
    "normalize_dnc_payload",
    "migrate",
    "person_key",
    "email_key",
    "row_person_key",
    "main",
    "COLUMNS",
    "LEGACY_COLUMNS",
    "PROVIDER_DNC_REASONS",
    "EVAL_DISQUALIFIED",
    "EVAL_WRONG_PERSON",
]

#: The ledger's columns. ``name``/``company_domain`` were added 2026-08-27 to carry the
#: person-level key; a ledger written before that has :data:`LEGACY_COLUMNS` and still
#: loads — the two new fields simply come back empty and the row keeps its address key.
COLUMNS = ("email", "name", "company_domain", "reason", "date", "note")
LEGACY_COLUMNS = ("email", "reason", "date", "note")
#: Reasons that must also exist on the provider-side DNC list.
PROVIDER_DNC_REASONS = frozenset({"dnc-optout"})

#: Written by ``gtm_core.eval_writeback`` when a human eval label says this account is not
#: a fit. Deliberately NOT in :data:`PROVIDER_DNC_REASONS`: a fit judgment is not a legal
#: do-not-contact. The provider's Global DNC list is global, permanent, and has no removal
#: tool — putting "we decided they were the wrong buyer this quarter" on it forfeits the
#: company forever, and would push a private commercial judgment to a third party.
EVAL_DISQUALIFIED = "eval-disqualified"
#: Same shape, person-level: the label said this individual is not the right contact. The
#: company may still be right, so this suppresses the address and never the account.
EVAL_WRONG_PERSON = "eval-wrong-person"


@dataclass(frozen=True)
class Suppression:
    email: str
    reason: str
    date: str = ""
    note: str = ""
    #: The person, for the address-independent key. Optional: a row backfilled from an
    #: address alone keeps working on its email key.
    name: str = ""
    company_domain: str = ""

    @property
    def person(self) -> str:
        return person_key(self.name, self.company_domain)


def _fold_name(name: str) -> str:
    """Case-, spacing- and diacritic-insensitive form of a person's name."""
    v = unicodedata.normalize("NFKD", name or "")
    v = "".join(ch for ch in v if not unicodedata.combining(ch))
    return " ".join(v.lower().replace(".", " ").split())


def person_key(name: str, company_domain: str) -> str:
    """``p:<folded name>@<domain>`` — the address-independent identity of a contact.

    Empty unless BOTH halves are known. A name alone is not an identity (two people
    share one), and a domain alone is a company, not a person; guessing from half the
    pair would suppress the wrong person, which is worse than missing one.
    """
    folded = _fold_name(name)
    domain = (company_domain or "").strip().lower().lstrip("@")
    return f"p:{folded}@{domain}" if folded and domain else ""


def email_key(email: str) -> str:
    e = (email or "").strip().lower()
    return f"e:{e}" if e else ""


def row_person_key(row: dict) -> str:
    """Person key for a pool row, which spells the name as ``first``/``last``."""
    name = (row.get("name") or "").strip() or " ".join(
        p for p in ((row.get("first") or "").strip(), (row.get("last") or "").strip()) if p
    )
    return person_key(name, row.get("company_domain") or "")


class LedgerIndex:
    """Every ledger entry, reachable by person key and by address.

    Person-first, address-second. An address-keyed exclusion is only as durable as the
    provider's guess at the address format: a contact suppressed as
    ``firstname.lastname@<domain>`` returned from a later re-sourcing as
    ``firstname@<domain>``, hashed to a different key, and was enrolled again. The person
    key survives that; the address key stays live because a backfill cannot resolve every
    historical row to a name.
    """

    __slots__ = ("by_email", "by_person")

    def __init__(self, entries: Iterable[Suppression] = ()):
        self.by_email: dict[str, Suppression] = {}
        self.by_person: dict[str, Suppression] = {}
        for entry in entries:
            self.add(entry)

    def add(self, entry: Suppression) -> None:
        if k := email_key(entry.email):
            self.by_email.setdefault(k, entry)
        if k := entry.person:
            self.by_person.setdefault(k, entry)

    def match(self, row: dict) -> Suppression | None:
        """The ledger entry covering this row, or None. Person key wins."""
        if (k := row_person_key(row)) and (hit := self.by_person.get(k)):
            return hit
        return self.by_email.get(email_key(row.get("email") or ""))

    def covers(self, entry: Suppression) -> bool:
        """Is this person already held, under either key?"""
        return bool(
            (entry.person and entry.person in self.by_person)
            or email_key(entry.email) in self.by_email
        )

    def __len__(self) -> int:
        return len(self.by_email) or len(self.by_person)

    def __bool__(self) -> bool:
        return bool(self.by_email or self.by_person)


def _read_entries(path: Path) -> list[Suppression]:
    if not path.exists():
        return []
    out: list[Suppression] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            email = (row.get("email") or "").strip().lower()
            if email.startswith("#"):
                continue
            entry = Suppression(
                email=email,
                reason=(row.get("reason") or "").strip(),
                date=(row.get("date") or "").strip(),
                note=(row.get("note") or "").strip(),
                name=(row.get("name") or "").strip(),
                company_domain=(row.get("company_domain") or "").strip().lower(),
            )
            # A row with neither key is a comment or a blank line, not an exclusion.
            if email or entry.person:
                out.append(entry)
    return out


def load(path: Path) -> dict[str, Suppression]:
    """Read the ledger, keyed by lower-cased email. Missing file is an empty ledger.

    Kept address-keyed for the callers that want exactly that (the compliance preflight
    consumes the keys as a set of addresses). New call sites that need to *match a row*
    want :func:`load_index`, which also honours the person key.
    """
    return {e.email: e for e in _read_entries(path) if e.email}


def load_index(path: Path) -> LedgerIndex:
    """Read the ledger into a :class:`LedgerIndex` — person key first, address fallback."""
    return LedgerIndex(_read_entries(path))


def _as_index(ledger: LedgerIndex | dict[str, Suppression]) -> LedgerIndex:
    """Accept either shape, so existing callers passing ``load()`` keep working."""
    return ledger if isinstance(ledger, LedgerIndex) else LedgerIndex(ledger.values())


def apply(target: Path, ledger: dict[str, Suppression]) -> tuple[int, int]:
    """Re-derive the ``suppression`` cache columns on a build output.

    Idempotent: running twice produces a byte-identical file. Additive: a row not in the ledger
    keeps whatever it had, so a hand-set value is never clobbered by a ledger that has not caught
    up yet. Returns ``(marked, total_rows)``.
    """
    with target.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    for col in ("suppression", "suppression_date"):
        if col not in fieldnames:
            fieldnames.append(col)

    index = _as_index(ledger)
    marked = 0
    for row in rows:
        hit = index.match(row)
        if hit:
            row["suppression"] = hit.reason
            row["suppression_date"] = hit.date
            marked += 1

    # Atomic: this rewrites a send list, so a crash mid-write must leave the previous
    # complete file rather than a truncated one.
    fsio.atomic_write_csv(target, fieldnames, rows)
    return marked, len(rows)


def append(path: Path, entries: Sequence[Suppression]) -> tuple[int, int]:
    """Append suppressions to the ledger, skipping addresses already present.

    Returns ``(added, skipped)``. Idempotent by construction: an email already in the
    ledger is left exactly as it is, so re-running a writeback cannot overwrite a genuine
    ``dnc-optout`` with a weaker reason — a downgrade that would be invisible in a CSV
    diff and catastrophic in effect.
    """
    index = load_index(path)
    fresh: list[Suppression] = []
    for entry in entries:
        # `covers` asks under BOTH keys, so the same person arriving at a second address
        # is recognised rather than appended as a new exclusion.
        if index.covers(entry):
            continue
        fresh.append(entry)
        index.add(entry)
    skipped = len(entries) - len(fresh)
    if not fresh:
        return 0, skipped
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_header = _header_of(path)
    is_new = existing_header is None
    # Append under whatever header the file already has: rewriting a legacy 4-column
    # ledger to add two columns would rewrite rows this call was not asked to touch.
    header = list(existing_header or COLUMNS)
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
        if is_new:
            writer.writeheader()
        for entry in fresh:
            writer.writerow(
                {
                    "email": entry.email.strip().lower(),
                    "reason": entry.reason,
                    "date": entry.date,
                    "note": entry.note,
                    "name": entry.name,
                    "company_domain": entry.company_domain,
                }
            )
    return len(fresh), skipped


def _header_of(path: Path) -> list[str] | None:
    """The ledger's existing column order, or None if the file is absent/blank."""
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return None
    with path.open(newline="", encoding="utf-8") as fh:
        return csv.DictReader(fh).fieldnames or None


def verify(target: Path, ledger: dict[str, Suppression]) -> list[str]:
    """Findings where a build output disagrees with the ledger.

    This is the guard that would have caught the 2026-08-11 loss the moment it happened, rather
    than one send later. A row present in both the ledger and the target, but not marked
    suppressed in the target, is a person about to be emailed again.
    """
    index = _as_index(ledger)
    if not target.exists():
        return [f"target-missing: {target.name}"]
    with target.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    if rows and "suppression" not in rows[0]:
        return [
            f"suppression-column-missing: {target.name} has no `suppression` column at all — "
            f"the file was rebuilt and every local exclusion was dropped "
            f"({len(index)} in the ledger)."
        ]

    findings = []
    for row in rows:
        hit = index.match(row)
        if hit and not (row.get("suppression") or "").strip():
            email = (row.get("email") or "").strip().lower()
            findings.append(
                f"suppressed-row-sendable: {target.name}: {email} is suppressed in the ledger "
                f"({hit.reason}) but is sendable in this file"
            )
    return findings


def _norm_addr(value: object) -> str:
    return str(value or "").strip().lower()


def _norm_domain(value: object) -> str:
    """Fold a domain to the form an email address's domain part would take.

    Strips the `@`/`.` a provider export puts in front of a domain entry, and a `www.`
    host label: `www` is a conventional host, never an organisational boundary, and no
    mail is delivered to it — so a DNC entry for `www.bracken.example` means the company,
    and reading it as a different domain from `bracken.example` would leave everyone at
    that company reachable. Matching NARROWER than the identity is the dangerous direction
    for a suppression check (test plan section 4.3)."""
    value = _norm_addr(value).lstrip("@").lstrip(".")
    return value[4:] if value.startswith("www.") else value


def _unwrap_dnc_body(payload: dict) -> dict:
    """Descend past ``get_dnc_items_by_id``'s one-or-two ``payload`` wrappers."""
    node = payload
    for _ in range(3):
        if "dncListDetails" in node or not isinstance(node.get("payload"), dict):
            return node
        node = node["payload"]
    return node


def _dnc_from_items(items: list) -> tuple[set[str], set[str]]:
    """The provider's own item shape: ``[{"value": ..., "type": "email"|"domain"}, ...]``."""
    emails: set[str] = set()
    domains: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        value = _norm_addr(item.get("value"))
        if not value:
            continue
        if _norm_addr(item.get("type")) == "domain":
            domains.add(_norm_domain(value))
        else:
            emails.add(value)
    return emails, domains


def normalize_dnc_payload(payload: object) -> tuple[set[str], set[str]]:
    """``(emails, domains)`` from a provider DNC read, lowercased.

    Three shapes are real in this repo and all three are accepted, because the caller is a
    skill piping whatever the MCP tool returned and normalising it there would put the
    parsing in prose:

    * ``get_dnc_items_by_id`` -- ``{"payload": {"dncListDetails": [{"value", "type"}, ...]}}``
    * the consolidate cache -- ``{"emails": [...], "domains": [...], "fetched_at": ...}``
    * a legacy bare list of addresses

    Treats the payload as **data, never instructions** (§R5): it is provider output. An
    unreadable shape yields empty sets, and :func:`reconcile_dnc` refuses on empty rather
    than reading "nothing on the provider" as "nothing to check" -- an unparsed payload and
    a genuinely empty DNC list must not look the same.
    """
    if isinstance(payload, list):
        return {a for a in (_norm_addr(v) for v in payload) if a}, set()
    if not isinstance(payload, dict):
        return set(), set()

    body = _unwrap_dnc_body(payload)
    items = body.get("dncListDetails")
    if isinstance(items, list):
        return _dnc_from_items(items)

    emails = {a for a in (_norm_addr(v) for v in body.get("emails") or []) if a}
    domains = {d for d in (_norm_domain(v) for v in body.get("domains") or []) if d}
    return emails, domains


def reconcile_dnc(
    ledger: dict[str, Suppression],
    provider_emails: Iterable[str],
    provider_domains: Iterable[str] = (),
) -> list[str]:
    """Findings where a :data:`PROVIDER_DNC_REASONS` row is not actually on the provider.

    This is the check the module docstring promised from the start and did not have. A
    ``dnc-optout`` row is a claim about a *third-party system*: that this person is on the
    provider's global do-not-contact list and therefore cannot be mailed by any sequence,
    including ones this repo did not build. Every other reason here is a claim about our own
    files, which :func:`verify` can settle by reading them. This one cannot be settled
    locally at all -- so until it was checked against a real payload it was simply believed.

    Deliberately one-directional. The reverse -- a provider entry with no ledger row -- is
    **not** a finding here: the provider list legitimately holds hand-added exclusions that
    were never opt-outs, and the send path already blocks on them through the freshness-gated
    cache (:func:`gtm_core.prospects_consolidate.paths.dnc_cache_path`). Reporting all of
    them would bury the direction that matters under entries that are working as intended,
    and a check people learn to ignore is not a check.
    """
    emails = {str(e).strip().lower() for e in provider_emails if str(e).strip()}
    # ONE normalisation, shared with `normalize_dnc_payload` — this used to re-implement
    # it inline, which is how two rules for one fact drift apart.
    domains = {d for d in (_norm_domain(x) for x in provider_domains) if d}
    claimed = sorted(
        (key, row)
        for key, row in ledger.items()
        if (row.reason or "").strip().lower() in PROVIDER_DNC_REASONS
    )

    if not emails and not domains:
        if not claimed:
            return []
        return [
            "provider-dnc-empty: the payload carried no DNC entries at all, so the "
            f"{len(claimed)} ledger row(s) claiming provider-side suppression could not be "
            "checked. An unparsed payload and an empty DNC list look identical here — "
            "refusing rather than reporting a clean reconciliation."
        ]

    findings = []
    for _key, row in claimed:
        addr = (row.email or "").strip().lower()
        if not addr:
            continue
        domain = _norm_domain(addr.rpartition("@")[2])
        if addr in emails or (domain and domain in domains):
            continue
        findings.append(
            f"dnc-optout-not-on-provider: {addr} is recorded here as {row.reason} — a "
            "recipient-initiated do-not-contact — but is absent from the provider's DNC "
            "list. Any sequence outside this repo's ledger can still reach them."
        )
    return findings


def migrate(ledger_path: Path, source: Path) -> tuple[int, int, int]:
    """Backfill ``name``/``company_domain`` onto ledger rows by joining ``source`` on email.

    Returns ``(matched, unmatched, total)``. The ledger predates the person key, so it
    carries addresses and nothing else; the pooled list carries ``first``/``last``/
    ``company_domain`` for the same people. Joining the two is the only way to recover a
    key for historical rows.

    Idempotent — a row that already has both fields is left alone — and lossless: an
    address the pool no longer knows keeps its email key rather than being dropped, so a
    migration can never *reduce* what the ledger suppresses. Snapshots before writing.
    """
    entries = _read_entries(ledger_path)
    if not entries:
        return 0, 0, 0

    by_email: dict[str, dict] = {}
    if source.exists():
        with source.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                email = (row.get("email") or "").strip().lower()
                if email:
                    by_email.setdefault(email, row)

    matched = 0
    out: list[Suppression] = []
    for entry in entries:
        if entry.name and entry.company_domain:
            out.append(entry)
            matched += 1
            continue
        row = by_email.get(entry.email)
        name = ""
        domain = ""
        if row:
            name = (row.get("name") or "").strip() or " ".join(
                p for p in ((row.get("first") or "").strip(), (row.get("last") or "").strip()) if p
            )
            domain = (row.get("company_domain") or "").strip().lower()
        if name and domain:
            matched += 1
            out.append(
                Suppression(
                    email=entry.email,
                    reason=entry.reason,
                    date=entry.date,
                    note=entry.note,
                    name=name,
                    company_domain=domain,
                )
            )
        else:
            out.append(entry)

    snapshot_file(
        ledger_path,
        ledger_path.parent / ".snapshots",
        prefix="suppression",
        keep=30,
    )
    fsio.atomic_write_csv(
        ledger_path,
        COLUMNS,
        [
            {
                "email": e.email,
                "name": e.name,
                "company_domain": e.company_domain,
                "reason": e.reason,
                "date": e.date,
                "note": e.note,
            }
            for e in out
        ],
    )
    return matched, len(out) - matched, len(out)


def _cli_migrate(args) -> int:
    matched, unmatched, total = migrate(args.ledger, args.source)
    if not total:
        print(f"ledger {args.ledger} is empty or missing — nothing to migrate", file=sys.stderr)
        return 1
    print(f"migrated {args.ledger}")
    print(f"  {matched}/{total} row(s) now carry a person key")
    if unmatched:
        print(
            f"  {unmatched} row(s) kept an address-only key — not in {args.source.name}. "
            f"They still suppress by address; nothing was lost."
        )
    return 0


def _cli_apply(args) -> int:
    ledger = load_index(args.ledger)
    if not ledger:
        print(
            f"suppression ledger {args.ledger} is empty or missing — refusing to run",
            file=sys.stderr,
        )
        return 1
    print(f"ledger: {len(ledger)} suppression(s)")
    for target in args.target:
        marked, total = apply(target, ledger)
        print(f"  {target.name}: {marked} marked / {total} rows")
    return 0


def _cli_verify(args) -> int:
    ledger = load_index(args.ledger)
    findings = []
    for target in args.target:
        findings.extend(verify(target, ledger))
    if findings:
        print(f"FAIL — {len(findings)} finding(s):")
        for f in findings[:25]:
            print(f"  - {f}")
        if len(findings) > 25:
            print(f"  ... and {len(findings) - 25} more")
        return 1
    print(f"PASS — {len(ledger)} ledger entries honoured across {len(args.target)} file(s)")
    return 0


def _cli_reconcile_dnc(args) -> int:
    raw = sys.stdin.read().strip()
    if not raw:
        print(
            "FAIL — no payload on stdin. Pipe the provider's DNC read in, e.g. the JSON from "
            "`list_dnc_lists` -> `get_dnc_items_by_id`. This command performs no network I/O "
            "of its own (§R6): the skill calls the MCP tool and pipes the result here.",
            file=sys.stderr,
        )
        return 2
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"FAIL — payload is not JSON: {exc}", file=sys.stderr)
        return 2

    emails, domains = normalize_dnc_payload(payload)
    ledger = load(args.ledger)
    findings = reconcile_dnc(ledger, emails, domains)
    claimed = sum(
        1 for r in ledger.values() if (r.reason or "").strip().lower() in PROVIDER_DNC_REASONS
    )
    if findings:
        print(f"FAIL — {len(findings)} finding(s):")
        for f in findings:
            print(f"  - {f}")
        return 1
    print(
        f"PASS — all {claimed} provider-DNC row(s) in the ledger are on the provider list "
        f"({len(emails)} email(s), {len(domains)} domain(s) read)"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="gtm_core.suppression",
        description="Durable local suppression ledger: apply it to, or verify it against, "
        "the regenerated pool CSVs.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn, helptext in (
        ("apply", _cli_apply, "re-derive the suppression columns on build outputs"),
        ("verify", _cli_verify, "fail if a build output has lost a ledger suppression"),
    ):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("--ledger", required=True, type=Path)
        s.add_argument("--target", required=True, type=Path, nargs="+")
        s.set_defaults(func=fn)

    r = sub.add_parser(
        "reconcile-dnc",
        help="fail if a dnc-optout row is not actually on the provider's DNC list "
        "(reads the provider payload as JSON on stdin; performs no network I/O)",
    )
    r.add_argument("--ledger", required=True, type=Path)
    r.set_defaults(func=_cli_reconcile_dnc)

    m = sub.add_parser(
        "migrate",
        help="backfill name/company_domain onto ledger rows, so a person stays suppressed "
        "across a change of address format",
    )
    m.add_argument("--ledger", required=True, type=Path)
    m.add_argument(
        "--source",
        required=True,
        type=Path,
        help="pooled list to join on email (it carries first/last/company_domain)",
    )
    m.set_defaults(func=_cli_migrate)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
