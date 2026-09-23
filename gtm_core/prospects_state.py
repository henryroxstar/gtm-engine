"""Safe read/merge/write for ``content/<profile>/prospects/latest.json``.

`latest.json` is a **cumulative** dashboard-state file: it grows across every
prospect run and carries the operator's per-account ``status`` edits
(disqualified/replied/do-not-contact) made between runs. A blind full-file
overwrite (writing only the current run's items) silently destroys every prior
account and every status edit — a data-loss class this module exists to prevent.

Guarantees:
  * **Merge, never replace.** New items upsert by a precise identity key
    (:func:`_identity_key` — domain-first, then id, then a non-lossy company
    name); an account already present keeps its operator-edited ``status`` (and
    other sticky fields), and every field the incoming item leaves out or blank
    (:mod:`gtm_core.prospects_merge` — a thin re-emit never erases research).
  * **Never drop an existing account.** The merged result always starts from
    *every* existing item and only appends/updates — even a pre-existing
    duplicate key is retained, never silently collapsed.
  * **Snapshot before every write.** The current file is copied to
    ``prospects/.snapshots/latest-<UTC-timestamp>.json`` first, so any bad write
    is one ``restore`` away. Snapshots are pruned to the most recent N.
  * **Shrink tripwire.** A write that would drop the item count below what's on
    disk is refused unless ``allow_shrink=True``. By construction a merge can't
    shrink, so this is a regression tripwire that catches any future change which
    reintroduces dropping.
  * **Atomic write.** tmp file + ``os.replace`` so a crash mid-write can't leave
    a truncated file.

stdlib-only, to match the rest of gtm_core.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from gtm_core.paths import _safe_segment, resolve_content_root
from gtm_core.prospects_lock import ledger_lock, serialised
from gtm_core.prospects_merge import AccountMatcher, merge_onto

SNAPSHOT_DIRNAME = ".snapshots"
SNAPSHOT_KEEP = 30
# Fields that belong to the operator / dashboard and must survive a re-merge.
STICKY_FIELDS = ("status", "priority", "notes", "owner", "last_touched")

#: The ledger's whole status vocabulary. ``new``/``disqualified``/``contact-resolved``/
#: ``contact-defective`` are the values live today (confirmed via a ``Counter`` over
#: ``content/<profile>/prospects/latest.json``); ``replied``/``do-not-contact``/
#: ``closed-lost`` are added for the lifecycle PS6 wires up. :func:`set_status` refuses
#: anything outside this set rather than let a typo'd status silently sit in the file —
#: e.g. it is exactly what the ``engaged-account`` hold trigger
#: (``gtm_core.lanes.context.DEFAULT_ENGAGED_STATUSES``) reads.
LEDGER_STATUSES = frozenset(
    {
        "new",
        "contact-resolved",
        "contact-defective",
        "disqualified",
        "replied",
        "meeting",
        "engaged",
        "in-conversation",
        "customer",
        "partner",
        "do-not-contact",
        "closed-lost",
    }
)

#: Statuses a positive reply must never override. Each is a deliberate, already-decided
#: exit from the pipeline (an eval disqualification, an opt-out, a closed-lost call) — a
#: reply arriving afterward does not undo it.
RETIRED_STATUSES = frozenset({"disqualified", "do-not-contact", "closed-lost"})

#: The account's durable, opaque identity, stamped here and carried by every derived
#: view. Sticky by the same rule as an operator's status edit — an incoming item never
#: overwrites one, so the id an account was first given is the id it keeps.
#:
#: **Why an opaque id rather than one more derivation.** Account identity was implemented
#: six times across this pipeline with mutually non-derivable semantics: the domain-first
#: key here, a suffix-stripping normaliser, an org token, an account-folder slug, a person
#: slug, and a lowercased email. Every one is a reasonable answer to "what do we call this
#: account"; none of them can answer "is this the same account as that one" across two
#: files. Most of the state-loss incidents of the last two months are that gap — one
#: prospect's status, suppression, and research living under keys that cannot find each
#: other.
#:
#: The derivations stay: they are how an id is *assigned* to a row that has none. What
#: changes is that they stop being the join.
ACCOUNT_ID_FIELD = "account_id"


def _utc_stamp() -> str:
    # microsecond resolution so back-to-back snapshots never collide on filename
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S-%fZ")


def _norm(company: str) -> str:
    n = company.lower().strip()
    n = re.sub(
        r"\b(inc|corp|corporation|ltd|llc|plc|group|holdings|company|co|limited|pte)\b",
        "",
        n,
    )
    n = re.sub(r"[^a-z0-9]+", " ", n).strip()
    return n


def _identity_key(item: dict) -> str:
    """The single source of truth for account identity across merge + dedup.

    Precise and non-lossy — unlike :func:`_norm`, it never strips corporate
    suffixes or non-ASCII characters, so genuinely-different companies can't
    collide and a non-Latin name can't collapse to an empty key.

    Precedence:
      * ``domain`` (the true unique business identifier) → ``"d:<domain>"``;
      * else ``id`` → ``"i:<id>"``;
      * else a whitespace-collapsed, lowercased company name (suffixes and
        non-ASCII preserved) → ``"c:<company>"``;
      * else ``""`` (caller must not drop an empty-key item — append it).

    Domain-first means two different companies can never merge (they can't share
    a domain). An existing domainless entry re-emitted *with* a domain still
    resolves, because the merge indexes and looks up every derivable key — see
    :func:`_identity_keys`.
    """
    keys = _identity_keys(item)
    return keys[0] if keys else ""


def _identity_keys(item: dict) -> list[str]:
    """Every key an item is reachable under, most-authoritative first.

    :func:`_identity_key` picks one key per item, which is the right answer for
    "what is this account called" and the wrong one for "have I seen it before":
    an account first stored under ``i:<id>`` and later re-emitted with a domain
    hashes to ``d:<domain>``, misses a single-key index, and is appended as a
    duplicate — stranding the operator's status on the orphan, invisibly, since
    the shrink tripwire only watches for the file getting smaller.

    Indexing an item under all of its keys, and looking an incoming item up under
    all of its keys, closes that gap in both directions (a key gained *or* lost).
    Two accounts can still never merge by accident: they would have to collide on
    a domain, an id, or an exact company name to share a bucket at all — and an
    exact-name collision is no longer enough on its own. ``AccountMatcher.find``
    refuses a name-only match whose domain differs from the candidate's, because two
    legal entities can share one name (a subsidiary and its parent, or two unrelated
    firms). Only a ``d:``/``i:``/``a:`` key — an identifier rather than a name — can
    merge rows that carry different domains.
    """
    keys: list[str] = []
    domain = str(item.get("domain") or "").strip().lower()
    if domain:
        keys.append(f"d:{domain}")
    cid = str(item.get("id") or "").strip().lower()
    if cid:
        keys.append(f"i:{cid}")
    company = " ".join(str(item.get("company") or "").split()).lower()
    if company:
        keys.append(f"c:{company}")
    # The stamped id, last in precedence because it is assigned rather than observed —
    # but present so a caller holding only an account_id (a derived CSV row, a writeback)
    # can address the account without re-deriving a key from fields it may not carry.
    account_id = str(item.get(ACCOUNT_ID_FIELD) or "").strip().lower()
    if account_id:
        keys.append(f"a:{account_id}")
    return keys


def latest_path(profile: str, content_root: Path | None = None) -> Path:
    root = content_root or resolve_content_root()
    # ``profile`` reaches here straight from --profile; guard it as a bare segment
    # before it is joined (CLAUDE.md tenant boundary). Every other path in this
    # module derives from this one.
    return root / _safe_segment(profile, "profile") / "prospects" / "latest.json"


def _snapshot_dir(profile: str, content_root: Path | None = None) -> Path:
    return latest_path(profile, content_root).parent / SNAPSHOT_DIRNAME


def load_latest(profile: str, content_root: Path | None = None) -> dict:
    """Return the current latest.json as a dict, or an empty skeleton if absent.

    Raises on a present-but-corrupt file rather than returning an empty skeleton —
    silently treating a corrupt cumulative file as empty is exactly how a merge
    would then "shrink" it to just the current run.
    """
    p = latest_path(profile, content_root)
    if not p.exists():
        return {"kind": "prospects", "profile": profile, "items": []}
    with p.open(encoding="utf-8") as f:
        data = json.load(f)
    # Fail loud on a valid-JSON-but-wrong-shape file rather than letting a later
    # .get()/iteration throw an opaque error mid-merge (same intent as raising on
    # a corrupt file: a malformed cumulative file must never be treated as empty).
    if not isinstance(data, dict):
        raise ValueError(f"latest.json must be a JSON object, got {type(data).__name__}: {p}")
    if not isinstance(data.get("items", []), list):
        raise ValueError(f'latest.json "items" must be a JSON array: {p}')
    return data


def snapshot(profile: str, content_root: Path | None = None) -> Path | None:
    """Copy the current latest.json into the snapshots dir. No-op if absent."""
    src = latest_path(profile, content_root)
    if not src.exists():
        return None
    snap_dir = _snapshot_dir(profile, content_root)
    snap_dir.mkdir(parents=True, exist_ok=True)
    dest = snap_dir / f"latest-{_utc_stamp()}.json"
    shutil.copy2(src, dest)
    _prune_snapshots(snap_dir)
    return dest


def _prune_snapshots(snap_dir: Path, keep: int = SNAPSHOT_KEEP) -> None:
    snaps = sorted(snap_dir.glob("latest-*.json"))
    for old in snaps[:-keep]:
        old.unlink(missing_ok=True)


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".latest-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@serialised(latest_path, create=True)
def upsert_latest(
    profile: str,
    new_items: list[dict],
    source_run: str,
    *,
    generated_at: str | None = None,
    allow_shrink: bool = False,
    content_root: Path | None = None,
    new_account_defaults: Callable[[dict], dict] | None = None,
    on_merged: Callable[[list[dict]], None] | None = None,
) -> dict:
    """Merge ``new_items`` into latest.json by :func:`_identity_key`.

    Merge-only by construction: the result starts from *every* existing account
    and only appends/updates — it can never drop a prior account, so a full-file
    overwrite is impossible through this path. Existing accounts keep their
    STICKY_FIELDS (operator status edits, etc.); other fields refresh only where the
    incoming item is populated (:func:`merge_onto`), so ``new_account_defaults`` — the
    fill for a NEW account — never reaches one. A keyless incoming item is appended
    (never dropped). Snapshots the current file first, writes atomically. Refuses
    to shrink the item count unless ``allow_shrink=True``. Returns a summary dict.

    ``on_merged`` is how a caller learns WHICH account each item landed on: it is called
    once, before anything is written, with a list parallel to ``new_items`` — element ``i``
    is a copy of the merged ledger row ``new_items[i]`` belongs to (``account_id`` already
    stamped). An exception from it propagates and leaves the ledger untouched.
    """
    current = load_latest(profile, content_root)
    existing_items = current.get("items", [])

    # Start from ALL existing items — none is ever dropped, even a pre-existing duplicate
    # key (the matcher merges into the first occurrence and leaves the duplicate listed).
    result_items = [dict(it) for it in existing_items]
    matcher = AccountMatcher(result_items, _identity_keys)

    added, updated, keyless_appended, ids_stamped = 0, 0, 0, 0
    landed_at: list[int] = []  # new_items[i] landed on result_items[landed_at[i]]
    for item in new_items:
        # Un-keyable (e.g. a pure-non-ASCII name with no domain/id): append rather than
        # silently drop, and report it — an invisible append is how duplicates accumulate.
        keyless = not _identity_keys(item)
        pos, keep = (None, ()) if keyless else matcher.find(item)
        is_new = pos is None
        if is_new:
            result_items.append(new_account_defaults(item) if new_account_defaults else dict(item))
            pos = len(result_items) - 1
            added += 1
            keyless_appended += keyless
        else:
            result_items[pos] = merge_onto(result_items[pos], item, sticky=STICKY_FIELDS, keep=keep)
            updated += 1
        matcher.note(pos, new=is_new)
        landed_at.append(pos)

    # Stamp an account_id on anything that lacks one — including pre-existing rows, so a
    # file written before this field existed gains ids on its next merge rather than
    # needing a migration. Never overwrites: the id is the account's identity, and
    # reassigning it would break every join that already quotes it.
    timestamp_str = generated_at or datetime.now(UTC).isoformat()
    for item in result_items:
        if not str(item.get(ACCOUNT_ID_FIELD) or "").strip():
            item[ACCOUNT_ID_FIELD] = f"a-{uuid.uuid4().hex[:10]}"
            ids_stamped += 1
        if not str(item.get("added_at") or "").strip():
            item["added_at"] = timestamp_str

    if on_merged is not None:
        on_merged([dict(result_items[pos]) for pos in landed_at])  # raising here writes nothing

    if not allow_shrink and len(result_items) < len(existing_items):
        raise ValueError(
            f"refusing to shrink latest.json: {len(existing_items)} -> "
            f"{len(result_items)} items; pass allow_shrink=True to override"
        )

    snap = snapshot(profile, content_root)

    out = dict(current)
    out["kind"] = "prospects"
    out["profile"] = profile
    out["source_run"] = source_run
    out["generated_at"] = generated_at or datetime.now(UTC).isoformat()
    out["items"] = result_items

    _atomic_write(latest_path(profile, content_root), out)

    return {
        "profile": profile,
        "source_run": source_run,
        "existing": len(existing_items),
        "added": added,
        "updated": updated,
        "keyless_appended": keyless_appended,
        "ids_stamped": ids_stamped,
        "total": len(result_items),
        "snapshot": str(snap) if snap else None,
    }


@serialised(latest_path)
def set_status(
    profile: str,
    updates: dict[str, str],
    *,
    reason: str = "",
    source: str = "",
    content_root: Path | None = None,
) -> dict:
    """Set ``status`` on existing accounts, keyed by :func:`_identity_key`.

    **Why this is not :func:`upsert_latest`.** That path treats ``status`` as a STICKY
    field: an existing account keeps its prior status and the incoming one is discarded.
    That rule is correct and load-bearing — it is what stops a routine prospect run from
    reverting an operator's hand-made edit. But it also means a merge can never *deliver*
    a status change, so pushing an eval disqualification through it would report success
    and change nothing. A deliberate, operator-approved status write needs its own verb
    that says so, rather than a flag that quietly weakens the merge contract for everyone.

    ``updates`` maps an identity key to a new status. Accounts not named are untouched;
    a key that matches no account is returned under ``unmatched`` rather than created —
    inventing an account from a writeback would be the same data-fabrication risk in the
    other direction. Snapshots first, writes atomically, and can never change the item
    count.

    Raises ``ValueError`` for any status outside :data:`LEDGER_STATUSES` — this is the
    ledger's whole vocabulary, so a typo here would otherwise sit in the file silently
    and be invisible to every reader (the dashboard, the engaged-account hold trigger).
    ``disqualified_reason``/``disqualified_by`` are stamped only when the new status IS
    ``disqualified`` — writing them for any other status would misleadingly imply an
    account was disqualified when it was not.
    """
    bad = {s for s in updates.values() if s not in LEDGER_STATUSES}
    if bad:
        raise ValueError(
            f"unknown status {sorted(bad)!r}; must be one of {sorted(LEDGER_STATUSES)}"
        )

    current = load_latest(profile, content_root)
    items = [dict(it) for it in current.get("items", [])]

    by_key: dict[str, list[int]] = {}
    for pos, item in enumerate(items):
        for key in _identity_keys(item):
            by_key.setdefault(key, []).append(pos)

    changed, unchanged, unmatched = [], [], []
    for key, status in updates.items():
        positions = by_key.get(key)
        if not positions:
            unmatched.append(key)
            continue
        # One account, one verdict — even where a pre-existing duplicate key put it
        # at several positions. Every position is still written (they are the same
        # account, so leaving one stale would re-introduce the disagreement), but
        # the caller is told about accounts, not rows.
        wrote = False
        for pos in positions:
            if items[pos].get("status") == status:
                continue
            items[pos]["status"] = status
            if status == "disqualified":
                if reason:
                    items[pos]["disqualified_reason"] = reason
                if source:
                    items[pos]["disqualified_by"] = source
            wrote = True
        (changed if wrote else unchanged).append(key)

    if changed:
        snap = snapshot(profile, content_root)
        out = dict(current)
        out["items"] = items
        # A status write is a write: leaving the prior stamp makes the file claim it
        # has not changed since the last merge.
        out["generated_at"] = datetime.now(UTC).isoformat()
        _atomic_write(latest_path(profile, content_root), out)
    else:
        snap = None

    return {
        "profile": profile,
        "changed": len(changed),
        "unchanged": len(unchanged),
        "unmatched": sorted(set(unmatched)),
        "total": len(items),
        "snapshot": str(snap) if snap else None,
    }


def mark_replied(
    profile: str,
    emails: list[str] | set[str],
    *,
    source: str,
    content_root: Path | None = None,
) -> dict:
    """Set ``status: replied`` in ``latest.json`` for the accounts that own these emails.

    Implementation and candidate-key resolution across sources (ledger, ready-to-load,
    master-list, cells.toml) live in :func:`gtm_core.reply_mark.mark_replied`.
    """
    from .reply_mark import mark_replied as _mark_replied

    return _mark_replied(profile, emails, source=source, content_root=content_root)


@serialised(latest_path)
def mutate_account(
    profile: str,
    account: str,
    updates: dict[str, str],
    *,
    content_root: Path | None = None,
) -> dict:
    """Mutate arbitrary fields on an account by its slug, id, or domain.

    This provides chat-native CRUD capability (e.g. dropping a zombie row).
    Snapshots first, writes atomically, and returns a summary.
    """
    current = load_latest(profile, content_root)
    items = [dict(it) for it in current.get("items", [])]

    changed = False
    found = False

    target_keys = {
        f"i:{account.lower()}",
        f"d:{account.lower()}",
        f"a:{account.lower()}",
        f"c:{account.lower()}",
    }

    for pos, item in enumerate(items):
        item_keys = set(_identity_keys(item))
        # Match by explicit ID, or any of the identity keys matching the account string
        if (
            item.get("id") == account
            or item.get(ACCOUNT_ID_FIELD) == account
            or (target_keys & item_keys)
        ):
            found = True
            for k, v in updates.items():
                if items[pos].get(k) != v:
                    items[pos][k] = v
                    changed = True

    if not found:
        return {"profile": profile, "account": account, "status": "not_found", "changed": False}

    if changed:
        snap = snapshot(profile, content_root)
        out = dict(current)
        out["items"] = items
        out["generated_at"] = datetime.now(UTC).isoformat()
        _atomic_write(latest_path(profile, content_root), out)
    else:
        snap = None

    return {
        "profile": profile,
        "account": account,
        "status": "ok",
        "changed": changed,
        "snapshot": str(snap) if snap else None,
    }


def restore(
    profile: str, snapshot_file: str | None = None, content_root: Path | None = None
) -> Path:
    """Restore latest.json from a snapshot (newest by default)."""
    snap_dir = _snapshot_dir(profile, content_root)
    if snapshot_file:
        src = Path(snapshot_file)
        if not src.is_absolute():
            src = snap_dir / snapshot_file
    else:
        snaps = sorted(snap_dir.glob("latest-*.json"))
        if not snaps:
            raise FileNotFoundError(f"no snapshots in {snap_dir}")
        src = snaps[-1]
    dest = latest_path(profile, content_root)
    with ledger_lock(dest):
        # snapshot the (possibly bad) current file first, so restore is itself reversible
        snapshot(profile, content_root)
        shutil.copy2(src, dest)
    return src


def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m gtm_core.prospects_state")
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser(
        "merge", help="merge an items JSON file into latest.json (snapshot + shrink-guard)"
    )
    m.add_argument("--profile", required=True)
    m.add_argument("--items", required=True, help="path to a JSON array of item objects")
    m.add_argument("--source-run", required=True)
    m.add_argument("--generated-at", default=None)
    m.add_argument(
        "--allow-shrink",
        action="store_true",
        help="override the shrink tripwire (only if the merge would legitimately reduce the item count)",
    )

    s = sub.add_parser("snapshot", help="take a manual snapshot of latest.json")
    s.add_argument("--profile", required=True)

    r = sub.add_parser("restore", help="restore latest.json from a snapshot (newest by default)")
    r.add_argument("--profile", required=True)
    r.add_argument("--from", dest="snapshot_file", default=None)

    ls = sub.add_parser("list-snapshots", help="list available snapshots")
    ls.add_argument("--profile", required=True)

    mut = sub.add_parser("mutate", help="mutate arbitrary fields on a specific account")
    mut.add_argument("--profile", required=True)
    mut.add_argument("--account", required=True, help="account slug, id, or domain")
    mut.add_argument(
        "--set", action="append", required=True, help="key=value to set (e.g., verdict=drop)"
    )
    mut.add_argument(
        "--reason", default="", help="reason, sets verdict_reason if verdict is mutated"
    )

    args = ap.parse_args(argv)

    if args.cmd == "merge":
        items = json.loads(Path(args.items).read_text(encoding="utf-8"))
        if not isinstance(items, list):
            print("ERROR: --items must be a JSON array", file=sys.stderr)
            return 2
        summary = upsert_latest(
            args.profile,
            items,
            args.source_run,
            generated_at=args.generated_at,
            allow_shrink=args.allow_shrink,
        )
        print(json.dumps(summary, indent=2))
        return 0

    if args.cmd == "snapshot":
        snap = snapshot(args.profile)
        print(snap or "(no latest.json to snapshot)")
        return 0

    if args.cmd == "restore":
        src = restore(args.profile, args.snapshot_file)
        print(f"restored latest.json from {src}")
        return 0

    if args.cmd == "list-snapshots":
        snap_dir = _snapshot_dir(args.profile)
        for s in sorted(snap_dir.glob("latest-*.json")):
            print(s)
        return 0

    if args.cmd == "mutate":
        updates = {}
        for s in args.set:
            if "=" not in s:
                print(f"ERROR: --set must be key=value, got {s}", file=sys.stderr)
                return 2
            k, v = s.split("=", 1)
            updates[k.strip()] = v.strip()
        if args.reason and "verdict" in updates:
            updates["verdict_reason"] = args.reason
        summary = mutate_account(args.profile, args.account, updates)
        print(json.dumps(summary, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
