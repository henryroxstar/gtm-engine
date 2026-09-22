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
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_LINTER_DIR = Path(__file__).resolve().parent.parent / "tests" / "linter"
if str(_LINTER_DIR) not in sys.path:  # pragma: no cover - import plumbing
    sys.path.insert(0, str(_LINTER_DIR))

from merge_render_linter import parse_spec, render  # noqa: E402
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
from .paths import _safe_segment, resolve_knowledge_file, resolve_profiles_root  # noqa: E402
from .prospect_paths import suppression_ledger  # noqa: E402
from .prospects_consolidate import _prospects_dir  # noqa: E402
from .signal_record import HOOK_CELL_COLUMN, SIGNAL_COLUMN  # noqa: E402
from .suppression import LedgerIndex  # noqa: E402
from .suppression import load_index as load_suppression_index  # noqa: E402

# --------------------------------------------------------------------------- loading real rows


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
            enriched["seat"] = seat_of(row.get("title") or "") or "-"
            enriched["cell"] = _row_cell(row, source, matrix)
            enriched["__spec"] = source.spec_path
            enriched["__csv"] = source.csv_path
            enriched["__staged"] = source.staged
            rows.append(enriched)
    return rows


def _load_matrix_if_present(profile: str, profiles_root: Path | None = None) -> Matrix | None:
    """The profile's parsed hook-matrix, or ``None`` if it has none.

    Never raises on a missing file: ``resolve_knowledge_file`` always returns a path,
    whether or not it exists, and a profile with no matrix (or a fixture profile in a
    test) must fall back to the pre-existing spec-declared-cell behaviour exactly, not
    crash on a read of a file that was never there."""
    root = profiles_root or resolve_profiles_root()
    path = resolve_knowledge_file(root, profile, "hook-matrix.md")
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
) -> tuple[list[dict], dict[str, list]]:
    """Every live row across every source, plus a ``spec_path -> touches`` map for
    rendering. One row dict per (recipient, source) — a recipient enrolled in only one
    sequence at a time in this pipeline, so no de-dup is needed.

    ``include_drafts`` adds the profile's drafted-but-unstaged cells
    (:func:`load_draft_sources`). Off by default: a caller measuring the live campaign
    must not silently pick up copy nobody ever sent."""
    matrix = _load_matrix_if_present(profile, profiles_root)
    sources = load_sources(profile, content_root, campaign=campaign)
    if include_drafts:
        sources = sources + load_draft_sources(profile, content_root)
    ledger_path = suppression_ledger(profile, content_root)
    suppression_index = load_suppression_index(ledger_path) if ledger_path.is_file() else None
    rows: list[dict] = []
    touches_by_spec: dict[str, list] = {}
    for src in sources:
        touches_by_spec[src.spec_path] = src.touches
        rows.extend(load_live_rows(src, matrix=matrix, suppression_index=suppression_index))
    return rows, touches_by_spec


# --------------------------------------------------------------------------- injection recipes

#: Each recipe mutates ONE thing away from a real, otherwise-unmodified row/render —
#: the real-data analogue of the mutation suite's synthetic fixtures. ``kind="row"``
#: mutates the row dict (caller re-renders from it); ``kind="text"`` mutates the already-
#: rendered subject/body directly. ``donor_ok`` filters which real rows this recipe may
#: be applied to (e.g. persona-lead-mismatch needs a non-security seat to mean anything).


def _mutate_row(rule: str, fn):
    return {"rule": rule, "kind": "row", "fn": fn, "donor_ok": lambda row: True}


def _mutate_text(rule: str, fn, donor_ok=lambda row: True):
    return {"rule": rule, "kind": "text", "fn": fn, "donor_ok": donor_ok}


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
    # --- COPY recipes (2026-09-22, PENDING.md EC8) ---------------------------------
    #
    # The four rules `merge_render_linter`'s catalogue gained on 2026-09-22 had no recipe,
    # so `rule_lifecycle_report` scored them "zero fires, no injected-label evidence" =
    # delete-candidate, on the live corpus, for the rule the operator had just asked for.
    #
    # All four are TEXT recipes, against the general preference for `_mutate_row`. That
    # preference exists because a row mutation survives a copy rewrite — but these rules
    # judge the TEMPLATE's prose, and no value in the row dict can put a credit verdict or
    # a disconnected ask into it. Positional edits (above) are the available substitute.
    #
    # Each was checked against 40 live donors: fires its own rule on 40/40, and the
    # unmutated render of every one of those donors is clean. The `cta-omits-gap` sentence
    # is deliberately ~16 words because a short one drops 4 of the 40 bodies under
    # `WORDS_HARD_MIN`, planting a second defect the label could not be attributed to.
    _mutate_text(
        "credit-is-verdict",
        # Grades the reader's competence in the opener. `CREDIT_OPENER_SENTENCES = 2`, and
        # `_sentences` glues the greeting onto the first sentence, so this has to land in
        # the paragraph straight after the greeting to be inside the scope.
        lambda subject, body: (
            subject,
            _insert_sentence(body, "You have the hard part done already.", after=0),
        ),
    ),
    _mutate_text(
        "problem-asserts-internals",
        # States an architecture as fact about this reader. Goes in the MIDDLE: the rule
        # reads `sentences[1:-1]`, and the greeting-glued first sentence is outside it.
        lambda subject, body: (
            subject,
            _insert_sentence(
                body,
                "Your gateway sits between every agent and the payment rail.",
                after=1,
            ),
        ),
    ),
    _mutate_text(
        "cta-omits-gap",
        # An ask that shares no vocabulary with the problem above it and carries no
        # back-reference — it would read the same under any body.
        lambda subject, body: (
            subject,
            _replace_offer(
                body,
                "Would fifteen minutes on Thursday or Friday be useful, or would the "
                "following week suit better?",
            ),
        ),
    ),
    _mutate_text(
        "offer-not-a-solution-overview",
        # An offer that names an inspection of THEIR build rather than an approach a class
        # of company could use. Keeps a content word ("agent") so `cta-omits-gap` does not
        # also fire and confound the label.
        lambda subject, body: (
            subject,
            _replace_offer(
                body,
                "Would it be helpful if I mapped your agent stack against section 2.1.2?",
            ),
        ),
    ),
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
        "specificity",
        lambda subj, body: (
            subj,
            body.split("\n\n")[0]
            + "\n\nI think about identity a lot these days, and it seems worth a conversation "
            "given how things are moving in the space right now for a lot of teams.\n\n"
            + body.rsplit("\n\n", 1)[-1],
        ),
    ),
    _mutate_text(
        "word-count",
        lambda subj, body: (
            subj,
            " ".join(body.split()[:30]) + "\n\n" + body.rsplit("\n\n", 1)[-1],
        ),
    ),
    _mutate_text(
        "hedge-missing",
        lambda subj, body: (
            subj,
            body.replace("Tell me if you've got this covered", "")
            .replace("tell me if you've got this covered", "")
            .replace("My hunch:", "")
            .replace("my hunch:", ""),
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
        "cta-overclaim",
        lambda subj, body: (
            subj,
            body.rsplit("\n\n", 2)[0]
            + "\n\nWould the one-pager on how the trail clears an examiner review be useful?\n\nHenry",
        ),
    ),
    _mutate_text(
        "cta-bundled",
        lambda subj, body: (
            subj,
            body.rsplit("\n\n", 2)[0]
            + "\n\nWant the one-pager and a short recorded demo?\n\nHenry",
        ),
    ),
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


def build_injected_golden_rows(
    pool: list[dict], touches_by_spec: dict, recipes: list[dict], n_injected: int
) -> tuple[list[GoldenRow], list[str]]:
    """Draw ``n_injected`` DIFFERENT rows (round-robin over ``recipes`` so the mix is
    diverse, not n copies of recipe 1) and apply one mutation each.

    Returns ``(rows, unusable_rules)``.

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

    def _changed(recipe: dict, row: dict) -> tuple[str, str] | None:
        """The mutated ``(subject, body)`` when this recipe visibly changes this donor's
        render, else ``None``. One implementation, so the viability sweep below and the
        emission sweep cannot disagree about what "visible" means."""
        if not recipe["donor_ok"](row):
            return None
        touches = touches_by_spec.get(row["__spec"], [])
        t1 = next((t for t in touches if t.number == 1), None)
        if t1 is None:
            return None
        clean_subject = render(t1.subject, row) or t1.subject
        clean_body = render(t1.body, row)
        if recipe["kind"] == "row":
            mutated_row = recipe["fn"](row)
            subject = render(t1.subject, mutated_row) or t1.subject
            body = render(t1.body, mutated_row)
        else:
            subject, body = recipe["fn"](clean_subject, clean_body)
        # The load-bearing check: did the labeler's view actually change?
        if (subject, body) == (clean_subject, clean_body):
            return None
        return subject, body

    out: list[GoldenRow] = []
    used_rows: set[str] = set()
    applied: set[str] = set()
    i = 0
    # Bounded sweep: for each recipe in round-robin order, walk donors until one produces
    # a visibly-changed render. A recipe that never does is reported, never silently kept.
    while len(out) < n_injected and i < len(recipes) * max(1, len(ranked)):
        recipe = recipes[i % len(recipes)]
        i += 1
        for row in ranked:
            key = _row_key(row)
            if key in used_rows:
                continue
            mutated = _changed(recipe, row)
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
    viable = {
        r["rule"]
        for r in recipes
        if r["rule"] in applied or any(_changed(r, row) for row in ranked)
    }
    unusable = sorted({r["rule"] for r in recipes} - viable)
    return out, unusable


def build_golden_set(
    profile: str,
    *,
    n_real: int = 80,
    n_injected: int = 20,
    content_root: Path | None = None,
    campaign: str | None = None,
    seed: str = "",
    include_drafts: bool = False,
) -> tuple[list[GoldenRow], list[str]]:
    """Returns ``(rows, unusable_rules)`` — the second element names every recipe whose
    defect never became visible in a rendered touch 1, so a caller can report it rather
    than let the sheet quietly under-cover those rules (see
    :func:`build_injected_golden_rows`)."""
    pool, touches_by_spec = all_live_rows(
        profile, content_root, campaign=campaign, include_drafts=include_drafts
    )
    if not pool:
        scope = f" campaign {campaign!r}" if campaign else ""
        raise ValueError(f"no live rows found for profile {profile!r}{scope} — check cells.toml")
    real, chosen = build_real_golden_rows(pool, touches_by_spec, n_real, seed=seed)
    chosen_keys = {_row_key(row) for row in chosen}
    remaining = [row for row in pool if _row_key(row) not in chosen_keys]
    injected, unusable = build_injected_golden_rows(
        remaining, touches_by_spec, INJECTION_RECIPES, n_injected
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
    sheet_path.write_text(render_labeling_sheet(rows, prefill=prefill), encoding="utf-8")

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
        args.profile, campaign=args.campaign, include_drafts=args.include_drafts
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
            "  Their defect never changes a rendered touch 1 — the field isn't in the copy,\n"
            "  or the sheet withholds it as PII. A human labeler cannot penalise what they\n"
            "  cannot see, so these rules get NO human evidence from this sheet and must be\n"
            "  excluded from the P2 keep/delete audit rather than read as 'nobody cared'.\n"
            "  Their negative control lives in tests/linter/test_merge_render_mutation_suite.py."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
