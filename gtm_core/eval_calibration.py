"""Email eval calibration — golden-set sampling, judge validation statistics, rule
lifecycle audit, and outcome reconciliation (PRD 2026-08-19-email-eval-calibration.md).

Four phases, one module, because they are one workflow read at different points in time
rather than four independent concerns:

* **P0** (:func:`sample_golden_set`, :func:`render_labeling_sheet`, label file I/O,
  :func:`seal_holdout`) — build the operator's ~2-hour labeling session: 80 real rendered
  emails (stratified, via the same round-robin sampler :mod:`gtm_core.adjudication` uses)
  plus ~20 fault-injected ones drawn from the mutation-suite catalogue, shown BLIND (no
  linter verdict, no injection flag), labeled binary, sealed into a class-balanced holdout.
* **P1** (:func:`confusion`, :func:`cohens_kappa`, :func:`flip_rate`,
  :func:`judge_meets_bar`) — validate a judge's predictions against the sealed holdout, and
  separately score the deterministic rule fleet as a baseline classifier on the SAME
  holdout, so the question "is the judge worth its cost" has an actual comparison behind it
  rather than being asserted.
* **P2** (:func:`rule_lifecycle_report`) — turn fire-rate (from persisted
  ``merge_render_linter --json`` QA records) and human evidence (from labels on the
  fault-injected stratum) into a keep / recalibrate / delete-candidate / no-control verdict
  per rule — the admission test every future rule 44+ must also pass.
* **P3** (:func:`reconcile_outcomes`) — once sends exist, join judge verdicts to reply
  outcomes and ask whether taste actually predicts reply. Guarded by a minimum-event floor
  per stratum (§3.6): below it, one reply could flip a label, so taste stays authoritative.

**Deliberately render-agnostic**, mirroring :mod:`gtm_core.adjudication`'s own precedent:
this module never renders a spec against a CSV itself, and operates on already-rendered
``{email, subject, body, context}`` dicts a caller supplies. This is a per-module design
choice, not a hard layering rule — :mod:`gtm_core.cells` already imports
``outreach_pack_linter.seat_of`` from ``tests/linter/`` via a ``sys.path`` insert, so the
dashboard's seat resolution can never drift from the persona-lead gate's. The render step
this module deliberately omits follows that exact precedent instead: see
:mod:`gtm_core.build_eval_sheet`, which imports ``merge_render_linter.render``/``parse_spec``
the same way ``cells.py`` imports ``seat_of``, resolves a profile's live sequences via
:func:`gtm_core.cells.load_cell_map`, draws the fault-injection stratum from its own
real-data mutation catalog, and calls this module's :func:`sample_golden_set` /
:func:`build_golden_row` / :func:`render_labeling_sheet` to produce the operator's actual
sheet.

Stdlib-only, no I/O beyond JSONL/JSON read-write on paths the caller supplies — same
convention as :mod:`gtm_core.outcomes` / :mod:`gtm_core.adjudication` /
:mod:`gtm_core.sequencer_outcomes`. No model call lives here: a judge's predictions and a
groundedness cascade's verdicts (:mod:`gtm_core.groundedness`) are produced upstream (by a
skill, which has Agent SDK access this module deliberately does not) and handed in as
plain dicts, exactly as :mod:`gtm_core.adjudication` takes recorded verdicts rather than
producing them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from .adjudication import DEFAULT_STRATA, sample
from .judge_calibration import (
    kappa_from_pairs,
    newest_holdout_passed,
    self_agreement,
    write_score_record,
)
from .messaging import card
from .paths import _safe_segment, resolve_content_root


def evals_dir(profile: str, content_root: Path | None = None) -> Path:
    """``content/<profile>/prospects/evals/`` — the one writable location for labels,
    holdouts, and sheets (§R9: PII stays under content/, never in code/tests/docs). Every
    segment passes through :func:`_safe_segment`, so a traversal-shaped profile name fails
    closed here exactly as it does in :mod:`gtm_core.paths`'s other resolvers."""
    root = content_root if content_root is not None else resolve_content_root()
    return root / _safe_segment(profile, "profile") / "prospects" / "evals"


def sealed_holdouts(profile: str, content_root: Path | None = None) -> list[Path]:
    """Every sealed holdout on disk for ``profile``, newest first. Empty means **this judge
    has never been measured against a human label**.

    The one question a caller must be able to ask cheaply before believing a verdict, and
    until 2026-08-27 nothing could ask it: the judge scored, wrote records, and returned a
    tidy ``{"verdicts": {...}}`` payload whether or not any holdout existed. It never did —
    for any profile — so every verdict the system had ever produced was unvalidated, and
    indistinguishable from a validated one at every layer that consumed it.

    Deliberately a file-existence check and nothing more. Whether a holdout that *does*
    exist is any good (large enough, class-balanced, free of prefilled rows) is
    :func:`verify_holdout`'s job, and is a different and stricter question. This answers
    only "has anyone ever tried", because that was the one being silently answered "yes".
    """
    d = evals_dir(profile, content_root)
    if not d.is_dir():
        return []
    # Sorted by the date in the filename, not mtime: a fresh clone, a `cp -r`, or a
    # restore rewrites every mtime at once and would silently reorder the history.
    return sorted(d.glob("*-holdout.json"), key=lambda p: p.name, reverse=True)


def is_calibrated(profile: str, content_root: Path | None = None) -> bool:
    """Whether the NEWEST sealed holdout for ``profile`` carries a score record that says PASS.

    Until 2026-09-03 this was ``bool(sealed_holdouts(...))`` — a holdout's mere existence made
    every judge record ``calibrated: true`` and let ``account_integrity`` obey its drops, even
    after the 2026-09-01 run FAILED all four bars against that very holdout. Now ``score``
    persists its verdict beside the holdout (:mod:`gtm_core.judge_calibration`), bound to the
    holdout's bytes; no record, a failed or provisional run, an older holdout's record, or a
    re-edited holdout all read as False — the direction in which the judge only ranks.
    """
    return newest_holdout_passed(sealed_holdouts(profile, content_root))


#: A drafted cell is a ``spec.md`` + ``rows.csv`` pair under ``evals/drafts/<cell-slug>/`` —
#: the same two files a staged sequence has, minus the sequencer. The names live here, beside
#: :func:`evals_dir`, because more than one module has to find them: the sheet builder samples
#: them and the message-axis audit counts them. Two copies of a path is how the two tools end
#: up disagreeing about which cells exist.
DRAFTS_DIRNAME = "drafts"
DRAFT_SPEC_NAME = "spec.md"
DRAFT_CSV_NAME = "rows.csv"


def draft_cell_dirs(profile: str, content_root: Path | None = None) -> list[tuple[str, Path, Path]]:
    """Every complete drafted cell as ``(slug, spec_path, csv_path)``, sorted by slug.

    A cell missing either file is skipped rather than reported: the directory is a working
    area, and a half-written cell is a draft in progress, not a defect to fail a run on.
    """
    root = evals_dir(profile, content_root) / DRAFTS_DIRNAME
    if not root.is_dir():
        return []
    out: list[tuple[str, Path, Path]] = []
    for cell_dir in sorted(root.iterdir()):
        if not cell_dir.is_dir():
            continue
        spec_path = cell_dir / DRAFT_SPEC_NAME
        csv_path = cell_dir / DRAFT_CSV_NAME
        if spec_path.is_file() and csv_path.is_file():
            out.append((cell_dir.name, spec_path, csv_path))
    return out


# --------------------------------------------------------------------------- P0: golden set

#: The binary sub-checks every label may carry, per schemas/email-eval-label.schema.json.
#: `send_it` (not in this tuple) is the only field that is never null — it is what the
#: judge is validated against and what seals the holdout.
#:
#: Every field but one is scoped to the ROW's fact and copy. `account_fit` is the one
#: account-scoped question, added 2026-08-23 because nothing else here can express
#: "the email is fine, the company is wrong" — see
#: :func:`gtm_core.eval_writeback._is_disqualifying` for the defect that required it.
#:
#: `fact_creates_problem`, `fact_supports_pitch`, and `bridge_depends_on_fact` were three
#: separate questions about the row's opening fact through 2026-09-01, added one at a time
#: as each prior pair turned out not to cover some way a fact could fail. Measured over the
#: 28 labels from the second 2026-09-01 round, Cohen's kappa between them was 1.00 / 0.84 /
#: 0.84 — three questions, one answer, on real data. Merged into `fact_earns_its_place`:
#: "does the opening fact earn its place — does this email need THIS fact, or would it read
#: the same under a different one?" Across all 121 label records on disk at merge time,
#: exactly 1 had the three fields disagreeing, so the merge is lossless to a rounding error
#: on the corpus that motivated it. See `_MERGED_LABEL_FIELDS` for how old records still
#: carry the pre-merge keys and load correctly without being rewritten.
#:
#: DERIVED since 2026-09-24 from the one quality card
#: (:data:`gtm_core.messaging.card.LABEL_QUESTIONS`, spelled for this surface by
#: :func:`~gtm_core.messaging.card.label_fields`), so the sheet, the judge rubric and this
#: schema are three declared subsets of one question set rather than three lists. The
#: card's derivation already honours the kappa merge above — it does not re-split it.
LABEL_FIELDS = card.label_fields(card.LABEL_QUESTIONS)

#: Renamed 2026-08-21, and the VALUES are inverted with it. It was
#: ``fact_refutes_pitch``, the one sub-check where Y meant BAD while the other three
#: meant good — a labeler answering four questions in a row had to reverse polarity on
#: the second one. Renaming rather than re-labelling in place is deliberate: a consumer
#: still reading the old key now raises instead of silently scoring True as its opposite.
#: That field was itself later merged into `fact_earns_its_place` — see
#: `_MERGED_LABEL_FIELDS` — but this entry keeps raising on its OWN old name regardless,
#: because a value under `fact_refutes_pitch` has inverted polarity and merging it forward
#: would silently score a value as its own opposite.
_RETIRED_LABEL_FIELDS = {"fact_refutes_pitch": "fact_supports_pitch"}

#: Merged, NOT renamed: polarity is preserved on every one of these three, unlike
#: `_RETIRED_LABEL_FIELDS`, which is why a record carrying an old key here migrates
#: forward at load instead of raising. See `label_from_dict` for the derivation rule and
#: `validate_label_dict` for why a record cannot carry both an old key and the survivor.
_MERGED_LABEL_FIELDS = {
    "fact_creates_problem": "fact_earns_its_place",
    "fact_supports_pitch": "fact_earns_its_place",
    "bridge_depends_on_fact": "fact_earns_its_place",
}
_MERGED_SURVIVORS = frozenset(_MERGED_LABEL_FIELDS.values())
HOLISTIC_FIELD = "send_it"


@dataclass(frozen=True)
class GoldenRow:
    """One rendered email, ready for the blind labeling sheet.

    ``email``/``injected``/``injected_rule`` are internal bookkeeping — the sheet renderer
    (:func:`render_labeling_sheet`) never prints them. A labeler who can see which rows are
    planted relearns the plant, not the defect.
    """

    row_id: str
    spec: str
    csv: str
    touch: int
    email: str
    subject: str
    body: str
    context: dict  # {"title": ..., "company": ..., "segment": ...}
    injected: bool = False
    injected_rule: str | None = None


def _norm_path(p: str) -> str:
    """A path in the one form ``_row_id`` may hash: relative to the content root.

    The row id is the join key between the labeling sheet and the judge's adjudication
    records, and it is derived from the spec and CSV paths. Hashing the caller's raw
    string made the id depend on how the caller happened to spell the path: the sheet
    builder resolves ``content_root`` to an absolute path, while a caller invoking the
    judge with ``content/<profile>/...`` passes a relative one. Same spec, same CSV, same
    recipient, two different ids — so on 2026-08-23 a full judge pass over the drawn cells
    joined to **0 of 30** sheet rows, and nothing errored, because a mismatched key is
    indistinguishable from a row nobody judged.

    Relative-to-content-root rather than absolute on purpose: an absolute id would change
    with the checkout directory, so the same sheet would not survive being read on another
    machine. A path outside the root keeps its resolved form — it has no root-relative
    spelling, and silently truncating it would collide two different files.
    """
    try:
        resolved = Path(p).resolve()
    except OSError:  # pragma: no cover - unresolvable path, hash what we were given
        return p
    try:
        return resolved.relative_to(resolve_content_root().resolve()).as_posix()
    except (ValueError, OSError):
        return resolved.as_posix()


def _row_id(spec: str, csv: str, email: str, touch: int) -> str:
    key = f"{_norm_path(spec)}|{_norm_path(csv)}|{email.strip().lower()}|{touch}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def build_golden_row(
    *,
    spec: str,
    csv: str,
    touch: int,
    email: str,
    subject: str,
    body: str,
    context: dict,
    injected: bool = False,
    injected_rule: str | None = None,
) -> GoldenRow:
    """Construct one already-rendered row. The caller (which has render/parse access,
    this module deliberately does not) supplies the rendered subject/body directly."""
    return GoldenRow(
        row_id=_row_id(spec, csv, email, touch),
        spec=spec,
        csv=csv,
        touch=touch,
        email=email,
        subject=subject,
        body=body,
        context=dict(context),
        injected=injected,
        injected_rule=injected_rule,
    )


def sample_golden_set(
    real_rows: Sequence[dict],
    *,
    n_real: int = 80,
    axes: Sequence[str] = DEFAULT_STRATA,
    seed: str = "",
) -> list[dict]:
    """The real-row half of the golden set: a stratified draw over ``real_rows`` (raw CSV
    dicts, pre-render) via :func:`gtm_core.adjudication.sample` — the same stable-hash,
    round-robin sampler the reading pass uses, so a rerun draws the same rows and two
    reviewers are comparable.

    Returns raw rows, not :class:`GoldenRow` — rendering happens in the caller, which has
    access to the spec/CSV render machinery this module does not import.
    """
    return sample(real_rows, n_real, axes, seed=seed)


def _fmt(value: bool | None) -> str:
    """``True``/``False``/``None`` -> ``Y``/``N``/``-``.

    Refuses anything else rather than coercing it. The old body was
    ``"Y" if value else "N"``, so a suggestions file written with the sheet's OWN vocabulary
    (``"N"``) rendered as ``Y`` — truthy string — and every negative sub-check silently
    flipped positive on the page the operator then corrects. Found 2026-09-22 building the
    first pre-filled round. A formatter that inverts an answer is worse than one that stops.
    """
    if value is None:
        return "-"
    if not isinstance(value, bool):
        raise TypeError(
            f"prefill answers are True/False/None, got {value!r} ({type(value).__name__}). "
            'Map the sheet\'s "Y"/"N"/"-" before passing them in.'
        )
    return "Y" if value else "N"


def _dewrap(body: str) -> str:
    """Join each paragraph's hard-wrapped lines into one, for display.

    A sequence spec wraps prose at ~90 chars so the ``.md`` source is readable. That wrap is
    an AUTHORING artifact and does not survive staging: the live sequencer payload stores one
    unbroken line per paragraph (verified 2026-08-23 against the staged sequences), so the
    recipient never sees it.

    A sheet that shows the wrapped form asks the labeler to judge something that will not
    exist, and it has already cost a label — a row was marked down for a "formatting error"
    that was a 102-char line in the sheet's own render, on copy that ships re-flowed. Findings
    have to be about the email, not about the instrument.
    """
    return "\n\n".join(
        " ".join(line.strip() for line in para.splitlines() if line.strip())
        for para in (body or "").split("\n\n")
    )


#: Printed once per group where each row's own paragraphs go. A literal both the renderer
#: and every sheet parser key on — change it and old sheets stop parsing.
PER_ROW_MARKER = "⟨ this part is different on every row below ⟩"


@dataclass(frozen=True)
class BodyGroup:
    """Rows sharing one rendered template — `(spec, touch)` — and the part of it they
    share. Built by :func:`group_rows_by_body`, consumed by :func:`render_labeling_sheet`.
    """

    index: int
    rows: tuple[GoldenRow, ...]
    #: The shared subject, or None when it varies anywhere in the group (rendered per row
    #: for every member in that case, never just the odd one out).
    subject: str | None
    #: Dewrapped paragraphs identical across every member, from the front and the back.
    #: Count-based, not position-based: a member with a different total paragraph count
    #: (a shape-changing fault injection) just shrinks these — there is no fallback mode
    #: that would render some rows differently from others in the same group.
    prefix: tuple[str, ...]
    suffix: tuple[str, ...]

    def own(self, row: GoldenRow) -> list[str]:
        """This row's own contiguous middle — everything the group does not share.
        Empty for a row whose whole body is prefix+suffix (a singleton group, or a
        member with no varying paragraph at all)."""
        paras = _dewrap(row.body).split("\n\n")
        return paras[len(self.prefix) : len(paras) - len(self.suffix)]


def group_rows_by_body(rows: Sequence[GoldenRow]) -> list[BodyGroup]:
    """Group rows that share a rendered template, for spec-major display.

    Key is `(spec, touch)`; groups in first-appearance order, rows keep their relative
    order inside a group. Sharing is discovered, not declared: "paragraphs 1..n are the
    spec template" is false in practice (a spec interpolates `{{Company}}` into whichever
    paragraph carries the pitch, so the shared set is a contiguous run from one END, not a
    fixed index range) — so the shared prefix/suffix is computed per group from the actual
    rendered bodies.

    **Duplicate split.** `build_eval_sheet.add_duplicates()` inserts the same `row_id`
    twice and deliberately spreads the copies apart so `intra_rater_agreement` measures
    self-consistency, not recall. Grouping by body would pull them adjacent and, with the
    shared text factored out, make the remaining per-row text byte-identical — exactly
    what that measurement must not see. So a `row_id` already present in the CURRENTLY
    OPEN instance for its key forces a new instance to open at that row's position; the
    same id reappearing in an EARLIER, already-closed instance of the same key is fine
    and expected — that is the duplicate landing where it was meant to.
    """
    groups: list[dict] = []
    open_for_key: dict[tuple[str, int], dict] = {}

    for row in rows:
        key = (row.spec, row.touch)
        g = open_for_key.get(key)
        if g is not None and row.row_id in g["ids"]:
            g = None
        if g is None:
            g = {"rows": [], "ids": set()}
            groups.append(g)
            open_for_key[key] = g
        g["rows"].append(row)
        g["ids"].add(row.row_id)

    result: list[BodyGroup] = []
    for i, g in enumerate(groups, 1):
        members: list[GoldenRow] = g["rows"]
        subjects = {m.subject for m in members}
        subject = next(iter(subjects)) if len(subjects) == 1 else None

        paras_list = [_dewrap(m.body).split("\n\n") for m in members]
        shortest = min(len(p) for p in paras_list)

        prefix_len = 0
        while prefix_len < shortest and len({p[prefix_len] for p in paras_list}) == 1:
            prefix_len += 1
        suffix_len = 0
        while (
            suffix_len < shortest - prefix_len
            and len({p[len(p) - 1 - suffix_len] for p in paras_list}) == 1
        ):
            suffix_len += 1

        first = paras_list[0]
        prefix = tuple(first[:prefix_len])
        suffix = tuple(first[len(first) - suffix_len :]) if suffix_len else ()
        result.append(
            BodyGroup(index=i, rows=tuple(members), subject=subject, prefix=prefix, suffix=suffix)
        )
    return result


def render_labeling_sheet(
    rows: Sequence[GoldenRow],
    prefill: dict[str, dict] | None = None,
    fields: Sequence[str] = LABEL_FIELDS,
) -> str:
    """Spec-major markdown labeling sheet: each shared body template is rendered ONCE as a
    `## Body` header block, with only each row's own varying paragraph(s) listed beneath
    it under a `### Row` card — the thing being judged is the thing that visually varies.
    Carries rendered subject + body + the row's own context — nothing a recipient would
    not themselves know, and nothing this pipeline computed about the row (no linter
    verdict, no adjudication score, no injection flag, no batch identity).

    Grouping (see :func:`group_rows_by_body`) is by identity — `(spec, touch)` — never by
    "which rows happen to render the same," which is what keeps this blind: a
    fault-injected row's mutated paragraph is simply part of what varies within its
    group, alongside every organic variation (an interpolated `{{Company}}`, a different
    signal clause), with no separate rendering mode for it.

    ``fields`` is the sheet's own declared subset of the quality card, in the label
    spelling — passed in by :mod:`gtm_core.build_eval_sheet` (the module that owns the
    sheet surface) and defaulting to everything a label can carry. It is a parameter
    rather than a read of :data:`LABEL_FIELDS` so that "what the sheet asks" and "what a
    label stores" are two declarations a test can compare, instead of one fact wearing
    two names.

    ``prefill`` maps ``row_id -> {"send_it": bool, ..., "note": str}``. A row present in it
    is rendered with those answers already filled in, for the operator to CORRECT rather
    than author; a row absent from it renders blank.

    **The blank rows are the methodologically load-bearing ones.** Pre-filling is a real
    concession: on a row where the operator keeps the suggested answer, agreement and
    acquiescence are indistinguishable afterward, so that label cannot cleanly validate the
    judge that suggested it (PRD §2.4's self-agreement problem, in a quieter form). Rows
    labeled cold carry no such doubt, which is why
    :func:`gtm_core.build_eval_sheet.build_golden_set` holds a stratified subset back and
    why :func:`seal_holdout` can restrict itself to them.
    """
    prefill = prefill or {}
    n_blank = sum(1 for r in rows if r.row_id not in prefill)
    field_list = " / ".join(f"`{f}`" for f in fields)
    lines = [
        "# Email eval — labeling sheet",
        "",
        f"{len(rows)} rows. For each: `send_it` (Y/N — would you send this to someone you "
        f"respect), then {field_list} (Y/N/`-` for not-applicable), then a one-line note on "
        "the FIRST thing that stood out — cause, not every downstream symptom.",
        "",
        '**`fact_earns_its_place`** — does the opening fact earn its place? Not "is the '
        'fact true" and not "is the fact interesting": would this email be *worse* '
        "without THIS fact, or would the sentence after it read exactly the same under a "
        "different one? Y = the email needs this fact.",
        "",
        "**`claim_within_status`** — would you defend every sentence here? N when the body "
        "claims a capability we cannot yet stand behind, or cites a figure we cannot show "
        "was measured. Y = nothing in it outruns what we can back.",
        "",
        "**`account_fit` is the only question about the COMPANY.** Answer N only when this "
        "company should not be contacted at all — they sell what we sell, they regulate it, "
        "they already shipped it. A weak opener, a claim that does not land, or copy you would "
        "rewrite are all `send_it: N` with `account_fit: Y`: the email is wrong, the account is "
        "fine. N here is durable and expensive — it disqualifies the account from every future "
        "send, not just this one.",
        "",
        f"**Answer `send_it` on your own read of the email, not by adding up the "
        f"{len(fields)} sub-checks.** It is the label the judge is validated against; "
        "deriving it from the others turns a taste judgment into arithmetic.",
        "",
    ]
    if prefill and n_blank:
        lines += [
            f"**{len(rows) - n_blank} rows are pre-filled with a model's suggestion — correct "
            f"them.** The other **{n_blank} are deliberately blank**: label those cold, without "
            "reference to anything. Only the blank ones can validate a judge (on a pre-filled "
            "row, keeping the answer is indistinguishable from agreeing with it), so they are "
            "what gets sealed as the holdout. If you only have time for part of this, do the "
            "blank ones.",
            "",
        ]
    elif prefill:
        lines += [
            f"**All {len(rows)} rows are pre-filled with a model's suggestion — correct them.** "
            "There is no blank holdout on this sheet, so agreement alone cannot validate the "
            "judge: a kept answer is indistinguishable from an unread one. What still counts as "
            "evidence is every row you **change** — a disagreement is unambiguous no matter what "
            "was printed. Change freely, and where you keep an answer, make the note say why.",
            "",
        ]
    n = 0  # reading-order row number — display only; row_id is the join key everywhere
    # else (parse_filled_sheet, build_from_sheet, internal-<date>.jsonl). Numbering by
    # reading order rather than draw order keeps the operator's "N of TOTAL" progress cue
    # monotonic down the page — do not "fix" this back to matching draw order.
    for group in group_rows_by_body(rows):
        n_rows = len(group.rows)
        lines += [f"## Body {group.index} — {n_rows} row{'s' if n_rows != 1 else ''}", ""]
        if group.subject is not None:
            lines += [f"**Subject:** {group.subject}", ""]
        else:
            lines += ["**Subject:** ⟨differs per row⟩", ""]
        if group.prefix:
            lines += ["\n\n".join(group.prefix), ""]
        lines += [PER_ROW_MARKER, ""]
        if group.suffix:
            lines += ["\n\n".join(group.suffix), ""]

        for r in group.rows:
            n += 1
            ctx = r.context
            ctx_line = " · ".join(
                f"{k}: {v}"
                for k, v in (("title", ctx.get("title")), ("company", ctx.get("company")))
                if v
            )
            pre = prefill.get(r.row_id)
            if pre:
                answers = "  ".join(
                    [f"`send_it:` {_fmt(pre.get('send_it'))}"]
                    + [f"`{f}:` {_fmt(pre.get(f))}" for f in fields]
                )
                note = f"`note:` {pre.get('note', '')}"
                marker = ""
            else:
                answers = "  ".join(["`send_it:` ___"] + [f"`{f}:` ___" for f in fields])
                note = "`note:`"
                marker = "  **← label this one cold**" if prefill else ""
            own = group.own(r)
            own_text = (
                "\n\n".join(own)
                if own
                else "_(this row's copy is the shared template above, unchanged)_"
            )
            lines += [
                f"### Row {n} — `{r.row_id}`{marker}",
                "",
                f"*{ctx_line}*" if ctx_line else "",
                "",
            ]
            if group.subject is None:
                lines += [f"**Subject:** {r.subject}", ""]
            lines += [
                own_text,
                "",
                answers,
                "",
                note,
                "",
                "---",
                "",
            ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- label I/O


@dataclass(frozen=True)
class Label:
    row_id: str
    spec_sha256: str
    csv_sha256: str
    send_it: bool
    touch: int = 1
    #: Does the opening fact earn its place — would this email be worse without THIS fact,
    #: or would the sentence after it read the same under a different one? Merges what were
    #: three separate fields through 2026-09-01 (`fact_creates_problem`, `fact_supports_pitch`,
    #: `bridge_depends_on_fact`) — see LABEL_FIELDS and `_MERGED_LABEL_FIELDS`. A record
    #: carrying only the old keys derives this at load time in `label_from_dict`; it is
    #: never written back into a file that didn't already have it.
    fact_earns_its_place: bool | None = None
    frame_fits_seat: bool | None = None
    right_person: bool | None = None
    #: Does the email stay inside what the sender can stand behind — no capability claimed
    #: beyond its registry `status`, no figure without a `measured` proof? Added 2026-09-24
    #: with the fact registry, and migration-safe for the same reason `account_fit` was:
    #: every label file written before it loads as None, which is "not answered".
    claim_within_status: bool | None = None
    #: The one ACCOUNT-scoped judgment on the label. True = the company is a fine
    #: prospect and whatever is wrong here is wrong with the *email*; False = do not
    #: contact this company regardless of what the copy says; None = not answered.
    #:
    #: `None` never disqualifies, which is what makes adding this field migration-safe:
    #: every label file written before 2026-08-23 loads as "no account-level evidence"
    #: rather than as an accusation. Re-collection happens on the next sheet.
    account_fit: bool | None = None
    note: str = ""
    prefilled: bool = False
    #: This body was re-composed by the repair loop, not authored cold. Excluded from the
    #: holdout for the same reason `prefilled` is, one step downstream: validating the
    #: judge on copy the judge shaped is circular. Still labeled, and reported as its own
    #: stratum, so the repair loop's effect on quality stays measurable.
    repaired: bool = False
    injected: bool = False
    injected_rule: str | None = None
    labeled_at: str = ""

    def to_dict(self) -> dict:
        # Sub-checks come from LABEL_FIELDS, not spelled out here — a field added to that
        # tuple reaches every writer without a second edit. Note the consequence: reading a
        # legacy file (old fact_* keys) and writing it back emits the survivor key only,
        # forward-migrating the file in place. Fine for a working labels file; never do
        # this to a sealed holdout — see the test that pins that.
        return {
            "row_id": self.row_id,
            "spec_sha256": self.spec_sha256,
            "csv_sha256": self.csv_sha256,
            "touch": self.touch,
            **{f: getattr(self, f) for f in LABEL_FIELDS},
            "send_it": self.send_it,
            "note": self.note,
            "prefilled": self.prefilled,
            "repaired": self.repaired,
            "injected": self.injected,
            "injected_rule": self.injected_rule,
            "labeled_at": self.labeled_at,
        }


_REQUIRED_LABEL_FIELDS = ("row_id", "spec_sha256", "csv_sha256", "send_it")


def validate_label_dict(d: dict) -> list[str]:
    """Hand-rolled schema check (no jsonschema dependency — stdlib-only, matching every
    sibling ledger module). Returns problem strings; empty means valid."""
    problems = []
    for f in _REQUIRED_LABEL_FIELDS:
        if f not in d:
            problems.append(f"missing required field {f!r}")
    if "send_it" in d and not isinstance(d["send_it"], bool):
        problems.append(f"send_it must be boolean, got {type(d['send_it']).__name__}")
    for f in (*LABEL_FIELDS, *_MERGED_LABEL_FIELDS):
        if f in d and d[f] is not None and not isinstance(d[f], bool):
            problems.append(f"{f} must be boolean or null, got {type(d[f]).__name__}")
    # A retired field is a hard error, never ignored. `label_from_dict` reads with `.get`,
    # so a pre-rename record would otherwise load as "not answered" — and its value means
    # the OPPOSITE of the field that replaced it, which is worse than missing.
    for old, new in _RETIRED_LABEL_FIELDS.items():
        if old in d:
            problems.append(
                f"{old!r} was renamed to {new!r} on 2026-08-21 and its polarity INVERTED "
                f"(Y now means the pitch survives the fact, not that it is refuted). "
                f"Migrate the record — do not rename the key alone."
            )
    # A merged field is NOT an error to carry — that is the whole point of the forward
    # map. It IS an error to carry an answered old field AND an answered survivor at once:
    # two answers to one question cannot be silently reconciled, and picking one over the
    # other would hide which answer actually came from the operator.
    survivors_answered = {
        new for old, new in _MERGED_LABEL_FIELDS.items() if old in d and d[old] is not None
    }
    for survivor in survivors_answered:
        if survivor in d and d[survivor] is not None:
            old_keys = ", ".join(
                repr(old) for old, new in _MERGED_LABEL_FIELDS.items() if new == survivor
            )
            problems.append(
                f"{survivor!r} was merged from {old_keys} and this record carries an "
                f"answered value for both. Two answers to one question cannot be "
                f"reconciled automatically — keep one and drop the other."
            )
    return problems


def _merged_value(d: dict, survivor: str) -> bool | None:
    """The survivor's value: itself if present, else derived from whichever of its
    retired predecessors were answered. False if ANY answered predecessor is False (the
    fact failed that reading), True if every answered predecessor is True, None if none
    were answered — NOT `all([])`, which is vacuously True and would score a wholly
    unanswered row as a pass.
    """
    if survivor in d and d[survivor] is not None:
        return d[survivor]
    answered = [
        d[old]
        for old, new in _MERGED_LABEL_FIELDS.items()
        if new == survivor and old in d and d[old] is not None
    ]
    if not answered:
        return None
    return all(answered)


def label_from_dict(d: dict) -> Label:
    problems = validate_label_dict(d)
    if problems:
        raise ValueError(f"invalid label record: {'; '.join(problems)}")
    subchecks = {
        f: _merged_value(d, f) if f in _MERGED_SURVIVORS else d.get(f) for f in LABEL_FIELDS
    }
    return Label(
        row_id=d["row_id"],
        spec_sha256=d["spec_sha256"],
        csv_sha256=d["csv_sha256"],
        touch=d.get("touch", 1),
        **subchecks,
        send_it=d["send_it"],
        note=d.get("note", ""),
        prefilled=d.get("prefilled", False),
        repaired=d.get("repaired", False),
        injected=d.get("injected", False),
        injected_rule=d.get("injected_rule"),
        labeled_at=d.get("labeled_at", ""),
    )


def read_labels(path: Path) -> list[Label]:
    """Read a labels-<date>.jsonl file. Skips blank lines; raises on a malformed record
    rather than silently dropping it — a bad label file is worth stopping on, not on."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(label_from_dict(json.loads(line)))
    return out


def write_labels(labels: Sequence[Label], path: Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "\n".join(json.dumps(label_.to_dict()) for label_ in labels) + "\n", encoding="utf-8"
    )
    return out


def labels_matching_fingerprint(
    labels: Sequence[Label], *, spec_sha256: str, csv_sha256: str
) -> list[Label]:
    """Labels whose fingerprint still matches the given spec/csv bytes — the same
    invalidate-on-edit contract ``merge_render_linter``'s QA record uses. A label whose
    fingerprint has gone stale is excluded, never silently treated as still valid."""
    return [
        label_
        for label_ in labels
        if label_.spec_sha256 == spec_sha256 and label_.csv_sha256 == csv_sha256
    ]


# --------------------------------------------------------------------------- filled-sheet parser

#: `#{2,3}` accepts both the pre-B1 flat sheet (`## Row N — \`id\``) and the spec-major
#: sheet (`### Row N — \`id\`` nested under a `## Body` group) — sheets already on disk
#: use the flat form, so both must keep parsing.
_ROW_HEADER_RE = re.compile(r"^#{2,3} Row\s+(\d+)\s+—\s+`([0-9a-f]{16})`", re.M)
#: Ends a row's block at whichever comes first: the NEXT row header, or the next `## Body`
#: group header. Slicing only at row headers would fold a following group's shared
#: prose into the last row's block, and `dict(_FIELD_RE.findall(...))` keeps the LAST
#: match for a repeated key — a backticked-looking phrase in shared prose could then
#: silently overwrite a real answer.
_SECTION_HEADER_RE = re.compile(r"^#{2,3} (?:Row|Body)\s+\d+\s+—", re.M)
_FIELD_RE = re.compile(r"`(\w+):`\s*([^\s`]*)")
_NOTE_RE = re.compile(r"`note:`[ \t]*(.*)")

_TRUE = {"y", "yes", "true", "1"}
_FALSE = {"n", "no", "false", "0"}
_SKIP = {"-", "--", "na", "n/a", "skip", ""}


def _parse_bool(raw: str, *, field: str, row: str) -> bool | None:
    v = (raw or "").strip().lower().strip("_")
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    if v in _SKIP:
        return None
    raise ValueError(f"row {row}: cannot read {field}={raw!r} — use Y, N, or -")


def parse_filled_sheet(
    text: str, *, spec_sha256: str = "", csv_sha256: str = "", prefilled_ids: Sequence[str] = ()
) -> list[Label]:
    """Read a filled-in labeling sheet back into :class:`Label` records.

    **Fails loudly, never silently.** An unreadable answer raises rather than being dropped
    or coerced — a label file that quietly lost rows would corrupt every statistic computed
    from it while looking complete, which is the failure mode this whole program exists to
    stop. A row whose ``send_it`` is still blank is skipped as *not yet labeled* (that is a
    legitimate half-finished session), but any row with a malformed value is an error.

    ``prefilled_ids`` marks which rows were rendered with a suggestion, so each label
    records whether it was authored cold or corrected — the two are not interchangeable
    evidence and downstream analysis must be able to tell them apart.
    """
    prefilled = set(prefilled_ids)
    out: list[Label] = []
    headers = list(_ROW_HEADER_RE.finditer(text))
    # Every section boundary — row AND group headers — so a row's block ends at whichever
    # comes first, never bleeding into a following group's shared prose.
    section_starts = sorted(m.start() for m in _SECTION_HEADER_RE.finditer(text))
    for m in headers:
        row_id = m.group(2)
        next_section = next((s for s in section_starts if s > m.start()), len(text))
        block = text[m.end() : next_section]
        fields = dict(_FIELD_RE.findall(block))
        if "send_it" not in fields:
            raise ValueError(f"row {row_id}: no `send_it:` field found in its block")
        send_raw = fields.get("send_it", "")
        send = _parse_bool(send_raw, field="send_it", row=row_id)
        if send is None:
            continue  # not yet labeled — a partial session is allowed
        note_m = _NOTE_RE.search(block)
        # Loop over LABEL_FIELDS rather than one hardcoded kwarg per field — a field added
        # to that tuple reaches this parser without a second edit here (the defect that
        # cost `bridge_depends_on_fact` its export-handler answer on 2026-09-01, see
        # tests/test_labeler_build_guards.py). A sheet still using a pre-merge field name
        # (e.g. `fact_creates_problem:`) is simply not asked for by this loop and its
        # value is dropped, not merged — merging belongs to `label_from_dict`, which reads
        # a *record*; a markdown sheet has no way to express which of three retired
        # questions produced which answer, so re-rendering is the correct fix, not a
        # markdown-layer merge.
        subchecks = {f: _parse_bool(fields.get(f, ""), field=f, row=row_id) for f in LABEL_FIELDS}
        out.append(
            Label(
                row_id=row_id,
                spec_sha256=spec_sha256,
                csv_sha256=csv_sha256,
                send_it=send,
                **subchecks,
                note=(note_m.group(1).strip() if note_m else ""),
                prefilled=row_id in prefilled,
            )
        )
    return out


# --------------------------------------------------------------------------- intra-rater agreement


def intra_rater_agreement(labels: Sequence[Label]) -> dict:
    """Agreement of the labeler with THEMSELVES, from duplicated rows.

    The sheet can carry the same email twice (unmarked). Because a duplicate shares its
    ``row_id``, a repeat shows up here as two labels with one id.

    **This number is the ceiling on the whole program.** A judge cannot meaningfully score
    better against the operator than the operator scores against themselves: at 80%
    self-agreement, a judge at 85% is already at the noise floor, and the PRD's κ ≥ 0.6 bar
    is measuring the target's inconsistency rather than the judge's skill. Without it, a
    disappointing validation run is unattributable — weak judge or noisy target, no way to
    tell.
    """
    by_id: dict[str, list[Label]] = defaultdict(list)
    for label_ in labels:
        by_id[label_.row_id].append(label_)
    repeats = {rid: ls for rid, ls in by_id.items() if len(ls) > 1}
    # The control that separates "changed their mind" from "the email changed": a repeat whose
    # labels were made against different spec/csv bytes is not a repeat. Excluded and counted
    # — silently pooling them lifted the 2026-09-01 self-κ from −0.50 to −0.32.
    mismatch = {
        rid
        for rid, ls in repeats.items()
        if len({(label_.spec_sha256, label_.csv_sha256) for label_ in ls}) > 1
    }
    repeats = {rid: ls for rid, ls in repeats.items() if rid not in mismatch}
    empty = {
        "duplicate_rows": 0,
        "agreements": 0,
        "agreement": None,
        "disagreed_rows": [],
        "fingerprint_mismatch": len(mismatch),
        "n": 0,
        "kappa": None,
        "yes_to_no": 0,
        "no_to_yes": 0,
    }
    if not repeats:
        return empty
    agree = [rid for rid, ls in repeats.items() if len({label_.send_it for label_ in ls}) == 1]
    disagree = sorted(set(repeats) - set(agree))
    pairs: list[tuple[bool, bool]] = []
    yes_to_no = no_to_yes = 0
    for ls in repeats.values():
        first, second = sorted(ls, key=lambda label_: label_.labeled_at or "")[:2]
        pairs.append((bool(first.send_it), bool(second.send_it)))
        if first.send_it and not second.send_it:
            yes_to_no += 1
        elif second.send_it and not first.send_it:
            no_to_yes += 1
    return {
        **empty,
        "duplicate_rows": len(repeats),
        "agreements": len(agree),
        "agreement": len(agree) / len(repeats),
        "disagreed_rows": disagree,
        "n": len(repeats),
        "kappa": kappa_from_pairs(pairs),
        "yes_to_no": yes_to_no,
        "no_to_yes": no_to_yes,
    }


# --------------------------------------------------------------------------- holdout sealing


def seal_holdout(
    labels: Sequence[Label], *, n_per_class: int = 15, cold_only: bool = True
) -> tuple[list[Label], list[Label]]:
    """Split ``labels`` into (train, holdout), class-balanced on ``send_it`` by a stable
    hash — ``n_per_class`` True and ``n_per_class`` False, chosen deterministically so a
    rerun reproduces the same split.

    Class-balancing matters: an unbalanced 30-row draw at, say, a 14% positive base rate
    yields ~4 positives, and TPR measured on 4 has a confidence interval too wide to
    distinguish a 0.90 judge from a 0.70 one (PRD §3.1). Raises if either class has fewer
    than ``n_per_class`` labels — better to fail loudly than seal an undersized holdout
    silently.
    """

    # `cold_only` keeps pre-filled rows OUT of the holdout. On a pre-filled row, keeping
    # the suggested answer is indistinguishable from agreeing with it, so validating the
    # suggesting judge against it is the same self-agreement the PRD rejects (§2.4) — just
    # quieter. Train may use every label; the holdout must be labels authored cold.
    # `repaired` joins `prefilled` in the exclusion for the same reason: copy the judge
    # shaped, labeled by a human, then used to validate that judge, is circular — the
    # judge is being scored against its own output wearing a human's signature.
    def _excluded(label_: Label) -> bool:
        return label_.prefilled or label_.repaired

    pool = [label_ for label_ in labels if not _excluded(label_)] if cold_only else list(labels)
    prefilled_pool = [label_ for label_ in labels if _excluded(label_)] if cold_only else []
    positives = sorted(
        (label_ for label_ in pool if label_.send_it), key=lambda label_: label_.row_id
    )
    negatives = sorted(
        (label_ for label_ in pool if not label_.send_it), key=lambda label_: label_.row_id
    )
    if len(positives) < n_per_class or len(negatives) < n_per_class:
        raise ValueError(
            f"cannot seal a class-balanced holdout of {n_per_class}/class from "
            f"{'cold-labeled' if cold_only else 'all'} rows: {len(positives)} positive, "
            f"{len(negatives)} negative available"
            + (
                f" ({len(prefilled_pool)} more exist but were pre-filled or repaired and are excluded — "
                "label more rows cold, or pass cold_only=False and accept that the holdout "
                "can no longer cleanly validate the judge that pre-filled it)"
                if prefilled_pool
                else ""
            )
        )

    def _split(pool: list[Label]) -> tuple[list[Label], list[Label]]:
        # Stable hash of row_id decides holdout membership — not just "first N sorted",
        # so the split is insensitive to how the label file happens to be ordered on disk.
        ranked = sorted(pool, key=lambda label_: hashlib.sha256(label_.row_id.encode()).hexdigest())
        return ranked[n_per_class:], ranked[:n_per_class]

    pos_train, pos_holdout = _split(positives)
    neg_train, neg_holdout = _split(negatives)
    # Pre-filled labels are never in the holdout, but they ARE legitimate training data.
    return pos_train + neg_train + prefilled_pool, pos_holdout + neg_holdout


# --------------------------------------------------------------------------- P1: judge validation


@dataclass(frozen=True)
class ConfusionStats:
    """Positive class = ``send_it is False`` (the operator would NOT send it) — the judge's
    job is catching defects, so "predicts a defect and there is one" is the true positive,
    matching PRD §3.2."""

    tp: int
    fp: int
    tn: int
    fn: int

    @property
    def tpr(self) -> float | None:
        denom = self.tp + self.fn
        return (self.tp / denom) if denom else None

    @property
    def tnr(self) -> float | None:
        denom = self.tn + self.fp
        return (self.tn / denom) if denom else None

    @property
    def n(self) -> int:
        return self.tp + self.fp + self.tn + self.fn


def confusion(labels: Sequence[Label], predictions: dict[str, bool]) -> ConfusionStats:
    """``predictions``: row_id -> predicted ``send_it`` (True = predicted sendable).
    A row_id with no prediction is skipped, not counted as a miss — a caller that forgot a
    row should see it via a coverage check, not via a silently-corrupted confusion matrix."""
    tp = fp = tn = fn = 0
    for label_ in labels:
        if label_.row_id not in predictions:
            continue
        pred_send = predictions[label_.row_id]
        actual_send = label_.send_it
        if not actual_send and not pred_send:
            tp += 1  # correctly caught a defect
        elif actual_send and not pred_send:
            fp += 1  # false alarm on a clean row
        elif actual_send and pred_send:
            tn += 1  # correctly passed a clean row
        else:
            fn += 1  # missed a real defect
    return ConfusionStats(tp=tp, fp=fp, tn=tn, fn=fn)


def cohens_kappa(labels: Sequence[Label], predictions: dict[str, bool]) -> float | None:
    """Chance-corrected agreement. Raw exact-match overstates a judge's discriminative
    power at a skewed base rate — this is the metric PRD §9 requires instead."""
    pairs = [
        (label_.send_it, predictions[label_.row_id])
        for label_ in labels
        if label_.row_id in predictions
    ]
    n = len(pairs)
    if n == 0:
        return None
    observed_agree = sum(1 for a, b in pairs if a == b) / n
    p_a_true = sum(1 for a, _ in pairs if a) / n
    p_b_true = sum(1 for _, b in pairs if b) / n
    expected_agree = p_a_true * p_b_true + (1 - p_a_true) * (1 - p_b_true)
    if expected_agree >= 1.0:
        return 1.0 if observed_agree >= 1.0 else 0.0
    return (observed_agree - expected_agree) / (1 - expected_agree)


def flip_rate(pairs: Sequence[tuple[bool, bool]]) -> float:
    """Share of (original-order verdict, reversed-order verdict) pairs that disagree.
    A judge whose verdict depends on rubric-item order is a coin, whatever its agreement
    score with the human labels — PRD §3.2's stability control against position bias."""
    if not pairs:
        return 0.0
    return sum(1 for a, b in pairs if a != b) / len(pairs)


@dataclass(frozen=True)
class JudgeVerdict:
    passed: bool
    tpr: float | None
    tnr: float | None
    kappa: float | None
    flip: float
    reasons: list[str] = field(default_factory=list)


def judge_meets_bar(
    stats: ConfusionStats,
    kappa: float | None,
    flip: float,
    *,
    min_tpr: float = 0.90,
    min_tnr: float = 0.80,
    min_kappa: float = 0.6,
    max_flip: float = 0.10,
) -> JudgeVerdict:
    """PRD §3.2's four bars, applied together. All four must pass — a judge that nails
    TPR/TNR but flips under rubric-order reversal is unstable, not validated."""
    reasons = []
    if stats.tpr is None or stats.tpr < min_tpr:
        reasons.append(
            f"TPR {stats.tpr} < {min_tpr}"
            if stats.tpr is not None
            else "TPR undefined (no positives)"
        )
    if stats.tnr is None or stats.tnr < min_tnr:
        reasons.append(
            f"TNR {stats.tnr} < {min_tnr}"
            if stats.tnr is not None
            else "TNR undefined (no negatives)"
        )
    if kappa is None or kappa < min_kappa:
        reasons.append(f"kappa {kappa} < {min_kappa}" if kappa is not None else "kappa undefined")
    if flip > max_flip:
        reasons.append(f"flip rate {flip} > {max_flip}")
    return JudgeVerdict(
        passed=not reasons, tpr=stats.tpr, tnr=stats.tnr, kappa=kappa, flip=flip, reasons=reasons
    )


# --------------------------------------------------------------------------- P2: rule lifecycle


@dataclass(frozen=True)
class RuleVerdict:
    rule: str
    fires: int
    total: int
    fire_rate: float | None
    injected_instances: int
    injected_penalised: int
    verdict: str  # "keep" | "recalibrate" | "delete-candidate" | "no-control" | "insufficient-data"
    reason: str


def rule_fire_rates(qa_records: Sequence[dict]) -> dict[str, tuple[int, int]]:
    """Aggregate ``fires`` / ``total renders`` per rule across one or more persisted
    ``merge_render_linter --json`` QA records (the ``by_rule`` + ``renders`` fields)."""
    fires: dict[str, int] = defaultdict(int)
    totals: dict[str, int] = defaultdict(int)
    for rec in qa_records:
        renders = rec.get("renders", 0)
        by_rule = rec.get("by_rule", {})
        checks_run = rec.get("checks_run", {})
        for rule in checks_run:
            totals[rule] += renders
        for rule, levels in by_rule.items():
            fires[rule] += sum(levels.values())
    return {rule: (fires.get(rule, 0), totals.get(rule, 0)) for rule in totals}


def rule_lifecycle_report(
    qa_records: Sequence[dict],
    labels: Sequence[Label],
    *,
    saturated_threshold: float = 0.40,
    dead_fire_threshold: int = 0,
    min_injected_for_verdict: int = 1,
    min_records_for_deletion: int = 2,
    not_human_visible: Sequence[str] = (),
) -> list[RuleVerdict]:
    """One :class:`RuleVerdict` per rule, per PRD §3.3's bands. A rule with no persisted
    QA record and no injected-label evidence is reported ``no-control`` rather than
    omitted — silence about a rule's status is exactly the failure this report exists to
    end.

    ``not_human_visible`` names rules whose defect **cannot appear on a labeling sheet** —
    the field never renders into the visible body, or the sheet withholds it as PII
    (:func:`gtm_core.build_eval_sheet.build_injected_golden_rows` returns exactly this
    list). They are reported ``not-human-visible`` and are **never** delete candidates:
    absence of a human penalty is meaningless for a defect no human could see, and reading
    it as "nobody cared" would retire working rules on an artifact of the sheet. Their
    real negative control is the mutation suite, which tests the linter directly.

    ``min_records_for_deletion`` guards the zero-fires band, and it exists because of what
    this report said on its first real run (2026-08-22): given ONE QA record from one clean
    list and no labels, it classified **62 of 69 rules** as delete candidates. Every one of
    those rules had simply never been exercised — that list contained no empty company, no
    trademark glyph, no role address. Zero fires across a single spec is *absence of
    evidence*, and reporting it as evidence of uselessness would retire the fleet on one
    healthy list. Below the threshold the band reports ``insufficient-data``, which is the
    same distinction this module already makes for ``no-control``: unknown is not fine.
    """
    invisible = set(not_human_visible)
    n_records = len(qa_records)
    rates = rule_fire_rates(qa_records)
    injected_by_rule: dict[str, list[Label]] = defaultdict(list)
    for label_ in labels:
        if label_.injected and label_.injected_rule:
            injected_by_rule[label_.injected_rule].append(label_)

    all_rules = set(rates) | set(injected_by_rule) | invisible
    out = []
    for rule in sorted(all_rules):
        fires, total = rates.get(rule, (0, 0))
        fire_rate = (fires / total) if total else None
        injected = injected_by_rule.get(rule, [])
        # "Penalised" = the operator's holistic label agrees the row should not send.
        penalised = sum(1 for label_ in injected if not label_.send_it)

        if rule in invisible:
            # Checked BEFORE every other band: this rule's defect cannot reach a labeling
            # sheet, so no human-evidence band applies to it in either direction.
            verdict, reason = (
                "not-human-visible",
                "defect never renders into a labeling sheet (field absent from the copy, "
                "or withheld as PII) — human labels carry no signal about it; judge it by "
                "the mutation suite, never by absence of a penalty",
            )
        elif fire_rate is None and not injected:
            verdict, reason = (
                "no-control",
                "no persisted QA record and no labeled injected instance",
            )
        elif injected and len(injected) >= min_injected_for_verdict and penalised == 0:
            verdict, reason = (
                "delete-candidate",
                f"fires but none of {len(injected)} injected instance(s) were penalised — "
                "the defect is real and nobody cares",
            )
        elif fire_rate is not None and fire_rate > saturated_threshold:
            verdict, reason = (
                "recalibrate",
                f"fires on {fire_rate:.0%} of renders — describes the list, doesn't screen it",
            )
        elif fire_rate is not None and fire_rate <= dead_fire_threshold and not injected:
            if n_records < min_records_for_deletion:
                verdict, reason = (
                    "insufficient-data",
                    f"zero fires, but across only {n_records} QA record(s) and no injected "
                    f"label — a rule this list never exercised is unproven, not useless "
                    f"(needs {min_records_for_deletion})",
                )
            else:
                verdict, reason = (
                    "delete-candidate",
                    f"zero fires across {n_records} QA records and no injected-label evidence",
                )
        elif injected and penalised > 0:
            verdict, reason = (
                "keep",
                f"{penalised}/{len(injected)} injected instances penalised by the operator",
            )
        elif fire_rate is not None:
            verdict, reason = "keep", f"fires at {fire_rate:.0%}, within the discriminating band"
        else:
            verdict, reason = (
                "insufficient-data",
                "fire rate known but no injected-label evidence yet",
            )

        out.append(
            RuleVerdict(
                rule=rule,
                fires=fires,
                total=total,
                fire_rate=fire_rate,
                injected_instances=len(injected),
                injected_penalised=penalised,
                verdict=verdict,
                reason=reason,
            )
        )
    return out


# --------------------------------------------------------------------------- P3: outcome reconciliation


@dataclass(frozen=True)
class ReconciliationResult:
    stratum: str
    n_events: int
    send_rate_judge_pass: float | None
    send_rate_judge_fail: float | None
    authoritative: str  # "outcomes" | "labels" — which source wins for this stratum
    note: str


def reconcile_outcomes(
    judged_rows: Sequence[dict],
    outcome_rows: Sequence[dict],
    *,
    min_events: int = 5,
    stratum_key: str = "stratum",
) -> list[ReconciliationResult]:
    """Join judge verdicts (``judged_rows``: dicts carrying at least ``email``,
    ``judge_send_it``, and ``stratum_key``) to reply outcomes (``outcome_rows``: the shape
    :mod:`gtm_core.outcomes` writes — ``channel``, ``outcome``, ``ref`` as the recipient
    email) and reports, per stratum, whether judge-pass rows out-reply judge-fail rows.

    Enforces PRD §3.6's rules: silence (a non-reply) is never a LABEL — the min-events
    floor is computed only from rows that appear in ``outcome_rows`` at all, and a stratum
    below ``min_events`` stays ``authoritative: "labels"`` (taste keeps the last word)
    rather than letting one reply flip a verdict. Silence is, however, correctly a RATE
    denominator: ``send_rate_judge_pass``/``_fail`` divide by every judged row in the
    group, not just the ones that replied — a 2-reply group of 100 is a 2% rate, not a
    100% rate over the 2 rows that happened to reply.
    """
    positive_outcomes = {"reply_positive", "positive_reply", "meeting"}
    positive_tags = {"positive:interested", "positive:meeting", "positive:closed"}
    negative_outcomes = {"opt_out", "reply_negative", "unsubscribe"}
    negative_tags = {"objection:do-not-contact", "objection:not-interested", "objection:timing"}

    # The producer (`sequencer_outcomes.plan_rows`) writes EVERY reply as `outcome: reply` and
    # the sentiment as a TAG (`positive:*` / `objection:*`), with the recipient in
    # `meta.prospect_email` while `ref` is the THREAD id. Until 2026-09-03 this read only
    # `outcome` and only `ref` — so a negative reply counted as positive and no real row
    # joined at all; the fixtures passed because they used untagged `ref=email` rows, which
    # stay valid below (an untagged `reply` is the legacy positive).
    def _who(row: dict) -> str:
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        return str(meta.get("prospect_email") or row.get("ref") or "").strip().lower()

    def _class(row: dict) -> str | None:
        outcome = str(row.get("outcome") or "")
        tags = {t for t in (row.get("tags") or []) if isinstance(t, str)}
        if outcome in positive_outcomes or tags & positive_tags:
            return "pos"
        if outcome in negative_outcomes or tags & negative_tags:
            return "neg"
        if outcome == "reply":
            return "pos" if not tags else "neutral"
        return None

    classified = [(_who(row), _class(row)) for row in outcome_rows if row.get("channel") == "email"]
    replied_positive = {who for who, c in classified if c == "pos"}
    replied_negative = {who for who, c in classified if c == "neg"}
    replied_neutral = {who for who, c in classified if c == "neutral"}
    had_any_outcome = replied_positive | replied_negative | replied_neutral

    by_stratum: dict[str, list[dict]] = defaultdict(list)
    for row in judged_rows:
        by_stratum[row.get(stratum_key, "")].append(row)

    out = []
    for stratum, rows in sorted(by_stratum.items()):
        # n_events (drives the min-events floor) counts only rows with SOME informative
        # outcome — that is the statistical-confidence question. The RATE below is a
        # different question ("what fraction of everyone judge-pass replied positively")
        # and must keep silence in the denominator: a group of 100 judge-pass rows with 2
        # replies is a 2% rate, not a 100% rate over the 2 rows that happened to reply.
        events = [r for r in rows if (r.get("email") or "").strip().lower() in had_any_outcome]
        n_events = len(events)
        judge_pass = [r for r in rows if r.get("judge_send_it")]
        judge_fail = [r for r in rows if not r.get("judge_send_it")]

        def _rate(group: list[dict]) -> float | None:
            if not group:
                return None
            positive = sum(
                1 for r in group if (r.get("email") or "").strip().lower() in replied_positive
            )
            return positive / len(group)

        rate_pass = _rate(judge_pass)
        rate_fail = _rate(judge_fail)
        if n_events >= min_events:
            authoritative = "outcomes"
            note = f"{n_events} events — enough to let outcomes speak for this stratum"
        else:
            authoritative = "labels"
            note = (
                f"only {n_events} event(s), below the {min_events}-event floor — one reply "
                "could flip a verdict here, so operator labels stay authoritative"
            )
        out.append(
            ReconciliationResult(
                stratum=stratum,
                n_events=n_events,
                send_rate_judge_pass=rate_pass,
                send_rate_judge_fail=rate_fail,
                authoritative=authoritative,
                note=note,
            )
        )
    return out


# --------------------------------------------------------------------------- CLI helpers


def _load_holdout(path: Path) -> list[Label]:
    """Read a sealed holdout file, refusing the two ways it can be silently worthless."""
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if not rows:
        # An empty holdout satisfies every purity assertion vacuously and reports a clean
        # pass. It is the failure this check exists to catch, not an edge case.
        raise ValueError(
            f"{path} is an EMPTY holdout — every purity check below would pass vacuously "
            "and the resulting judge score would mean nothing. Seal a real holdout first."
        )
    labels = [label_from_dict(d) for d in rows]
    prefilled = [label_ for label_ in labels if label_.prefilled]
    if prefilled:
        raise ValueError(
            f"{len(prefilled)} PRE-FILLED row(s) in the holdout. Validating a judge against "
            "rows it suggested the answers for is self-agreement wearing a statistic's "
            "clothes — re-seal with cold_only=True."
        )
    repaired = [label_ for label_ in labels if label_.repaired]
    if repaired:
        raise ValueError(
            f"{len(repaired)} REPAIRED row(s) in the holdout. That copy was re-composed by "
            "the judge's own repair loop, so scoring the judge on labels of it is the same "
            "circularity as a pre-filled row, one step further downstream — re-seal."
        )
    return labels


def _predictions_from_records(path: Path) -> tuple[dict[str, bool], int]:
    """``row_id -> predicted send_it`` from an adjudication JSONL, plus a skipped count.

    A judge ``verdict`` of ``send`` maps to ``send_it=True``; ``re-angle``/``drop`` map to
    False. Unscored rows and rows carrying no ``row_id`` are skipped and COUNTED — a
    silently short prediction set produces a confusion matrix over fewer rows than the
    reader thinks, which is the quietest way to overstate a judge.
    """
    from .adjudication import read_records

    preds: dict[str, bool] = {}
    skipped = 0
    for rec in read_records(Path(path)):
        if rec.unscored or not rec.row_id or rec.verdict not in {"send", "re-angle", "drop"}:
            skipped += 1
            continue
        # A repaired body is copy the judge shaped. Scoring the judge on it is the same
        # circularity `prefilled` already guards against, one step further downstream.
        if rec.repaired:
            skipped += 1
            continue
        preds[rec.row_id] = rec.verdict == "send"
    return preds, skipped


def _cli_score(args) -> int:
    try:
        labels = _load_holdout(Path(args.holdout))
    except ValueError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1

    preds, skipped = _predictions_from_records(Path(args.predictions))
    stats = confusion(labels, preds)
    kappa = cohens_kappa(labels, preds)

    # Flip rate: measured only against a genuinely separate reversed-rubric run. Reporting
    # 0.0 for "we never ran it" would read as the strongest result on the page.
    flip: float | None = None
    if args.reversed_predictions:
        rev, _ = _predictions_from_records(Path(args.reversed_predictions))
        pairs = [(preds[rid], rev[rid]) for rid in preds if rid in rev]
        flip = flip_rate(pairs) if pairs else None

    print(f"holdout: {len(labels)} row(s); predictions cover {stats.n}; {skipped} skipped")
    if stats.n < len(labels):
        print(
            f"  NOTE {len(labels) - stats.n} holdout row(s) have NO prediction — the "
            "statistics below describe only the covered subset"
        )
    verdict = judge_meets_bar(stats, kappa, flip if flip is not None else 0.0)
    print(
        f"\nJUDGE   TPR {_pct(stats.tpr)}  TNR {_pct(stats.tnr)}  "
        f"kappa {_num(kappa)}  flip {_pct(flip) if flip is not None else 'NOT MEASURED'}"
    )
    if flip is None:
        print(
            "  the flip bar is UNPROVEN — pass --reversed-predictions from a second run "
            "with reverse_rubric=true. A judge whose verdict moves with rubric order is a "
            "coin however good its agreement score."
        )

    if args.baseline_predictions:
        base = {}
        for line in Path(args.baseline_predictions).read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                base[d["row_id"]] = bool(d["send_it"])
        bstats = confusion(labels, base)
        bkappa = cohens_kappa(labels, base)
        print(
            f"RULES   TPR {_pct(bstats.tpr)}  TNR {_pct(bstats.tnr)}  "
            f"kappa {_num(bkappa)}  (n={bstats.n}) — the 69-rule fleet on the SAME holdout"
        )
        if bkappa is not None and kappa is not None and bkappa >= kappa:
            print(
                "  the deterministic fleet matches or beats the judge here. The honest "
                "reading is that the judge is not earning its cost on this holdout."
            )
    else:
        print(
            "RULES   NOT SUPPLIED — run `python -m gtm_core.rule_baseline` and pass "
            "--baseline-predictions. Without it, 'the judge catches defects' is "
            "unfalsifiable: the rules catch defects too."
        )

    # Persist the verdict beside the holdout: `is_calibrated` reads THIS, not the holdout's
    # existence. A provisional pass (flip bar unmeasured) is recorded as not passed.
    record = write_score_record(
        Path(args.holdout),
        verdict,
        n=stats.n,
        provisional=flip is None and verdict.passed,
        predictions=args.predictions,
        reversed_predictions=args.reversed_predictions,
        baseline_predictions=args.baseline_predictions,
    )
    print(f"score record -> {record}")
    print(f"\n{'PASS' if verdict.passed else 'FAIL'}")
    for reason in verdict.reasons:
        print(f"  - {reason}")
    if flip is None and verdict.passed:
        print("  (a PASS with an unmeasured flip rate is provisional, not validated)")
        return 1
    return 0 if verdict.passed else 1


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _cli_rules(args) -> int:
    qa_dir = Path(args.qa_dir)
    records = []
    read_files = []
    for path in sorted(qa_dir.glob("*.json")):
        rec = json.loads(path.read_text(encoding="utf-8"))
        # Scoping matters: a directory holding two campaigns' records aggregates them into
        # one confident, wrong fire rate. Filter, and SAY which files were read.
        if args.sequence_id and rec.get("sequence_id") != args.sequence_id:
            continue
        records.append(rec)
        read_files.append(path.name)
    labels = read_labels(Path(args.labels))
    # Join the answer key, exactly as `gtm_core.eval_writeback` does and for the same reason
    # it documents there: `lab.injected` is DEAD on any label this program's own tooling
    # produced, because the page must not show the labeler which rows are planted. Reading it
    # straight made P2's `keep` band unreachable from a real round — the first live labeling
    # round (2026-09-22, 53 labels, 21 of them on planted rows covering 18 rules) reported
    # every one of those rules `delete-candidate` for want of this join.
    if args.internal:
        key = {}
        for line in Path(args.internal).read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                key[rec["row_id"]] = (bool(rec.get("injected")), rec.get("injected_rule"))
        joined, hit = [], 0
        for lab in labels:
            inj, rule = key.get(lab.row_id, (False, None))
            if inj and rule:
                hit += 1
                joined.append(replace(lab, injected=True, injected_rule=rule))
            else:
                joined.append(lab)
        labels = joined
        print(f"joined the answer key: {hit} of {len(labels)} label(s) sit on a planted row")
    else:
        print(
            "note: no --internal passed. If these labels came from the HTML labeler they all "
            "carry `injected: false`, so no rule can earn a `keep` — pass the round's "
            "internal-<date>.jsonl."
        )
    invisible = tuple(r.strip() for r in args.not_human_visible.split(",") if r.strip())

    if not records:
        print(
            f"no QA records matched in {qa_dir}"
            + (f" for sequence {args.sequence_id!r}" if args.sequence_id else "")
            + " — every rule will report `no-control`. That is a statement about the "
            "evidence, not about the rules: pass --json on the merge-render gate."
        )
    else:
        print(f"read {len(records)} QA record(s): {', '.join(read_files)}")
    print(f"{len(labels)} label(s), {len(invisible)} rule(s) marked not-human-visible\n")

    report = rule_lifecycle_report(records, labels, not_human_visible=invisible)
    by_verdict: dict[str, list] = defaultdict(list)
    for rv in report:
        by_verdict[rv.verdict].append(rv)
    for verdict in (
        "delete-candidate",
        "recalibrate",
        "keep",
        "insufficient-data",
        "not-human-visible",
        "no-control",
    ):
        items = by_verdict.get(verdict, [])
        if not items:
            continue
        print(f"{verdict.upper()} ({len(items)})")
        for rv in items:
            rate = "n/a" if rv.fire_rate is None else f"{rv.fire_rate:.0%}"
            print(f"  {rv.rule:38} fires {rv.fires:5}/{rv.total:<6} ({rate:>5})  {rv.reason}")
        print()
    # A report recommending the deletion of most of the fleet is nearly always a statement
    # about the evidence, not about the rules — the same "budget the output or nobody reads
    # it" principle gtm_core.finding_budget applies to the gates themselves.
    deletions = len(by_verdict.get("delete-candidate", []))
    if report and deletions / len(report) > 0.5:
        print(
            f"⚠  {deletions} of {len(report)} rules are delete candidates. Read that as a "
            f"finding about the EVIDENCE, not the fleet: {len(records)} QA record(s) from a "
            f"small number of lists cannot exercise a rule guarding a defect those lists do "
            f"not contain. Accumulate records across several campaigns before retiring "
            f"anything, and never retire a rule whose mutation-suite control still passes.\n"
        )
    print(
        "Deletions are PRs, never automatic: the mutation suite pins the catalogue count "
        "and requires one negative-control test per rule."
    )
    return 0


def _cli_reconcile(args) -> int:
    from .adjudication import read_records

    judged = [
        {
            "email": rec.email,
            "stratum": rec.stratum,
            "judge_send_it": rec.verdict == "send",
        }
        for rec in read_records(Path(args.judged))
        if not rec.unscored
    ]
    outcomes_path = (
        Path(args.outcomes)
        if args.outcomes
        else evals_dir(args.profile).parent.parent / "outcomes.jsonl"
    )
    outcome_rows = []
    if outcomes_path.exists():
        for line in outcomes_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                outcome_rows.append(json.loads(line))
    else:
        print(f"no outcomes file at {outcomes_path} — reporting against zero events")

    results = reconcile_outcomes(judged, outcome_rows, min_events=args.min_events)
    print(f"{len(judged)} judged row(s), {len(outcome_rows)} outcome event(s)\n")
    for r in results:
        print(f"{r.stratum or '(no stratum)'}")
        print(
            f"  events {r.n_events}  judge-pass reply rate {_pct(r.send_rate_judge_pass)}  "
            f"judge-fail {_pct(r.send_rate_judge_fail)}"
        )
        print(f"  authoritative: {r.authoritative} — {r.note}")
    if all(r.authoritative == "labels" for r in results):
        print(
            "\nEvery stratum is below the event floor. Taste stays authoritative — and "
            "that is the correct answer today, not a gap in this report."
        )
    return 0


# --------------------------------------------------------------------------- CLI


def _cli_self_agreement(args) -> int:
    from .adjudication import read_records

    a = [r for p in args.a for r in read_records(Path(p))]
    b = [r for p in args.b for r in read_records(Path(p))]
    try:
        res = self_agreement(a, b)
    except ValueError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(
        f"joined {res['joined']} row(s) ({res['skipped']} skipped) — 3-way "
        f"{_pct(res['agreement_3way'])} · binary {_pct(res['agreement_binary'])} · "
        f"binary kappa {_num(res['kappa_binary'])}"
    )
    for k, v in res["transitions"].items():
        print(f"  {v:5}  {k}")
    return 0


def _cli_intra_rater(args) -> int:
    labels = [label_ for p in args.file for label_ in read_labels(Path(p))]
    res = intra_rater_agreement(labels)
    print(
        f"{res['n']} byte-identical repeat(s) ({res['fingerprint_mismatch']} repeat(s) excluded: "
        f"bytes changed) — agreement {_pct(res['agreement'])} · kappa {_num(res['kappa'])} · "
        f"yes→no {res['yes_to_no']} · no→yes {res['no_to_yes']}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m gtm_core.eval_calibration")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_validate = sub.add_parser("validate", help="validate a labels-<date>.jsonl file")
    p_validate.add_argument("--profile", required=True)
    p_validate.add_argument("--file", required=True, help="path to the labels JSONL file")

    p_seal = sub.add_parser("seal-holdout", help="split a label file into train/holdout")
    p_seal.add_argument("--profile", required=True)
    p_seal.add_argument("--file", required=True)
    p_seal.add_argument("--n-per-class", type=int, default=15)
    p_seal.add_argument(
        "--out-stem",
        required=True,
        help="bare filename stem (no path) — writes <stem>-train.jsonl / <stem>-holdout.json "
        "under content/<profile>/prospects/evals/, never elsewhere (§R9)",
    )

    p_score = sub.add_parser(
        "score", help="validate a judge's predictions against the sealed holdout (P1)"
    )
    p_score.add_argument("--profile", required=True)
    p_score.add_argument("--holdout", required=True, help="the sealed <stem>-holdout.json")
    p_score.add_argument(
        "--predictions", required=True, help="adjudication JSONL carrying row_id + verdict"
    )
    p_score.add_argument(
        "--reversed-predictions",
        default="",
        help="a SECOND judging run with the rubric order reversed — PRD §3.2's stability "
        "control. Without it the flip rate is reported as NOT MEASURED rather than 0.0, "
        "because an unmeasured flip rate and a perfect one look identical in a report.",
    )
    p_score.add_argument(
        "--baseline-predictions",
        default="",
        help="JSONL of {row_id, send_it} from `gtm_core.rule_baseline` — the 69-rule fleet "
        "scored on the SAME holdout. Without it the 'is the judge worth its cost' question "
        "is reported unanswered.",
    )

    p_vh = sub.add_parser(
        "verify-holdout",
        help="prove a sealed holdout can actually validate a judge before trusting any score",
    )
    p_vh.add_argument("--profile", required=True)
    p_vh.add_argument("--holdout", required=True)

    p_sa = sub.add_parser(
        "self-agreement",
        help="the judge against ITSELF: two runs joined on row_id (needs no labels, no key)",
    )
    p_sa.add_argument("--profile", required=True)
    p_sa.add_argument("--a", required=True, nargs="+", help="adjudication JSONL file(s), run A")
    p_sa.add_argument("--b", required=True, nargs="+", help="adjudication JSONL file(s), run B")

    p_ir = sub.add_parser(
        "intra-rater",
        help="the operator against THEMSELVES: repeats across label files, bytes-matched",
    )
    p_ir.add_argument("--profile", required=True)
    p_ir.add_argument("--file", required=True, action="append", help="labels JSONL (repeatable)")

    p_rules = sub.add_parser("rules", help="keep / recalibrate / delete verdict per rule (P2)")
    p_rules.add_argument("--profile", required=True)
    p_rules.add_argument(
        "--qa-dir", required=True, help="directory of persisted merge_render_linter --json records"
    )
    p_rules.add_argument("--labels", required=True, help="labels-<date>.jsonl")
    p_rules.add_argument(
        "--sequence-id",
        default="",
        help="only read QA records for this sequence — without it a directory holding two "
        "campaigns' records yields confident, wrong fire rates",
    )
    p_rules.add_argument(
        "--not-human-visible",
        default="",
        help="comma-separated rules whose defect cannot appear on a labeling sheet "
        "(build_eval_sheet's unusable list). These are never delete candidates.",
    )
    p_rules.add_argument(
        "--internal",
        default="",
        help="the internal-<date>.jsonl for this round. REQUIRED IN PRACTICE: a blind "
        "sheet's export writes `injected: false` on every row by design (the labeler must "
        "not see the answer key), so without this join no label carries injected evidence "
        "and the `keep` band is unreachable — every rule reads delete-candidate however "
        "carefully the round was labeled.",
    )

    p_rec = sub.add_parser("reconcile", help="join judge verdicts to reply outcomes (P3)")
    p_rec.add_argument("--profile", required=True)
    p_rec.add_argument("--judged", required=True, help="adjudication JSONL")
    p_rec.add_argument("--outcomes", default="", help="outcomes.jsonl (default: the profile's)")
    p_rec.add_argument("--min-events", type=int, default=5)

    args = ap.parse_args(argv)
    _safe_segment(args.profile, "profile")  # fail-closed on a traversal-shaped profile name

    if args.cmd == "validate":
        try:
            labels = read_labels(Path(args.file))
        except (ValueError, json.JSONDecodeError) as exc:
            print(f"INVALID: {exc}", file=sys.stderr)
            return 1
        print(f"{len(labels)} valid label(s) in {args.file}")
        return 0

    if args.cmd == "seal-holdout":
        labels = read_labels(Path(args.file))
        train, holdout = seal_holdout(labels, n_per_class=args.n_per_class)
        stem = _safe_segment(args.out_stem, "out-stem")
        out_dir = evals_dir(args.profile)
        train_path = write_labels(train, out_dir / f"{stem}-train.jsonl")
        holdout_path = out_dir / f"{stem}-holdout.json"
        holdout_path.parent.mkdir(parents=True, exist_ok=True)
        holdout_path.write_text(
            json.dumps([label_.to_dict() for label_ in holdout], indent=2) + "\n", encoding="utf-8"
        )
        print(f"train: {len(train)} -> {train_path}")
        print(f"holdout: {len(holdout)} ({args.n_per_class}/class) -> {holdout_path}")
        return 0

    if args.cmd == "self-agreement":
        return _cli_self_agreement(args)
    if args.cmd == "intra-rater":
        return _cli_intra_rater(args)
    if args.cmd == "verify-holdout":
        # The one check that decides whether every judge number downstream means anything.
        # A CLI verb rather than a documented `python -c` one-liner on purpose: the shell
        # policy denies inline -c (§R3), and a probe that only runs when someone pastes it
        # correctly is the same shape as the inert components this whole program is undoing.
        try:
            labels = _load_holdout(Path(args.holdout))
        except ValueError as exc:
            print(f"UNUSABLE HOLDOUT: {exc}", file=sys.stderr)
            return 1
        positives = sum(1 for label_ in labels if label_.send_it)
        negatives = len(labels) - positives
        print(
            f"holdout clean: {len(labels)} row(s) — {positives} positive / {negatives} "
            f"negative, none prefilled, none repaired"
        )
        if not positives or not negatives:
            print(
                "  WARNING one class is empty — TPR or TNR will be undefined, and "
                "`judge_meets_bar` will fail on it rather than report a misleading number",
                file=sys.stderr,
            )
            return 1
        return 0

    if args.cmd == "score":
        return _cli_score(args)

    if args.cmd == "rules":
        return _cli_rules(args)

    if args.cmd == "reconcile":
        return _cli_reconcile(args)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
