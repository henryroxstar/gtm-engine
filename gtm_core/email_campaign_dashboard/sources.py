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
        from outreach_pack_linter import parse_prospect_pack
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
        out["packs"].append(
            {
                "account": path.parent.name,
                "company": blocks[0].company,
                "to": blocks[0].to,
                "subject": blocks[0].subject,
                "body": blocks[0].body,
                "capability": (cap.group("value").strip() if cap else ""),
                "words": len(blocks[0].body.split()),
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
