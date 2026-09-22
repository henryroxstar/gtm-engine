from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path

from ..paths import resolve_knowledge_file, resolve_profiles_root
from ..prospects_consolidate import _prospects_dir
from ..signal_record import HOOK_CELL_COLUMN, SIGNAL_COLUMN
from .config import (
    EXEMPLARS,
    JACCARD_MAX,
    MAX_NGRAM_EMAILS,
    MAX_SPECS_PER_CAPABILITY,
    MIN_ARGUMENTS,
    MIN_RECIPIENTS,
    MIN_SEGMENT_FIT,
    MIN_SIGNAL_ATTESTATION,
    NGRAM_N,
    PACK_GLOBS,
    SIGNAL_TERM_NOISE_SHARE,
    draft_cell_dirs,
    load_cell_map,
    persona_of,
    seat_of,
)
from .coverage import Coverage, persona_coverage
from .declared import declared_cell
from .distinctness import argument_distinctness, shared_phrases
from .fit import _norm_segment, segment_fit, signal_fit
from .matrix import UnknownHookCell, _cells_equal, parse_matrix, persona_key_of_label
from .premise import capability_monotone, capability_vocab, declared_capability
from .rows import derive_row_cell


def campaign_packs(
    profile: str,
    campaign: str,
    content_root: Path | None = None,
) -> list[tuple[str, Path]]:
    """The 1:1 outreach packs a campaign produced, as ``(key, path)``.

    **Why this exists.** ``argument-monotone`` was built for sequence specs and read only
    ``cells.toml``, which is the sequence row set. A Tier-A 1:1 pack is never in it, so the
    cap was structurally blind to the artifact it most needed to see: on 2026-09-04 all five
    packs in one campaign argued ``identity`` while every per-file gate passed at zero
    errors, because no per-pack rule can see a sibling and no campaign-level rule could see
    a pack. That was a scope gap from the field's introduction (2026-08-24), not a
    regression — ``git log -S "capability:"`` over ``plugin/skills/draft-outreach/`` is
    empty across all history.

    Packs are found by DATE, taken from the campaign slug's trailing 8 digits, because a
    pack carries no campaign field — its filename carries the run date and it lives in the
    account folder. A campaign slug with no date returns ``[]`` rather than globbing every
    pack on disk: silently auditing another campaign's copy is worse than auditing none.
    """
    m = re.search(r"(\d{8})$", (campaign or "").strip())
    if not m:
        return []
    date = m.group(1)
    # Same resolver the sequences dir uses, so both sides of this audit obey the one
    # content-root rule (and the GTM_CONTENT_ROOT override) rather than two.
    root = _prospects_dir(profile, content_root).parent / "accounts"
    if not root.is_dir():
        return []
    out: dict[str, Path] = {}
    for pattern in PACK_GLOBS:
        for path in sorted(root.glob(pattern.format(date=date))):
            # Keyed by the account folder + filename so the report names something a reader
            # can open, and so the two globs cannot double-count one file.
            out[f"packs/{path.parent.name}/{path.name}"] = path
    return sorted(out.items())


def audit_campaign(
    profile: str,
    campaign: str = "",
    *,
    content_root: Path | None = None,
    profiles_root: Path | None = None,
    min_recipients: int = MIN_RECIPIENTS,
    min_arguments: int = MIN_ARGUMENTS,
    min_segment_fit: float = MIN_SEGMENT_FIT,
    min_signal_attestation: float = MIN_SIGNAL_ATTESTATION,
    include_drafts: bool = False,
    include_packs: bool = False,
) -> Coverage:
    """Measure one campaign's message axis, from ``cells.toml`` outward.

    ``cells.toml`` is the row set, never a glob over the sequences directory. The
    directory holds supersets that were split and replaced, so a glob double-counts —
    and mis-pairing a list with a spec is the exact error ``cells.toml``'s own header
    exists to prevent.

    ``include_drafts`` additionally folds in every complete cell under ``evals/drafts/``.
    Those are deliberately not in ``cells.toml``, so without the flag a drafted pilot is
    invisible here — off by default because a drafted cell has no outcomes and must never
    silently widen a report about live copy.

    ``include_packs`` folds in the campaign's 1:1 Tier-A outreach packs (see
    :func:`campaign_packs`). They carry a body and a declared capability but no list, so
    they participate in ``argument-monotone`` and the shared-phrase check and in nothing
    that needs rows. Off by default for the same reason drafts are: a pack is a different
    artifact from a staged sequence, and a reader must never be told a finding is about
    live enrolled copy when it is about a manual pack.
    """
    profiles_root = profiles_root or resolve_profiles_root()
    cov = Coverage(
        campaign=campaign,
        profile=profile,
        min_recipients=min_recipients,
        min_arguments=min_arguments,
        min_segment_fit=min_segment_fit,
        min_signal_attestation=min_signal_attestation,
    )

    cov.matrix = parse_matrix(
        resolve_knowledge_file(profiles_root, profile, "hook-matrix.md"), profile=profile
    )
    if not cov.matrix.ok:
        cov.findings.append(f"matrix-unsupported: hook-matrix.md — {cov.matrix.reason}")
    elif cov.matrix.unmapped_personas():
        unmapped = ", ".join(cov.matrix.unmapped_personas()[:EXEMPLARS])
        cov.findings.append(
            f"persona-unmapped: hook-matrix.md — {len(cov.matrix.unmapped_personas())} "
            f"matrix persona(s) do not normalise onto the role vocabulary ({unmapped}); "
            f"recipients can never be attributed to them"
        )

    cap_vocab = capability_vocab(profile, profiles_root)

    seq_dir = _prospects_dir(profile, content_root) / "sequences"
    sources = [
        s
        for s in load_cell_map(profile, content_root)
        if not campaign or s.get("campaign") == campaign
    ]
    # A ``cells.toml`` entry names its files relative to the sequences dir; a drafted cell
    # is found by walking. Both are resolved to absolute paths HERE, once, so the loop below
    # has one kind of source rather than two — and so ``cov.sources`` keeps exactly the shape
    # cells.toml gives it, with no resolution key smuggled into it for the loop's benefit.
    resolved: list[tuple[dict, Path, Path]] = [
        (s, seq_dir / s["spec"], seq_dir / s["csv"]) for s in sources
    ]
    # Drafted cells are absent from cells.toml by design (they have no outcomes to
    # attribute), so an audit that read only that file was structurally blind to a pilot —
    # exactly the artifact `argument-monotone` exists to judge. Folded in behind a flag, and
    # tagged in `cov.drafts`, so a reader is never told a pilot's finding is about live copy.
    if include_drafts:
        for slug, spec_path, csv_path in draft_cell_dirs(profile, content_root):
            key = f"drafts/{slug}/{spec_path.name}"
            cov.drafts.add(key)
            src = {"campaign": campaign or "drafts", "spec": key, "csv": str(csv_path)}
            sources.append(src)
            resolved.append((src, spec_path, csv_path))
    # 1:1 packs have a body and a declared capability but no list. Passing a
    # non-existent CSV path is deliberate rather than a special case: the row loop below
    # already skips a missing csv, so a pack flows through the same code path as a spec
    # and cannot acquire a second, divergent one.
    if include_packs:
        for key, pack_path in campaign_packs(profile, campaign, content_root):
            cov.packs.add(key)
            src = {"campaign": campaign or "packs", "spec": key, "csv": ""}
            sources.append(src)
            resolved.append((src, pack_path, pack_path.with_suffix(".__no_csv__")))
    cov.sources = sources

    spec_texts: dict[str, str] = {}
    for src, spec_path, csv_path in resolved:
        if spec_path.is_file():
            text = spec_path.read_text(encoding="utf-8")
            spec_texts[src["spec"]] = text
            try:
                cov.declared[src["spec"]] = declared_cell(text, cov.matrix)
            except UnknownHookCell as exc:
                cov.declared[src["spec"]] = None
                cov.findings.append(f"hook-cell-unknown: {src['spec']} — {exc}")
            try:
                cov.capabilities[src["spec"]] = declared_capability(text, cap_vocab)
            except ValueError as exc:
                cov.capabilities[src["spec"]] = ""
                cov.findings.append(f"capability-unknown: {src['spec']} — {exc}")
        if not csv_path.is_file():
            continue
        # Per-spec, not only campaign-wide: the fit checks below ask whether THIS spec
        # matches THIS list, a question the aggregate counters cannot answer.
        spec_segments: Counter = Counter()
        spec_evidence: list[str] = []
        spec_recorded: list[str] = []
        row_cells: Counter = Counter()
        rows_seen = 0
        with csv_path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if (row.get("suppression") or "").strip():
                    continue
                rows_seen += 1
                cov.rows += 1
                title = (row.get("title") or "").strip()
                row_segment = (row.get("segment") or "").strip()
                recorded = (row.get(HOOK_CELL_COLUMN) or "").strip()
                if not recorded and cov.matrix is not None and cov.matrix.ok:
                    # HOOK_CELL_COLUMN is unpopulated on every list measured 2026-08-23
                    # (0/207 live rows). Derive it from the row's own title/segment/signal
                    # rather than leaving `row_cells` empty and `cell-row-mismatch` inert —
                    # this is the fix for the exact defect this module exists to close.
                    rc = derive_row_cell(
                        cov.matrix,
                        email=(row.get("email") or "?").strip(),
                        title=title,
                        segment=row_segment,
                        signal_column=row.get(SIGNAL_COLUMN) or "",
                    )
                    if rc.cell is not None:
                        recorded = f"{rc.cell.persona} × {rc.cell.signal}"
                if recorded:
                    row_cells[recorded] += 1
                cov.seats[seat_of(title) or "unresolved"] += 1
                key = persona_of(title)
                if key:
                    cov.personas[key] += 1
                    if cov.matrix is not None and cov.matrix.ok:
                        norm_seg = _norm_segment(row_segment)
                        in_grid = any(
                            persona_key_of_label(c.persona) == key
                            and _norm_segment(c.segment) == norm_seg
                            for c in cov.matrix.cells.values()
                        )
                        if not in_grid:
                            cov.unassignable[f"{key}/{norm_seg or '(no segment)'}"] += 1
                else:
                    cov.unresolved[title or "(no title)"] += 1
                spec_segments[row_segment] += 1
                spec_evidence.append(
                    " ".join(
                        (row.get(f) or "") for f in ("signal_clause", "signal_evidence", "why_now")
                    )
                )
                # Positionally aligned with `spec_evidence`: a row that recorded its signal
                # is decided by equality below rather than by matching terms in that prose.
                spec_recorded.append((row.get(SIGNAL_COLUMN) or "").strip())
        declared_for_spec = cov.declared.get(src["spec"])
        sfit = segment_fit(src["spec"], declared_for_spec, cov.matrix, spec_segments)
        if sfit is not None:
            cov.segment_fits[src["spec"]] = sfit
        gfit = signal_fit(src["spec"], declared_for_spec, cov.matrix, spec_evidence, spec_recorded)
        if gfit is not None:
            cov.signal_fits[src["spec"]] = gfit
        cov.row_cells[src["spec"]] = (row_cells, rows_seen)

    # Packs were exempt until 2026-09-04, because `draft-outreach` did not ask for a hook cell
    # and exempting them was the honest reading. It now does (see its Compose step 1), so a pack
    # without one is the same finding as a spec without one: a hook nobody recorded cannot be
    # checked, and six Tier-A packs shipped in this campaign never having opened the matrix.
    undeclared = [name for name, d in cov.declared.items() if d is None]
    if undeclared:
        shown = ", ".join(sorted(undeclared)[:EXEMPLARS])
        more = f", +{len(undeclared) - EXEMPLARS} more" if len(undeclared) > EXEMPLARS else ""
        cov.findings.append(
            f"hook-cell-missing: {len(undeclared)} of {cov.specs} spec(s) — "
            f"no hook_cell declared ({shown}{more}); the matrix cell each spec "
            f"implements is unverifiable"
        )

    cov.overlaps = argument_distinctness(spec_texts)
    cov.shared = shared_phrases(spec_texts)
    if cov.shared:
        worst_phrase = cov.shared[0]
        cov.findings.append(
            f"template-share: {len(cov.shared)} phrase(s) — a {NGRAM_N}-word phrase "
            f"appears verbatim in more than {MAX_NGRAM_EMAILS} of {cov.specs} spec(s), "
            f'worst "{worst_phrase.phrase}" in {worst_phrase.count}; '
            f"scaffold and argument are indistinguishable at this scope by count alone"
        )
    same = [p for p in cov.overlaps if p.same_argument]
    if same:
        worst = same[0]
        cov.findings.append(
            f"argument-monoculture: {len(same)} of {len(cov.overlaps)} spec pair(s) — "
            f"overlap above {JACCARD_MAX:.2f}, worst {worst.a} vs {worst.b} at "
            f"{worst.jaccard:.2f}; these are one argument wearing several subjects"
        )

    # `argument-monoculture` above measures the COPY (bag-of-word overlap); this measures
    # what the spec SAYS it argues. They catch different failures: the 2026-08-23 pilot
    # scored as distinguishable prose while making one argument seven times, because
    # different nouns for one claim are still one claim.
    cov.findings.extend(capability_monotone(cov.capabilities))
    undeclared = sorted(s for s, c in cov.capabilities.items() if not c)
    if undeclared and cap_vocab:
        shown = ", ".join(undeclared[:EXEMPLARS])
        more = f" (+{len(undeclared) - EXEMPLARS} more)" if len(undeclared) > EXEMPLARS else ""
        # Advisory, not blocking: `capability:` is new and every spec written before it
        # predates the field, exactly as `premise:` did. An unmigrated spec is not a
        # violation — but an uncounted one weakens the cap, so it is never silent.
        cov.warnings.append(
            f"capability-undeclared: {len(undeclared)} of {cov.specs} spec(s) declare no "
            f"`capability:` ({shown}{more}); they are invisible to the "
            f"{MAX_SPECS_PER_CAPABILITY}-per-group cap until they do"
        )

    if cov.declared and cov.arguments < min_arguments:
        cov.findings.append(
            f"argument-count: {cov.campaign or profile} — {cov.arguments} distinct "
            f"argument(s) across {cov.specs} spec(s) and {cov.rows} recipient(s), "
            f"target >= {min_arguments}"
        )

    for name in sorted(cov.segment_fits):
        fit = cov.segment_fits[name]
        if not fit.failed(min_segment_fit):
            continue
        other = ", ".join(f"{k or '(blank)'} {n}" for k, n in fit.counts.most_common(EXEMPLARS))
        cov.findings.append(
            f"cell-segment-fit: {name} — declared cell sits in the "
            f"{fit.declared_segment!r} grid but only {fit.matched}/{fit.rows} "
            f"({fit.share:.0%}) of recipients are {fit.declared_segment}, below "
            f"{min_segment_fit:.0%}; list is {other}"
        )

    for name in sorted(cov.signal_fits):
        fit = cov.signal_fits[name]
        # A PASSING score carried entirely by list-wide words is more dangerous than a
        # failing one, because nothing else in the report contradicts it. Warned separately
        # and before the threshold check, so a cell nobody can measure is never silently
        # counted as a cell that measured well.
        if fit.noise_dominated:
            common = ", ".join(sorted(fit.noise_terms))
            cov.warnings.append(
                f"cell-signal-unmeasurable: {name} — the declared signal {fit.signal!r} "
                f"yields only list-wide terms ({common}), each matching over "
                f"{SIGNAL_TERM_NOISE_SHARE:.0%} of these recipients. Its "
                f"{fit.share:.0%} attestation is NOT evidence the signal was observed; read "
                f"it as 'this gate cannot see this cell'. Fix the matrix label or research "
                f"the signal — a threshold change would only hide it."
            )
            continue
        if not fit.failed(min_signal_attestation):
            continue
        matched = ", ".join(f"{t} x{n}" for t, n in fit.hits.most_common(EXEMPLARS)) or "none"
        cov.warnings.append(
            f"cell-signal-fit: {name} — {fit.attested}/{fit.rows} ({fit.share:.0%}) of "
            f"recipients carry evidence for the declared signal {fit.signal!r}, below "
            f"{min_signal_attestation:.0%}; distinctive terms "
            f"{'/'.join(fit.terms) or '(none derivable)'}, matched {matched}"
        )

    # `cell-row-mismatch` — the equality test the two fit heuristics above are approximating.
    #
    # `cell-segment-fit` and `cell-signal-fit` both have to INFER the row's side of the
    # comparison, because only the spec declares anything: segment fit reads an enumerated
    # column (strong), signal fit reads free text for distinctive terms (weak, hence WARN).
    # Once `prospect` records the cell it actually picked for the account, there is nothing
    # to infer — the row states its cell and the spec states its cell, and they either match
    # or they do not. That is why this is an ERROR while `cell-signal-fit` is a WARN.
    #
    # Migration: a list written before the column exists reports ONE advisory line naming the
    # spec, never a per-row wall and never a failure. Same shape as the record-columns
    # migration in `signal_record` — a gate that answers "your list is from last week" with N
    # identical errors is the unreadable-output failure `finding_budget` exists to stop.
    for name in sorted(cov.row_cells):
        counts, rows_seen = cov.row_cells[name]
        declared = cov.declared.get(name)
        if declared is None or not rows_seen:
            continue
        if not counts:
            cov.warnings.append(
                f"cell-row-unrecorded: {name} — none of {rows_seen} row(s) record a "
                f"{HOOK_CELL_COLUMN!r}, so which cell each recipient belongs to is still "
                f"inferred rather than known; cell-segment-fit / cell-signal-fit stay "
                f"heuristics on this list. Re-run the prospect skill's research step."
            )
            continue
        matching = sum(n for cell, n in counts.items() if _cells_equal(cell, declared.raw))
        if matching == rows_seen:
            continue
        other = ", ".join(f"{c!r} x{n}" for c, n in counts.most_common(EXEMPLARS))
        cov.findings.append(
            f"cell-row-mismatch: {name} — spec declares {declared.raw!r} but only "
            f"{matching}/{rows_seen} recipients were researched into that cell; "
            f"rows record {other}. The copy argues one cell at a list selected for another."
        )

    cov.findings.extend(
        persona_coverage(cov.personas, cov.declared, cov.matrix, min_recipients=min_recipients)
    )
    return cov
