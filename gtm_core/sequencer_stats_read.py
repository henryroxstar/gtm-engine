"""Read the sequencer's replies strictly: a field the caller joins on is present, or the run stops.

Shared by :mod:`gtm_core.sequencer_reconcile` and :mod:`gtm_core.sequence_health`. Both are
MCP-free, like :mod:`gtm_core.email_compliance` — the agent fetches the replies over MCP and pipes
them in as JSON files, so this module adds no egress surface.

**Refuse, never skip.** :func:`gtm_core.sequencer_sends.sequences_in` drops a row it cannot key and
carries on; that is right for a ledger that writes what it understood, and wrong for a checker. A
check that silently reads fewer sequences than it was given reports a confident clean answer about
a smaller world. Every reader here raises :class:`PayloadError` naming the file, the row and the
field instead, and the CLIs turn that into exit 3 — distinct from 0 (clean) and 2 (a finding).
:func:`sequence_rows` still cross-checks itself against ``sequences_in`` so the two readers can
never disagree about how many sequences a reply holds.

**Real shapes this reads** (key names copied from live replies; every value in this repo's tests is
fictional):

* ``get_sequence_stats`` -> ``{message, payload: {sequenceId, sequenceName, prospects: [{total,
  contacted, unsubscribed, bounced, ...}], emails: {total, status: {delivered, replied, bounced,
  blockBounced, hardBounced, softBounced, scheduled, ...}}}}``. Counters arrive as digit strings
  in ``prospects[0]`` and as integers in ``emails``.
* ``list_sequences`` -> ``{message, payload: [{id, title, active, steps: [...]}]}``.
* ``get_consolidated_stats`` -> ``{message, payload: {data: [row, ...], hasMore, nextPageNumber}}``
  where a row carries ``Sequence Id``, ``Step Number``, ``Sender Email``, ``Recipient Email``,
  ``Email Sent At`` (``Sun Sep 27 2026 22:10:37 GMT-4 (America/New_York)``) and the
  ``Unsubscribed`` / ``Bounced`` / ``Replied`` flags as the words ``Yes`` / ``No``. A row has no
  bounce *type*: a block-bounce count exists per sequence only.

Stdlib only.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from gtm_core.sequencer_sends import sequences_in

#: A provider sequence id: Saleshandy's are ten characters, letters and digits. Anything else in a
#: registry is a placeholder somebody typed.
PROVIDER_ID_RE = re.compile(r"[A-Za-z0-9]{10}")
MAX_ID_LEN = 200


class PayloadError(Exception):
    """An input is unreadable or lacks a field the check joins on. The CLIs exit 3 on it."""


def shown(value, limit: int = 60) -> str:
    """Text from an input quoted back safely — escaped and cut, because inputs are untrusted (§R5)."""
    text = "".join(ch if ch.isprintable() else ascii(ch)[1:-1] for ch in str(value))
    return text if len(text) <= limit else text[: limit - 1] + "…"


def usable_id(value) -> bool:
    """A key every reader here compares exactly: text, no edge whitespace, nothing unprintable."""
    return (
        isinstance(value, str)
        and 0 < len(value) <= MAX_ID_LEN
        and value == value.strip()
        and value.isprintable()
    )


def is_provider_id(value: str) -> bool:
    return PROVIDER_ID_RE.fullmatch(value) is not None


def read_json(path: Path, what: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PayloadError(
            f"{what} {shown(path.name)} is not readable JSON ({type(exc).__name__})"
        ) from exc


def count(value, field: str, where: str, *, required: bool = True) -> int | None:
    """A counter as the provider types it: a whole number, or its digits as text."""
    if isinstance(value, bool):
        raise PayloadError(f"{where}: {field} is {shown(value)} — a counter is a whole number")
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 15:
        return int(value)
    if value in (None, "") and not required:
        return None
    if value in (None, ""):
        raise PayloadError(f"{where}: {field} is missing — a missing count is not a zero")
    raise PayloadError(f"{where}: {field} is {shown(value)} — a counter is a whole number")


def _node(payload, key: str):
    return payload.get(key) if isinstance(payload, dict) else None


def list_rows(payload, what: str) -> list[dict]:
    """The rows of a ``list_sequences`` reply (or several pages of one). Each carries ``id`` and ``active``."""
    pages = payload if isinstance(payload, list) and _all_replies(payload) else [payload]
    rows: list = []
    for page in pages:
        body = _node(page, "payload")
        if body is None and isinstance(page, list):
            body = page
        if not isinstance(body, list):
            raise PayloadError(f"{what}: expected {{payload: [sequence, ...]}} or a list of rows")
        rows.extend(body)
    if not rows:
        raise PayloadError(
            f"{what} holds no sequences — an empty read is not a clean read (nothing was reconciled)"
        )
    seen: set[str] = set()
    for n, row in enumerate(rows):
        where = f"{what} row {n}"
        if not isinstance(row, dict) or not usable_id(row.get("id")):
            raise PayloadError(f"{where} has no usable id (found {shown(_node(row, 'id'))})")
        if not isinstance(row.get("active"), bool):
            raise PayloadError(
                f"{where} ({shown(row['id'])}) has no true/false `active` — "
                "a missing status is not proof of paused"
            )
        if row["id"] in seen:
            raise PayloadError(f"{what} lists sequence {shown(row['id'])} twice")
        seen.add(row["id"])
    return rows


def _all_replies(items: list) -> bool:
    return bool(items) and all(isinstance(i, dict) and "payload" in i for i in items)


def sequence_rows(payload, what: str) -> list[dict]:
    """The sequence rows of a ``get_sequence_stats`` reply, a ``{sequences: [...]}`` file or a bare row.

    Every row must carry a usable text ``sequenceId``. ``sequences_in`` (the sends ledger's reader)
    is run alongside as a witness: if it finds a different number of sequences than this reader,
    the input is refused rather than read two ways.
    """
    body = payload
    if isinstance(body, dict) and "payload" in body:
        body = body["payload"]
    if isinstance(body, dict) and "sequences" in body:
        body = body["sequences"]
    rows = [body] if isinstance(body, dict) else body
    if not isinstance(rows, list) or not rows:
        raise PayloadError(f"{what} holds no sequence (expected a get_sequence_stats reply)")
    for n, row in enumerate(rows):
        if not isinstance(row, dict) or not usable_id(row.get("sequenceId")):
            raise PayloadError(
                f"{what} row {n} has no usable sequenceId (found {shown(_node(row, 'sequenceId'))})"
                " — a row is joined on it and is never dropped without a word"
            )
    if len(sequences_in(payload)) != len(rows):
        raise PayloadError(f"{what}: two readers disagree on how many sequences it holds")
    return rows


@dataclass(frozen=True)
class StatsFigures:
    """One sequence's counters from ``get_sequence_stats``, validated."""

    sequence_id: str
    name: str
    total: int
    contacted: int | None
    unsubscribed: int | None
    replied: int | None
    delivered: int | None
    bounced: int | None
    block_bounced: int | None
    hard_bounced: int | None
    soft_bounced: int | None

    @property
    def sent(self) -> int | None:
        """Emails that left a mailbox: delivered plus bounced (``emails.total`` also counts scheduled)."""
        if self.delivered is None or self.bounced is None:
            return None
        return self.delivered + self.bounced


def _block(row: dict, key: str, where: str) -> dict:
    value = row.get(key)
    if not isinstance(value, dict):
        raise PayloadError(f"{where}: `{key}` is missing or not an object")
    return value


def figures(row: dict, what: str, *, need_emails: bool) -> StatsFigures:
    """Validate one ``get_sequence_stats`` row. ``prospects[0].total`` is always required.

    ``need_emails`` additionally requires the email counters a stop-line check divides by:
    ``prospects[0].contacted``/``unsubscribed`` and ``emails.status.delivered``/``bounced``. A bounce
    count the payload omits is refused, not read as zero. The aggregate ``bounced`` is the sum of
    the block, hard and soft buckets; adding all four double counts, so this reads ``bounced``, and
    falls back to the three buckets only when it is absent.
    """
    where = f"{what} ({shown(row['sequenceId'])})"
    prospects = row.get("prospects")
    if not isinstance(prospects, list) or not prospects or not isinstance(prospects[0], dict):
        raise PayloadError(f"{where}: `prospects` is missing — the join needs prospects[0].total")
    p0 = prospects[0]
    total = count(p0.get("total"), "prospects[0].total", where)
    name = row.get("sequenceName") if isinstance(row.get("sequenceName"), str) else ""
    if not need_emails:
        return StatsFigures(row["sequenceId"], name, total, *([None] * 8))
    status = _block(_block(row, "emails", where), "status", where)
    soft = count(status.get("softBounced"), "emails.status.softBounced", where, required=False)
    hard = count(status.get("hardBounced"), "emails.status.hardBounced", where, required=False)
    block = count(status.get("blockBounced"), "emails.status.blockBounced", where, required=False)
    bounced = count(status.get("bounced"), "emails.status.bounced", where, required=False)
    if bounced is None:
        if None in (soft, hard, block):
            raise PayloadError(f"{where}: emails.status has no bounce counts — not read as zero")
        bounced = soft + hard + block
    return StatsFigures(
        sequence_id=row["sequenceId"],
        name=name,
        total=total,
        contacted=count(p0.get("contacted"), "prospects[0].contacted", where),
        unsubscribed=count(p0.get("unsubscribed"), "prospects[0].unsubscribed", where),
        replied=count(status.get("replied"), "emails.status.replied", where, required=False) or 0,
        delivered=count(status.get("delivered"), "emails.status.delivered", where),
        bounced=bounced,
        block_bounced=block if block is not None else 0,
        hard_bounced=hard if hard is not None else 0,
        soft_bounced=soft if soft is not None else 0,
    )


def load_stats_dir(directory: Path, *, need_emails: bool) -> dict[str, StatsFigures]:
    """``sequenceId -> figures`` for every ``*.json`` file in ``directory``, joined on the payload's own id.

    The file name is never the key. Two files for one sequence are refused (which is the newer?).
    """
    if not directory.is_dir():
        raise PayloadError(f"--stats-dir {shown(str(directory))} is not a directory")
    files = sorted(directory.glob("*.json"))
    if not files:
        raise PayloadError(f"no *.json stats files in {shown(directory.name)} — nothing to check")
    out: dict[str, StatsFigures] = {}
    for path in files:
        what = f"stats file {shown(path.name)}"
        for row in sequence_rows(read_json(path, "stats file"), what):
            fig = figures(row, what, need_emails=need_emails)
            if fig.sequence_id in out:
                raise PayloadError(
                    f"two stats files carry sequenceId {shown(fig.sequence_id)} — "
                    "keep one, so the figures read are the ones you meant"
                )
            out[fig.sequence_id] = fig
    return out


# ── get_consolidated_stats ────────────────────────────────────────────────────────────────────

_STAMP_RE = re.compile(
    r"^\w{3} (\w{3}) (\d{1,2}) (\d{4}) (\d{2}):(\d{2}):(\d{2})(?: GMT([+-])(\d{1,2})(?::?(\d{2}))?)?"
)
_MONTHS = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}


def parse_sent_at(text: str) -> datetime:
    """``Sun Sep 27 2026 22:10:37 GMT-4 (America/New_York)`` or ISO-8601 -> an aware datetime.

    The day of the returned value is the day *as the account sees it* (the offset in the text is
    kept), which is the day a per-mailbox daily cap is counted in.
    """
    text = text.strip()
    match = _STAMP_RE.match(text)
    if match and match.group(1) in _MONTHS:
        month, day, year, hh, mm, ss, sign, oh, om = match.groups()
        offset = timedelta(hours=int(oh or 0), minutes=int(om or 0))
        tz = timezone(-offset if sign == "-" else offset) if sign else UTC
        return datetime(int(year), _MONTHS[month], int(day), int(hh), int(mm), int(ss), tzinfo=tz)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PayloadError(
            f"`Email Sent At` {shown(text)} is not a date this reader knows"
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _flag(row: dict, field: str, where: str) -> bool:
    value = row.get(field)
    word = value.strip().lower() if isinstance(value, str) else ""
    if word not in ("yes", "no"):
        raise PayloadError(f"{where}: {field} is {shown(value)} — expected Yes or No")
    return word == "yes"


@dataclass(frozen=True)
class SentEmail:
    """One sent email from ``get_consolidated_stats``."""

    sequence_id: str
    step: int
    recipient: str
    sender: str
    sent_at: datetime
    unsubscribed: bool
    bounced: bool
    replied: bool


def _email_row(row, where: str) -> SentEmail:
    if not isinstance(row, dict):
        raise PayloadError(f"{where} is not an object")
    if not usable_id(row.get("Sequence Id")):
        raise PayloadError(f"{where} has no usable `Sequence Id`")
    step = row.get("Step Number")
    step = int(step) if isinstance(step, str) and step.isdigit() else step
    if isinstance(step, bool) or not isinstance(step, int) or step < 1:
        raise PayloadError(
            f"{where} has no usable `Step Number` (found {shown(row.get('Step Number'))})"
        )
    recipient = row.get("Recipient Email")
    if not isinstance(recipient, str) or not recipient.strip():
        raise PayloadError(f"{where} has no `Recipient Email`")
    sent_at = row.get("Email Sent At")
    if not isinstance(sent_at, str):
        raise PayloadError(f"{where} has no `Email Sent At`")
    sender = row.get("Sender Email")
    return SentEmail(
        sequence_id=row["Sequence Id"],
        step=step,
        recipient=recipient.strip().lower(),
        sender=sender.strip().lower() if isinstance(sender, str) else "",
        sent_at=parse_sent_at(sent_at),
        unsubscribed=_flag(row, "Unsubscribed", where),
        bounced=_flag(row, "Bounced", where),
        replied=_flag(row, "Replied", where),
    )


def consolidated_emails(pages: list, what: str) -> tuple[list[SentEmail], int]:
    """``(sent emails, exact duplicates collapsed)`` from one or more ``get_consolidated_stats`` replies.

    An email's identity is ``(sequence, step, recipient, sent-at)``: page overlap repeats a row
    exactly and collapses, while a second send to the same person at the same step at a different
    time is kept, because collapsing it would shrink the denominator. If every page says
    ``hasMore`` the last page was never fetched, and the set is refused.
    """
    flags: list[bool] = []
    emails: dict[tuple, SentEmail] = {}
    dupes = 0
    for n, page in enumerate(pages):
        body = page.get("payload") if isinstance(page, dict) and "payload" in page else page
        data = body.get("data") if isinstance(body, dict) else body
        if not isinstance(data, list):
            raise PayloadError(f"{what} page {n} has no `payload.data` list of sent emails")
        flags.append(bool(body.get("hasMore")) if isinstance(body, dict) else False)
        for m, row in enumerate(data):
            email = _email_row(row, f"{what} page {n} row {m}")
            key = (email.sequence_id, email.step, email.recipient, email.sent_at)
            if key in emails:
                dupes += 1
            emails[key] = email
    if flags and all(flags):
        raise PayloadError(
            f"{what}: every page says hasMore — the last page was not fetched, so the counts "
            "would be a smaller world; fetch until hasMore is false"
        )
    if not emails:
        raise PayloadError(f"{what} holds no sent emails — an empty read is not a clean read")
    return sorted(
        emails.values(), key=lambda e: (e.sequence_id, e.step, e.sent_at, e.recipient)
    ), dupes
