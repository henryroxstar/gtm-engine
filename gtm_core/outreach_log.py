"""Roll up every drafted outreach pack for a profile into one log (md + csv).

The ``prospect`` skill writes one Tier-A outreach pack per account under
``content/<profile>/accounts/<slug>/prospects-*-outreach-<slug>.md`` (see
CLAUDE.md "Per-account outputs"). There is no single place to see everything
drafted across accounts and runs — this module builds that rollup by parsing
the packs already on disk (no new ledger, no duplicated write path) and
writing ``content/<profile>/prospects/outreach-log.{md,csv}``.

Pure stdlib, SDK-free — like :mod:`gtm_core.people` / :mod:`gtm_core.ledgers`,
this shells out cleanly from a skill via ``python -m gtm_core.outreach_log``.

CLI::

    python -m gtm_core.outreach_log build --profile P
"""

from __future__ import annotations

import argparse
import csv
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .paths import PathConfig, _safe_segment

_TITLE_RE = re.compile(r"^#\s*Outreach Pack\s*—\s*(.+?)\s*—\s*([\d\-]+)\s*$", re.M)
_TITLE_FALLBACK_RE = re.compile(r"^#\s*(.+?)\s*—\s*outreach pack", re.M | re.I)
_DATE_IN_NAME_RE = re.compile(r"(\d{8})")
_TIER_RE = re.compile(r"\*\*Tier:\*\*\s*([A-Za-z0-9/]+)")
_SCORE_RE = re.compile(r"\*\*Score:\*\*\s*([\d./]+)")
_PERSONA_RE = re.compile(r"\*\*Primary persona:\*\*\s*(.+)")
_PERSONA_SPLIT_RE = re.compile(r"([^—(]+?)\s*—\s*([^(]+?)\s*\(([^)]*)\)\s*$")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_SUBJECT_RE = re.compile(r"\*\*Subject:\*\*\s*(.+)")
_TOUCH_RE = re.compile(r"\*\*Touch \d+\*\*")
_EMAIL_SECTION_RE = re.compile(r"^##\s*Email", re.M)


@dataclass
class OutreachRow:
    account: str
    draft_date: str
    tier: str
    score: str
    persona_name: str
    persona_title: str
    email_verified: str
    email_address: str
    channels: str
    email_subject: str
    followup_touches_in_ladder: int
    file: str


def _parse_pack(path: Path, content_root: Path) -> OutreachRow:
    text = path.read_text(encoding="utf-8")

    title_m = _TITLE_RE.search(text)
    if title_m:
        account, draft_date = title_m.group(1).strip(), title_m.group(2).strip()
    else:
        fallback_m = _TITLE_FALLBACK_RE.search(text)
        account = fallback_m.group(1).strip() if fallback_m else path.parent.name
        date_m = _DATE_IN_NAME_RE.search(path.name)
        draft_date = (
            f"{date_m.group(1)[:4]}-{date_m.group(1)[4:6]}-{date_m.group(1)[6:]}" if date_m else ""
        )

    tier_m = _TIER_RE.search(text)
    score_m = _SCORE_RE.search(text)
    persona_m = _PERSONA_RE.search(text)
    persona_raw = persona_m.group(1).strip() if persona_m else ""

    name, ptitle, contact = "", "", ""
    split_m = _PERSONA_SPLIT_RE.match(persona_raw)
    if split_m:
        name, ptitle, contact = (g.strip() for g in split_m.groups())
    else:
        name = persona_raw

    verified = "yes" if "verified" in contact.lower() else ("no" if contact else "")
    email_m = _EMAIL_RE.search(contact)
    email_address = email_m.group(0) if email_m else ""

    subject_m = _SUBJECT_RE.search(text)
    subject = subject_m.group(1).strip() if subject_m else ""

    has_linkedin = "LinkedIn DM" in text
    has_email = bool(_EMAIL_SECTION_RE.search(text))
    channels = "+".join(
        c for c in (("LinkedIn" if has_linkedin else ""), ("Email" if has_email else "")) if c
    )

    followups = len(_TOUCH_RE.findall(text))

    try:
        rel = str(path.relative_to(content_root.parent))
    except ValueError:
        rel = str(path)

    return OutreachRow(
        account=account,
        draft_date=draft_date,
        tier=tier_m.group(1).strip() if tier_m else "",
        score=score_m.group(1).strip() if score_m else "",
        persona_name=name,
        persona_title=ptitle,
        email_verified=verified,
        email_address=email_address,
        channels=channels,
        email_subject=subject,
        followup_touches_in_ladder=followups,
        file=rel,
    )


def collect_rows(content_root: Path, profile: str) -> list[OutreachRow]:
    """Parse every outreach pack under ``content/<profile>/accounts/*/``, newest first."""
    accounts_dir = content_root / _safe_segment(profile, "profile") / "accounts"
    files = sorted(accounts_dir.glob("*/prospects-*outreach-*.md"))
    rows = [_parse_pack(f, content_root) for f in files]
    rows.sort(key=lambda r: (r.draft_date or "0000-00-00", r.account), reverse=True)
    return rows


def _esc(value: str) -> str:
    return (value or "").replace("|", "/").replace("\n", " ").strip()


def render_markdown(rows: list[OutreachRow], profile: str) -> str:
    lines = [
        f"# Outreach Log — {profile}",
        "",
        f"{len(rows)} outreach pack(s) drafted. Generated from files under "
        f"`content/{profile}/accounts/*/prospects-*outreach-*.md` — regenerate with "
        "`python -m gtm_core.outreach_log build --profile "
        f"{profile}` after any prospecting run.",
        "",
        "| Date | Account | Tier | Persona | Email | Subject | Channels | File |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        persona = _esc(r.persona_name)
        if r.persona_title:
            persona += f" ({_esc(r.persona_title)})"
        email = r.email_address or ("to enrich" if r.email_verified == "no" else "")
        lines.append(
            f"| {r.draft_date} | {_esc(r.account)} | {_esc(r.tier)} | {persona} | "
            f"{_esc(email)} | {_esc(r.email_subject)} | {r.channels} | `{r.file}` |"
        )
    return "\n".join(lines) + "\n"


def build_outreach_log(content_root: Path, profile: str) -> dict:
    """Parse all outreach packs for ``profile`` and (re)write the rollup md + csv.

    Returns ``{"total", "md_path", "csv_path"}`` for the caller to report/log.
    """
    rows = collect_rows(content_root, profile)
    out_dir = content_root / _safe_segment(profile, "profile") / "prospects"
    out_dir.mkdir(parents=True, exist_ok=True)

    md_path = out_dir / "outreach-log.md"
    md_path.write_text(render_markdown(rows, profile), encoding="utf-8")

    csv_path = out_dir / "outreach-log.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        fieldnames = (
            list(asdict(rows[0]).keys())
            if rows
            else [f.name for f in OutreachRow.__dataclass_fields__.values()]
        )
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(asdict(r))

    return {"total": len(rows), "md_path": str(md_path), "csv_path": str(csv_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.outreach_log",
        description="Roll up drafted outreach packs into content/<profile>/prospects/outreach-log.{md,csv}.",
    )
    parser.add_argument("--repo-root", type=Path, default=None, help="Override repo root.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    build = sub.add_parser("build", help="(Re)generate the outreach log for a profile.")
    build.add_argument("--profile", required=True)

    args = parser.parse_args(argv)
    cfg = PathConfig.from_env(repo_root=args.repo_root)

    if args.cmd == "build":
        try:
            result = build_outreach_log(cfg.content_root, args.profile)
        except ValueError as exc:
            raise SystemExit(f"[outreach-log] {exc}") from exc
        print(f"{result['total']} rows -> {result['md_path']}")
        return 0

    parser.error(f"unknown command {args.cmd!r}")
    return 1  # unreachable


if __name__ == "__main__":
    raise SystemExit(main())
