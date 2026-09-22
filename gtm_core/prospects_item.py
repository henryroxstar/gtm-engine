"""Normalise the prospect items an LLM writes into ``items.json`` — and nothing more.

Split out of :mod:`gtm_core.prospects_import` (which re-exports the public names) so the
rules for reading an untrusted item sit in one place, apart from the CSV/merge plumbing.

``items.json`` is brain output, so it is untrusted structured data (CLAUDE.md §R5): every
value is type-checked and coerced here, and a value that cannot be read is a loud
:class:`ItemError` naming the row and the field — raised before anything is written, never
a traceback halfway through a merge, and never a silent ``0``.

Two shapes come out of this module, and the difference is the point:

  * :func:`normalise_item` — what the caller SAID. Every canonical field is present, and
    one the caller left out is *blank*. This is what merges onto an existing account, where
    a blank can never overwrite a value (:mod:`gtm_core.prospects_merge`).
  * :func:`new_account_defaults` — that, plus the bookkeeping defaults a brand-new ledger
    row needs (``status: new``, ``heat: 0`` …). Applied only when an account is created.

Filling every field up front and then merging is what let a seven-field re-discovery erase
an account's research. And four fields are never defaulted at all — ``verdict``,
``category_relation``, ``signal_subject``, ``signal_agent_kind`` are fail-closed in
:func:`gtm_core.signal_record.check_record`, so a default there does not fill a gap, it
forges a research conclusion and erases the BLOCK that would have asked for one.

stdlib-only, matching the rest of gtm_core.
"""

from __future__ import annotations

import math
from typing import Any

from gtm_core.lane_verdicts import LANE_VERDICTS
from gtm_core.prospects_merge import is_blank
from gtm_core.signal_record import AgentKind, CategoryRelation, Verdict
from gtm_core.slugify import slug as _slug

CANONICAL_FIELDS: tuple[str, ...] = (
    "id",
    "company",
    "segment",
    "market",
    "tier",
    "score",
    "why_now",
    "qualification_path",
    "contact_name",
    "contact_title",
    "status",
    "priority",
    "heat",
    "intent_feeds",
    "new_in_role",
    "signal_source_url",
    "signal_observed",
    "signal_evidence",
    "signal_subject",
    "signal_agent_kind",
    "category_relation",
    "signal_column",
    "hook_cell",
    "verdict",
    "verdict_reason",
    "lane",
    "lane_reason",
)

#: Canonical fields read as plain text. ``domain`` is not canonical but is an identity key,
#: so it is held to the same check.
_TEXT_FIELDS = (
    "market",
    "why_now",
    "qualification_path",
    "contact_name",
    "contact_title",
    "status",
    "priority",
    "signal_source_url",
    "signal_observed",
    "signal_evidence",
    "signal_subject",
    "signal_column",
    "hook_cell",
    "verdict_reason",
    "lane_reason",
    "domain",
)

#: Fields that are not canonical but that the run's CSV reads as TEXT (it calls string
#: methods on them). They "ride along" untouched by the canonical checks, which is how
#: ``employees_range: 500`` sailed through normalisation, into the ledger, and then crashed
#: the CSV writer — after the ledger write the docstring above promises never happens.
_CSV_TEXT_RIDERS = (
    "contact_email",
    "email_status",
    "contact_phone",
    "contact_linkedin_url",
    "city",
    "country",
    "region",
    "employees_range",
    "revenue_range",
    "industry",
    "persona_tier",
    "case_study",
    "gtm_source",
)
#: Riders the CSV writes as-is: a number may stay a number, but never a list or an object.
_CSV_SCALAR_RIDERS = ("employees_number", "top_intent_score", "conf")


def _words(cls: type) -> frozenset[str]:
    return frozenset(v for k, v in vars(cls).items() if not k.startswith("_"))


#: The closed vocabularies, read from the modules that own them rather than retyped here.
#: ``hold``/``excluded`` are lanes the router parks a row in; the rest may enrol.
_VOCABULARIES: dict[str, frozenset[str]] = {
    "verdict": _words(Verdict),
    "lane": (frozenset(LANE_VERDICTS) - {""}) | {"hold", "excluded"},
    "signal_agent_kind": _words(AgentKind),
    "category_relation": _words(CategoryRelation),
}

#: What the scorer's ``tier: drop`` means, recorded as the refusal's reason.
BELOW_THRESHOLD_REASON = "below publish threshold"

_TRUE, _FALSE = {"true", "yes", "y", "1"}, {"false", "no", "n", "0"}


class ItemError(ValueError):
    """One item cannot be read. The message names the row and the field."""


def _text(raw: dict, field: str, where: str) -> str:
    value = raw.get(field)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ItemError(f"{where}: {field} must be text, got {type(value).__name__} {value!r}")
    return value.strip()


def _word(raw: dict, field: str, where: str) -> str:
    value = _text(raw, field, where).lower()
    if value and value not in _VOCABULARIES[field]:
        allowed = ", ".join(sorted(_VOCABULARIES[field]))
        raise ItemError(f"{where}: {field} {value!r} is not one of: {allowed}")
    return value


def _number(raw: dict, field: str, where: str) -> int | float | str:
    """A finite number, ``""`` when absent. A float stays a float — half-points are real
    scores — and a string that is not a number ("9/10") is refused, never read as 0."""
    value = raw.get(field)
    if is_blank(value):
        return ""
    try:
        if isinstance(value, bool):
            raise ValueError
        number = float(value)
        if not math.isfinite(number):
            raise ValueError
    except (TypeError, ValueError):
        raise ItemError(f"{where}: {field} {value!r} is not a number") from None
    return int(number) if number.is_integer() else number


def _heat(raw: dict, where: str) -> int | str:
    if "heat" in raw and raw["heat"] is None:
        raise ItemError(f"{where}: heat None is not a number — omit the field if unmeasured")
    heat = _number(raw, "heat", where)
    if isinstance(heat, float):
        raise ItemError(f"{where}: heat {raw['heat']!r} is not a whole number")
    return heat


def _flag(raw: dict, field: str, where: str) -> bool | str:
    """A real boolean: ``bool("false")`` is ``True``, which is how a string became a fact."""
    value = raw.get(field)
    if is_blank(value):
        return ""
    token = str(value).strip().lower() if isinstance(value, (str, int)) else ""
    if token not in _TRUE | _FALSE:
        raise ItemError(f"{where}: {field} {value!r} is not true/false")
    return token in _TRUE


def _feeds(raw: dict, where: str) -> list[str]:
    """A list of feed names. ``list("vibe-topic")`` is ten one-letter feeds."""
    value = raw.get("intent_feeds")
    if isinstance(value, str):
        value = [value]
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ItemError(f"{where}: intent_feeds must be a list of feed names, got {value!r}")
    return [v.strip() for v in value if v.strip()]


def _scalar(raw: dict, field: str, where: str) -> Any:
    value = raw[field]
    if value is not None and (isinstance(value, bool) or not isinstance(value, (str, int, float))):
        raise ItemError(f"{where}: {field} must be text or a number, got {value!r}")
    return value


def _topic(entry: Any, where: str) -> dict:
    if isinstance(entry, str):
        return {"topic": entry.strip()}
    if not isinstance(entry, dict) or not isinstance(entry.get("topic", ""), str):
        raise ItemError(f"{where}: intent_topics entry {entry!r} is not a topic")
    if entry.get("score") is None:
        return {k: v for k, v in entry.items() if k != "score"}
    return {**entry, "score": _number(entry, "score", f"{where}: intent_topics")}


def _topics(value: Any, where: str) -> list[dict]:
    """``[{"topic", "score"}, …]`` from that, a ``{topic: score}`` mapping, or bare names.
    A topic nobody scored carries no ``score`` — it is never given a 0 it did not earn."""
    if isinstance(value, str):
        value = [part for part in value.split(";") if part.strip()]
    elif isinstance(value, dict):
        value = [{"topic": topic, "score": score} for topic, score in value.items()]
    if value is None:
        return []
    if not isinstance(value, list):
        raise ItemError(f"{where}: intent_topics must be a list of topics, got {value!r}")
    return [_topic(entry, where) for entry in value]


def _riders(raw: dict, where: str) -> dict[str, Any]:
    """The non-canonical fields the CSV reads, type-checked like everything else. Only
    fields the caller supplied — a rider left out stays out, so it can erase nothing."""
    riders: dict[str, Any] = {}
    for field in _CSV_TEXT_RIDERS:
        if field in raw:
            value = _scalar(raw, field, where)
            riders[field] = "" if value is None else str(value).strip()
    riders.update({f: _scalar(raw, f, where) for f in _CSV_SCALAR_RIDERS if f in raw})
    if "intent_topics" in raw:
        riders["intent_topics"] = _topics(raw["intent_topics"], where)
    return riders


def _refuse(item: dict) -> None:
    """Record the scorer's or the researcher's refusal as one the whole pipeline can read.

    The scorer marks a below-threshold row ``tier: drop`` and keeps it. Upper-cased into
    the ledger with ``status: new`` and no verdict, that row was indistinguishable from a
    prospect and was exported as one. ``tier: drop`` outranks a ``send`` on the same item:
    the refusal is the fail-closed reading.
    """
    if item["tier"] == "DROP":
        if item["verdict"] not in ("", Verdict.DROP):
            item["verdict_reason"] = ""  # it explained the verdict this refusal replaces
        item["verdict"] = Verdict.DROP
        item["verdict_reason"] = item["verdict_reason"] or BELOW_THRESHOLD_REASON
    if item["verdict"] == Verdict.DROP:
        item["lane"] = "excluded"


def is_refusal(item: dict) -> bool:
    """Whether a normalised item is a recorded refusal — ledger only, never the send CSV."""
    return item.get("verdict") == Verdict.DROP


def normalise_item(raw: Any, row: int = 1) -> dict[str, Any]:
    """One untrusted item in canonical shape; a field the caller left out is blank.

    ``id`` is always re-derived: it becomes a ledger key and an account-folder name, so an
    item does not get to choose it.
    """
    if not isinstance(raw, dict):
        raise ItemError(f"row {row}: an item must be a JSON object, got {type(raw).__name__}")
    company = _text(raw, "company", f"row {row}") or _text(raw, "name", f"row {row}")
    if not company:
        raise ItemError(f"row {row}: no company — an account with no name cannot be merged")
    where = f"row {row} ({company!r})"

    item: dict[str, Any] = {f: _text(raw, f, where) for f in _TEXT_FIELDS}
    item.update({f: _word(raw, f, where) for f in _VOCABULARIES})
    item.update(id=_slug(company), company=company)
    item["segment"] = _text(raw, "segment", where).lower()
    item["tier"] = _text(raw, "tier", where).upper()
    item["signal_evidence"] = item["signal_evidence"] or _text(raw, "evidence", where)
    item["score"] = _number(raw, "score" if raw.get("score") is not None else "fit_score", where)
    item["heat"] = _heat(raw, where)
    item["intent_feeds"] = _feeds(raw, where)
    item["new_in_role"] = _flag(raw, "new_in_role", where)
    if not item["tier"] and item["score"] != "":
        item["tier"] = "A" if item["score"] >= 8 else "B"
    _refuse(item)

    item.update(_riders(raw, where))

    ordered = {f: item[f] for f in CANONICAL_FIELDS}
    # Everything else (contact_email, city, intent_topics …) rides along for the CSV —
    # the fields the CSV reads, as :func:`_riders` read them.
    ordered.update({k: item.get(k, v) for k, v in raw.items() if k not in ordered})
    return ordered


def normalise_items(raw_items: Any) -> list[dict[str, Any]]:
    """Every item, or an :class:`ItemError` for the first bad one — all-or-nothing, so a
    batch is never half-merged."""
    if not isinstance(raw_items, list):
        raise ItemError(f"items must be a JSON array of objects, got {type(raw_items).__name__}")
    return [normalise_item(raw, row) for row, raw in enumerate(raw_items, start=1)]


def new_account_defaults(item: dict[str, Any]) -> dict[str, Any]:
    """``item`` plus the bookkeeping a NEW ledger row needs. Never a research conclusion."""
    full = dict(item)
    scored = not is_blank(full.get("score"))
    if not scored:
        full["score"] = 0
    if is_blank(full.get("tier")):
        # A tier is derived from a score the caller SUPPLIED; an unscored row gets none, because
        # "B" would read downstream as "scored, and publishable".
        full["tier"] = ("A" if full["score"] >= 8 else "B") if scored else ""
    defaults = {
        "segment": "startup",
        "status": "new",
        "priority": "high" if full["tier"] == "A" else "medium",
        "heat": 0,
        "new_in_role": False,
    }
    full.update({f: v for f, v in defaults.items() if is_blank(full.get(f))})
    return full


def build_standard_item(raw: dict[str, Any]) -> dict[str, Any]:
    """What a minimal finding looks like as a brand-new ledger row / a CSV row."""
    return new_account_defaults(normalise_item(raw))


def build_standard_items(raw_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [new_account_defaults(item) for item in normalise_items(raw_items)]
