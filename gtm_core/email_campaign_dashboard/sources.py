"""Where a CAMPAIGN's own facts come from — its roster, its packs, its actual emails.

Split out of :mod:`~gtm_core.email_campaign_dashboard.model` because these three answer a
different question from the rest of it. The model assembles the shared, profile-wide picture
(pool, cells, outcomes, live sequencer stats); everything here reads artifacts that belong to
one campaign and were previously invisible to this page — the run exports its manifest
declares, the 1:1 packs on disk, and the bodies themselves.

That invisibility is the whole reason they exist: a pack is in no ``cells.toml``, has no
sequence id and enrols nobody, so every other source returned nothing for it and the page
silently described the merge lane only.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..prospects_consolidate import _prospects_dir

#: Date-agnostic twins of ``hook_coverage.config.PACK_GLOBS`` — that module scopes to one
#: campaign's date, this page is profile-wide and shows every pack on disk.
_PACK_GLOBS = ("*/prospects-*-outreach-*.md", "*/email-*.md")
_RULES_RE = re.compile(r"^Rules-Version:\s*(\S+)", re.MULTILINE)
_PACK_DATE_RE = re.compile(r"(\d{8}|\d{4}-\d{2}-\d{2})")
#: The date ``capability:`` became a pack front-block field. A pack older than this does not
#: *lack* a declaration — the field did not exist yet — so counting the 930 packs written
#: before it as "undeclared" would report a wrong finding with total confidence. They are
#: summarised as a count and never mixed into the cap.
CAPABILITY_SINCE = "20260904"


def seat_fit(m: dict, profile: str = "", content_root: Path | None = None) -> dict:
    """Do the merge lanes' recipients hold the seat their own spec declares?

    The generic lane is **signal-free, not seat-free**, and the difference is the whole
    finding. Its body makes no claim about the recipient's company — that is what "generic"
    buys — but the spec still declares a `hook_cell` whose left half is a PERSONA, and the
    body still argues that persona's problem and that persona's stakes. So a recipient who is
    not that seat gets an argument written for somebody else, and being on the generic lane
    does not excuse it.

    Counted with :func:`gtm_core.hook_coverage.config.persona_of`, the same classifier the
    coverage gate uses, rather than by reading titles. The sentence this replaces was
    hand-written and said "7 of the 18" — measured, it is 15, because a hand count of
    unfamiliar titles is exactly the thing a classifier is for.
    """
    from ..hook_coverage.config import persona_of
    from ..hook_coverage.declared import declared_cell
    from ..hook_coverage.matrix import persona_key_of_label

    samples = m.get("samples") or {}
    addrs = {r["to"] for r in samples.get("rendered") or []}
    if not addrs:
        return {}
    titles = {
        (row.get("email") or "").strip().lower(): (row.get("seat") or "").strip()
        for row in ((m.get("roster") or {}).get("rows") or [])
    }
    # The persona each merge spec declares, read with hook_coverage's own front-block reader
    # rather than a fourth parser for the same field.
    seq_dir = _prospects_dir(profile, content_root) / "sequences" if profile else None
    declared: set[str] = set()
    for msg in m.get("messages") or []:
        spec = (seq_dir / msg["spec"]) if seq_dir and msg.get("spec") else None
        if not (spec and spec.is_file()):
            continue
        dc = declared_cell(spec.read_text(encoding="utf-8", errors="replace"))
        key = persona_key_of_label(dc.persona) if dc and dc.persona else None
        if key:
            declared.add(key)

    matched, elsewhere, unresolved = [], [], []
    for a in sorted(addrs):
        title = titles.get(a.lower(), "")
        key = persona_of(title, profile or None)
        if key is None:
            unresolved.append((a, title))
        elif not declared or key in declared:
            matched.append((a, title))
        else:
            elsewhere.append((a, title, key))
    return {
        "total": len(addrs),
        "declared": sorted(d for d in declared if d),
        "matched": len(matched),
        "elsewhere": len(elsewhere),
        "unresolved": len(unresolved),
        "no_title": sum(1 for _a, t in unresolved if not t),
        "off_seat_titles": sorted({t for _a, t in unresolved if t})[:6],
    }


def samples_model(profile: str, campaign: str, content_root: Path | None = None) -> dict:
    """The actual emails — the thing every other number on this page is about.

    Nine tiles and four tables described a campaign without ever showing one sentence that a
    recipient would read. Two kinds are shown, and the difference is the point:

    * **1:1 packs** are already rendered — one named person, no merge fields — so the body IS
      what is sent, verbatim.
    * **Sequence touches** are templates. They are shown WITH their merge tags intact rather
      than filled in with a sample row, because filling them here would be a second renderer
      beside ``outreach.render``, and two renderers is how a page starts showing
      copy nobody sends. The merge-render gate is what proves the template renders.

    Parsers come from ``hook_coverage.config``, which is this package's one home for the
    linter import — not a third copy of the pack/spec parsing.
    """
    from outreach import render

    from ..hook_coverage.config import parse_spec
    from ..hook_coverage.declared import _CAPABILITY_RE

    try:
        from outreach import parse_ladder, parse_prospect_pack
    except ImportError:  # pragma: no cover - the import above puts it on sys.path
        return {"packs": [], "touches": [], "rendered": []}

    date = (re.search(r"(\d{8})$", campaign or "") or [None, ""])[1] if campaign else ""
    base = _prospects_dir(profile, content_root)
    out: dict = {"packs": [], "touches": [], "rendered": []}
    if not date:
        return out

    for path in sorted((base.parent / "accounts").glob(f"*/prospects-{date}-outreach-*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        _v, blocks, _m = parse_prospect_pack(text)
        if not blocks:
            continue
        cap = _CAPABILITY_RE.search(text)
        # A pack's ladder is what says how long the hand-sent lane runs. Only the EMAIL
        # touches count: touch 1 of the standard ladder is a LinkedIn connection request,
        # which is neither an email nor bound by the mailbox ceiling, and counting it would
        # inflate the send total by one per pack.
        mail_days = [d for _n, d, ch in parse_ladder(text) if ch.lower().startswith("email")]
        out["packs"].append(
            {
                "account": path.parent.name,
                "company": blocks[0].company,
                "to": blocks[0].to,
                "subject": blocks[0].subject,
                "body": blocks[0].body,
                "capability": (cap.group("value").strip() if cap else ""),
                "words": len(blocks[0].body.split()),
                "mail_days": mail_days,
            }
        )

    # Rendered per row, with the SAME renderer the merge-render gate uses — not a second
    # implementation. Showing only the template meant the page displayed 10 emails for a
    # campaign that drafts 23; the 13 sequence recipients are the ones whose evidence was too
    # thin for a 1:1, so leaving them as one template is exactly where a reader stops looking.
    import csv as _csv

    for spec in sorted((base / "sequences").glob(f"*{date}*.md")):
        stem = spec.stem
        rows: list[dict] = []
        for cand in sorted((base / "sequences").glob(f"*{date}*.csv")):
            if not cand.stem.startswith(stem.rsplit("-", 2)[0]):
                continue
            lane = "inbox" if "inbox" in cand.stem else "named"
            if ("inbox" in stem) != (lane == "inbox"):
                continue
            with cand.open(newline="", encoding="utf-8") as fh:
                rows = [r for r in _csv.DictReader(fh) if (r.get("email") or "").strip()]
        try:
            touches = parse_spec(spec.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            touches = []
        for tch in touches:
            for row in rows:
                out["rendered"].append(
                    {
                        "spec": stem,
                        "n": getattr(tch, "number", 1),
                        "company": (row.get("company") or "").strip(),
                        "to": (row.get("email") or "").strip(),
                        "subject": render(getattr(tch, "subject", "") or "", row),
                        "body": render(getattr(tch, "body", ""), row),
                    }
                )

    for spec in sorted((base / "sequences").glob(f"*{date}*.md")):
        try:
            touches = parse_spec(spec.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a malformed spec must not take the page down  # nosec B112
            continue
        for tch in touches:
            out["touches"].append(
                {
                    "spec": spec.stem,
                    "n": getattr(tch, "number", 1),
                    "day": getattr(tch, "day", 0),
                    "subject": getattr(tch, "subject", "") or "(reply in the same thread)",
                    "body": getattr(tch, "body", ""),
                    "words": len(getattr(tch, "body", "").split()),
                }
            )
    return out


def packs_model(profile: str, content_root: Path | None = None) -> dict:
    """Every 1:1 outreach pack on disk, with the capability each one declares.

    The 1:1 lane is invisible to every other source this page reads. A pack is not in
    ``cells.toml``, has no sequence id and enrols nobody, so campaigns / cells / outcomes all
    return nothing for it — which meant the page's answer to "what are we saying" silently
    described the **merge lane only**. That is the same structural blind spot
    :mod:`gtm_core.hook_coverage` had until ``--include-packs`` on 2026-09-04, and it is why a
    hand-rolled campaign page existed alongside this one.

    The capability is read with ``hook_coverage``'s own regex rather than a second one: the
    declaration is one fact, and two parsers for it would drift apart silently.
    """
    from ..hook_coverage.declared import _CAPABILITY_RE

    root = _prospects_dir(profile, content_root).parent / "accounts"
    rows: list[dict] = []
    if root.is_dir():
        seen: set[Path] = set()
        for pattern in _PACK_GLOBS:
            for path in sorted(root.glob(pattern)):
                if path in seen:
                    continue
                seen.add(path)
                text = path.read_text(encoding="utf-8", errors="replace")
                cap = _CAPABILITY_RE.search(text)
                date = _PACK_DATE_RE.search(path.name)
                rows.append(
                    {
                        "account": path.parent.name,
                        "file": path.name,
                        "date": date.group(1) if date else "",
                        "capability": (cap.group("value").strip() if cap else ""),
                        "rules_version": (lambda m: m.group(1) if m else "")(
                            _RULES_RE.search(text)
                        ),
                    }
                )
    rows.sort(key=lambda r: (r["date"], r["account"]), reverse=True)
    current = [r for r in rows if r["date"].replace("-", "") >= CAPABILITY_SINCE]
    spread: dict[str, int] = {}
    for r in current:
        if r["capability"]:
            spread[r["capability"]] = spread.get(r["capability"], 0) + 1
    return {
        "packs": current,
        "spread": sorted(spread.items(), key=lambda kv: -kv[1]),
        "undeclared": sum(1 for r in current if not r["capability"]),
        "legacy": len(rows) - len(current),
        "since": CAPABILITY_SINCE,
    }


# `roster_model` and `judge_queue` moved to `.roster` on 2026-09-10 (§R10 headroom for the
# filter-bar work). Re-exported here because `model.py` and four tests import them from this
# module — the move is meant to be invisible to every caller.
from .roster import judge_queue as judge_queue  # noqa: E402,F401
from .roster import roster_model as roster_model  # noqa: E402,F401
from .roster import roster_sources as roster_sources  # noqa: E402,F401
