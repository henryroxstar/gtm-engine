"""Who is in which sequence — assembled from the records the system keeps, then classified.

The outreach log used to be a list of drafted packs. This builds the other half: every person we
know is in a sequence, what is known about whether they were emailed, and the email text. It is
a DERIVED view — it keeps no state of its own and reads only:

* the lists registered in ``cells.toml`` (through the one shared traversal in
  :mod:`gtm_core.lanes.context`, with the deleted sequences left in);
* the closed-out files ``contacted-*.csv`` (people who WERE emailed, and how it went);
* the dispatcher's own ledger ``enrolments.jsonl``;
* the Saleshandy figures snapshot, the suppression ledger, and ``history.jsonl``.

The vocabulary, containers and suppression map are in :mod:`gtm_core.outreach_model`; the
close-out findings in :mod:`gtm_core.outreach_findings`; the email text in
:mod:`gtm_core.outreach_touches`. It imports no writer of any kind.

A corrupt structural input raises :class:`~gtm_core.outreach_strict.StrictInputError`, naming the
file, rather than building a smaller answer. The page package is imported only inside functions
(``prospects_dashboard`` imports :mod:`gtm_core.outreach_log`; a top-level import is a cycle).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from . import outreach_strict as strict
from .lanes.context import REG_MISSING, REG_OK, load_enrolments_by_sequence
from .optout_sets import optout_key
from .outreach_findings import close_out
from .outreach_model import (
    _ATTENTION_STATES,
    CLOSED_OUT,
    COLUMNS,
    DELETED_NO_SENDS,
    DELETED_SENDS,
    DONT_KNOW,
    DRAFTED,
    IN_SEQUENCE,
    KIND_CONTACTED,
    KIND_PACK,
    KIND_SEQUENCE,
    NO,
    S_ABSENT,
    S_ACTIVE,
    S_DELETED,
    S_PAUSED,
    S_STAGED,
    S_UNKNOWN,
    SUPPRESSED,
    YES,
    Member,
    SeqFacts,
    SuppressionMap,
    View,
    is_stale,
    state_words,
)
from .outreach_strict import StrictInputError
from .outreach_touches import NO_LIST_ROW, SpecCache, pack_cells, sequence_cells
from .prospect_paths import sequences_dir
from .sequencer_stats_read import is_provider_id


def _person_name(row: dict) -> str:
    name = str(row.get("name") or "").strip()
    if name:
        return name
    parts = (str(row.get("first") or "").strip(), str(row.get("last") or "").strip())
    return " ".join(p for p in parts if p)


def _sequence_state(deleted: bool, snap_present: bool, row, stale: bool) -> str:
    """The sequence's state from the provider's own flag. Strictly cautious: a missing flag, a
    missing counter or an old row is *Status unknown*, never *Paused* or *Not started*."""
    from .prospect_lede import LIVE_STATUSES

    if deleted:
        return S_DELETED
    if not snap_present:
        return S_UNKNOWN
    if row is None:
        return S_ABSENT
    if stale:
        return S_UNKNOWN
    if (row.status or "") in LIVE_STATUSES:
        return S_ACTIVE
    if row.status == "paused" and row.contacted is not None:
        return S_STAGED if row.contacted == 0 else S_PAUSED
    return S_UNKNOWN


class _Builder:
    """One build. Every step is its own method so each stays small enough to read whole."""

    def __init__(self, profile: str, content_root: Path, now: datetime):
        self.profile, self.root, self.now = profile, content_root, now
        self.seq_dir = sequences_dir(profile, content_root)
        self.notes: list[str] = []
        self.problems: list[str] = []
        self.rows: list[dict] = []
        self.suppressed_by_reason: Counter = Counter()
        self.no_list_row = 0
        self.specs = SpecCache(self.seq_dir)
        self.words = state_words()

    # ── inputs ───────────────────────────────────────────────────────────────────────────

    def load(self) -> None:
        p, root = self.profile, self.root
        self.cells = strict.read_cells(p, root)
        self.hist = strict.read_history_facts(p, root)
        self.snap = strict.read_snapshot(p, root)
        self.contacted = strict.read_contacted(p, root)
        self.enrol, enrol_bad = strict.read_enrolments(p, root)
        self.supp = SuppressionMap(strict.read_suppression(p, root))
        from .prospects_consolidate.suppression import _load_sent

        self.sent_emails, _ = _load_sent(p, root)
        self._input_notes(enrol_bad)

    def _input_notes(self, enrol_bad: int) -> None:
        if not self.cells.present and self.snap.rows:
            raise StrictInputError(
                self.seq_dir / "cells.toml",
                "the Saleshandy figures list sequences but cells.toml is missing",
            )
        n = self.notes
        if not self.cells.present:
            n.append(
                "No cells.toml: no sequences are registered, so only drafted packs are listed."
            )
        if self.cells.skipped:
            n.append(
                f"{self.cells.skipped} cells.toml entries are missing an id, list or spec and are not listed."
            )
        if not self.snap.present:
            n.append("Saleshandy figures missing: every sequence reads 'Status unknown'.")
        elif self.snap.edited:
            n.append(
                "The Saleshandy figures file was edited by hand; its numbers are shown as written."
            )
        if enrol_bad:
            n.append(f"{enrol_bad} line(s) of enrolments.jsonl could not be read and are skipped.")

    # ── membership ───────────────────────────────────────────────────────────────────────

    def membership(self) -> None:
        sink: dict = {}
        regs = load_enrolments_by_sequence(
            self.profile,
            self.root,
            self.seq_dir,
            registrations=self.cells.registrations,
            read_list=strict.read_list_strict(self.seq_dir, sink),
            deleted_ids=set(self.hist.deleted),
            include_deleted=True,
        )
        self.known_ids = (
            {r.sequence_id.strip() for r in regs} | set(self.snap.rows) | set(self.hist.known)
        )
        self.members: dict[str, dict[str, Member]] = defaultdict(dict)
        self.meta: dict[str, dict] = {}
        self.missing: dict[str, list[str]] = defaultdict(list)
        self.first_sid: dict[str, str] = {}
        self.order: list[str] = []
        for i, reg in enumerate(regs):
            self._add_registration(i, reg, sink)
        unrecognised = self._add_closed_out() | self._add_dispatcher()
        for sid in self.snap.rows:
            self._see(sid)
        self.in_a_sequence = {e for sids in self.members.values() for e in sids}
        if unrecognised:
            self.notes.append(
                f"{len(unrecognised)} closed-out sequence cell(s) name a sequence nobody here "
                "recognises; those people are listed as 'contacted' with no sequence."
            )

    def _see(self, sid: str) -> None:
        if sid not in self.order:
            self.order.append(sid)

    def _add_registration(self, i: int, reg, sink: dict) -> None:
        sid = reg.sequence_id.strip()
        if reg.status == "draft":
            return
        meta = self.meta.setdefault(
            sid, {"title": reg.title, "lane": reg.lane, "campaign": reg.campaign, "spec": reg.spec}
        )
        self._see(sid)
        meta["lane"] = meta["lane"] or reg.lane
        if reg.status == REG_MISSING:
            self.missing[sid].append(reg.csv)
            return
        rows_of = sink.get(reg.csv, {})
        for email in reg.emails:
            if email in self.members[sid]:
                self.members[sid][email].via.add("list")
            else:
                self.members[sid][email] = Member(
                    sid, email, rows_of.get(email, {}), reg.csv, reg.spec, i, {"list"}
                )
            if reg.status == REG_OK:
                self.first_sid.setdefault(email, sid)

    def _add_closed_out(self) -> set[str]:
        unrecognised: set[str] = set()
        for c in self.contacted.values():
            for token in c.tokens:
                if strict.provider_token(token, self.known_ids):
                    m = self.members[token].setdefault(
                        c.email, Member(token, c.email, {}, None, None, None)
                    )
                    m.via.add("closed-out")
                    self._see(token)
                elif token and is_provider_id(token):
                    unrecognised.add(token)
        return unrecognised

    def _add_dispatcher(self) -> set[str]:
        unrecognised: set[str] = set()
        for email, sid in self.enrol:
            sid = sid.strip()
            if is_provider_id(sid):
                m = self.members[sid].setdefault(email, Member(sid, email, {}, None, None, None))
                m.via.add("dispatcher")
                self._see(sid)
            else:
                unrecognised.add(sid)
        return unrecognised

    # ── per-sequence facts ───────────────────────────────────────────────────────────────

    def sequence_facts(self) -> None:
        closed = set(self.contacted)
        self.facts: list[SeqFacts] = []
        self.by_sid: dict[str, SeqFacts] = {}
        for sid in self.order:
            f = self._facts_for(sid, closed)
            self.facts.append(f)
            self.by_sid[sid] = f

    def _facts_for(self, sid: str, closed: set[str]) -> SeqFacts:
        row = self.snap.rows.get(sid)
        stale = row is not None and is_stale(row.stamp, self.now)
        deleted = sid in self.hist.deleted
        people = set(self.members.get(sid, {}))
        m0 = self.meta.get(sid, {})
        return SeqFacts(
            id=sid,
            name=(row.name if row and row.name else m0.get("title") or sid),
            lane=m0.get("lane", ""),
            campaign=m0.get("campaign", ""),
            spec=m0.get("spec", ""),
            state=_sequence_state(deleted, self.snap.present, row, stale),
            registered=sid in self.meta,
            deleted=deleted,
            has_row=row is not None,
            provider_total=row.total if row else None,
            emailed_shown=row.contacted if row else None,
            emailed_trusted=row.contacted if (row is not None and not stale) else None,
            stamp=row.stamp if row else None,
            people=len(people),
            closed_out=len(people & closed),
            missing_lists=tuple(self.missing.get(sid, ())),
        )

    # ── rows ─────────────────────────────────────────────────────────────────────────────

    def _blank_row(self, kind: str) -> dict:
        row = dict.fromkeys(COLUMNS, "")
        row["kind"] = kind
        return row

    def _judge(self, email: str, name: str, domain: str) -> tuple[bool, str]:
        suppressed, reason = self.supp.verdict(email, name, domain)
        if suppressed:
            self.suppressed_by_reason[reason] += 1
        return suppressed, reason

    @staticmethod
    def _progress(f: SeqFacts) -> str:
        if not f.has_row:
            return "no Saleshandy figures"
        total = "?" if f.provider_total is None else f.provider_total
        sent = "?" if f.emailed_shown is None else f.emailed_shown
        return (
            f"{sent} of {total} emailed (Saleshandy figures of {(f.stamp or '')[:10] or 'undated'})"
        )

    def _status(self, c, suppressed: bool, f: SeqFacts) -> str:
        if c is not None:
            return CLOSED_OUT
        if suppressed:
            return SUPPRESSED
        if f.deleted:
            return DELETED_NO_SENDS if f.emailed_trusted == 0 else DELETED_SENDS
        return IN_SEQUENCE

    def _emailed(self, c, email: str, f: SeqFacts) -> str:
        if c is not None or email in self.sent_emails:
            return YES
        return NO if f.emailed_trusted == 0 else DONT_KNOW

    def sequence_rows(self) -> None:
        for sid in self.order:
            for email, m in self.members.get(sid, {}).items():
                self.rows.append(self._sequence_row(self.by_sid[sid], m, email))

    def _sequence_row(self, f: SeqFacts, m: Member, email: str) -> dict:
        row = self._blank_row(KIND_SEQUENCE)
        lrow = m.row
        c = self.contacted.get(email)
        name = _person_name(lrow) or (c.name if c else "")
        domain = str(lrow.get("company_domain") or "").strip().lower() or (c.domain if c else "")
        suppressed, reason = self._judge(email, name, domain)
        row.update(
            account=str(lrow.get("company") or "").strip() or (c.domain if c else ""),
            persona_name=name,
            email_address=email,
            person_status=self._status(c, suppressed, f),
            emailed=self._emailed(c, email, f),
            sequence_state=self.words[f.state],
            sequence_name=f.name,
            progress=self._progress(f),
            outcome=c.outcome if c else "",
            suppression_reason=reason,
            persona_title=str(lrow.get("title") or "").strip(),
            tier=str(lrow.get("tier") or "").strip(),
            score=str(lrow.get("score") or "").strip(),
            channels="Email",
            file=str((self.seq_dir / m.reg_csv).relative_to(self.root.parent)) if m.reg_csv else "",
            sequence_id=f.id,
            lane=f.lane,
        )
        self._fill_touches(row, f, m)
        return row

    def _fill_touches(self, row: dict, f: SeqFacts, m: Member) -> None:
        if not (m.reg_csv and m.spec):
            self.no_list_row += 1
            row["touch_1_body"] = NO_LIST_ROW
            return
        spec = self.specs.get(m.spec)
        cells_, probs = sequence_cells(spec, m.row)
        row.update(cells_)
        row["email_subject"] = cells_["touch_1_subject"]
        row["followup_touches_in_ladder"] = "" if spec.problem else max(len(spec.touches) - 1, 0)
        if probs and f.state in _ATTENTION_STATES:
            self.problems.extend(f"{f.name}: {p}" for p in probs)

    def contacted_rows(self) -> None:
        for email, c in self.contacted.items():
            if email in self.in_a_sequence:
                continue
            _suppressed, reason = self._judge(email, c.name, c.domain)
            row = self._blank_row(KIND_CONTACTED)
            row.update(
                account=c.domain,
                persona_name=c.name,
                email_address=email,
                person_status=CLOSED_OUT,
                emailed=YES,
                outcome=c.outcome,
                suppression_reason=reason,
                file=", ".join(c.sources),
            )
            self.rows.append(row)

    def pack_rows(self) -> None:
        from .outreach_log import collect_rows

        for p in collect_rows(self.root, self.profile):
            email = optout_key(p.email_address)
            suppressed, reason = self._judge(email, p.persona_name, "") if email else (False, "")
            c = self.contacted.get(email) if email else None
            row = self._blank_row(KIND_PACK)
            row.update(
                account=p.account,
                persona_name=p.persona_name,
                email_address=email,
                person_status=CLOSED_OUT if c else SUPPRESSED if suppressed else DRAFTED,
                emailed=YES if (c or email in self.sent_emails) else DONT_KNOW,
                outcome=c.outcome if c else "",
                suppression_reason=reason,
                draft_date=p.draft_date,
                tier=p.tier,
                score=p.score,
                persona_title=p.persona_title,
                email_verified=p.email_verified,
                channels=p.channels,
                email_subject=p.email_subject,
                followup_touches_in_ladder=p.followup_touches_in_ladder,
                file=p.file,
            )
            row.update(pack_cells(self.root.parent / p.file))
            self.rows.append(row)

    # ── result ───────────────────────────────────────────────────────────────────────────

    def finish(self) -> View:
        if self.no_list_row:
            self.notes.append(
                f"{self.no_list_row} row(s) are people known to be in a sequence but on no "
                "registered list, so their email text cannot be filled in."
            )
        findings, not_checkable = close_out(self.facts, self.now.strftime("%Y-%m-%d"))
        return View(
            rows=self.rows,
            sequences=self.facts,
            findings=findings,
            not_checkable=not_checkable,
            notes=self.notes,
            problems=self.problems,
            snapshot_present=self.snap.present,
            snapshot_fetched=self.snap.fetched,
            people=len({r["email_address"] for r in self.rows if r["kind"] == KIND_SEQUENCE}),
            suppressed_by_reason=self.suppressed_by_reason,
            first_sid=self.first_sid,
            state_by_sid={f.id: f.state for f in self.facts},
        )


def build_view(profile: str, content_root: Path, *, now: datetime | None = None) -> View:
    """Read everything, refuse on a corrupt structural input, return the classified view.
    Raises :class:`StrictInputError` (naming the file) rather than build a smaller answer."""
    b = _Builder(profile, content_root, now or datetime.now(UTC))
    b.load()
    b.membership()
    b.sequence_facts()
    b.sequence_rows()
    b.contacted_rows()
    b.pack_rows()
    return b.finish()
