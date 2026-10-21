"""How one incoming prospect item lands on the account it re-describes.

Split out of :mod:`gtm_core.prospects_state` (which calls it from ``upsert_latest``) so the
merge RULE is readable on its own, apart from the snapshot/atomic-write plumbing.

The rule is field-wise, because the writers are not peers. A research pass writes an
account's signal record, domain and verdict once; every later discovery run re-emits the same
account from a *thinner* source — the skill's own "minimal item" carries seven fields. When the
merge replaced the row wholesale, that thin re-emit erased the research: the why-now and its
provenance, the domain, the verified email. And with the domain gone the account no longer
matched its own do-not-contact status downstream. So:

  * a field the incoming item does not carry, or carries blank, never overwrites a value;
  * ``verdict`` + ``verdict_reason`` move as one researcher-owned pair, and only when the
    incoming item states a verdict;
  * ``id`` / ``account_id`` / ``added_at`` are the account's, not the run's.

stdlib-only and import-free, so :mod:`gtm_core.prospects_state` can depend on it without a cycle.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

#: Identity and provenance an account keeps once it has them. ``id`` is here because it
#: names the account's folder: a ledger written before the canonical slugifier holds
#: hand-made ids, and letting a later run's re-derived slug replace one orphans the folder.
PINNED_FIELDS = ("id", "account_id", "added_at")


def is_blank(value: Any) -> bool:
    """Whether ``value`` says nothing. ``0`` and ``False`` are answers, not blanks — a run
    that measured heat 0 has to be able to record it."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return isinstance(value, (list, tuple, dict, set)) and not value


#: Mirrors ``score_prospects.BELOW_THRESHOLD_REASON`` — the one reason only the scorer writes.
_SCORER_DROP_REASON = "below publish threshold"


def _scorer_drop_lifted(prior: dict, incoming: dict) -> bool:
    return (
        str(prior.get("verdict") or "").strip().lower() == "drop"
        and str(prior.get("verdict_reason") or "").strip().lower() == _SCORER_DROP_REASON
        and is_blank(incoming.get("verdict"))
        and str(incoming.get("tier") or "").strip().upper() in ("A", "B")
    )


def _merge_verdict(prior: dict, incoming: dict, merged: dict) -> None:
    """Keep ``verdict_reason`` attached to the verdict it explains."""
    if _scorer_drop_lifted(prior, incoming):
        # The scorer's OWN "below threshold" drop, and the row has since scored into a
        # published tier: that drop described a score that no longer exists. A researcher's
        # drop (any other reason) is never lifted this way.
        merged["verdict"] = ""
        merged["verdict_reason"] = ""
        if str(merged.get("lane") or "").strip().lower() == "excluded":
            merged["lane"] = ""
    elif is_blank(incoming.get("verdict")):
        # No verdict stated: a stray reason must not re-caption the researcher's verdict.
        if "verdict_reason" in prior:
            merged["verdict_reason"] = prior["verdict_reason"]
        else:
            merged.pop("verdict_reason", None)
    elif incoming["verdict"] != prior.get("verdict") and is_blank(incoming.get("verdict_reason")):
        merged["verdict_reason"] = ""  # the old reason explained the OLD verdict


def merge_onto(
    prior: dict,
    incoming: dict,
    *,
    sticky: tuple[str, ...] = (),
    keep: tuple[str, ...] = (),
) -> dict:
    """``prior`` updated with every populated field of ``incoming``.

    ``sticky`` fields keep an operator-set value (anything but blank/``"new"``); ``keep``
    fields, like :data:`PINNED_FIELDS`, keep any populated prior value.
    """
    merged = dict(prior)
    held = (*PINNED_FIELDS, *keep)
    for field, value in incoming.items():
        if is_blank(value):
            continue
        if field in held and not is_blank(prior.get(field)):
            continue
        if field in sticky and prior.get(field) not in (None, "", "new"):
            continue
        merged[field] = value
    _merge_verdict(prior, incoming, merged)
    return merged


def _domain(item: dict) -> str:
    return str(item.get("domain") or "").strip().lower()


#: The ONLY words the name fallback may look past: a legal form says how a company is
#: registered, never which company it is. ``group`` / ``holdings`` / ``company`` / ``co`` are
#: deliberately absent — "Zephyrine Holdings" and "Zephyrine Group" are two companies, and
#: equating them put one account's contact, why-now and score onto the other's ledger row.
LEGAL_FORMS = frozenset(
    "inc incorporated llc ltd limited corp corporation plc pte gmbh ag sa bv pty".split()
)


def legal_name_key(company: str) -> str:
    """``company`` without case, punctuation and TRAILING legal-form tokens.

    "Contoso Freight, Inc." and "contoso freight" share a key; a name that is nothing but a
    legal form keeps it, and non-ASCII survives (a CJK name is its own key, not an empty one).
    """
    name = re.sub(r"(?<=\w)\.", "", str(company or "").lower())  # "S.A." / "Inc." -> "sa" / "inc"
    tokens = re.sub(r"[\W_]+", " ", name).split()
    while len(tokens) > 1 and tokens[-1] in LEGAL_FORMS:
        tokens.pop()
    return " ".join(tokens)


class AccountMatcher:
    """Where an incoming item's account already sits in ``rows`` — the merge's one index.

    A precise identity key (``keys_of``) wins. :func:`legal_name_key` is the fallback for an
    item every precise key missed: a provider's legal name ("… , Inc.") and the cleaned name a
    later step emits are one account, and without this they became two ledger rows with the
    operator's status stranded on one of them.

    The fallback only ever ADDS a match and never guesses: a candidate whose domain differs
    from the item's is a different company, and two surviving candidates are an ambiguity,
    appended as new rather than merged into whichever came first. It also recognises without
    renaming — older pooled rows still join on the exact name the ledger holds, so a
    fallback match keeps its ``company`` (the ``keep`` half of :meth:`find`'s answer).
    """

    def __init__(self, rows: list[dict], keys_of: Callable[[dict], list[str]]) -> None:
        self.rows = rows
        self._keys_of = keys_of
        self._index: dict[str, int] = {}
        self._names: dict[str, list[int]] = {}
        for pos in range(len(rows)):
            self.note(pos, new=True)

    def note(self, pos: int, *, new: bool = False) -> None:
        """(Re-)index ``rows[pos]`` under every key it now carries, so a key gained on this
        run is reachable by the next item. First occurrence wins — a pre-existing duplicate
        stays in the list but is never the merge target."""
        for key in self._keys_of(self.rows[pos]):
            self._index.setdefault(key, pos)
        name = legal_name_key(self.rows[pos].get("company") or "")
        if new and name:  # an empty key matches nothing
            self._names.setdefault(name, []).append(pos)

    def find(self, item: dict) -> tuple[int | None, tuple[str, ...]]:
        """``(position, fields the account keeps)`` — ``(None, ())`` for a new account."""
        domain = _domain(item)
        for key in self._keys_of(item):
            pos = self._index.get(key)
            if pos is None:
                continue
            # A ``c:<company>`` hit rests on a NAME and nothing else. Two legal entities
            # can share an exact name — a subsidiary and its parent, or two unrelated
            # firms — so it gets the SAME domain guard the legal-name fallback below
            # already applies: a candidate whose domain differs is a different company.
            # Without it, two rows with one name and two domains merged and a domain was
            # silently discarded; merging onto an existing row also carried the
            # operator's status/notes onto the other company. The precise keys
            # (``d:``/``i:``/``a:``) are identifiers, not names, and keep matching as
            # before — including a domainless row that later gains a domain, since the
            # guard only fires when BOTH sides carry one.
            if (
                key.startswith("c:")
                and domain
                and _domain(self.rows[pos])
                and _domain(self.rows[pos]) != domain
            ):
                continue
            return pos, ()
        hits = [
            p
            for p in self._names.get(legal_name_key(item.get("company") or ""), [])
            if not (domain and _domain(self.rows[p]) and _domain(self.rows[p]) != domain)
        ]
        return (hits[0], ("company",)) if len(hits) == 1 else (None, ())
