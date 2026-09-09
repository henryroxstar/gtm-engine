"""The canonical locations of the prospecting pipeline's shared state.

Every path here was being spelled out by hand at each call site, and they had
drifted. The suppression ledger is the clearest case: the writeback appended to
``prospects/suppression.csv`` while every gate that enforces it reads
``prospects/sequences/.pool/suppression.csv``, so an eval disqualification landed
in a file nothing consults. Two skills documented a third spelling. The ledger was
doing its job; it was simply not where the readers were looking.

A path is a fact, and this module is its one home. Callers quote these helpers
rather than composing segments, so a rename moves one line instead of eight, and a
typo is an ImportError instead of a silently-empty ledger.

Every segment passes through :func:`gtm_core.paths._safe_segment`: a
traversal-shaped profile name fails closed here exactly as it does everywhere else
under the tenant boundary. Stdlib-only and dependency-light on purpose, so any gate
can import it without dragging in a profile.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .paths import _safe_segment, resolve_content_root

__all__ = [
    "prospects_dir",
    "sequences_dir",
    "pool_dir",
    "evals_dir",
    "latest_json",
    "master_list",
    "ready_to_load",
    "needs_verification",
    "suppression_ledger",
    "adjudications_dir",
    "adjudication_records",
    "outcomes_jsonl",
    "accounts_dir",
    "status_page",
    "LEGACY_SUPPRESSION_LEDGERS",
    "main",
]


def prospects_dir(profile: str, content_root: Path | None = None) -> Path:
    root = content_root if content_root is not None else resolve_content_root()
    return root / _safe_segment(profile, "profile") / "prospects"


def sequences_dir(profile: str, content_root: Path | None = None) -> Path:
    return prospects_dir(profile, content_root) / "sequences"


def pool_dir(profile: str, content_root: Path | None = None) -> Path:
    """Hidden home for everything that is not the one human-facing load file."""
    return sequences_dir(profile, content_root) / ".pool"


def evals_dir(profile: str, content_root: Path | None = None) -> Path:
    """Labels, sealed holdouts, sheets, and adjudication records."""
    return prospects_dir(profile, content_root) / "evals"


def latest_json(profile: str, content_root: Path | None = None) -> Path:
    """The cumulative account ledger — the record of which every CSV is a view."""
    return prospects_dir(profile, content_root) / "latest.json"


def master_list(profile: str, content_root: Path | None = None) -> Path:
    return pool_dir(profile, content_root) / "master-list.csv"


def ready_to_load(profile: str, content_root: Path | None = None) -> Path:
    """The ONE file a human (or the email-sequence skill) loads from."""
    return sequences_dir(profile, content_root) / "ready-to-load.csv"


def needs_verification(profile: str, content_root: Path | None = None) -> Path:
    return pool_dir(profile, content_root) / "needs-verification.csv"


def suppression_ledger(profile: str, content_root: Path | None = None) -> Path:
    """The durable local exclusion ledger.

    Lives beside the pooled lists it protects, because it outlives every one of
    them: the CSVs are rebuilt, the ledger is not.
    """
    return pool_dir(profile, content_root) / "suppression.csv"


#: Spellings that shipped before this module existed, newest-wrong-guess first.
#: Only ever *read* from, so an exclusion written to the wrong place still keeps a
#: person out of a send while the operator reconciles the files. Never written to.
LEGACY_SUPPRESSION_LEDGERS: tuple[str, ...] = ("suppression.csv",)


def legacy_suppression_ledgers(profile: str, content_root: Path | None = None) -> list[Path]:
    base = prospects_dir(profile, content_root)
    canonical = suppression_ledger(profile, content_root)
    return [p for name in LEGACY_SUPPRESSION_LEDGERS if (p := base / name) != canonical]


def adjudications_dir(profile: str, content_root: Path | None = None) -> Path:
    """Judge records live with the other eval artifacts, not beside the send lists.

    They are measurement, not pipeline state: the holdouts they are scored against
    are already here, and splitting the two made a scoring run read one directory
    and write the other.
    """
    return evals_dir(profile, content_root)


def adjudication_records(profile: str, date: str, content_root: Path | None = None) -> Path:
    """``evals/adjudication-<date>.jsonl`` for a run on ``date`` (ISO ``YYYY-MM-DD``)."""
    return (
        adjudications_dir(profile, content_root)
        / f"adjudication-{_safe_segment(date, 'date')}.jsonl"
    )


def outcomes_jsonl(profile: str, content_root: Path | None = None) -> Path:
    """Append-only per-wave send outcomes — the input the wave gate reads."""
    return prospects_dir(profile, content_root) / "outcomes.jsonl"


def accounts_dir(profile: str, content_root: Path | None = None) -> Path:
    """Per-account deliverables — dossiers, outreach packs, briefs.

    A sibling of ``prospects/``, not a child: the run-level files describe a *run*, these
    describe an *account* that outlives it. The outreach-log rollup globs only here, so a
    pack written anywhere else is invisible to it.
    """
    root = content_root if content_root is not None else resolve_content_root()
    return root / _safe_segment(profile, "profile") / "accounts"


def status_page(profile: str, content_root: Path | None = None) -> Path:
    """The operator's status page — the one page to open when asking "where are we".

    Owned by :mod:`gtm_core.email_campaign_dashboard`, which is why the filename is
    imported rather than spelled here. Listed in this module because an operator asking
    where the state lives should not have to already know which module renders it: the
    page moved once (``prospects/status.html`` is now a redirect stub) and the skills
    naming the old path went on naming it.
    """
    from .email_campaign_dashboard.config import dashboard_path

    return dashboard_path(profile, content_root)


#: Printed by the CLI, in the order a run touches them.
_RESOLVERS = (
    ("latest.json", latest_json, "the cumulative account ledger — the record of record"),
    ("master-list.csv", master_list, "every person-row ever folded in (audit)"),
    ("ready-to-load.csv", ready_to_load, "the ONE file a human or the sequencer loads"),
    ("needs-verification.csv", needs_verification, "hold queue: deliverability unproven"),
    ("suppression.csv", suppression_ledger, "durable local exclusions; survives a rebuild"),
    ("outcomes.jsonl", outcomes_jsonl, "per-wave send outcomes; read by the wave gate"),
    ("evals/", evals_dir, "labels, sealed holdouts, adjudication records"),
    ("accounts/", accounts_dir, "per-account dossiers and outreach packs"),
    (
        "status page",
        status_page,
        "the operator page: who we're emailing, what we're saying, what's blocking",
    ),
)


def main(argv: list[str] | None = None) -> int:
    """Print this profile's canonical pipeline locations.

    Exists so a skill can *ask* where a file lives rather than spelling the path out and
    drifting from the code — which is how the suppression ledger came to be documented
    in three places, one of them wrong.
    """
    import argparse

    p = argparse.ArgumentParser(
        prog="gtm_core.prospect_paths",
        description="Print the canonical locations of a profile's prospecting state.",
    )
    p.add_argument("--profile", required=True)
    p.add_argument("--name", default="", help="print only this path, bare (for scripting)")
    args = p.parse_args(argv)

    if args.name:
        match = next((fn for label, fn, _ in _RESOLVERS if label == args.name), None)
        if match is None:
            known = ", ".join(label for label, _, _ in _RESOLVERS)
            print(f"unknown path {args.name!r}; known: {known}", file=sys.stderr)
            return 2
        print(match(args.profile))
        return 0

    width = max(len(label) for label, _, _ in _RESOLVERS)
    for label, fn, note in _RESOLVERS:
        path = fn(args.profile)
        mark = " " if path.exists() else "-"
        print(f"{mark} {label:<{width}}  {path}")
        print(f"  {'':<{width}}  {note}")
    print("\n('-' marks a path that does not exist yet — normal on a first run.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
