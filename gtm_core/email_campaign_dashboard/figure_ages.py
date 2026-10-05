"""How old the sending figures are — one date per sequence, rolled up once, in the model.

``sequence-stats.json`` (written by :mod:`gtm_core.sequencer_snapshot`) stamps every sequence
with the date its own figures were fetched. The page's age is the **oldest** of the sequences it
is about, because a number is only as current as the stalest figure behind it.

*Which* sequences it is about is the part that has to be stated, not assumed:

* an unscoped page covers every current (non-archived) sequence any campaign lists, plus any
  sequence in the figures that no campaign lists at all — a sequence nobody archived is still
  sending;
* a scoped page covers only its own campaigns' current sequences, so a fresh campaign is not
  dragged old by another campaign's stale one — and the page says that is the set it used.

A sequence the page covers but the figures do not hold, or hold with no usable date, makes the
age **unknown** (``fetched`` becomes ``""``, which the health module reads as undated), never
quietly fresh. So does a file whose body no longer matches what the refresh command wrote.

Applies only to the live-stats file in a shape that carries per-sequence dates; the older state
file and an unreadable file are left exactly as the loader returned them.
"""

from __future__ import annotations

from datetime import UTC, datetime

from gtm_core.sequence_snapshot_format import fall_sentence

from . import health, provenance

#: Why a covered sequence's age is unknown — closed, so a new cause names itself here.
CAUSES = {
    "missing": "no figures on file",
    "unstamped": "figures carry no date",
    "unusable": "date cannot be read",
}


def _current(campaigns: dict) -> list[str]:
    """The ids the campaigns list as current, as TEXT — the snapshot's own ids are the writer's
    ``row_id`` strings, and a manifest that spells an id as a number must still meet them (and
    must sort beside them) rather than raise."""
    seen: list[str] = []
    for c in campaigns.get("campaigns", []):
        for s in c.get("sequences", []):
            sid = str(s["sequence_id"])
            if sid not in seen:
                seen.append(sid)
    return seen


def _listed(campaigns: dict) -> set[str]:
    return {str(sid) for c in campaigns.get("campaigns", []) for sid in health._listed(c)}


def per_id(snapshot: dict, campaigns: dict, now: datetime, *, scoped: bool = False) -> dict | None:
    """``{members, scoped, edited}`` — each covered id's stamp and cause — or None when this
    snapshot has no per-sequence dates to read (state file, unreadable, nothing covered)."""
    meta = snapshot.get("file_meta")
    if snapshot.get("source") != "stats" or snapshot.get("unreadable") or meta is None:
        return None
    ids = _current(campaigns)
    if not scoped:
        ids += [i for i in meta["ids"] if i and i not in _listed(campaigns) and i not in ids]
    if not ids:
        return None
    members = {}
    for sid in ids:
        stamp = meta["stamps"].get(sid)
        if sid not in meta["ids"]:
            cause = "missing"
        elif not stamp:
            cause = "unstamped"
        elif health.figures_age_days(stamp, now) is None:
            cause = "unusable"
        else:
            cause = None
        members[sid] = {"stamp": stamp, "cause": cause, "inherited": sid in meta["inherited"]}
    return {"members": members, "scoped": scoped, "edited": bool(meta["edited"])}


def with_effective_age(snapshot: dict, campaigns: dict, now: datetime, *, scoped: bool = False):
    """The snapshot with ``fetched`` replaced by the oldest covered stamp. Idempotent: it
    recomputes from the per-sequence stamps and never reads the ``fetched`` it wrote."""
    ages = per_id(snapshot, campaigns, now, scoped=scoped)
    if ages is None:
        return snapshot
    members = ages["members"]
    broken = {i: v["cause"] for i, v in members.items() if v["cause"]}
    dated = {i: health._parse_fetched(v["stamp"]) for i, v in members.items() if not v["cause"]}
    oldest = min(dated, key=lambda i: dated[i]) if dated else None
    cause = "edited" if ages["edited"] else (next(iter(broken.values())) if broken else None)
    # An unreadable or future stamp is passed through so the page words it as what it is
    # (unknown, or in the future); a missing or hand-edited one blanks the date.
    bad = next((i for i, c in broken.items() if c == "unusable"), None)
    if cause == "edited" or (broken and bad is None):
        fetched = ""
    elif bad is not None:
        fetched = members[bad]["stamp"]
    else:
        fetched = members[oldest]["stamp"]
    return dict(
        snapshot,
        fetched=fetched,
        file_fetched=snapshot.get("file_fetched", snapshot.get("fetched")),
        age_cause=cause,
        age_ids=sorted(i for i in broken if not members[i]["inherited"]),
        age_basis={
            "scoped": scoped,
            "count": len(members),
            "oldest": oldest,
            "oldest_inherited": bool(oldest and members[oldest]["inherited"]),
            "unlisted": [i for i in members if i not in _current(campaigns)],
        },
    )


def rescope(m: dict, campaigns: dict, now: datetime | None = None) -> None:
    """Re-judge a model after ``scope_to_campaign`` narrowed ``campaigns``: the age, the
    ``figures`` the surfaces read, the ``figures-old`` strip reason and the sending-figures row of
    the sources table, all from one recompute — the header, the strip, the per-campaign line, the
    table and the date ``--check-fresh`` records must say the same thing on a scoped page.

    A model with no ``status.snapshot`` (a hand-built one, or a profile that never wrote the
    figures) has nothing to re-judge and is left exactly as it was, like every other consumer of
    the snapshot, which reads it defensively.
    """
    snapshot = (m.get("status") or {}).get("snapshot")
    if not snapshot:
        return
    now = now or m.get("_now") or datetime.now(UTC)
    snap = with_effective_age(snapshot, campaigns, now, scoped=True)
    if snap is snapshot:
        return
    m["status"] = dict(m["status"], snapshot=snap)
    m["figures"] = health.figures_state(m["status"], now)
    if m.get("sources"):
        # Only the figures row depends on which sequences are in scope; every other source is a
        # file the whole profile shares and stays as built.
        m["sources"] = dict(m["sources"], figures=provenance.figures_row(m))
    warnings = [w for w in m.get("warnings") or [] if w != "figures-old"]
    if m["figures"]["over_limit"]:
        warnings.append("figures-old")
    m["warnings"] = warnings


def basis_text(fig: dict) -> str:
    """The set of sequences the age was taken over, in plain words — "" when it is just the one."""
    basis = fig.get("basis")
    if not basis:
        return ""
    n = basis["count"]
    parts = []
    if basis["scoped"]:
        parts.append(f"the oldest of the {n} sequence{'s' if n != 1 else ''} on this page")
    elif n > 1:
        parts.append(f"the oldest of {n} sequences in use")
    if basis["unlisted"]:
        parts.append(f"{len(basis['unlisted'])} of them in no campaign")
    if basis["oldest_inherited"]:
        parts.append("date from the file")
    return ", ".join(parts)


def cause_ids(fig: dict) -> list[str]:
    return list(fig.get("cause_ids") or [])


def cause_text(fig: dict) -> str:
    """Why the age is unknown, as a clause for a sentence — "" when no cause was recorded."""
    cause = fig.get("cause")
    ids = ", ".join(cause_ids(fig))
    if cause == "edited":
        return " (the sending figures file was changed by hand since the refresh command wrote it)"
    if cause in CAUSES and ids:
        return f" ({ids}: {CAUSES[cause]})"
    return ""


def fall_note(m: dict, sid: str) -> str:
    """The recorded-fall sentence for one sequence, or "" — shown beside its status word."""
    meta = ((m.get("status") or {}).get("snapshot") or {}).get("file_meta") or {}
    fall = (meta.get("falls") or {}).get(sid)
    return fall_sentence(fall) if fall else ""
