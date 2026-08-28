"""Turn human eval labels into changes to the NEXT send list.

This is the step that was missing. Everything upstream of it worked: the sheet builds, the
labels validate, the holdout seals. And then the labels sat in a JSONL file that nothing
read, so an operator could correctly identify five accounts that should never have been
contacted and the next generated CSV would contain all five again.

**The double write, and why one half is not enough.** Verified 2026-08-22 against
``prospects_consolidate.py``: its only ``status`` handling is Apollo's ``email_status``
(deliverability). **Nothing in the send-list build filters on lifecycle ``status``.** So
setting ``status: disqualified`` in ``latest.json`` is necessary — the dashboard and every
human reader go by it — but on its own it is decoration: the row returns in the next build
output regardless. The durable half is the **suppression ledger**, which
``suppression verify`` enforces against regenerated CSVs. Hence both, always:

1. ``latest.json`` lifecycle ``status`` (via :func:`gtm_core.prospects_state.set_status`,
   which exists because the normal merge path treats ``status`` as sticky and would
   silently discard the change);
2. a suppression-ledger row under :data:`gtm_core.suppression.EVAL_DISQUALIFIED` — which
   is deliberately **not** in ``PROVIDER_DNC_REASONS``, because a fit judgment is not a
   legal do-not-contact and must never be pushed to a provider's permanent DNC list.

**Three grains, on purpose.** A label carries judgments at three different scopes and
conflating them destroys good accounts. ``right_person is False`` says *this individual* is
wrong — the company may be exactly right, so it suppresses the address and leaves the
account alone. ``account_fit is False`` says *this company* is wrong, and only that field
does: it is the one question on the label asked about the account rather than about the
row's fact or copy. Everything else is a statement about *this email*.

``send_it is False`` **alone is never disqualifying**, and neither is any combination of
the fact-scoped sub-checks. ``send_it: N`` is the most common label on the sheet and it
usually means "this copy is wrong", which the repair loop fixes. This module previously
read a conjunction of three fact-scoped booleans as an account judgment and disqualified a
good account on a weak-but-honest signal for it; see :func:`_is_disqualifying` for that
defect and for why ``category_relation`` cannot serve as the evidence instead.

Dry-run (``plan``) is the default and writes nothing. ``apply`` refuses above a 20%
disqualification ceiling without ``--force``: a join bug that matches too broadly looks
exactly like a devastating list, and the ceiling is what makes the two distinguishable
before rather than after the write.

Stdlib-only, no I/O beyond the profile's own state files.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .eval_calibration import Label, read_labels
from .paths import _safe_segment
from .prospect_paths import suppression_ledger
from .prospects_state import _identity_key, load_latest, set_status
from .suppression import EVAL_DISQUALIFIED, EVAL_WRONG_PERSON, Suppression, append

__all__ = [
    "DISQUALIFY_CEILING",
    "Action",
    "WritebackPlan",
    "build_plan",
    "main",
]

#: Refuse to apply if more than this share of the list would be disqualified. A writeback
#: that disqualifies most of a list is far more likely to be a join defect than a genuinely
#: terrible list, and the two are indistinguishable from the summary line alone.
DISQUALIFY_CEILING = 0.20


@dataclass
class Action:
    """One durable change a label implies, with the evidence that implies it."""

    email: str
    company: str
    kind: str  # "disqualify-account" | "suppress-person"
    reason: str
    row_id: str
    note: str = ""
    #: The account's stamped id, when the joined row carries one. Preferred over any
    #: derived key: it is the identity `latest.json` itself assigned, so the join cannot
    #: disagree with the ledger about which account this is.
    account_id: str = ""
    #: The COMPANY's domain, carried from the joined row. Not derivable from ``email``:
    #: a contact reachable only at free webmail, at an alias domain, or at an academic
    #: address yields a domain that belongs to a mail provider or a university, not to
    #: the account being disqualified.
    company_domain: str = ""


@dataclass
class WritebackPlan:
    actions: list[Action] = field(default_factory=list)
    labels_read: int = 0
    labels_joined: int = 0
    unjoined_row_ids: list[str] = field(default_factory=list)
    stale_row_ids: list[str] = field(default_factory=list)
    list_size: int = 0

    @property
    def disqualifications(self) -> list[Action]:
        return [a for a in self.actions if a.kind == "disqualify-account"]

    @property
    def suppressions(self) -> list[Action]:
        return [a for a in self.actions if a.kind == "suppress-person"]

    @property
    def share_disqualified(self) -> float:
        if not self.list_size:
            return 0.0
        return len({a.email for a in self.disqualifications}) / self.list_size


def _read_internal(path: Path) -> dict[str, dict]:
    """``row_id -> {email, company, ...}`` from the internal eval record.

    An accumulate, never a dict comprehension. The same email legitimately appears under
    several row_ids (one per touch), and a comprehension keyed the other way silently keeps
    the last — the exact defect that inverted a segment count in this workstream three
    times.
    """
    out: dict[str, dict] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        row_id = rec.get("row_id")
        if row_id:
            out[row_id] = rec
    return out


def _is_disqualifying(label: Label) -> bool:
    """Does this label say the ACCOUNT is a bad fit?

    One field, answered about the account. ``account_fit is False`` and nothing else.

    **What this replaced, and why the replacement is not a widening.** Until 2026-08-23 this
    asked for the conjunction of ``send_it``/``fact_creates_problem``/``fact_supports_pitch``
    all being False. The conjunction was chosen to stop a copy defect being read as an
    account defect — and it could not do that job, because **all three of its inputs are
    scoped to the row's own signal, so none of them can express a judgment about the
    company.** Requiring three fact-scoped Falses does not approximate an account judgment;
    it just makes a fact-scoped one rarer.

    The shape that broke it: a hospital system whose ``{{Why Now}}`` clause said it was
    already centralising AI oversight and governance. That fact genuinely creates no problem
    we solve and genuinely does not support the pitch, and the operator genuinely would not
    send *that email* — three honest Falses. The account is a fine prospect, and the
    operator's own note on the row was a copy complaint ("speak plainly", "contextualise for
    a healthcare company"), not a fit complaint. It was disqualified anyway. A weak-but-true
    signal on a good account is the single most common way to score three Falses, so the old
    predicate was most wrong exactly where it fired most.

    Do not restore the trio as a fallback (``account_fit is False or <trio>``). That
    reinstates the false positive verbatim — the trio's failure is not that it is too
    narrow, it is that it is measuring the wrong grain — and a belt-and-braces disjunction
    inherits the weaker branch's errors, not the stronger branch's judgment.

    **Why not** ``category_relation`` **, the pipeline's existing account-level field?**
    Evaluated 2026-08-23 and rejected on three independent grounds:

    * *It is unreachable.* ``competitor`` and ``regulator`` are ``block``-level findings in
      :func:`gtm_core.signal_record.check_record`, surfaced as hard errors by
      :mod:`gtm_core.account_integrity`. A row carrying either never reaches a staged
      sequence CSV, and :func:`gtm_core.build_eval_sheet.load_live_rows` samples only staged
      ``verdict=send`` rows — so a real sheet row structurally cannot carry an adverse value.
    * *Its only positive class is the one we must not act on.* The sole sheet rows that do
      carry an adverse relation are the ``relation-competitor`` / ``relation-regulator``
      injections from ``INJECTION_RECIPES`` — planted defects this module already excludes,
      because acting on one would disqualify a company for a mutation this program authored.
    * *It is circular.* ``build_eval_sheet`` hides ``category_relation`` from the labeler
      precisely because it is a conclusion the label is supposed to REACH. Gating the
      writeback on the recorded value would mean human review could never overturn a
      research error — and a row mis-recorded as ``prospect`` is exactly the failure this
      writeback exists to catch. Measured on the 2026-08-21 sheet: every account the
      operator correctly disqualified was recorded ``prospect``, so re-keying on the
      recorded relation would have disqualified none of them.

    ``None`` — not answered — never disqualifies. That is the fail-safe direction and it is
    load-bearing twice: a missing answer is an absence of account-level evidence rather than
    an accusation, and it is what lets this field ship without migrating a single existing
    label file. Labels written before the field simply produce no disqualifications until
    the question is asked on the next sheet.
    """
    return label.account_fit is False


def build_plan(
    labels: list[Label],
    internal: dict[str, dict],
    *,
    list_size: int = 0,
    spec_sha256: str = "",
    csv_sha256: str = "",
) -> WritebackPlan:
    """Join labels to their rows and derive the durable actions, without writing anything.

    When ``spec_sha256``/``csv_sha256`` are supplied, a label whose fingerprint no longer
    matches is REFUSED rather than applied — a re-cut spec invalidates every row_id, and
    applying a stale label writes a judgment about copy that no longer exists to a row that
    never received it.
    """
    plan = WritebackPlan(labels_read=len(labels), list_size=list_size)
    # Accumulate per email: one address can carry several labels (multiple touches, or a
    # duplicated consistency-check row). Every one of them must survive the join.
    per_email: dict[str, list[tuple[Label, dict]]] = defaultdict(list)

    for label in labels:
        if spec_sha256 and csv_sha256:
            if label.spec_sha256 != spec_sha256 or label.csv_sha256 != csv_sha256:
                plan.stale_row_ids.append(label.row_id)
                continue
        row = internal.get(label.row_id)
        if row is None:
            plan.unjoined_row_ids.append(label.row_id)
            continue
        email = (row.get("email") or "").strip().lower()
        if not email:
            plan.unjoined_row_ids.append(label.row_id)
            continue
        plan.labels_joined += 1
        per_email[email].append((label, row))

    for email, pairs in sorted(per_email.items()):
        company = next((r.get("company") or "" for _, r in pairs), "")
        company_domain = next(
            (r.get("company_domain") or "" for _, r in pairs if r.get("company_domain")), ""
        )
        account_id = next((r.get("account_id") or "" for _, r in pairs if r.get("account_id")), "")
        # An injected row is a planted defect in copy, not evidence about a real account.
        # Acting on one would disqualify a company for a mutation this program authored.
        real = [(lab, row) for lab, row in pairs if not lab.injected]
        if not real:
            continue
        if any(_is_disqualifying(lab) for lab, _ in real):
            note = next((lab.note for lab, _ in real if _is_disqualifying(lab) and lab.note), "")
            plan.actions.append(
                Action(
                    email=email,
                    company=company,
                    kind="disqualify-account",
                    reason=EVAL_DISQUALIFIED,
                    row_id=next(lab.row_id for lab, _ in real if _is_disqualifying(lab)),
                    note=note,
                    company_domain=company_domain,
                    account_id=account_id,
                )
            )
        elif any(lab.right_person is False for lab, _ in real):
            note = next((lab.note for lab, _ in real if lab.right_person is False and lab.note), "")
            plan.actions.append(
                Action(
                    email=email,
                    company=company,
                    kind="suppress-person",
                    reason=EVAL_WRONG_PERSON,
                    row_id=next(lab.row_id for lab, _ in real if lab.right_person is False),
                    note=note,
                    company_domain=company_domain,
                    account_id=account_id,
                )
            )
    return plan


def _render(plan: WritebackPlan) -> str:
    lines = [
        f"{plan.labels_read} label(s) read, {plan.labels_joined} joined to a row",
        f"{len(plan.disqualifications)} account disqualification(s), "
        f"{len(plan.suppressions)} person suppression(s)",
    ]
    if plan.list_size:
        lines.append(
            f"{plan.share_disqualified:.0%} of the {plan.list_size}-row list would be "
            f"disqualified (ceiling {DISQUALIFY_CEILING:.0%})"
        )
    if plan.stale_row_ids:
        lines.append(
            f"REFUSED {len(plan.stale_row_ids)} stale label(s): the spec or CSV changed "
            f"since they were written, so their row_ids describe copy that no longer exists"
        )
    if plan.unjoined_row_ids:
        lines.append(
            f"WARNING {len(plan.unjoined_row_ids)} label(s) joined to no row — check the "
            f"internal eval record is the one this sheet was built from"
        )
    lines.append("")
    for action in plan.actions:
        lines.append(f"  {action.kind:20} {action.reason:20} {action.email}  ({action.company})")
        if action.note:
            lines.append(f"  {'':20} {'':20} note: {action.note}")
    if not plan.actions:
        lines.append(
            "  no durable changes. If you expected some, check the join above: "
            "'0 disqualified' and 'the join matched nothing' print identically."
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m gtm_core.eval_writeback",
        description=(
            "Apply human eval labels to the prospect list: disqualify bad-fit accounts and "
            "suppress wrong-person addresses, so a labeled defect changes the NEXT run. "
            "`plan` is the default and writes nothing."
        ),
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, helptext in (
        ("plan", "show what would change; write nothing"),
        ("apply", "make the durable changes (latest.json + suppression ledger)"),
    ):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("--profile", required=True)
        s.add_argument("--labels", required=True, type=Path)
        s.add_argument(
            "--internal",
            required=True,
            type=Path,
            help="the internal-<date>-<campaign>.jsonl that maps row_id -> email/company",
        )
        s.add_argument("--spec-sha256", default="", help="refuse labels whose fingerprint moved")
        s.add_argument("--csv-sha256", default="")
        s.add_argument(
            "--ledger",
            type=Path,
            default=None,
            help=(
                "suppression ledger CSV (default: "
                "content/<profile>/prospects/sequences/.pool/suppression.csv)"
            ),
        )
        if name == "apply":
            s.add_argument(
                "--force",
                action="store_true",
                help="apply even above the disqualification ceiling",
            )

    args = ap.parse_args(argv)
    _safe_segment(args.profile, "profile")

    labels = read_labels(args.labels)
    internal = _read_internal(args.internal)
    try:
        list_size = len(load_latest(args.profile).get("items", []))
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"cannot read latest.json: {exc}", file=sys.stderr)
        return 1

    plan = build_plan(
        labels,
        internal,
        list_size=list_size,
        spec_sha256=args.spec_sha256,
        csv_sha256=args.csv_sha256,
    )
    print(_render(plan))

    if args.cmd == "plan":
        print("\nDRY RUN — nothing written. Re-run as `apply` to make these changes.")
        return 0

    if plan.share_disqualified > DISQUALIFY_CEILING and not args.force:
        print(
            f"\nREFUSED: {plan.share_disqualified:.0%} of the list would be disqualified, "
            f"above the {DISQUALIFY_CEILING:.0%} ceiling. A writeback this broad is more "
            f"likely a join defect than a genuinely bad list — check the join, then pass "
            f"--force if it is real.",
            file=sys.stderr,
        )
        return 1

    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    # The canonical ledger, beside the lists it protects. This default used to be
    # `prospects/suppression.csv` — one segment short of where every gate reads —
    # so a disqualification was durably recorded somewhere nothing consulted.
    ledger_path = args.ledger or suppression_ledger(args.profile)

    # Half one: the durable half. The ledger is what `suppression verify` enforces against
    # a regenerated CSV, so this is the write that actually keeps the row out of the next
    # send. It goes first — if the second write fails, the person is still protected.
    entries = [
        Suppression(
            email=a.email,
            reason=a.reason,
            date=stamp,
            note=(a.note or a.kind)[:200],
        )
        for a in plan.actions
    ]
    added, skipped = append(ledger_path, entries)
    print(f"\nsuppression ledger: {added} added, {skipped} already present -> {ledger_path}")

    # Half two: the visible half. `set_status`, not `merge` — the merge path treats status
    # as sticky and would discard this silently.
    updates = {}
    for action in plan.disqualifications:
        # The COMPANY's domain, never the email's. Splitting the address gave
        # `gmail.com` for a freemail contact and the university for an academic one,
        # so the key named a mail provider rather than the account — and because a
        # parsed domain is never empty, the company fallback below was unreachable.
        # The stamped id first: it is what latest.json assigned, so it cannot disagree
        # with the ledger. The derived keys remain as the fallback for a row that
        # predates the stamping.
        key = (
            (f"a:{action.account_id.strip().lower()}" if action.account_id.strip() else "")
            or _identity_key({"domain": action.company_domain.strip().lower()})
            or _identity_key({"company": action.company})
        )
        if key:
            updates[key] = "disqualified"
    if updates:
        summary = set_status(
            args.profile,
            updates,
            reason=EVAL_DISQUALIFIED,
            source=f"eval-{stamp}",
        )
        print(
            f"latest.json: {summary['changed']} changed, {summary['unchanged']} already set, "
            f"{len(summary['unmatched'])} unmatched of {summary['total']} account(s)"
        )
        if summary["unmatched"]:
            print(
                "  unmatched keys (no such account — NOT created): "
                + ", ".join(summary["unmatched"][:5])
            )
    else:
        print("latest.json: no account-level changes")

    print(
        "\nVerify the change survives a rebuild:\n"
        f"  uv run python -m gtm_core.suppression verify --ledger {ledger_path} "
        "--target <regenerated.csv>"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
