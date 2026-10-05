"""R6.2: who was enrolled, in which sequence, under which kind of evidence, and when.

Written by the dispatcher after a successful enrolment and by nothing else. It is the only record
that ties a person to the sequence they were actually enrolled in, so a later reply can be
attributed to what the person was shown: a card's approval says what was planned, this says what
happened. Append-only, one line per person, never read by anything that decides who is emailed.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from .paths import _safe_segment

try:  # pragma: no cover - Windows has no fcntl
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

_FIELDS = ("signal_class", "premise_via", "source_id")

GENERAL = "general"
#: The reader's words for a stored class, shared by the card and the outcomes report; an unknown
#: class is shown as stored, never hidden, and no class at all is the comparison group.
CLASS_LABELS = {
    "source_list": "on a published list",
    "job_post": "new job post",
    GENERAL: "general email",
}


def label_for(signal_class: str) -> str:
    return CLASS_LABELS.get(signal_class or GENERAL, signal_class)


def path_for(content_root: Path, profile: str) -> Path:
    return content_root / _safe_segment(profile, "profile") / "prospects" / "enrolments.jsonl"


def _emails(draft: dict) -> list[str]:
    rows = draft.get("prospect_list") or []
    emails = (
        str(r.get("Email") or r.get("email") or "").strip().lower()
        for r in rows
        if isinstance(r, dict)
    )
    return [e for e in emails if e]


def record(content_root: Path, profile: str, draft: dict, *, now: str | None = None) -> int:
    """Append one line per addressed person in ``draft``; return how many. Zero when it names none.

    A draft that enrols by lead id carries no addresses, so nothing can be tied to a person and
    nothing is written. ``draft["attribution"]`` (written by the send-cards step, only when a member
    carries evidence) supplies each person's class; everyone else is recorded with it empty, which is
    what makes the comparison group countable.
    """
    emails = _emails(draft)
    if not emails:
        return 0
    path = path_for(content_root, profile)
    by_email = {
        str(a.get("email") or "").strip().lower(): a
        for a in draft.get("attribution") or []
        if isinstance(a, dict)
    }
    stamp = now or datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    cell = (draft.get("card_ids") or [""])[0]
    lines = []
    for email in emails:
        given = by_email.get(email, {})
        rec = {
            "email": email,
            "sequence_id": str(draft.get("sequence_id") or ""),
            "cell_id": str(cell),
            **{k: str(given.get(k) or "") for k in _FIELDS},
            "dispatched_at": stamp,
        }
        lines.append(json.dumps(rec, ensure_ascii=False))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        fh.write("\n".join(lines) + "\n")
    return len(lines)
