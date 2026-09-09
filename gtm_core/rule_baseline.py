"""The 69-rule fleet, scored as a classifier on the same holdout the judge is scored on.

PRD §3.2 requires this comparison and nothing produced it. Without it, "the judge catches
defects" is unfalsifiable: the rules already catch defects, so a judge that matches the
operator 90% of the time might be adding nothing a regex was not already contributing. The
honest outcome of running this is sometimes *drop the judge* — which is a result the
program has to be able to reach, or the validation is theatre.

The baseline is the crudest possible classifier over the existing gate, deliberately:
**a row predicts "would not send" iff the linter emits any ERROR for it.** No weighting,
no rule selection, no tuning. A tuned baseline would be a second model competing with the
judge; an untuned one answers the only question that matters — is the deterministic tier
already doing this job.

WARN is excluded on purpose. WARNs are advisory by design and several fire on most of a
healthy list; folding them in would make the baseline predict "do not send" for nearly
every row, which scores a great TPR and a useless TNR — a classifier that says no to
everything is not a baseline, it is a broken one.

Imports the linter through the same ``sys.path`` insert :mod:`gtm_core.cells` uses for
``seat_of`` and :mod:`gtm_core.build_eval_sheet` uses for ``render``/``parse_spec``. Two
implementations of "what does this row's copy actually look like" would drift, and the
drift would be invisible: the baseline would score copy nobody sends.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from .eval_calibration import _row_id

_LINTER_DIR = Path(__file__).resolve().parent.parent / "tests" / "linter"
if str(_LINTER_DIR) not in sys.path:  # pragma: no cover - import plumbing
    sys.path.insert(0, str(_LINTER_DIR))

from merge_render_linter import (  # noqa: E402
    _load_bans,
    _load_domain_aliases,
    _load_premise_vocab,
    lint_merge_render,
    parse_spec,
)

__all__ = ["baseline_predictions", "main"]

#: Pseudo-addresses the linter uses for findings that belong to the spec, the pack, or the
#: whole list rather than to one recipient. Kept for documentation and for the test that
#: pins them — but the ACTUAL filter is :func:`_is_recipient`, a positive test, because a
#: denylist of magic strings is the wrong shape here. There turned out to be a third form
#: (``"touch1 (all 2 renders)"``, the template-wide aggregate) that a two-entry denylist
#: silently missed, and a fourth can be added to the linter at any time without this module
#: hearing about it. Asking "is this an address" fails safe; listing what isn't does not.
_FILE_LEVEL_LABELS = frozenset({"PACK", "SPEC"})


def _is_recipient(label: str) -> bool:
    """Whether a violation's ``email`` names an actual recipient rather than the file.

    Deliberately a positive test. Only a per-recipient finding may condemn a row; anything
    else — a spec-level premise finding, a pack-level note, a template-wide aggregate — is
    a statement about the copy or the list, and attributing it to every row would score the
    fleet as a reject-everything classifier with a perfect TPR and a useless TNR.
    """
    value = (label or "").strip()
    if not value or value.upper() in _FILE_LEVEL_LABELS:
        return False
    # An aggregate label reads like "touch1 (all 2 renders)" — no address can contain a
    # space, so this one check covers every aggregate form the linter emits today or later.
    return "@" in value and " " not in value


def baseline_predictions(
    spec_path: Path,
    csv_path: Path,
    *,
    signoff: str = "",
    touch: int = 1,
    profile: str = "",
    ban_file: Path | None = None,
    case_study_file: Path | None = None,
    stem_file: Path | None = None,
    artifact_file: Path | None = None,
    hook_matrix: Path | None = None,
) -> dict[str, bool]:
    """``row_id -> predicted send_it`` from the deterministic rule fleet.

    ``True`` means the rules found no ERROR for that row, i.e. the fleet predicts it is
    sendable — the same polarity as a label's ``send_it`` and as a judge prediction, so
    all three go into :func:`gtm_core.eval_calibration.confusion` unchanged.

    **Pass the same auxiliary inputs the real gate gets** — ``profile`` (premise vocabulary
    and domain aliases), the ban/case-study/stem/artifact lists, the hook matrix. Found by
    running this on a live list 2026-08-22: without them the fleet predicted **57 of 57
    rows sendable**, because most ERROR-level rules are opt-in on a profile-supplied list
    and simply cannot fire. A baseline that understates the rules biases the whole
    comparison toward keeping the judge, which is the one direction an honest test must not
    lean. The caller is warned when the fleet flags nothing at all.

    Rows the linter reports at file level (``email`` in :data:`_FILE_LEVEL_LABELS`) are
    template- or list-wide findings and are not attributed to any row: counting them would
    mark the entire list unsendable on one template defect, which tells you nothing about
    per-row discrimination.

    **Where to run this, and where not to.** Running it on an already-gated send list
    (2026-08-22) returned 57 of 57 sendable — correctly. That list had already been through
    the gate and repaired; every per-row defect the fleet can see was fixed before the file
    was written, and the one remaining ERROR was a SPEC-level statement about the list
    against its argument. **A post-gate list cannot measure the fleet's discrimination,
    because the fleet already spent it.** Run this against the golden set's source instead —
    it carries the fault-injected stratum the rules are supposed to catch, which is the only
    population where "did the rules find the bad rows" is an answerable question.
    """
    spec_text = Path(spec_path).read_text(encoding="utf-8")
    touches = parse_spec(spec_text)
    with Path(csv_path).open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    violations, _stats = lint_merge_render(
        touches,
        rows,
        signoff=signoff,
        extra_bans=_load_bans(ban_file) if ban_file else (),
        case_studies=_load_bans(case_study_file) if case_study_file else (),
        banned_stems=_load_bans(stem_file) if stem_file else (),
        gift_artifacts=_load_bans(artifact_file) if artifact_file else (),
        spec_text=spec_text,
        hook_matrix=(Path(hook_matrix).read_text(encoding="utf-8") if hook_matrix else ""),
        premise_vocab=_load_premise_vocab(profile) if profile else None,
        domain_aliases=_load_domain_aliases(profile) if profile else None,
    )
    errored = {
        (v.email or "").strip().lower()
        for v in violations
        if v.level == "ERROR" and _is_recipient(v.email)
    }
    out: dict[str, bool] = {}
    for row in rows:
        email = (row.get("email") or "").strip()
        if not email:
            continue
        out[_row_id(str(spec_path), str(csv_path), email, touch)] = email.lower() not in errored
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m gtm_core.rule_baseline",
        description=(
            "Score the deterministic rule fleet as a baseline classifier, so the question "
            "'is the judge worth its cost' has a comparison behind it rather than an assertion."
        ),
    )
    ap.add_argument("--spec", required=True, type=Path)
    ap.add_argument("--csv", required=True, type=Path)
    ap.add_argument("--signoff", default="")
    ap.add_argument("--touch", type=int, default=1)
    ap.add_argument(
        "--profile",
        default="",
        help="PASS THIS. Without it the premise checks are off and the fleet is scored "
        "weaker than it really is, which biases the judge comparison in the judge's favour.",
    )
    ap.add_argument("--ban-file", type=Path)
    ap.add_argument("--case-study-file", type=Path)
    ap.add_argument("--stem-file", type=Path)
    ap.add_argument("--artifact-file", type=Path)
    ap.add_argument("--hook-matrix", type=Path)
    ap.add_argument("--out", required=True, type=Path, help="JSONL of {row_id, send_it}")
    args = ap.parse_args(argv)

    preds = baseline_predictions(
        args.spec,
        args.csv,
        signoff=args.signoff,
        touch=args.touch,
        profile=args.profile,
        ban_file=args.ban_file,
        case_study_file=args.case_study_file,
        stem_file=args.stem_file,
        artifact_file=args.artifact_file,
        hook_matrix=args.hook_matrix,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "".join(
            json.dumps({"row_id": rid, "send_it": ok}) + "\n" for rid, ok in sorted(preds.items())
        ),
        encoding="utf-8",
    )
    sendable = sum(1 for v in preds.values() if v)
    print(
        f"rule-fleet baseline: {len(preds)} row(s), {sendable} predicted sendable, "
        f"{len(preds) - sendable} predicted not-sendable -> {args.out}"
    )
    if preds and sendable == len(preds):
        print(
            "\nWARNING the fleet flagged NOTHING, so as a classifier it will score TPR 0 "
            "and make any judge look good by comparison. Before believing that, check you "
            "passed --profile and the ban/case-study/stem/artifact lists: most ERROR-level "
            "rules are opt-in on a profile-supplied list and cannot fire without them.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
