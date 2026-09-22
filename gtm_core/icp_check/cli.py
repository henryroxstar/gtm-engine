"""``python -m gtm_core.prospects icp <verb>`` — check, keyword, propose.

Read-only, always. No subcommand here writes ``profiles/``, and none exposes ``--apply`` or
``--write`` — see :mod:`tests.contracts.test_icp_check_is_read_only`, which asserts that
structurally rather than trusting this docstring.

The only things any verb writes are the optional ``--out`` JSON under ``content/<profile>/``
(operator-requested, named on the command line, never implicit) and one ``icp_check``
``history.jsonl`` event carrying **finding-code counts only, never finding text** — findings
name real third-party companies by design (§R9).
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

from .. import prospects_backlog as pb
from ..confine import ConfinementError, confined_output_path
from ..paths import resolve_content_root, resolve_profiles_root
from ..role_vocabulary import VocabularyError
from .checks import run_checks
from .keyword import DEFAULT_FIELD, DEFAULT_MAX_HITS, DEFAULT_SAMPLE, keyword_hits
from .propose import propose

#: Printed by every `check` run, clean or not. An operator reading a clean result as "our ICP
#: is validated" has read it wrong, and the command must say so in its own output rather than
#: only in the PRD (§6).
#: Failures an operator can fix by editing a file. Each of these already carries a message
#: naming the file and the next action, so they are reported as one line rather than a
#: traceback. Anything NOT in this tuple is a bug in this package and keeps its stack trace.
_OPERATOR_FIXABLE = (
    FileNotFoundError,
    UnicodeDecodeError,
    VocabularyError,
    tomllib.TOMLDecodeError,
)

HONEST_LIMITS = (
    "What this cannot tell you: whether this is the RIGHT ICP. These three checks answer "
    "only whether a criterion is queryable, a persona mappable, and a rubric discriminating "
    "— structural properties. Whether the ICP is correct needs replies, which this command "
    "cannot see. A clean result is not validation of a targeting strategy."
)


#: Gate-marker delimiters. Company names and rubric phrases printed by this CLI are scraped
#: provider text (§R5); the brain pastes that output into a run header, so a marker carried in
#: a company name would land in a document where markers are read. Neutralised at the ONE place
#: untrusted text is rendered — replaced visibly rather than stripped, so the operator can see
#: that something was there.
_MARKER_DELIMS = {"\u27e6": "[", "\u27e7": "]"}


def _inert(text: str) -> str:
    """Render untrusted text with gate-marker delimiters defanged."""
    out = str(text)
    for bad, safe in _MARKER_DELIMS.items():
        out = out.replace(bad, safe)
    return out


def _content_root(args: argparse.Namespace) -> Path:
    return (
        Path(args.content_root).expanduser().resolve()
        if getattr(args, "content_root", None)
        else resolve_content_root()
    )


def _profiles_root(args: argparse.Namespace) -> Path:
    return (
        Path(args.profiles_root).expanduser().resolve()
        if getattr(args, "profiles_root", None)
        else resolve_profiles_root()
    )


def _record_history(profile: str, result, content_root: Path) -> None:
    """One ``icp_check`` event carrying counts by code — never the finding text.

    A finding names real third-party companies; the ledger is not a place for them (§R9).
    Best-effort: a checkout with no content root still gets the critique, which is the point
    of the command — the ledger row is a bonus, matching `email_compliance._ledgers_for`.
    """
    try:
        from gtm_core.ledgers import Ledgers
        from gtm_core.paths import PathConfig

        cfg = PathConfig(
            content_root=content_root,
            profiles_root=resolve_profiles_root(),
            default_profile=profile,
        )
        Ledgers(cfg, profile).append_history(
            {
                "event": "icp_check",
                "skill": "icp-check",
                "profile": profile,
                "product": result.product,
                "overlay": result.overlay,
                "backlog_size": result.backlog_size,
                "finding_counts": result.counts,
            }
        )
    except Exception as exc:  # pragma: no cover - the critique must not fail on a ledger issue
        print(f"[icp-check] note: history event not recorded ({exc})", file=sys.stderr)


def _fail(exc: Exception) -> int:
    """An expected, operator-fixable failure printed as one line, never a stack trace.

    Every message these raise already names the file and the next action (``load_rubric``'s
    FileNotFoundError, ``VocabularyError``, ``TOMLDecodeError``). A traceback buries that under
    frames the operator cannot act on, so it is caught here and printed plainly — the run still
    exits non-zero, so nothing downstream reads it as success.
    """
    print(f"[icp-check] {exc}", file=sys.stderr)
    return 2


def _cli_check(args: argparse.Namespace) -> int:
    content_root = _content_root(args)
    try:
        result = run_checks(
            args.profile,
            profiles_root=_profiles_root(args),
            content_root=content_root,
            product=args.product,
            overlay=args.overlay,
            max_hits=args.max_hits,
        )
    except _OPERATOR_FIXABLE as exc:
        return _fail(exc)

    payload = {
        "profile": result.profile,
        "product": result.product,
        "overlay": result.overlay,
        "backlog_size": result.backlog_size,
        "finding_counts": result.counts,
        "findings": result.findings,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        # ONE summary block, never a per-criterion stream. The 2026-08-19 finding is binding:
        # "Acknowledging 388 findings is not an action a human performs."
        scope = result.profile
        if result.product:
            scope += f" · product {result.product}"
        if result.overlay:
            scope += f" · overlay {result.overlay}"
        print(f"ICP check — {scope} ({result.backlog_size} accounts)")
        print()
        if result.findings:
            counts = ", ".join(f"{code} {n}" for code, n in sorted(result.counts.items()))
            print(f"  FAIL — {len(result.findings)} finding(s): {counts}")
            for f in result.findings:
                print(f"    - {f}")
        else:
            print("  PASS — no ICP findings.")
        print()
        print(f"  {HONEST_LIMITS}")

    if args.out:
        # Confined to the resolved content root. Without this, `--out
        # ../profiles/<t>/knowledge/icp-scoring.toml` would write there — and create parents on
        # the way — which is exactly the tenant-boundary property this package advertises. The
        # flag is operator-supplied and the skill does not cite it, but "the brain cannot reach
        # it today" is a reason to confine it, not a reason to trust it.
        try:
            out = confined_output_path(args.out, content_root=content_root)
        except ConfinementError as exc:
            return _fail(exc)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out}")

    _record_history(args.profile, result, content_root)

    return 0 if (args.warn_only or not result.failed) else 1


def _cli_propose(args: argparse.Namespace) -> int:
    """Print add/amend/retire proposals. Writes nothing, ever — see the module docstring."""
    try:
        result = run_checks(
            args.profile,
            profiles_root=_profiles_root(args),
            content_root=_content_root(args),
            product=args.product,
            overlay=args.overlay,
            max_hits=args.max_hits,
        )
    except _OPERATOR_FIXABLE as exc:
        return _fail(exc)
    print(propose(result.findings), end="")
    return 0


def _cli_keyword(args: argparse.Namespace) -> int:
    try:
        accounts = pb.load_backlog_accounts(
            args.profile, content_root=_content_root(args), strict=True
        )
    except _OPERATOR_FIXABLE as exc:
        return _fail(exc)
    hits = keyword_hits(args.phrase, accounts, field=args.field)

    print(f"phrase:      {hits.phrase!r}")
    print(f"field:       {hits.field}  (rubric key: {hits.rubric_key})")
    print(f"backlog:     {len(accounts)} accounts")
    print(f"hits:        {hits.count}")
    if hits.count > args.max_hits:
        print(f"verdict:     ABOVE the ceiling of {args.max_hits} — read the sample before adding")
    elif hits.count == 0:
        print("verdict:     0 hits — this phrase selects nothing and cannot identify a cohort")
    else:
        print(f"verdict:     within the ceiling of {args.max_hits}")
    if hits.count:
        print(f"sample (up to {args.sample}):")
        for rec in hits.sample(args.sample):
            print(f"  - {_inert(rec.get('company', '<unnamed>'))}")
    print()
    print(
        "This prints the evidence and refuses to classify it — read the sample and judge "
        "whether every hit is a real shape for this cohort; a computer cannot."
    )
    return 0


def _add_common(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--profile", required=True)
    sp.add_argument("--content-root", default=None)
    sp.add_argument("--profiles-root", default=None)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gtm_core.icp_check", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    cp = sub.add_parser("check", help="critique the resolved ICP before anything spends")
    _add_common(cp)
    cp.add_argument("--product", default=None)
    cp.add_argument("--overlay", default=None)
    cp.add_argument("--max-hits", type=int, default=DEFAULT_MAX_HITS)
    cp.add_argument("--json", action="store_true", help="print the finding set as JSON")
    cp.add_argument("--warn-only", action="store_true", help="report findings but exit 0")
    cp.add_argument(
        "--out",
        default=None,
        help="also write the finding set here (under content/<profile>/), so it can be diffed",
    )

    pp = sub.add_parser("propose", help="add/amend/retire proposals — never written by the tool")
    _add_common(pp)
    pp.add_argument("--product", default=None)
    pp.add_argument("--overlay", default=None)
    pp.add_argument("--max-hits", type=int, default=DEFAULT_MAX_HITS)

    kp = sub.add_parser("keyword", help="hit-count a candidate phrase against the live backlog")
    _add_common(kp)
    kp.add_argument("--phrase", required=True)
    kp.add_argument(
        "--field",
        choices=["description", "industry"],
        default=DEFAULT_FIELD,
        help=f"which backlog field to test (default: {DEFAULT_FIELD})",
    )
    kp.add_argument("--max-hits", type=int, default=DEFAULT_MAX_HITS)
    kp.add_argument("--sample", type=int, default=DEFAULT_SAMPLE)

    args = p.parse_args(argv)
    if args.cmd == "check":
        return _cli_check(args)
    if args.cmd == "propose":
        return _cli_propose(args)
    if args.cmd == "keyword":
        return _cli_keyword(args)
    return 2  # pragma: no cover - argparse's `required=True` on subparsers makes this unreachable


if __name__ == "__main__":
    sys.exit(main())
