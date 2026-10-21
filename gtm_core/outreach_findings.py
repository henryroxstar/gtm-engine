"""The two close-out findings, and the words the operator reads.

Saleshandy tells us HOW MANY people a sequence emailed, never WHICH. The only record of who was
emailed that survives the sequence being deleted is a ``contacted-*.csv`` the operator exports
before deleting it. These findings say when that export is due:

* ``emailed-without-closeout`` — a sequence that is not sending, that the provider says emailed
  N people, of whom fewer than N are in any closed-out list.
* ``gone-with-sends-unexported`` — the same, for a sequence that has since been deleted (judged
  from its last figures, so the warning outlives the sequence it is about).

A sequence that is sending now is silent by design — nothing is due yet. A sequence the log
cannot evaluate (no figures, an old row, or no row at all) is COUNTED, so silence is never read
as safe. Each finding fires at most once per sequence.

The sequence name is untrusted text from the provider: it is stripped of control characters and
capped before it goes into a sentence, so a name cannot forge a line of the report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CODE_EMAILED = "emailed-without-closeout"
CODE_GONE = "gone-with-sends-unexported"
#: The columns a close-out export needs: the newer closed-out layout the readers prefer.
EXPORT_COLUMNS = "email, name, company_domain, sequences, max_step, outcome, source"

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class Finding:
    code: str
    sequence_id: str
    message: str


def clean(text: object, limit: int = 120) -> str:
    out = _CONTROL.sub(" ", str(text or "")).strip()
    return out if len(out) <= limit else out[: limit - 1] + "…"


def _people(n: int) -> str:
    return f"{n} person" if n == 1 else f"{n} people"


def close_out(facts, today: str) -> tuple[list[Finding], int]:
    """``(findings, number of sequences that could not be checked)``.

    ``facts`` are :class:`gtm_core.outreach_people.SeqFacts`. A sequence is checkable only
    from a TRUSTED emailed counter — present, and from a row that is not stale.
    """
    findings: list[Finding] = []
    not_checkable = 0
    for f in facts:
        if f.state == "active":
            continue  # sending now: nothing is due yet
        if not (f.registered or f.people or f.has_row):
            continue  # a bare id with nothing known about it is not a sequence we can speak of
        n = f.emailed_trusted
        if n is None:
            not_checkable += 1
            continue
        if n == 0 or f.closed_out >= n:
            continue
        name = clean(f.name or f.id)
        if f.deleted:
            msg = (
                f"Sequence '{name}' is gone from Saleshandy. Its last figures showed {n} emailed, "
                f"and only {f.closed_out} of its people are in a closed-out list. The rest are "
                "not protected from a re-send. If you know who was emailed, add them to a "
                "contacted file."
            )
            findings.append(Finding(CODE_GONE, f.id, msg))
        else:
            msg = (
                f"Sequence '{name}' is not sending, and Saleshandy says it emailed "
                f"{_people(n)}, but only {f.closed_out} of its people are in a closed-out list. "
                "Before you delete it: in Saleshandy open the sequence, export its prospects with "
                f"their status, and save that as contacted-{today}.csv in prospects/sequences/ "
                f"with the columns {EXPORT_COLUMNS}. Then run the log again."
            )
            findings.append(Finding(CODE_EMAILED, f.id, msg))
    return findings, not_checkable
