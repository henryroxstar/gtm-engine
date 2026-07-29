"""GTM outcome distiller — the learning tier of the closed loop (PRD Phase 4).

The GTM analogue of ``/lesson-distill``: reads the outcomes ledger (the capture tier,
``gtm_core.outcomes``), correlates results by **tag** (the angle/hook/persona/segment axis), and
writes a human-readable learnings note ``content/<profile>/learnings/<period>.md`` with a
``## Promote?`` section — candidate edits to the knowledge corpus (``hook-matrix.md``, ``voice.md``,
``case-studies.md``) that an operator applies by hand. It **owns** (overwrites) its own note and only
**reads** the tier below (outcomes.jsonl); it never edits the live corpus — closing the loop that
lets skills improve from results (pain #7).

Deterministic + files-only: a promote-worthy learning is one with enough observations and a clear
rate lift over baseline (the GTM analogue of lesson-distill's "appeared 2+ times"). Reusable across
every profile — takes ``content_root`` + ``profile``, never a global.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from . import knowledge_staging
from . import outcomes as oc
from .paths import resolve_content_root, resolve_profiles_root

#: A tag needs at least this many sends before its rate is trustworthy enough to promote.
DEFAULT_MIN_SENT = 5
#: A tag's reply rate must beat (or trail) the baseline by this factor to be a promote candidate.
DEFAULT_LIFT = 1.3


def _period_label(today: date) -> str:
    return today.strftime("%Y-%m")


def _rate(v):
    return "—" if v is None else f"{v * 100:.1f}%"


def promote_candidates(
    summary: dict, *, min_sent: int = DEFAULT_MIN_SENT, lift: float = DEFAULT_LIFT
) -> list[dict]:
    """Tags that clearly out- or under-perform the baseline reply rate with enough observations."""
    baseline = summary["totals"].get("reply_rate")
    if not baseline:
        return []
    out = []
    for tag, b in summary["by_tag"].items():
        rate = b.get("reply_rate")
        if rate is None or b["sent"] < min_sent:
            continue
        if rate >= baseline * lift:
            out.append({"tag": tag, "direction": "outperforms", "rate": rate, "sent": b["sent"]})
        elif rate <= baseline / lift:
            out.append({"tag": tag, "direction": "underperforms", "rate": rate, "sent": b["sent"]})
    out.sort(key=lambda c: (c["direction"], -c["rate"]))
    return out


def render_learnings(profile: str, period: str, summary: dict, candidates: list[dict]) -> str:
    t = summary["totals"]
    lines = [
        f"# GTM learnings — {profile} — {period}",
        "",
        "> Distilled from `content/"
        + profile
        + "/outcomes.jsonl` by `gtm_core.gtm_distill`. This is"
        " an analysis note, not the corpus — apply the promote candidates below to the named"
        " knowledge topics by hand.",
        "",
        f"**Baseline:** reply rate {_rate(t['reply_rate'])}, meeting rate {_rate(t['meeting_rate'])} "
        f"(sent {t['sent']:g}, replies {t['replies']:g}, meetings {t['meetings']:g}).",
        "",
        "## By channel",
        "",
        "| Channel | Sent | Replies | Reply rate | Meeting rate |",
        "|---|---|---|---|---|",
    ]
    for ch, b in summary["by_channel"].items():
        lines.append(
            f"| {ch} | {b['sent']:g} | {b['replies']:g} | {_rate(b['reply_rate'])} | {_rate(b['meeting_rate'])} |"
        )

    lines += [
        "",
        "## By tag (angle / persona / segment)",
        "",
        "| Tag | Sent | Replies | Reply rate |",
        "|---|---|---|---|",
    ]
    ranked = sorted(
        summary["by_tag"].items(),
        key=lambda kv: (kv[1]["reply_rate"] is None, -(kv[1]["reply_rate"] or 0)),
    )
    for tag, b in ranked:
        lines.append(f"| `{tag}` | {b['sent']:g} | {b['replies']:g} | {_rate(b['reply_rate'])} |")

    lines += ["", "## Promote?", ""]
    if candidates:
        lines.append(
            "Clear signals worth folding into the knowledge corpus (apply by hand to the named "
            "topic, then re-stamp its `refreshed:`):"
        )
        lines.append("")
        for c in candidates:
            topic = (
                "hook-matrix.md" if c["direction"] == "outperforms" else "hook-matrix.md / voice.md"
            )
            verb = "strengthen" if c["direction"] == "outperforms" else "reconsider / soften"
            lines.append(
                f"- `{c['tag']}` **{c['direction']}** — reply rate {_rate(c['rate'])} "
                f"(n={c['sent']:g}). {verb.capitalize()} this angle in `{topic}`."
            )
    else:
        lines.append("_No signal yet clears the promote threshold (need more observations)._")
    lines.append("")
    return "\n".join(lines)


def distill(
    content_root: Path,
    profile: str,
    *,
    period: str | None = None,
    today: date | None = None,
    stage: bool = False,
    profiles_root: Path | None = None,
) -> Path:
    """Write ``content/<profile>/learnings/<period>.md`` from the outcomes ledger. Returns its path.

    With ``stage=True`` also stages a ``learnings`` knowledge candidate via ``knowledge_staging`` so
    the operator can promote it through the standard Phase-3 flow (otherwise the note's ``Promote?``
    section is applied to existing topics by hand)."""
    today = today or date.today()
    period = period or _period_label(today)
    rows = oc.read_outcomes(content_root, profile)
    summary = oc.summarize(rows)
    candidates = promote_candidates(summary)
    note = render_learnings(profile, period, summary, candidates)

    target = content_root / profile / "learnings" / f"{period}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(note, encoding="utf-8")

    if stage and candidates:
        knowledge_staging.stage(content_root, profile, "learnings", note)
    return target


# --- CLI ----------------------------------------------------------------------


def _all_profiles(profiles_root: Path) -> list[str]:
    if not profiles_root.is_dir():
        return []
    return sorted(
        c.name for c in profiles_root.iterdir() if c.is_dir() and (c / "PROFILE.md").is_file()
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.gtm_distill",
        description="Distill the outcomes ledger into a per-profile learnings note (Promote? gated).",
    )
    parser.add_argument("command", choices=("distill",))
    parser.add_argument("--profile", default=None)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--period", default=None, help="note label (default current YYYY-MM)")
    parser.add_argument(
        "--stage", action="store_true", help="also stage a learnings knowledge candidate"
    )
    parser.add_argument("--content-root", default=None)
    args = parser.parse_args(argv)

    content_root = (
        Path(args.content_root).expanduser().resolve()
        if args.content_root
        else resolve_content_root()
    )
    if args.all:
        profiles = _all_profiles(resolve_profiles_root())
    elif args.profile:
        profiles = [args.profile]
    else:
        raise SystemExit("[gtm-distill] pass --profile <slug> or --all")

    for profile in profiles:
        path = distill(content_root, profile, period=args.period, stage=args.stage)
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
