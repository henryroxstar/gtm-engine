"""Build the operator's blind labeling sheet from real, currently-staged sequences —
the render step ``gtm_core/eval_calibration.py``'s own docstring names as not-yet-built
(PRD 2026-08-19-email-eval-calibration.md, P0).

**Why this is not inside ``eval_calibration.py``.** That module stays render-agnostic on
purpose, mirroring :mod:`gtm_core.adjudication`'s precedent — operating on already-rendered
dicts, never importing template machinery. This module makes a DIFFERENT, also-precedented
choice: :mod:`gtm_core.cells` already reaches into ``tests/linter/outreach_pack_linter.py``
via a ``sys.path`` insert to import ``seat_of``, specifically so the dashboard's seat
resolution can never drift from the persona-lead gate's. This module extends that exact
reasoning to rendering: the golden set must show the labeler PRECISELY what
``lint_merge_render`` would send, so it imports ``parse_spec``/``render`` from
``merge_render_linter.py`` the same way, rather than re-implementing ``{{Tag}}``
substitution as a second, driftable copy.

**What it does:**

1. Resolves every live sequence for a profile via ``gtm_core.cells.load_cell_map`` +
   ``cells.toml`` (the same join the campaign dashboard reads — never guessed from
   filenames).
2. Loads every live (non-suppressed) row from each sequence's CSV, tags each with a
   derived ``seat`` (:func:`outreach_pack_linter.seat_of` on the title — matching
   :mod:`gtm_core.cells`'s own enrichment, not a CSV column) so the stratified sampler
   in :mod:`gtm_core.eval_calibration` actually varies across the axis that matters.
3. Draws ``n_real`` rows via :func:`gtm_core.eval_calibration.sample_golden_set` and
   renders touch 1 of each against its own spec.
4. Draws ``n_injected`` DIFFERENT rows (excluded from step 3's pool, so no row_id ever
   carries two different rendered contents) and applies one crafted mutation each, from
   :data:`INJECTION_RECIPES` — the real-data analogue of
   ``tests/linter/test_merge_render_mutation_suite.py``'s synthetic fixtures: a real
   recipient's real row, with exactly one defect deliberately introduced.
5. Writes the blind sheet (``sheet-<date>.md``) and the internal record binding each
   sheet row to its true rule/injection status (``internal-<date>.jsonl`` — never shown
   to the operator, consumed later by :func:`gtm_core.eval_calibration.rule_lifecycle_report`
   once real labels exist) under ``content/<profile>/prospects/evals/`` (§R9).

CLI::

    python -m gtm_core.build_eval_sheet --profile acme --n-real 80 --n-injected 20
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_LINTER_DIR = Path(__file__).resolve().parent.parent / "tests" / "linter"
if str(_LINTER_DIR) not in sys.path:  # pragma: no cover - import plumbing
    sys.path.insert(0, str(_LINTER_DIR))

from merge_render_linter import (  # noqa: E402
    Touch,
    _figures,
    lint_merge_render,
    parse_spec,
    render,
)
from outreach_pack_linter import seat_of  # noqa: E402

from .adjudication import collapsed_axes  # noqa: E402
from .cells import load_cell_map  # noqa: E402
from .eval_calibration import (  # noqa: E402
    GoldenRow,
    build_golden_row,
    draft_cell_dirs,
    evals_dir,
    render_labeling_sheet,
    sample_golden_set,
)
from .hook_coverage import Matrix, declared_cell, derive_row_cell, parse_matrix  # noqa: E402
from .messaging import card  # noqa: E402
from .paths import _safe_segment, resolve_knowledge_file, resolve_profiles_root  # noqa: E402
from .prospect_paths import suppression_ledger  # noqa: E402
from .prospects_consolidate import _prospects_dir  # noqa: E402
from .signal_record import HOOK_CELL_COLUMN, SIGNAL_COLUMN, check_record  # noqa: E402
from .suppression import LedgerIndex  # noqa: E402
from .suppression import load_index as load_suppression_index  # noqa: E402

# --------------------------------------------------------------------------- loading real rows


#: The questions the operator's sheet asks — this module's DECLARED subset of the one
#: quality card (:mod:`gtm_core.messaging.card`), in the spelling a label file stores them
#: under. Declared here because this module owns the sheet surface; a question added to the
#: card reaches the operator only by being added to
#: :data:`gtm_core.messaging.card.SHEET_QUESTIONS`, and a question added HERE that the card
#: does not hold turns ``tests/unit/test_messaging_card.py`` red rather than quietly
#: collecting an answer no label field can store.
SHEET_FIELDS: tuple[str, ...] = card.label_fields(card.SHEET_QUESTIONS)


@dataclass(frozen=True)
class SourcedTouches:
    spec_path: str
    csv_path: str
    touches: list  # list[Touch]
    #: The hook-matrix coordinate this spec's copy was written against, read from its own
    #: front block. Carried onto every row so :data:`gtm_core.adjudication.DEFAULT_STRATA`'s
    #: ``cell`` axis can see the messaging dimension — without it the sampler spreads across
    #: who receives the copy and is blind to which argument they receive.
    cell: str = ""
    #: False for copy drafted for the eval but never staged to a sequencer. A sheet may
    #: mix the two — it is asking "is this email any good", which does not depend on
    #: whether anyone enrolled it — but it must never *hide* that it did, because a
    #: drafted cell has no outcomes and cannot be read as evidence about a live campaign.
    staged: bool = True


def load_sources(
    profile: str, content_root: Path | None = None, campaign: str | None = None
) -> list[SourcedTouches]:
    """Every (spec, touches) pair a profile's ``cells.toml`` names, spec parsed once.

    ``campaign`` scopes the sample to one campaign's sequences. A profile that is
    mid-recut has BOTH the live sequences and their replacements in ``cells.toml`` at
    once, and a sheet that mixes them calibrates the judge partly against copy that is
    being retired — so the caller must be able to say which generation it means."""
    seq_dir = _prospects_dir(profile, content_root) / "sequences"
    out = []
    for src in load_cell_map(profile, content_root):
        if campaign is not None and src.get("campaign") != campaign:
            continue
        spec_path = seq_dir / src["spec"]
        csv_path = seq_dir / src["csv"]
        if not spec_path.is_file() or not csv_path.is_file():
            continue
        spec_text = spec_path.read_text(encoding="utf-8")
        touches = parse_spec(spec_text)
        if not touches:
            continue
        declared = declared_cell(spec_text)
        out.append(
            SourcedTouches(
                str(spec_path), str(csv_path), touches, cell=(declared.raw if declared else "")
            )
        )
    return out


#: Drafted-but-unstaged copy lives one directory per cell under the profile's evals dir.
#: Each holds the same two files a staged sequence has — a spec and its row CSV — so the
#: renderer, the linter and the sampler all treat it identically; the ONLY difference is
#: that nothing enrolled it. Discovery is :func:`gtm_core.eval_calibration.draft_cell_dirs`,
#: shared with the message-axis audit so the two tools cannot disagree about which cells exist.


def load_draft_sources(profile: str, content_root: Path | None = None) -> list[SourcedTouches]:
    """Every drafted cell under ``content/<profile>/prospects/evals/drafts/<cell>/``.

    **Why this exists.** ``load_sources`` reads ``cells.toml``, which is the *staged
    sequence* join — so before this, an eval could only test copy that had already been
    committed to a sequencer. That is backwards: the point of an eval is to judge copy
    *before* it ships, and the cheapest way to widen hook-matrix coverage is to draft a
    cell and measure it without enrolling anybody.

    Drafted cells are deliberately NOT added to ``cells.toml``. That file is the
    outcome-attribution join, and its own header warns that a wrong list-or-spec pairing
    silently credits one seat's replies to another. A drafted cell has no replies to
    attribute, so registering it there would corrupt live learning data to make a sheet
    look wider."""
    out = []
    for _slug, spec_path, csv_path in draft_cell_dirs(profile, content_root):
        spec_text = spec_path.read_text(encoding="utf-8")
        touches = parse_spec(spec_text)
        if not touches:
            continue
        declared = declared_cell(spec_text)
        out.append(
            SourcedTouches(
                str(spec_path),
                str(csv_path),
                touches,
                cell=(declared.raw if declared else ""),
                staged=False,
            )
        )
    return out


def _row_cell(row: dict, source: SourcedTouches, matrix: Matrix | None) -> str:
    """The message-axis stratum for one row — prefer what the ROW itself carries over
    what the SPEC declares, so :data:`gtm_core.adjudication.DEFAULT_STRATA`'s ``cell``
    axis can finally vary WITHIN one list, not just across specs.

    Three tiers, most-specific first:

    1. ``HOOK_CELL_COLUMN`` on the row, if a research pass (or a hand backfill) wrote it.
    2. Derived from ``SIGNAL_COLUMN`` + the row's own ``title``/``segment`` via
       :func:`gtm_core.hook_coverage.derive_row_cell`, when a matrix is available —
       exactly the same derivation ``lint_signal_cell`` and ``audit_campaign`` use, so a
       row's stratum here can never disagree with what the linter and the coverage
       report say about the same row.
    3. The spec's own declared cell, then the spec path — unchanged from before this
       column existed, so a list with no per-row signal recorded stratifies exactly as
       it always did.
    """
    recorded = (row.get(HOOK_CELL_COLUMN) or "").strip()
    if recorded:
        return recorded
    signal_value = (row.get(SIGNAL_COLUMN) or "").strip()
    if signal_value and matrix is not None and matrix.ok:
        rc = derive_row_cell(
            matrix,
            email=(row.get("email") or "?").strip(),
            title=row.get("title") or "",
            segment=row.get("segment") or "",
            signal_column=signal_value,
        )
        if rc.cell is not None:
            return f"{rc.cell.persona} × {rc.cell.signal}"
    return source.cell or f"spec:{Path(source.spec_path).name}"


def load_live_rows(
    source: SourcedTouches,
    matrix: Matrix | None = None,
    suppression_index: LedgerIndex | None = None,
    profile: str | None = None,
) -> list[dict]:
    """Non-suppressed rows from one sequence's CSV, enriched with a derived ``seat`` and
    their source spec/csv paths — matching :mod:`gtm_core.cells`'s own enrichment so the
    sample strata are computed the same way the dashboard computes its cells.

    ``matrix``, when supplied, lets :func:`_row_cell` derive a row's OWN cell from its
    recorded signal rather than always falling back to the spec's declared one.

    ``suppression_index``, when supplied, is consulted the same way
    :mod:`gtm_core.account_integrity` consults it — never trusting the row's own
    ``suppression`` column alone. That column is stamped by ``suppression apply`` onto
    ``ready-to-load.csv`` / ``master-list.csv``; these per-spec sequence CSVs are older,
    unconsolidated snapshots that `apply` never touches, so a person suppressed today
    keeps showing up in a fresh eval draw indefinitely. Found 2026-09-01: two people
    disqualified via `eval_writeback apply` minutes earlier were re-sampled by the very
    next draw, because the column check alone had nothing to say about them.
    """
    rows = []
    with open(source.csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if (row.get("suppression") or "").strip():
                continue
            if suppression_index is not None and suppression_index.match(row):
                continue
            # A researched list carries its own send/drop verdict (gtm_core.signal_record).
            # A dropped row renders `{{Why Now}}` blank, so labeling it would spend a slot
            # on an email that cannot be sent. Absent column => pre-verdict list => keep.
            if (row.get("verdict") or "send").strip() != "send":
                continue
            enriched = dict(row)
            enriched["seat"] = seat_of(row.get("title") or "", profile) or "-"
            enriched["cell"] = _row_cell(row, source, matrix)
            enriched["__spec"] = source.spec_path
            enriched["__csv"] = source.csv_path
            enriched["__staged"] = source.staged
            rows.append(enriched)
    return rows


def _load_matrix_if_present(
    profile: str, profiles_root: Path | None = None, overlay: str | None = None
) -> Matrix | None:
    """The profile's parsed hook-matrix, or ``None`` if it has none.

    Never raises on a missing file: ``resolve_knowledge_file`` always returns a path,
    whether or not it exists, and a profile with no matrix (or a fixture profile in a
    test) must fall back to the pre-existing spec-declared-cell behaviour exactly, not
    crash on a read of a file that was never there."""
    root = profiles_root or resolve_profiles_root()
    path = resolve_knowledge_file(root, profile, "hook-matrix.md", overlay=overlay)
    if not path.is_file():
        return None
    m = parse_matrix(path)
    return m if m.ok else None


def all_live_rows(
    profile: str,
    content_root: Path | None = None,
    campaign: str | None = None,
    include_drafts: bool = False,
    profiles_root: Path | None = None,
    overlay: str | None = None,
) -> tuple[list[dict], dict[str, list]]:
    """Every live row across every source, plus a ``spec_path -> touches`` map for
    rendering. One row dict per (recipient, source) — a recipient enrolled in only one
    sequence at a time in this pipeline, so no de-dup is needed.

    ``include_drafts`` adds the profile's drafted-but-unstaged cells
    (:func:`load_draft_sources`). Off by default: a caller measuring the live campaign
    must not silently pick up copy nobody ever sent."""
    matrix = _load_matrix_if_present(profile, profiles_root, overlay=overlay)
    sources = load_sources(profile, content_root, campaign=campaign)
    if include_drafts:
        sources = sources + load_draft_sources(profile, content_root)
    ledger_path = suppression_ledger(profile, content_root)
    suppression_index = load_suppression_index(ledger_path) if ledger_path.is_file() else None
    rows: list[dict] = []
    touches_by_spec: dict[str, list] = {}
    for src in sources:
        touches_by_spec[src.spec_path] = src.touches
        rows.extend(load_live_rows(src, matrix, suppression_index, profile))
    return rows, touches_by_spec


# --------------------------------------------------------------------------- injection recipes

#: Each recipe mutates ONE thing away from a real, otherwise-unmodified row/render —
#: the real-data analogue of the mutation suite's synthetic fixtures. ``kind="row"``
#: mutates the row dict (caller re-renders from it); ``kind="text"`` mutates the already-
#: rendered subject/body directly. ``donor_ok`` filters which real rows this recipe may
#: be applied to (e.g. persona-lead-mismatch needs a non-security seat to mean anything).
#:
#: **The ``rule`` field is a claim about the gate, and it is now verified.** It is stamped
#: onto the internal record as ``injected_rule`` and is the key
#: :func:`gtm_core.eval_calibration.rule_lifecycle_report` buckets operator labels under, so
#: a recipe naming a rule the gate cannot raise plants a defect attributed to nothing. That
#: is not hypothetical: on 2026-09-24 the FR3 retirement deleted 25 rules and EIGHT recipes
#: here kept naming them — ``credit-is-verdict``, ``problem-asserts-internals``,
#: ``cta-omits-gap``, ``offer-not-a-solution-overview``, ``specificity``, ``hedge-missing``,
#: ``cta-overclaim``, ``cta-bundled`` — roughly a third of every sheet. The recipes are gone;
#: what stops the next eight is :func:`build_injected_golden_rows`'s differential check,
#: which requires the named rule to fire on the MUTANT and not on the DONOR before a row is
#: emitted at all.


def _mutate_row(rule: str, fn):
    return {"rule": rule, "kind": "row", "fn": fn, "donor_ok": lambda row: True}


def _mutate_text(rule: str, fn, donor_ok=lambda row: True):
    return {"rule": rule, "kind": "text", "fn": fn, "donor_ok": donor_ok}


#: Rules raised by the RESEARCH-RECORD gate (:func:`gtm_core.signal_record.check_record`)
#: rather than by the copy linter, so their absence from ``outreach.ALL_RULE_IDS`` is
#: correct and not staleness. Named here so the catalogue test can tell "raised somewhere
#: else" from "retired", and pinned by a positive control that makes ``check_record``
#: actually emit each of them (§R12) — a list nobody exercises is how the first eight
#: survived.
RESEARCH_RECORD_RULES: tuple[str, ...] = (
    "relation-competitor",
    "relation-regulator",
    "signal-subject-mismatch",
)

#: Rules whose defect lives in the spec's FRONT BLOCK and never reaches a rendered body, so
#: no labeling sheet can ever carry one. ``slot-attribution`` fires on a ``slot_<id>:`` line
#: that is missing or contradicts the declared angle; the bodies render byte-identically
#: either way, which is the whole reason a reviewer could not check provenance by reading
#: the email. A recipe for it would be a mutation that changes nothing a labeler sees —
#: precisely the no-op this module's own comment warns about — so it is DECLARED invisible
#: instead, and joins the ``unusable`` list :func:`build_golden_set` hands to
#: ``eval_calibration --not-human-visible``. That band is never a delete candidate, which is
#: the honest verdict: absence of a human penalty says nothing about a defect no human saw.
STRUCTURALLY_INVISIBLE_RULES: tuple[str, ...] = ("slot-attribution",)


#: Positional edits on a rendered body, used by the copy recipes below. A body is one
#: sentence per line: greeting, blank, prose paragraphs, blank, sign-off.
#:
#: These are POSITIONAL on purpose. The recipes that went stale did so because they matched
#: a phrase ("Tell me if you've got this covered") that the copy later stopped using, and a
#: recipe that silently no-ops records a clean row as carrying a planted defect. "The line
#: before the sign-off" and "the paragraph after the greeting" survive a rewrite of the
#: words; a quoted sentence does not. See PENDING.md EC8.


def _prose_lines(body: str) -> list[int]:
    return [i for i, line in enumerate(body.split("\n")) if line.strip()]


def _insert_sentence(body: str, sentence: str, *, after: int) -> str:
    """Insert ``sentence`` as its own paragraph after the ``after``-th non-empty line."""
    lines = body.split("\n")
    idx = _prose_lines(body)
    if len(idx) <= after:
        return body
    return "\n".join(lines[: idx[after] + 1] + ["", sentence] + lines[idx[after] + 1 :])


def _replace_offer(body: str, sentence: str) -> str:
    """Replace the last prose line — the ask — leaving the sign-off in place."""
    lines = body.split("\n")
    idx = _prose_lines(body)
    if len(idx) < 2:
        return body
    lines[idx[-2]] = sentence
    return "\n".join(lines)


INJECTION_RECIPES = [
    # --- the FR3 retirement (2026-09-24) -------------------------------------------
    #
    # Eight COPY recipes stood at the top of this list until 2026-09-24, four of them added
    # only two days earlier (PENDING.md EC8) for rules the catalogue had just gained. FR3
    # retired 25 rules and every one of the eight named a rule that no longer exists:
    # `credit-is-verdict`, `problem-asserts-internals`, `cta-omits-gap`,
    # `offer-not-a-solution-overview`, `specificity`, `hedge-missing`, `cta-overclaim`,
    # `cta-bundled`. Their intent did not vanish — it moved to the quality card's
    # `fact_earns_its_place` / `claim_within_status` / `bridge_depends_on_fact` /
    # `frame_fits_seat` questions (`outreach.model`'s retirement block is the mapping's
    # home) — but a card question is not a rule id, and an injected row stamped with one
    # would be bucketed under a rule the report has no denominator for.
    #
    # They are not replaced one-for-one. The registry rules that inherited their job are
    # built by `registry_recipes` from the TENANT's own facts, because a `do_not_say`
    # phrase and a `disputed` figure are tenant data and cannot be literals in `gtm_core`
    # (§R4/§R9).
    #
    # --- AIM recipes (2026-08-21) --------------------------------------------------
    #
    # Every recipe below this block mutates row HYGIENE or copy SURFACE — a broken name, a
    # banned word, a subject in title case. None of them injects a MIS-AIMED email: right
    # argument, wrong account situation. That is the defect that actually shipped to 397
    # recipients, and the golden set could not measure it, which meant `rule_lifecycle_report`
    # had no human evidence for any aim rule and the operator was never shown the class they
    # kept rejecting rows for. An instrument that cannot see the dominant failure reports a
    # clean bill on a sick patient.
    #
    # These four inject it. They mutate the ROW's research record rather than its prose, so
    # the rendered email stays word-perfect and the only thing wrong is that it is aimed at
    # the wrong account — which is exactly what makes them hard, and exactly what makes a
    # human label on them worth having.
    _mutate_row(
        "premise-unsupported",
        # The Forgeworks/Atlas/ORBIT shape: one product on one platform, under a body whose
        # next paragraph claims a multi-framework or cross-platform pain.
        lambda row: {
            **row,
            "signal_evidence": (
                "Introducing a new generative AI feature inside the company's own studio "
                "product, built to help teams move from idea to output faster."
            ),
            "signal_clause": "shipped a new AI feature inside its own studio product",
        },
    ),
    _mutate_row(
        "relation-competitor",
        # Pitching agent identity to a company that sells agent identity. The registry
        # already knew about these accounts; the row shipped anyway.
        lambda row: {
            **row,
            "category_relation": "competitor",
            "signal_evidence": (
                "The company launched an agent identity and authorization product for "
                "enterprise customers, covering registration, delegation and audit."
            ),
            "signal_clause": "launched an agent identity and authorization product",
        },
    ),
    _mutate_row(
        "relation-regulator",
        # The MAS shape: a supervisory body, researched as a prospect, pitched a commercial
        # framing of the rules it wrote itself.
        lambda row: {
            **row,
            "category_relation": "regulator",
            "signal_evidence": (
                "The authority published a supervisory framework introducing a governance "
                "checkpoint between every agent decision and its execution."
            ),
            "signal_clause": "published a supervisory framework for agent governance",
        },
    ),
    _mutate_row(
        "signal-subject-mismatch",
        # The wrong-account shape: the clause names one company, the email goes to another. Renders
        # perfectly; the reader sees a paragraph about somebody else.
        lambda row: {
            **row,
            "signal_subject": "Northwind Logistics Group",
            "signal_evidence": (
                "Northwind Logistics Group announced an expansion of its autonomous "
                "dispatch programme across three regions."
            ),
            "signal_clause": "expanded its autonomous dispatch programme",
        },
    ),
    _mutate_row(
        "signal-off-topic",
        lambda row: {**row, "signal_clause": "Opened a new regional office last quarter"},
    ),
    _mutate_row(
        "signal-stray-digit",
        lambda row: {**row, "signal_clause": "Raised $45M for its agent platform in March 2026"},
    ),
    _mutate_row(
        "signal-contradicts-pitch",
        lambda row: {
            **row,
            "signal_clause": "Issues verified agent identities for every internal service",
        },
    ),
    _mutate_row(
        "signal-agent-homonym",
        lambda row: {
            **row,
            "signal_clause": "Licensed insurance agents now cover all fifty states",
        },
    ),
    _mutate_row(
        "last-name-symbols",
        lambda row: {**row, "last": (row.get("last") or "Smith") + "\U0001f366"},
    ),
    _mutate_row(
        "company-headline",
        lambda row: {**row, "company": f"{row.get('company') or 'Acme'} | We Build Trust"},
    ),
    _mutate_row(
        "company-allcaps", lambda row: {**row, "company": (row.get("company") or "ACME").upper()}
    ),
    _mutate_row(
        "email-domain-mismatch",
        lambda row: {
            **row,
            "email": (row.get("email") or "person@x.example").split("@")[0]
            + "@totally-different-org.example",
        },
    ),
    _mutate_row("first-name-initial", lambda row: {**row, "first": (row.get("first") or "J")[0]}),
    _mutate_text(
        "word-count",
        lambda subj, body: (
            subj,
            " ".join(body.split()[:30]) + "\n\n" + body.rsplit("\n\n", 1)[-1],
        ),
    ),
    _mutate_text(
        "banned-word", lambda subj, body: (subj, body.replace("Hi ", "Wanted to reach out, hi ", 1))
    ),
    _mutate_text("em-dash", lambda subj, body: (subj, body.replace(". ", " — ", 1))),
    _mutate_text(
        "no-links",
        lambda subj, body: (
            subj,
            body.replace("?\n\nHenry", " — details at https://example.com/agents?\n\nHenry"),
        ),
    ),
    _mutate_text("subject-lowercase", lambda subj, body: (subj.title(), body)),
    _mutate_text(
        "persona-lead-mismatch",
        lambda subj, body: (
            subj,
            body.replace(
                "Henry",
                "An auditor and an examiner will ask for the attribution trail before anything "
                "else, and that is attributable to nobody today.\n\nHenry",
                1,
            ),
        ),
        donor_ok=lambda row: row.get("seat") not in ("security", None, ""),
    ),
]


def registry_recipes(registry) -> list[dict]:
    """Injection recipes for the derivation rules, built from the TENANT's own registry.

    These cannot be literals in :data:`INJECTION_RECIPES`. ``claim-status`` fires on a
    phrase a tenant's own ``claims.toml`` lists under ``do_not_say``, and ``proof-status``
    on a figure its ``proof.toml`` records as ``disputed`` — both are tenant knowledge, and
    a copy of either in ``gtm_core`` would be the de-branding breach §R4 forbids *and* stale
    the day the operator appends a phrase. So the recipe closes over the live value.

    Deterministic: the first phrase and the first disputed figure in id order, so two runs
    of the same registry build the same sheet.

    The third derivation rule, ``slot-attribution``, gets no recipe on purpose — see
    :data:`STRUCTURALLY_INVISIBLE_RULES`. A registry holding no ``do_not_say`` phrase or no
    ``disputed`` proof yields fewer than two recipes rather than a recipe that cannot fire:
    an empty list is a smaller sheet, a sham recipe is a wrong label.
    """
    out: list[dict] = []
    phrases = sorted(
        (claim.id, phrase) for claim in registry.claims.values() for phrase in claim.do_not_say
    )
    if phrases:
        phrase = phrases[0][1]
        out.append(
            _mutate_text(
                "claim-status",
                # Straight after the greeting, as its own paragraph: the rule searches the
                # whole copy, so position is free, and a paragraph of its own keeps the
                # planted sentence from changing the word count of any other one.
                lambda subject, body, _p=phrase: (
                    subject,
                    _insert_sentence(body, f"To put it plainly, {_p}.", after=0),
                ),
            )
        )
    figures = sorted(
        (p.id, f)
        for p in registry.proof.values()
        if p.figure_kind == "disputed"
        for f in _figures(p.statement)
    )
    if figures:
        figure = figures[0][1]
        out.append(
            _mutate_text(
                "proof-status",
                # Planted WITH the unit `_figures` normalised it to (`90%`, `3 hour`): since
                # 2026-09-24 the unit is half a figure's identity, so a bare numeral would
                # plant a defect the gate cannot raise — which `_Probe` refuses, loudly.
                lambda subject, body, _f=figure: (
                    subject,
                    _insert_sentence(body, f"Our own number for this is {_f}.", after=1),
                ),
            )
        )
    return out


def _row_key(row: dict) -> str:
    return hashlib.sha256(
        f"{row.get('__spec')}|{row.get('__csv')}|{(row.get('email') or '').lower()}".encode()
    ).hexdigest()


# --------------------------------------------------------------------------- build the set


def build_real_golden_rows(
    pool: list[dict], touches_by_spec: dict, n_real: int, seed: str = ""
) -> tuple[list[GoldenRow], list[dict]]:
    """Sample + render ``n_real`` clean rows. Returns (golden rows, chosen raw rows) —
    the raw rows are needed by the caller to exclude them from the injection draw."""
    chosen = sample_golden_set(pool, n_real=n_real, seed=seed)
    out = []
    for row in chosen:
        touches = touches_by_spec.get(row["__spec"], [])
        t1 = next((t for t in touches if t.number == 1), None)
        if t1 is None:
            continue
        subject = render(t1.subject, row) or (touches[0].subject if touches else "")
        body = render(t1.body, row)
        out.append(
            build_golden_row(
                spec=row["__spec"],
                csv=row["__csv"],
                touch=1,
                email=row.get("email") or "",
                subject=subject,
                body=body,
                context={"title": row.get("title"), "company": row.get("company")},
                injected=False,
            )
        )
    return out, chosen


_SIGNOFF_RE = re.compile(r"^Sign-off:\s*(\S+)", re.MULTILINE)


class _Probe:
    """Runs the real outbound gates over one candidate mutation, and caches what it can.

    Its whole job is to answer *did this recipe plant the rule it names?* — the check that
    distinguishes a planted defect from a changed sentence. Kept as an object rather than a
    nest of closures so the caches (one spec read per spec, one clean lint per donor) are
    explicit, and so :meth:`planted` can be exercised directly by a test without building a
    whole sheet around it.
    """

    def __init__(self, touches_by_spec: dict, *, premise_vocab=None, registry=None) -> None:
        self._touches_by_spec = touches_by_spec
        self._premise_vocab = premise_vocab
        self._registry = registry
        self._spec_texts: dict[str, str] = {}
        self._clean_rules: dict[str, frozenset[str]] = {}

    def _spec_text(self, row: dict) -> str:
        """The donor's own spec, read once. ``lint_premise`` and the derivation rules judge
        the SPEC, not the render, so a probe that passed ``""`` here would report every
        spec-scoped recipe unusable and quietly shrink the sheet to the row-scoped ones."""
        path = str(row.get("__spec") or "")
        if path not in self._spec_texts:
            p = Path(path)
            self._spec_texts[path] = p.read_text(encoding="utf-8") if p.is_file() else ""
        return self._spec_texts[path]

    def _lint(self, touch, row: dict) -> frozenset[str]:
        """Every rule id the outbound gates raise against ONE touch rendered over ONE row.

        Two raisers, because the recipes span two gates and a probe that knew only one
        would report the other's rules as unplantable: the copy/merge gate
        (``lint_merge_render``, which also folds in ``merge_hygiene.check_row``, the premise
        rule and the derivation rules) and the research-record gate
        (``signal_record.check_record``, which owns :data:`RESEARCH_RECORD_RULES`).

        The sign-off comes off the donor's own spec header for the reason the CLI reads it
        there: a placeholder default makes every CTA rule inspect the signature line.
        """
        spec_text = self._spec_text(row)
        m = _SIGNOFF_RE.search(spec_text)
        violations, _ = lint_merge_render(
            [touch],
            [row],
            signoff=m.group(1) if m else "Alex",
            spec_text=spec_text,
            premise_vocab=self._premise_vocab,
            registry=self._registry,
        )
        return frozenset({v.rule for v in violations} | {f.rule for f in check_record(row)})

    def _subject_and_body(self, row: dict) -> tuple[str, str]:
        t1 = self.touch_one(row)
        return (render(t1.subject, row) or t1.subject, render(t1.body, row))

    def _as_touch(self, subject: str, body: str):
        """A one-touch spec whose template IS the finished text, so ``render`` is the
        identity on it and the gate sees exactly the bytes the labeler will."""
        return Touch(number=1, day=1, subject=subject, body=body)

    def _clean(self, row: dict, kind: str) -> frozenset[str]:
        """What the UNMUTATED donor already raises — measured the SAME WAY the mutant will
        be, and cached per (row, kind).

        The two kinds are not interchangeable and a single baseline would be the §R18
        failure in miniature. A ``row`` recipe keeps the real template, so rules that read
        a merge tag (``lint_signal_relevance`` wants a ``{{Why Now}}`` to decide the signal
        is even used) can still fire. A ``text`` recipe mutates the RENDER, which no
        template can express, so both sides are linted as a pre-rendered touch. Comparing a
        pre-rendered mutant against a templated baseline would report every tag-dependent
        rule as "newly raised" and call a no-op mutation a planted defect.

        Known and deliberate consequence of the ``text`` shape: a rule that judges the
        TEMPLATE sees this donor's merged values as if the author had typed them, so a
        company name carrying a digit puts ``proof-status`` in the baseline and the recipe
        moves to the next donor. That errs toward refusing a usable donor rather than
        crediting an unplanted defect, which is the direction this whole check exists to
        err in.
        """
        key = f"{_row_key(row)}|{kind}"
        if key not in self._clean_rules:
            t1 = self.touch_one(row)
            if t1 is None:
                self._clean_rules[key] = frozenset()
            elif kind == "row":
                self._clean_rules[key] = self._lint(t1, row)
            else:
                self._clean_rules[key] = self._lint(
                    self._as_touch(*self._subject_and_body(row)), row
                )
        return self._clean_rules[key]

    def touch_one(self, row: dict):
        return next(
            (t for t in self._touches_by_spec.get(row["__spec"], []) if t.number == 1), None
        )

    def changed(self, recipe: dict, row: dict) -> tuple[str, str, dict] | None:
        """The mutated ``(subject, body, row)`` when this recipe visibly changes this
        donor's render, else ``None``. One implementation, so the viability sweep and the
        emission sweep cannot disagree about what "visible" means. The row comes back too
        because a ``kind="row"`` recipe's defect lives in it, and the fires check has to
        lint the mutated row rather than the donor's."""
        if not recipe["donor_ok"](row):
            return None
        t1 = self.touch_one(row)
        if t1 is None:
            return None
        clean_subject, clean_body = self._subject_and_body(row)
        mutated_row = row
        if recipe["kind"] == "row":
            mutated_row = recipe["fn"](row)
            subject = render(t1.subject, mutated_row) or t1.subject
            body = render(t1.body, mutated_row)
        else:
            subject, body = recipe["fn"](clean_subject, clean_body)
        # The load-bearing check: did the labeler's view actually change?
        if (subject, body) == (clean_subject, clean_body):
            return None
        return subject, body, mutated_row

    def planted(self, recipe: dict, row: dict) -> tuple[str, str] | None:
        """The mutated ``(subject, body)`` when this recipe visibly changes this donor's
        render AND plants the rule it names, else ``None``.

        Ordered cheap-first on purpose: a recipe whose defect never renders (the
        ``last-name-symbols`` class) is rejected by :meth:`changed` and costs no gate run at
        all, so the lint is spent only where a mutation is genuinely a candidate.
        """
        mutated = self.changed(recipe, row)
        if mutated is None:
            return None
        subject, body, mutated_row = mutated
        rule = recipe["rule"]
        if rule in self._clean(row, recipe["kind"]):
            # Already broken before the mutation: labelling this "planted" would credit the
            # recipe with a defect the donor arrived with, and the operator's label would be
            # attributed to a mutation that changed nothing about why the rule fires.
            return None
        after = (
            self._lint(self.touch_one(row), mutated_row)
            if recipe["kind"] == "row"
            else self._lint(self._as_touch(subject, body), row)
        )
        return (subject, body) if rule in after else None


def build_injected_golden_rows(
    pool: list[dict],
    touches_by_spec: dict,
    recipes: list[dict],
    n_injected: int,
    *,
    premise_vocab: dict | None = None,
    registry=None,
) -> tuple[list[GoldenRow], list[str]]:
    """Draw ``n_injected`` DIFFERENT rows (round-robin over ``recipes`` so the mix is
    diverse, not n copies of recipe 1) and apply one mutation each.

    Returns ``(rows, unusable_rules)``.

    **And a mutation only counts if it plants the defect it CLAIMS to plant.** The
    visible-change check below is necessary and was never sufficient: a recipe can change
    the text and still fail to raise the rule it is stamped with — because the rule
    retired, because the copy moved out from under a phrase match, or because the recipe
    was wrong about what the rule reads. Eight recipes were in exactly that state on
    2026-09-24 (see :data:`INJECTION_RECIPES`), ~8 of every ~20 sheet rows, and the visible-
    change check passed every one of them: the text HAD changed. So the test is
    differential and runs the real gate — the named rule must fire on the mutant and must
    NOT already fire on the unmutated donor. The second half matters as much as the first:
    a donor whose clean render already trips the rule would be recorded as carrying a
    planted defect that was there before anyone planted anything.

    ``premise_vocab`` and ``registry`` are the two inputs the gate needs before it can
    raise ``premise-unsupported`` and the derivation rules. Without them those rules cannot
    fire, so a recipe naming one is reported unusable rather than emitted unverified — the
    same fail-closed direction as everything else here.

    **A mutation only counts if it changes what the labeler will actually see.** Found
    2026-08-20 by running a blind judge pass over the first real sheet and scoring it
    against this file's own ground truth: 4 of 20 planted defects were "missed", and none
    of the four was a judging failure —

    * ``last-name-symbols`` / ``email-domain-mismatch`` — those fields never render into
      touch 1's body, and the sheet withholds the address as PII, so the defect was
      *invisible on the page by construction*;
    * ``company-allcaps`` — ``{{Company}}`` does not appear in touch 1 of the technical
      variant, so uppercasing it changed nothing a reader could see;
    * ``hedge-missing`` — the recipe stripped ``"Tell me if you've got this covered"``
      while the shipped copy says ``"Tell me if this is already handled"``, so the
      mutation **silently no-opped** and a perfectly clean row was recorded as carrying a
      planted defect.

    Both failure modes corrupt P2 the same way, and in the most dangerous possible
    direction: :func:`gtm_core.eval_calibration.rule_lifecycle_report` reads "fires, but
    no labeled injected instance was penalised" as **delete-candidate**, so four working
    rules would have been retired on evidence that was an artifact of the sheet, not a
    fact about the rules. Verifying the render actually changed subsumes both — an
    invisible mutation and a no-op mutation are the same thing from the labeler's side,
    and neither belongs on a human labeling sheet. (Those rules keep their real negative
    control in ``tests/linter/test_merge_render_mutation_suite.py``, which asserts against
    the linter directly and does not care whether a human could see the defect.)
    """
    ranked = sorted(pool, key=_row_key)
    probe = _Probe(touches_by_spec, premise_vocab=premise_vocab, registry=registry)
    _planted = probe.planted

    out: list[GoldenRow] = []
    used_rows: set[str] = set()
    applied: set[str] = set()
    i = 0
    # Bounded sweep: for each recipe in round-robin order, walk donors until one produces a
    # visibly-changed render that RAISES the recipe's own rule. A recipe that never does is
    # reported, never silently kept.
    while len(out) < n_injected and i < len(recipes) * max(1, len(ranked)):
        recipe = recipes[i % len(recipes)]
        i += 1
        for row in ranked:
            key = _row_key(row)
            if key in used_rows:
                continue
            mutated = _planted(recipe, row)
            if mutated is None:
                continue
            subject, body = mutated
            used_rows.add(key)
            applied.add(recipe["rule"])
            out.append(
                build_golden_row(
                    spec=row["__spec"],
                    csv=row["__csv"],
                    touch=1,
                    # The real email, unmodified — the caller already excludes every row
                    # chosen for the real stratum from this pool, so no row_id collision
                    # is possible; suffixing it would only corrupt the field P3's
                    # reconciliation joins outcomes on.
                    email=row.get("email") or "",
                    subject=subject,
                    body=body,
                    context={"title": row.get("title"), "company": row.get("company")},
                    injected=True,
                    injected_rule=recipe["rule"],
                )
            )
            break
    # Viability is a property of the RECIPE and the COPY, never of ``n_injected``. Deriving
    # it from `applied` alone made it a property of the budget: the emission sweep stops at
    # `n_injected` successes, so every recipe the rotation had not yet reached was reported
    # as "its defect never became visible". Measured on a live tenant corpus 2026-09-22,
    # same recipes and same copy: 11 unusable at `--n-injected 12`, 7 at 16, 5 at 18, 3 at
    # 20. That number is fed to `eval_calibration --not-human-visible`, where it means "no
    # human could see this defect, so absence of a penalty is meaningless" — a claim about
    # the sheet that silently became a claim about the operator's budget, hiding working
    # rules from the only report that can retire them. `applied` short-circuits the walk, so
    # a recipe that already produced a row costs nothing here.
    #
    # Since 2026-09-24 the sweep asks `planted`, not `changed`: "viable" now means the
    # recipe plants ITS OWN rule on some donor, not merely that it moves some bytes. A
    # recipe that changes the text and raises nothing is a recipe whose label is wrong, and
    # reporting it as viable is what let eight dead ones go on stamping sheet rows.
    viable = {
        r["rule"]
        for r in recipes
        if r["rule"] in applied or any(_planted(r, row) for row in ranked)
    }
    unusable = sorted(({r["rule"] for r in recipes} - viable) | set(STRUCTURALLY_INVISIBLE_RULES))
    return out, unusable


def _load_premise_vocab(profile: str, *, overlay: str | None = None) -> dict | None:
    """The tenant's premise vocabulary, or ``None`` when it ships none.

    ``{}`` and ``None`` are the same thing to ``lint_premise`` — both switch the check off —
    so this normalises to ``None``, which is the value the CLI's own loader passes and the
    one this module's "the rule cannot fire, so the recipe is unusable" branch reads.
    """
    from .hook_coverage import load_premise_vocab

    return load_premise_vocab(profile, overlay=overlay) or None


def _load_registry(profile: str, *, overlay: str | None = None):
    """The tenant's fact registry, or ``None`` with a loud reason on stderr.

    Same shape and the same argument as the linter CLI's ``_load_registry``: a tenant that
    has not written ``claims.toml`` still gets a sheet, and the derivation recipes come back
    in ``unusable`` rather than being emitted with a rule nothing could raise. The reason is
    printed because "off" must never be mistaken for "clean" — the registry has its own
    gate, and the notice names it.
    """
    from .messaging.registry import RegistryError
    from .messaging.registry import load as _load

    try:
        return _load(profile, overlay=overlay)
    except RegistryError as exc:
        first = str(exc).splitlines()[0] if str(exc) else "unreadable"
        print(
            f"note: derivation injections OFF for {profile!r} — registry did not load "
            f"({first}). Run `uv run python -m gtm_core.messaging check --profile {profile}`.",
            file=sys.stderr,
        )
        return None


def build_golden_set(
    profile: str,
    *,
    n_real: int = 80,
    n_injected: int = 20,
    content_root: Path | None = None,
    campaign: str | None = None,
    seed: str = "",
    include_drafts: bool = False,
    overlay: str | None = None,
) -> tuple[list[GoldenRow], list[str]]:
    """Returns ``(rows, unusable_rules)`` — the second element names every recipe whose
    defect never became visible in a rendered touch 1, or that never raised the rule it
    names, so a caller can report it rather than let the sheet quietly under-cover those
    rules (see :func:`build_injected_golden_rows`).

    The profile's premise vocabulary and fact registry are loaded here, once, and handed
    down for two jobs: they are what lets ``registry_recipes`` build the derivation
    injections out of this tenant's own facts, and what lets the gate raise
    ``premise-unsupported`` / ``claim-status`` / ``proof-status`` at all when the planted-
    defect check asks it to. Either being absent narrows the sheet; neither is fatal, and
    the recipes that depended on it come back in ``unusable`` rather than going out
    unverified."""
    pool, touches_by_spec = all_live_rows(
        profile, content_root, campaign=campaign, include_drafts=include_drafts, overlay=overlay
    )
    if not pool:
        scope = f" campaign {campaign!r}" if campaign else ""
        raise ValueError(f"no live rows found for profile {profile!r}{scope} — check cells.toml")
    real, chosen = build_real_golden_rows(pool, touches_by_spec, n_real, seed=seed)
    chosen_keys = {_row_key(row) for row in chosen}
    remaining = [row for row in pool if _row_key(row) not in chosen_keys]
    premise_vocab = _load_premise_vocab(profile, overlay=overlay)
    registry = _load_registry(profile, overlay=overlay)
    recipes = INJECTION_RECIPES + (registry_recipes(registry) if registry is not None else [])
    injected, unusable = build_injected_golden_rows(
        remaining,
        touches_by_spec,
        recipes,
        n_injected,
        premise_vocab=premise_vocab,
        registry=registry,
    )
    return real + injected, unusable


# --------------------------------------------------------------------------- write outputs


def add_duplicates(rows: list[GoldenRow], n: int) -> list[GoldenRow]:
    """Repeat ``n`` rows, unmarked and spread through the sheet, so the labeler meets the
    same email twice without knowing it.

    A duplicate shares its ``row_id``, so :func:`gtm_core.eval_calibration.intra_rater_agreement`
    can recover self-agreement afterward — the ceiling on the whole program, and the only
    way to tell "the judge is weak" from "the target is noisy" when validation disappoints.
    Chosen by stable hash and interleaved at fixed offsets rather than randomly, so the
    sheet reproduces exactly on a re-run."""
    if n <= 0 or not rows:
        return list(rows)
    n = min(n, len(rows))
    picked = sorted(rows, key=lambda r: r.row_id)[:n]
    out = list(rows)
    # Spread the repeats across the back half, far from their originals so the labeler is
    # unlikely to recognise them from short-term memory alone.
    step = max(1, len(out) // (n + 1))
    for i, dup in enumerate(picked):
        out.insert(min(len(out), len(out) // 2 + (i + 1) * step), dup)
    return out


def choose_blank(rows: list[GoldenRow], n_blank: int) -> set[str]:
    """Which ``row_id``s stay unfilled when the sheet is pre-filled.

    Stratified across injected/real by stable hash, because the blank set becomes the
    sealed holdout and a holdout with no planted defects in it would have almost no
    negative class to measure TNR against.

    **Every duplicated row is forced blank**, over and above ``n_blank``. A duplicate
    exists to measure the labeler against themselves; pre-filling it would show the same
    suggestion in both places, the labeler would keep it both times, and
    :func:`gtm_core.eval_calibration.intra_rater_agreement` would report a flawless 1.0
    that measures nothing but the suggestion's stability. Relying on sort order to make
    this happen (as it did by luck on the first real run) is not a guarantee, so it is
    enforced here."""
    if n_blank <= 0 and not _duplicated_ids(rows):
        return set()
    injected = sorted({r.row_id for r in rows if r.injected})
    real = sorted({r.row_id for r in rows if not r.injected})
    share_inj = round(n_blank * len(injected) / max(1, len(injected) + len(real)))
    take_inj = injected[: min(share_inj, len(injected))]
    take_real = real[: max(0, n_blank - len(take_inj))]
    return set(take_inj) | set(take_real) | _duplicated_ids(rows)


def _duplicated_ids(rows: Sequence[GoldenRow]) -> set[str]:
    seen: set[str] = set()
    dups: set[str] = set()
    for r in rows:
        if r.row_id in seen:
            dups.add(r.row_id)
        seen.add(r.row_id)
    return dups


def write_golden_set(
    profile: str,
    rows: list[GoldenRow],
    *,
    content_root: Path | None = None,
    prefill: dict | None = None,
) -> dict:
    out_dir = evals_dir(profile, content_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    date = datetime.now(UTC).strftime("%Y-%m-%d")

    sheet_path = out_dir / f"sheet-{date}.md"
    sheet_path.write_text(
        render_labeling_sheet(rows, prefill=prefill, fields=SHEET_FIELDS), encoding="utf-8"
    )

    internal_path = out_dir / f"internal-{date}.jsonl"
    internal_path.write_text(
        "\n".join(
            json.dumps(
                {
                    "row_id": r.row_id,
                    "spec": r.spec,
                    "csv": r.csv,
                    "touch": r.touch,
                    "email": r.email,
                    "injected": r.injected,
                    "injected_rule": r.injected_rule,
                }
            )
            for r in rows
        )
        + "\n",
        encoding="utf-8",
    )
    return {"sheet": str(sheet_path), "internal": str(internal_path), "rows": len(rows)}


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m gtm_core.build_eval_sheet")
    ap.add_argument("--profile", required=True)
    ap.add_argument(
        "--campaign",
        help="only sample sequences whose cells.toml row carries this campaign. During a "
        "re-cut both generations sit in cells.toml at once; without this the sheet mixes "
        "copy that is being retired into the calibration set.",
    )
    ap.add_argument(
        "--seed",
        default="",
        help="varies WHICH exemplar each stratum contributes. Same seed reproduces a sheet "
        "exactly; a new seed draws different rows from the same strata, so consecutive evals "
        "stop re-reading the same rows. Default (empty) reproduces the historical draw.",
    )
    ap.add_argument(
        "--include-drafts",
        action="store_true",
        help="also sample drafted-but-unstaged cells from evals/drafts/<cell>/. This is how "
        "an eval widens hook-matrix coverage without enrolling anybody: copy scoped per "
        "staged sequence collapses the messaging axis to however many campaigns happen to "
        "be live. Drafted rows are marked DRAFT in the coverage report and carry no "
        "outcomes, so nothing about them may be read as evidence about a live campaign.",
    )
    ap.add_argument(
        "--overlay",
        default=None,
        help="resolve hook-matrix.md through this experiment overlay "
        "(profiles/<t>/experiments/<slug>/) instead of the live one. Explicit and never "
        "ambient, exactly like --product: an eval sheet built against an experimental grid "
        "must say so, or its conclusions are attributed to copy nobody ran.",
    )
    ap.add_argument("--n-real", type=int, default=80)
    ap.add_argument("--n-injected", type=int, default=20)
    ap.add_argument(
        "--duplicate",
        type=int,
        default=0,
        help="repeat N rows unmarked, to measure the labeler's agreement with THEMSELVES "
        "(the ceiling on the whole program — see eval_calibration.intra_rater_agreement)",
    )
    ap.add_argument(
        "--prefill",
        help="JSONL of model suggestions (row_id + send_it + sub-checks + note) to "
        "pre-populate the sheet with, for the operator to CORRECT rather than author",
    )
    ap.add_argument(
        "--blank",
        type=int,
        default=35,
        help="with --prefill, how many rows stay UNFILLED. These are the only rows that can "
        "cleanly validate the judge that pre-filled the rest, and are what seal_holdout uses "
        "(default 35, stratified across injected/real)",
    )
    args = ap.parse_args(argv)
    _safe_segment(args.profile, "profile")

    rows, unusable = build_golden_set(
        args.profile,
        n_real=args.n_real,
        n_injected=args.n_injected,
        campaign=args.campaign,
        seed=args.seed,
        include_drafts=args.include_drafts,
        overlay=args.overlay,
    )
    if args.duplicate:
        rows = add_duplicates(rows, args.duplicate)

    prefill = None
    if args.prefill:
        suggestions = {}
        for line in Path(args.prefill).read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                suggestions[d["row_id"]] = d
        blank = choose_blank(rows, args.blank)
        prefill = {rid: s for rid, s in suggestions.items() if rid not in blank}
        missing = {r.row_id for r in rows} - set(suggestions) - blank
        if missing:
            print(f"warning: {len(missing)} row(s) have no suggestion and will render blank")

    # Coverage, printed every run. The 2026-08-21 sheet drew 30 rows across 4 seats and 2
    # segments and reported itself as a stratified sample — while every row carried one of
    # TWO hook-matrix cells out of 74, because copy is scoped per list and the sampler had no
    # messaging axis. Every conclusion from that sheet was about two arguments, and nothing in
    # its output said so. An eval that cannot state what it did NOT cover is not an eval.
    pool_all, _ = all_live_rows(
        args.profile,
        campaign=args.campaign,
        include_drafts=args.include_drafts,
        overlay=args.overlay,
    )
    drawn_specs = {r.spec for r in rows}
    drawn = [row for row in pool_all if row["__spec"] in drawn_specs]
    cells_drawn = sorted({row["cell"] for row in drawn})
    # Which of the drawn cells came from unstaged drafts. Printed per cell rather than as a
    # total, because "9 cells" reads as nine live arguments unless the sheet says otherwise.
    draft_cells = {row["cell"] for row in drawn if not row.get("__staged", True)}
    collapsed = collapsed_axes(pool_all)
    print(f"\ncoverage: {len(cells_drawn)} hook-matrix cell(s) across {len(drawn_specs)} spec(s)")
    for c in cells_drawn:
        print(f"    {'[DRAFT] ' if c in draft_cells else '        '}{c}")
    if draft_cells:
        print(
            f"  {len(draft_cells)} of {len(cells_drawn)} cell(s) are DRAFT — copy written for "
            f"this eval and never staged to a sequencer. Judge the writing; do not read them "
            f"as evidence about a live campaign, and do not enroll them."
        )
    if collapsed:
        print(
            f"  COLLAPSED AXES (did not vary, so this sample says nothing about them): "
            f"{', '.join(collapsed)}"
        )
    if len(cells_drawn) < 4:
        print(
            f"  ⚠ only {len(cells_drawn)} argument(s) under test. Findings generalise to these "
            f"cells and no further — `hook_coverage` targets >= 4 distinct arguments. Draft "
            f"across more cells before reading any result as a fact about 'the copy'."
        )

    real_n = sum(1 for r in rows if not r.injected)
    injected_n = sum(1 for r in rows if r.injected)
    result = write_golden_set(args.profile, rows, prefill=prefill)
    dup_note = f", {args.duplicate} duplicated" if args.duplicate else ""
    if prefill is not None:
        print(
            f"{result['rows']} rows ({real_n} real, {injected_n} injected{dup_note}) — "
            f"{len(prefill)} pre-filled, {result['rows'] - len(prefill)} blank"
        )
    else:
        print(f"{result['rows']} rows ({real_n} real, {injected_n} injected{dup_note})")
    print(f"sheet    -> {result['sheet']}")
    print(f"internal -> {result['internal']}  (NEVER show this to the labeler)")
    if unusable:
        print(
            f"\nNOT injectable on this sheet ({len(unusable)}): {', '.join(unusable)}\n"
            "  Either their defect never changes a rendered touch 1 — the field isn't in the\n"
            "  copy, the sheet withholds it as PII, or it lives in the spec's front block —\n"
            "  or the mutation ran and the rule did not fire, which means the recipe's label\n"
            "  is wrong. A human labeler cannot penalise what they cannot see, so these rules\n"
            "  get NO human evidence from this sheet and must be excluded from the P2\n"
            "  keep/delete audit rather than read as 'nobody cared'.\n"
            "  Their negative control lives in tests/linter/test_merge_render_mutation_suite.py."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
