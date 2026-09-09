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


#: How a judge verdict ranks against another for the SAME recipient. A recipient scored on
#: two lanes gets the worst of the two, never the friendliest. One address per campaign
#: typically has two rows — a role-inbox merge row and a 1:1 pack written to the same inbox —
#: and reporting the kinder of the pair would tell a reader a row is workable while a sibling
#: row says it is held.
_JUDGE_RANK = {"send": 0, "re-angle": 1, "drop": 2}

#: What each queue destination means to the person reading the page, in plain English. The
#: destination is the FOLLOW-UP, and it is the half a bare verdict word leaves out: eleven of
#: this campaign's twenty-four rejections are not asking for better copy at all.
_DESTINATION_GLOSS = {
    "prospect:re-target": (
        "find a different seat",
        "the argument is fine, the recipient does not own the problem — this goes back to "
        "prospecting for a person who does, not back to the writer",
    ),
    "spec:re-argue": (
        "rewrite the argument",
        "the seat is right and the copy is what the judge rejected — this goes back to the "
        "spec or the pack",
    ),
}


def _normalise_defect(raw: str) -> str:
    """One id per finding, via the adjudication package's own normaliser."""
    try:
        from ..adjudication.defects import normalize_defect_class
    except ImportError:  # pragma: no cover - the package is in-repo
        return raw.strip().lower().replace("_", "-")
    return normalize_defect_class(raw) or raw.strip()


def judge_queue(profile: str, content_root: Path | None = None) -> dict[str, dict]:
    """The email judge's verdict and FOLLOW-UP per recipient, from the retarget queue.

    A verdict word on its own is half a finding. ``re-angle`` printed beside an account tells
    a reader something was wrong and nothing about what happens next — and on this campaign
    the two answers are as far apart as they get: 11 of 24 rejections say *the copy is fine,
    the person is wrong* and route to prospecting, while 13 say *the person is fine, the copy
    is wrong* and route to the spec. Those are different teams, different weeks and different
    money, and the page rendered them as the same word.

    Read from the newest ``evals/retarget-queue-*.jsonl`` on disk. Rows are keyed by address
    and the worst verdict wins (:data:`_JUDGE_RANK`) — see the two-rows-one-address case in
    its docstring. This is a RANKING, never a gate: the deterministic ``account_integrity``
    check is what refuses a row, and nothing here changes what may be sent.
    """
    import json as _json

    base = _prospects_dir(profile, content_root) / "evals"
    files = sorted(base.glob("retarget-queue-*.jsonl"))
    if not files:
        return {}
    out: dict[str, dict] = {}
    tally: dict[str, int] = {}
    for line in files[-1].read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            r = _json.loads(line)
        except ValueError:
            continue
        key = (r.get("email") or "").strip().lower()
        if not key:
            continue
        # Counted per ROW, before the per-address fold below. The two denominators differ by
        # one on this campaign (24 rows, 23 addresses) and a page that quotes one under the
        # other's label is the restated-number failure this tally exists to remove.
        tally["rows"] = tally.get("rows", 0) + 1
        tally["backend"] = (r.get("backend") or "").strip() or tally.get("backend", "")
        tally["batch"] = max(int(tally.get("batch") or 0), int(r.get("judge_batch") or 0))
        if (r.get("copy_revised") or "").strip():
            tally["revised"] = tally.get("revised", 0) + 1
        tally[f"verdict:{(r.get('verdict') or '—').strip()}"] = (
            tally.get(f"verdict:{(r.get('verdict') or '—').strip()}", 0) + 1
        )
        dest = (r.get("destination") or "—").strip()
        cls = (r.get("defect_class") or "").strip()
        if cls and dest:
            # Per (destination, class), so the page can NAME the classes behind each queue
            # instead of carrying a hand-typed list of them. The list it used to carry was
            # written against one judge run and was wrong by the next: a re-scored batch
            # returns a different vocabulary for the same findings, and a stale parenthetical
            # reads exactly like a current one.
            # Normalised, so the judge's kebab and snake spellings of one finding
            # (`fact-earns-its-place` / `fact_earns_its_place`) count as one class rather than
            # rendering as two entries of the same thing.
            key = f"class:{dest}|{_normalise_defect(cls)}"
            tally[key] = tally.get(key, 0) + 1
        if dest == "spec:re-argue":
            # Of the rows routed back to the writer, how many are a defect the operator has
            # ALREADY ruled on rather than an open rewrite. Two classes qualify and both are
            # argued elsewhere on this page: `no-signal-evidence` / `fact-not-earned` fire on
            # a generic lane because it carries no per-row signal by design, and an
            # unclassified rejection of a 1:1 pack is the category-claim conflict the operator
            # settled in favour of the category claim. Counting them keeps a reader from
            # reading "13 to rewrite" as 13 pieces of writing that are waiting to be done.
            bucket = (
                "settled"
                if cls in {"no-signal-evidence", "fact-not-earned", "(unclassified)"}
                else "open"
            )
            tally[f"reargue:{bucket}"] = tally.get(f"reargue:{bucket}", 0) + 1
        tally[f"dest:{(r.get('destination') or '—').strip()}"] = (
            tally.get(f"dest:{(r.get('destination') or '—').strip()}", 0) + 1
        )
        prev = out.get(key)
        if prev and _JUDGE_RANK.get(prev["verdict"], 0) >= _JUDGE_RANK.get(r.get("verdict"), 0):
            prev["also"] = True
            continue
        act, why = _DESTINATION_GLOSS.get(r.get("destination") or "", ("", ""))
        out[key] = {
            "verdict": (r.get("verdict") or "").strip(),
            "destination": (r.get("destination") or "").strip(),
            "action": act,
            "action_why": why,
            "defect": (r.get("defect_class") or "").strip(),
            "note": (r.get("note") or "").strip(),
            "calibrated": bool(r.get("judge_calibrated")),
            "filed": (r.get("filed") or "").strip(),
            # When this row's COPY was last revised, if it has been. A verdict describes the
            # bytes it read, so a page that cannot say whether those bytes have since changed
            # reports a stale opinion as a current one.
            "revised": (r.get("copy_revised") or "").strip(),
            "attempt": r.get("attempt") or 1,
            "source": files[-1].name,
            "also": bool(prev),
        }
    out["__tally__"] = tally  # type: ignore[assignment]
    return out


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
        key = persona_of(title)
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


def roster_model(profile: str, globs, content_root: Path | None = None) -> dict:
    """The campaign's OWN account roster, from the run exports its manifest declares.

    Everything else on this page counts the shared prospect pool, which is the right
    denominator for "who could we email next" and the wrong one for "how is this campaign
    doing". A campaign whose whole roster is 26 accounts rendered "731 people we will
    actually email of 859 loaded" — true of the profile, false of the campaign.

    One account may appear in several exports (a discovery pass, then an enrichment pass), so
    rows are folded by company and the RICHEST row wins: an address beats none, then a
    why-now, then a verdict. Taking the first or last row instead would silently report the
    pre-enrichment snapshot — on 2026-09-04 the run's first export held 18 rows with 2
    addresses, while the four together hold 26 accounts with 23.
    """
    import csv as _csv

    base = _prospects_dir(profile, content_root)
    best: dict[str, tuple[tuple, dict]] = {}
    files: list[str] = []
    for pattern in globs or []:
        for path in sorted(base.glob(pattern)):
            files.append(path.name)
            try:
                with path.open(newline="", encoding="utf-8") as fh:
                    for r in _csv.DictReader(fh):
                        company = (r.get("Company Name") or "").strip()
                        if not company:
                            continue
                        rank = (
                            bool((r.get("Email") or "").strip()),
                            bool((r.get("GTM_Why_Now") or "").strip()),
                            bool((r.get("GTM_Verdict") or "").strip()),
                        )
                        if company not in best or rank > best[company][0]:
                            best[company] = (rank, r)
            except OSError:
                continue
    rows = [v[1] for v in best.values()]
    judged = judge_queue(profile, content_root)
    judge_tally = judged.pop("__tally__", {})

    def _n(key: str) -> int:
        return sum(1 for r in rows if (r.get(key) or "").strip())

    def _count(key: str) -> list[tuple[str, int]]:
        out: dict[str, int] = {}
        for r in rows:
            out[(r.get(key) or "").strip() or "—"] = (
                out.get((r.get(key) or "").strip() or "—", 0) + 1
            )
        return sorted(out.items(), key=lambda kv: -kv[1])

    return {
        "sources": files,
        "accounts": len(rows),
        "contact_verified": _n("Email"),
        "named_seat": _n("First Name"),
        "signal": _n("GTM_Why_Now"),
        "signal_sourced": _n("GTM_Signal_Source_URL"),
        "tiers": _count("GTM_Tier"),
        "verdicts": _count("GTM_Verdict"),
        "companies": sorted(best),
        "judge_source": next(iter(judged.values()), {}).get("source", ""),
        "judge_tally": judge_tally,
        "judge_actions": sorted(
            {(j["action"], j["action_why"]) for j in judged.values() if j["action"]}
        ),
        "rows": sorted(
            (
                {
                    "company": (r.get("Company Name") or "").strip(),
                    "seat": (r.get("Job Title") or "").strip(),
                    "email": (r.get("Email") or "").strip(),
                    "named": bool((r.get("First Name") or "").strip()),
                    "tier": (r.get("GTM_Tier") or "").strip(),
                    "verdict": (r.get("GTM_Verdict") or "").strip(),
                    "score": (r.get("GTM_Score") or "").strip(),
                    "signal": bool((r.get("GTM_Why_Now") or "").strip()),
                    # A verdict word with no reason is an assertion the reader cannot check.
                    # "drop" on its own reads as our opinion; "Gate B - body-shop SI" is a
                    # finding they can disagree with.
                    "verdict_reason": (r.get("GTM_Verdict_Reason") or "").strip(),
                    "why_now": (r.get("GTM_Why_Now") or "").strip(),
                    # The email judge's read of the DRAFTED copy for this address, and what
                    # it routes to. A different question from GTM_Verdict, which is the
                    # researcher's call on the ACCOUNT before any copy existed — the two are
                    # kept side by side rather than merged, because they disagree usefully.
                    "judge": judged.get((r.get("Email") or "").strip().lower()),
                }
                for r in rows
            ),
            key=lambda x: (x["tier"] != "A", x["company"].lower()),
        ),
    }


def samples_model(profile: str, campaign: str, content_root: Path | None = None) -> dict:
    """The actual emails — the thing every other number on this page is about.

    Nine tiles and four tables described a campaign without ever showing one sentence that a
    recipient would read. Two kinds are shown, and the difference is the point:

    * **1:1 packs** are already rendered — one named person, no merge fields — so the body IS
      what is sent, verbatim.
    * **Sequence touches** are templates. They are shown WITH their merge tags intact rather
      than filled in with a sample row, because filling them here would be a second renderer
      beside ``merge_render_linter.render``, and two renderers is how a page starts showing
      copy nobody sends. The merge-render gate is what proves the template renders.

    Parsers come from ``hook_coverage.config``, which is this package's one home for the
    linter import — not a third copy of the pack/spec parsing.
    """
    from merge_render_linter import render

    from ..hook_coverage.config import parse_spec
    from ..hook_coverage.declared import _CAPABILITY_RE

    try:
        from outreach_pack_linter import parse_ladder, parse_prospect_pack
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
