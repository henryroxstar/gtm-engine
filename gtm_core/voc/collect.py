"""Enumerate the six voice-of-customer sources for a profile → a coverage manifest.

The manifest is the un-fakeable "as-of" header the brief cites: for each source
its latest artifact, date, age in days, corpus size, the **speaker** it belongs to
(customer-voice / bd-focus / expert-lens / mixed), and a finer provenance
class for the confidence rubric. The speaker is assigned HERE, in code, by source
identity alone — so "what the market says" and "what BD believes" can never be
silently merged downstream (the skill's primary rigor guarantee).

Everything is read-only and cost-free: filesystem globs + a few structured-JSON
counts. No model reads free text to produce a number here.

CLI::

    python -m gtm_core.voc.collect --profile P            # print manifest JSON to stdout
    python -m gtm_core.voc.collect --profile P --out F     # also write F
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path

from .. import outreach_log
from ..paths import PathConfig, _safe_segment

# The latest date embedded in a filename is the artifact's freshness signal. We take
# the MAX over: full ISO dates, a `YYYY-MM-DD-to-DD` range end (so a two-week window
# sorts on its end day, not its start), and a compact YYYYMMDD (prospect briefs /
# linkedin drafts). Taking the max is robust to a filename carrying more than one date.
_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_RANGE_END_RE = re.compile(r"(\d{4})-(\d{2})-\d{2}-to-(\d{1,2})(?!\d)")
_COMPACT_DATE_RE = re.compile(r"(?<!\d)(\d{8})(?!\d)")

# Speaker vocabulary — the mechanical separation behind the brief's structure.
CUSTOMER_VOICE = "customer-voice"
BD_FOCUS = "bd-focus"
EXPERT_LENS = "expert-lens"  # real named practitioners' published frameworks, synthesized by us
MIXED = "mixed"


def _date_from_name(name: str) -> str | None:
    """Return the latest date embedded in *name* (ISO, ``-to-DD`` range end, or
    compact ``YYYYMMDD``), else None. Invalid day/month values are skipped."""
    candidates: list[str] = [f"{y}-{m}-{d}" for y, m, d in _ISO_DATE_RE.findall(name)]
    candidates += [f"{y}-{m}-{int(dd):02d}" for y, m, dd in _RANGE_END_RE.findall(name)]
    candidates += [f"{s[:4]}-{s[4:6]}-{s[6:]}" for s in _COMPACT_DATE_RE.findall(name)]
    valid = [c for c in candidates if _is_valid_iso(c)]
    return max(valid) if valid else None


def _is_valid_iso(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _mtime_date(path: Path) -> str:
    return date.fromtimestamp(path.stat().st_mtime).isoformat()


def _age_days(iso_date: str | None, today: date) -> int | None:
    if not iso_date:
        return None
    try:
        return (today - date.fromisoformat(iso_date)).days
    except ValueError:
        return None


def _latest(paths: list[Path], today: date) -> dict | None:
    """Newest artifact among *paths* — by embedded filename date, mtime as fallback."""
    if not paths:
        return None
    dated = [(p, _date_from_name(p.name) or _mtime_date(p)) for p in paths]
    # Sort by (date, name) so equal-dated files pick deterministically; newest last.
    dated.sort(key=lambda pd: (pd[1], pd[0].name))
    path, iso = dated[-1]
    return {"path": str(path), "date": iso, "age_days": _age_days(iso, today)}


def _json_array(path: Path, key: str) -> list | None:
    """Return the JSON array at ``key`` in *path*, or None if absent/unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get(key) if isinstance(data, dict) else None
    return value if isinstance(value, list) else None


def _count_json_array(path: Path, key: str) -> int | None:
    arr = _json_array(path, key)
    return len(arr) if arr is not None else None


def _source(  # one manifest row
    *,
    id: str,
    label: str,
    speaker: str,
    provenance_class: str,
    present: bool,
    corpus_count: int | None = None,
    latest: dict | None = None,
    extra: dict | None = None,
    note: str = "",
) -> dict:
    return {
        "id": id,
        "label": label,
        "speaker": speaker,
        "provenance_class": provenance_class,
        "present": present,
        "corpus_count": corpus_count,
        "latest": latest,
        "extra": extra or {},
        "note": note,
    }


def collect(
    content_root: Path,
    profiles_root: Path,
    profile: str,
    today: date | None = None,
) -> dict:
    """Build the voice-of-customer source-coverage manifest for *profile*.

    Read-only. Missing sources are reported ``present: false`` (never an error) so
    the brief can state coverage honestly and degrade gracefully.
    """
    today = today or date.today()
    prof = _safe_segment(profile, "profile")
    croot = content_root / prof
    knowledge = profiles_root / prof / "knowledge"

    sources: list[dict] = []

    # 1 — Syften social listening (organic market chatter). CUSTOMER VOICE.
    sig = sorted((croot / "market-signals").glob("*.md"))
    sources.append(
        _source(
            id="syften_market_signals",
            label="Syften social-listening snapshot",
            speaker=CUSTOMER_VOICE,
            provenance_class="external-organic",
            present=bool(sig),
            corpus_count=len(sig),
            latest=_latest(sig, today),
            note="Organic practitioner chatter. Caveat: Syften has no LinkedIn coverage; short windows.",
        )
    )

    # 2 — News / market research. MIXED: news+regulation is customer voice; our own
    #     research syntheses in this folder are our-interpretation — split at read time.
    research = sorted([*(croot / "research").glob("*.md"), *(croot / "research").glob("*.html")])
    sources.append(
        _source(
            id="news_research",
            label="News & market-signal research",
            speaker=MIXED,
            provenance_class="external-plus-interpretation",
            present=bool(research),
            corpus_count=len(research),
            latest=_latest(research, today),
            note="Split at read time: third-party news/regulation = customer-voice; "
            "our own competitive/prospect syntheses = bd-focus (our-interpretation).",
        )
    )

    # 3a — Intent topics (behavioral demand). CUSTOMER VOICE.
    intent = croot / "prospects" / "intent" / "rr-intentsify-latest.json"
    intent_extra = {}
    if intent.is_file():
        topic_names = _json_array(intent, "topics") or []
        intent_extra = {
            "topics": len(topic_names),
            "topic_names": [t for t in topic_names if isinstance(t, str)],
            "surging_accounts": _count_json_array(intent, "accounts"),
        }
    sources.append(
        _source(
            id="intent_topics",
            label="Top-of-funnel intent (Intentsify/Bombora)",
            speaker=CUSTOMER_VOICE,
            provenance_class="behavioral",
            present=intent.is_file(),
            latest=_latest([intent], today) if intent.is_file() else None,
            extra=intent_extra,
            note="Behavioral in-market signal. Surging-account list is empty until the first "
            "weekly Intentsify cycle; Bombora heat rides inline in the prospect briefs.",
        )
    )

    # 3b — Prospect briefs + pipeline pointer (our scoring/targeting). BD FOCUS.
    briefs = sorted((croot / "prospects").glob("prospects-*.md"))
    pipeline = croot / "prospects" / "latest.json"
    brief_extra = {}
    if pipeline.is_file():
        brief_extra["pipeline_accounts"] = _count_json_array(pipeline, "items")
    sources.append(
        _source(
            id="prospect_briefs",
            label="Prospect briefs & scored pipeline",
            speaker=BD_FOCUS,
            provenance_class="our-interpretation",
            present=bool(briefs) or pipeline.is_file(),
            corpus_count=len(briefs),
            latest=_latest(briefs, today),
            extra=brief_extra,
            note="Where BD is pointed: heat/tier/why-now are our scoring, not the market's pull.",
        )
    )

    # 4 — LinkedIn reply/post drafts (our framing of a customer's post). BD FOCUS.
    #     The quoted post inside each draft is customer-voice — extract at read time.
    li = sorted(
        [*(croot / "linkedin").glob("*.md"), *(croot / "accounts").glob("*/linkedin-reply-*.md")]
    )
    sources.append(
        _source(
            id="linkedin_replies",
            label="LinkedIn reply/post drafts",
            speaker=BD_FOCUS,
            provenance_class="our-interpretation",
            present=bool(li),
            corpus_count=len(li),
            latest=_latest(li, today),
            note="Each draft leads with a `## Voice capture` block (linkedin-reply v0.6.0+): the "
            "`<!-- voc:customer-voice -->` half is the poster's own words (customer-voice); the "
            "`<!-- voc:bd-focus -->` half is our reply framing (bd-focus). Extract each side from its "
            "marker. Older drafts without the block: pull the quoted original post as customer-voice.",
        )
    )

    # 5 — Account dossiers + outreach packs. BD FOCUS (quoted customer materials = customer-voice).
    try:
        rows = outreach_log.collect_rows(content_root, prof)
    except (ValueError, OSError):
        rows = []
    specs = sorted((croot / "accounts").glob("*/dossier-spec.json"))
    dossier_latest = None
    if rows:
        newest = rows[0]  # collect_rows returns newest-first
        dossier_latest = {
            "path": newest.file,
            "date": newest.draft_date or None,
            "age_days": _age_days(newest.draft_date or None, today),
        }
    sources.append(
        _source(
            id="account_dossiers",
            label="Account dossiers & outreach packs",
            speaker=BD_FOCUS,
            provenance_class="our-interpretation",
            present=bool(rows) or bool(specs),
            corpus_count=len({*(r.account for r in rows), *(p.parent.name for p in specs)}),
            latest=dossier_latest,
            extra={"outreach_packs": len(rows), "dossier_specs": len(specs)},
            note="Our account framing/hooks = bd-focus; a customer's own quoted site/JD/exec "
            "words inside a dossier = customer-voice (extract those separately).",
        )
    )

    # 6 — Adversary-testing viewpoints. EXPERT LENS: real named practitioners'
    #     published frameworks, synthesized by us — not fabricated, not raw demand.
    personas = sorted(p for p in knowledge.glob("adversary-testing/*.md") if p.name != "README.md")
    sources.append(
        _source(
            id="adversary_personas",
            label="Expert lenses (adversary-testing)",
            speaker=EXPERT_LENS,
            provenance_class="expert-synthesis",
            present=bool(personas),
            corpus_count=len(personas),
            latest=_latest(personas, today),
            note="Reasoning lenses built from real, named practitioners' PUBLISHED frameworks (each "
            "file names its source). Real external expert thinking, synthesized and hedged by us — "
            "NOT the person's verified quotes and NOT a demand count. Use to ask which expert "
            "archetype cares and how they'd scrutinize us; name the sources in the brief.",
        )
    )

    present_ids = [s["id"] for s in sources if s["present"]]
    return {
        "kind": "voc-source-manifest",
        "profile": prof,
        "as_of": today.isoformat(),
        "speakers": {
            "customer-voice": "what the market says & does (ground-truth demand)",
            "bd-focus": "where our commercial org is pointed (strategy, not demand)",
            "expert-lens": "real named practitioners' published frameworks, synthesized + hedged by "
            "us (not verified quotes, not a demand count)",
            "mixed": "holds both — split at read time per the source note",
        },
        "sources": sources,
        "summary": {
            "present": len(present_ids),
            "total": len(sources),
            "missing": [s["id"] for s in sources if not s["present"]],
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.voc.collect",
        description="Enumerate the six voice-of-customer sources for a profile → coverage manifest.",
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument("--repo-root", type=Path, default=None, help="Override repo root.")
    parser.add_argument("--out", type=Path, default=None, help="Also write the manifest JSON here.")
    args = parser.parse_args(argv)

    cfg = PathConfig.from_env(repo_root=args.repo_root)
    try:
        manifest = collect(cfg.content_root, cfg.profiles_root, args.profile)
    except ValueError as exc:
        raise SystemExit(f"[voc-collect] {exc}") from exc

    text = json.dumps(manifest, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
