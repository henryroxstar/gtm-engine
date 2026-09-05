"""Sequencer replies → the outcomes ledger, attributed to a learning cell.

This is the producer half of the closed loop. :mod:`gtm_core.outcomes` has been the
capture tier since the knowledge-lifecycle work and :mod:`gtm_core.gtm_distill` the
learning tier, but the email path never fed either — ``outcomes.jsonl`` was an unfed
sink, so the distiller correlated an empty file and every "which persona wins?"
question was unanswerable no matter how many emails shipped.

**Provider reality, verified 2026-08-18 — three things the design has to work around.**

1. ``get_outcomes`` does *not* return results. It returns the **taxonomy**: eight
   default categories (Interested, Not Interested, Meeting Booked, Out of Office,
   Closed, Not Now, Do Not Contact, Uncategorized), each with a sentiment. Both this
   module's earlier design note and ``PENDING.md`` described it as a results feed;
   it is not.
2. ``get_email_list`` carries the per-reply row (thread id, ``categoryId``,
   ``sentiment``, ``isRepliedByProspect``) but **no prospect address** — only a
   display name. ``get_email_thread`` carries ``to``/``fromEmail``, which is the only
   reliable cell join key. Hence two stages: list to find replies, thread to identify
   who replied. Replies are rare, so stage two stays cheap.
3. The two endpoints use **different id spaces** for the same categories:
   ``get_outcomes`` returns opaque string ids while ``get_email_list`` returns a small
   integer. The integer appears to be the 1-based position in the default taxonomy,
   which is an *inference*, so every mapping is cross-checked against the item's own
   ``sentiment`` and any disagreement is reported ``unresolved`` rather than guessed.
   A gate that reads only the field it polices is not a gate.

MCP-free, like :mod:`gtm_core.email_compliance`: the agent fetches the payloads over
MCP and pipes them in as JSON, so the judging is identical every run and this module
adds no egress surface.

CLI::

    python -m gtm_core.sequencer_outcomes --profile P \\
        --emails emails.json --threads threads.json --taxonomy taxonomy.json
    # ... same, plus --apply to actually append to outcomes.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gtm_core.cells import email_index, lane_index
from gtm_core.outcomes import append_outcome, read_outcomes
from gtm_core.paths import resolve_content_root

#: Provider category name → the canonical tag written onto the outcome row.
#: Keys are the provider's own default names so the operator tags in ONE place — the
#: Saleshandy inbox they already work — instead of maintaining a second vocabulary here.
CATEGORY_TO_TAG = {
    "interested": "positive:interested",
    "meeting booked": "positive:meeting",
    "closed": "positive:closed",
    "not interested": "objection:not-interested",
    "not now": "objection:timing",
    "do not contact": "objection:do-not-contact",
    "out of office": "noise:out-of-office",
    "uncategorized": "unclassified",
}

#: Diagnostic categories the eight defaults cannot express, and which the PMF question
#: actually turns on. The provider supports custom outcomes but exposes no create tool,
#: so these are a one-time operator setup step in the Saleshandy UI; until they exist,
#: "they already solve this with a vendor" is indistinguishable from "not interested" —
#: a positioning finding collapsed into a rejection.
RECOMMENDED_CUSTOM = (
    "Already solved (vendor)",
    "Wrong person",
    "Price / budget",
)

#: Outcome name for the operator's classification row. Carries ``value: 0`` so it
#: annotates a reply without double-counting it — the ledger is append-only, so a
#: classification is a new row, never an edit to the reply row it describes.
CLASSIFIED = "reply_classified"


def build_category_map(taxonomy: dict | list) -> dict[int, dict]:
    """``categoryId -> {name, sentiment}`` from a ``get_outcomes`` payload.

    The integer id is positional (1-based) — an inference, which is why every lookup
    in :func:`plan_rows` re-checks it against the item's own sentiment.
    """
    items = taxonomy
    if isinstance(taxonomy, dict):
        items = taxonomy.get("payload", taxonomy)
        if isinstance(items, dict):
            items = items.get("items", [])
    return {
        i + 1: {"name": str(it.get("name", "")), "sentiment": str(it.get("sentiment", ""))}
        for i, it in enumerate(items or [])
        if isinstance(it, dict)
    }


def _items(payload: dict | list) -> list[dict]:
    """Unwrap ``{payload: {items: [...]}}`` / ``{payload: [...]}`` / a bare list."""
    node = payload
    if isinstance(node, dict):
        node = node.get("payload", node)
    if isinstance(node, dict):
        node = node.get("items", [])
    return [x for x in (node or []) if isinstance(x, dict)]


def prospect_email_by_thread(threads: dict | list) -> dict[str, str]:
    """``thread_id -> prospect email`` from ``get_email_thread`` payloads.

    The provider labels the prospect side of every message itself: ``toProspectId`` on
    our outbound, ``fromProspectId`` on their reply, carrying the same id both ways. We
    read that label rather than inferring which address is ours — inference would
    absorb a CC'd third party into "us" and make it invisible, and an address we treat
    as ours is an address whose replies never reach a cell.

    A thread naming two different prospects is skipped, not resolved arbitrarily.
    """
    out: dict[str, str] = {}
    payloads = threads if isinstance(threads, list) else [threads]
    for entry in payloads:
        if not isinstance(entry, dict):
            continue
        tid = entry.get("emailThreadId") or entry.get("thread_id") or ""
        seen: set[str] = set()
        for e in _items(entry):
            if e.get("fromProspectId"):
                addr = str(e.get("fromEmail", "") or "").lower()
                if addr:
                    seen.add(addr)
            if e.get("toProspectId"):
                for addr in e.get("to") or []:
                    addr = str(addr or "").lower()
                    if addr:
                        seen.add(addr)
        if tid and len(seen) == 1:
            out[tid] = seen.pop()
    return out


#: Cell tag for a reply that genuinely happened but cannot be attributed to a group.
#: Dropping such a reply undercounts the campaign's own reply total — the one number the
#: run exists to measure — so it is recorded with the gap made explicit instead.
UNATTRIBUTED = "unattributed"


def plan_rows(
    emails: dict | list,
    threads: dict | list,
    taxonomy: dict | list,
    email_to_cell: dict[str, str],
    *,
    include_unattributed: bool = False,
    email_to_lane: dict[str, str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Outcome records for every prospect reply, plus the rows that could not be
    attributed. Pure — no I/O — so the mapping is unit-testable against real payloads.
    """
    cat_map = build_category_map(taxonomy)
    thread_email = prospect_email_by_thread(threads)

    records: list[dict] = []
    unresolved: list[dict] = []

    for item in _items(emails):
        if not item.get("isRepliedByProspect"):
            continue
        tid = str(item.get("emailThreadId") or "")
        seq = str(item.get("sequenceId") or "")
        sentiment = str(item.get("sentiment") or "")
        cat_id = item.get("categoryId")

        cat = cat_map.get(cat_id if isinstance(cat_id, int) else -1)
        # Cross-check the positional inference against the item's own sentiment.
        if cat and sentiment and cat["sentiment"].lower() != sentiment.lower():
            cat = None
        tag = CATEGORY_TO_TAG.get(cat["name"].lower()) if cat else None

        email = thread_email.get(tid, "")
        cell = email_to_cell.get(email) if email else None

        reason = None
        if not email:
            reason = "no prospect address — thread payload missing or ambiguous"
        elif not cell:
            reason = f"{email} is not in any enrolment list in cells.toml"
        if reason:
            unresolved.append(
                {
                    "thread_id": tid,
                    "sequence_id": seq,
                    "prospect": item.get("prospectName", ""),
                    "reason": reason,
                }
            )
            if not include_unattributed:
                continue
            cell = UNATTRIBUTED

        tags = [f"cell:{cell}", f"seq:{seq}"]
        lane = (email_to_lane or {}).get(email) if email else None
        if lane:
            tags.append(f"lane:{lane}")
        if tag:
            tags.append(tag)
        else:
            tags.append("unclassified")

        records.append(
            {
                "channel": "email",
                "outcome": "reply",
                "ref": tid,
                "value": 1,
                "ts": item.get("sentAt") or None,
                "tags": tags,
                "meta": {
                    "sequence_id": seq,
                    # The recipient, so a judge-record join (`eval_calibration.reconcile`) can
                    # resolve the thread `ref` to a person without re-fetching the thread.
                    "prospect_email": email,
                    "sentiment": sentiment,
                    "category_id": cat_id,
                    "category": cat["name"] if cat else None,
                    "category_resolved": bool(cat),
                    "subject": item.get("subject", ""),
                    "unsubscribed": bool(item.get("isUnsubscribed")),
                },
            }
        )
        # A positive category is also a distinct, rate-bearing outcome so the cell
        # table can separate "replied" from "replied and it was good news".
        if tag == "positive:interested":
            records.append({**records[-1], "outcome": "positive_reply"})
        elif tag == "positive:meeting":
            records.append({**records[-1], "outcome": "meeting"})
        # An unsubscribe is its own outcome (never a reply): the ledger, not only
        # history.jsonl, then knows who said stop.
        if item.get("isUnsubscribed") or tag == "objection:do-not-contact":
            records.append({**records[-1], "outcome": "opt_out", "value": 1})

    return records, unresolved


def reconcile(planned: list[dict], existing: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split planned rows into (new replies, re-classifications) against the ledger.

    Ingest is re-run as the operator works the inbox, and the ledger is append-only, so
    neither "append every time" (double-counts one reply per run) nor "skip if seen"
    (freezes the first, usually ``Uncategorized``, reading) is right. A reply is
    appended once, keyed on its thread ``ref``; a later category change appends a
    zero-valued :data:`CLASSIFIED` row instead, which records the new judgement without
    moving the reply count.
    """
    seen_refs = {r.get("ref") for r in existing if r.get("outcome") == "reply"}
    latest_tag: dict[str, str] = {}
    for r in existing:
        ref = r.get("ref")
        if not ref:
            continue
        for t in r.get("tags") or []:
            if isinstance(t, str) and (
                t.startswith(("objection:", "positive:", "noise:")) or t == "unclassified"
            ):
                latest_tag[ref] = t

    new_rows: list[dict] = []
    reclass: list[dict] = []
    for row in planned:
        ref = row.get("ref")
        if ref not in seen_refs:
            new_rows.append(row)
            continue
        if row.get("outcome") != "reply":
            continue  # derived positive/meeting rows ride with their parent
        tag = next(
            (
                t
                for t in row.get("tags", [])
                if t.startswith(("objection:", "positive:", "noise:")) or t == "unclassified"
            ),
            None,
        )
        if tag and latest_tag.get(ref) != tag:
            reclass.append(
                {
                    "channel": "email",
                    "outcome": CLASSIFIED,
                    "ref": ref,
                    "value": 0,
                    "tags": [t for t in row.get("tags", []) if not t.startswith("seq:")],
                    "meta": {**row.get("meta", {}), "supersedes_tag": latest_tag.get(ref)},
                }
            )
    return new_rows, reclass


def missing_custom_categories(taxonomy: dict | list) -> list[str]:
    """Which :data:`RECOMMENDED_CUSTOM` diagnostics the tenant has not created yet."""
    have = {str(i.get("name", "")).strip().lower() for i in _items(taxonomy)}
    return [c for c in RECOMMENDED_CUSTOM if c.lower() not in have]


def _load(path: str | None):
    if not path:
        return []
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Map sequencer replies onto cell-tagged outcome rows.")
    ap.add_argument("--profile", required=True)
    ap.add_argument("--content-root", default=None)
    ap.add_argument("--emails", required=True, help="get_email_list payload (JSON file)")
    ap.add_argument("--threads", default=None, help="get_email_thread payloads (JSON array)")
    ap.add_argument("--taxonomy", default=None, help="get_outcomes payload (JSON file)")
    ap.add_argument(
        "--apply", action="store_true", help="append to outcomes.jsonl (default: dry run)"
    )
    ap.add_argument(
        "--include-unattributed",
        action="store_true",
        help="also record replies that cannot be traced to a group, tagged "
        "cell:unattributed — keeps the reply TOTAL honest while leaving the gap visible",
    )
    args = ap.parse_args(argv)

    root = Path(args.content_root) if args.content_root else resolve_content_root()
    taxonomy = _load(args.taxonomy)
    planned, unresolved = plan_rows(
        _load(args.emails),
        _load(args.threads),
        taxonomy,
        email_index(args.profile, root),
        include_unattributed=args.include_unattributed,
        email_to_lane=lane_index(args.profile, root),
    )
    records, reclass = reconcile(planned, read_outcomes(root, args.profile))

    for r in records + reclass:
        print(("APPEND " if args.apply else "DRY    ") + json.dumps(r, ensure_ascii=False))
    if args.apply:
        for r in records + reclass:
            append_outcome(root, args.profile, {k: v for k, v in r.items() if v is not None})

    print(
        f"\n{len(records)} new row(s), {len(reclass)} re-classification(s), "
        f"{len(unresolved)} unattributed"
    )
    for u in unresolved:
        print(f"  ! {u['prospect'] or u['thread_id']}: {u['reason']}")
    if taxonomy:
        missing = missing_custom_categories(taxonomy)
        if missing:
            print(
                "\nnote: these diagnostic categories do not exist in the sequencer, so the "
                "replies they describe collapse into a coarser one — create them in the "
                "provider UI (no API):\n  " + "\n  ".join(missing)
            )
    # Unattributed replies are a real defect (a reply that teaches nothing), so they
    # are a non-zero exit even when everything else worked.
    return 1 if unresolved else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
