"""The dispatch-intent ledger — "a send that cannot prove it is not a duplicate must not guess" (F4).

Always-on hardening PRD (2026-10-19), F4. Every external send (an
enrollment into a sequencer, a publish) writes an INTENT before the outbound call and an OUTCOME after it.
A crash, a timeout or a 5xx therefore leaves an intent whose outcome is *unknown*, and an unknown outcome
**blocks** a repeat until a human resolves it — instead of the old behaviour of recording a timeout as a plain
failure and re-sending.

Two stores, ONE decision:

* :class:`FileIntentStore` — the single-operator VPS: ``content/<profile>/dispatch-intents.jsonl``, written
  through :class:`gtm_core.ledgers.Ledgers` with the existing ``flock`` held across check-and-append.
* ``backend.services.dispatch_intents.PgIntentStore`` — the hosted backend: a table under FORCE RLS.

Both call :func:`decide`, and are held to the same scenarios (``tests/unit/dispatch_intent_scenarios.py``).

The ledger is append-only: a row is never edited or removed. Release (``failed`` / ``not_sent`` / ``removed``)
is a NEW row, and state is derived by replaying them in order. Fail-closed throughout: an unreadable ledger
refuses the send (:class:`IntentLedgerUnreadable`), a claim with no outcome reads as unknown, and an empty
batch is refused rather than vacuously allowed.

Identity is per recipient — ``(scope, recipient_key)`` where ``scope`` is the sequence id (or
``publish:<platform>``) and the key is the lower-cased, trimmed email, or ``id:<lead_id>`` when the draft carries
lead ids only. NOT merged, by design and tested: plus-addressing, the same person under both draft shapes, and
the same person in another sequence (that is ``load_prior_contacts``' job).
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# -- states ------------------------------------------------------------------------------------
INTENT = "intent"  # claimed; no outcome recorded yet (the process may have died)
DISPATCHED = "dispatched"  # provider confirmed accepted
UNKNOWN = "unknown"  # timeout / connection error / 5xx / unparseable / killed
RELEASED = "released"  # proven not sent, or a human said so
#: States that block a repeat. ``intent`` is blocking on purpose: it is an outcome nobody recorded.
BLOCKING = frozenset({INTENT, DISPATCHED, UNKNOWN})
OUTCOMES = frozenset({"dispatched", "failed", "unknown"})
RESOLUTIONS = frozenset({"sent", "not_sent", "sent_recipients"})

#: The scope every publish intent lives under (one per platform; LinkedIn is the only pinned destination).
PUBLISH_SCOPE = "publish:linkedin"


class IntentLedgerUnreadable(RuntimeError):
    """The intent ledger cannot be read faithfully, so no send may be authorised against it."""


@dataclass(frozen=True)
class Decision:
    ok: bool
    overlap: tuple[str, ...] = ()
    code: str | None = None


@dataclass(frozen=True)
class ClaimResult:
    ok: bool
    batch_id: str | None = None
    code: str | None = None  # already_dispatched | outcome_unknown | on_hold | empty_batch
    overlap: tuple[str, ...] = ()
    message: str = ""
    #: Batches whose outcome nobody has settled and which own an overlapping recipient — what a
    #: human resolves ("it went out" / "it didn't") to unblock a repeat. Empty unless unresolved.
    open_batches: tuple[str, ...] = ()


@dataclass(frozen=True)
class OpenBatch:
    batch_id: str
    scope: str
    tool: str
    count: int
    state: str  # intent | unknown
    ts: str
    label: str | None = None


def recipient_key(email: str | None = None, lead_id: Any = None) -> str:
    """The identity of one recipient inside one scope. Raises ``ValueError`` if there is none."""
    e = (email or "").strip().lower()
    if e:
        return e
    lid = str(lead_id).strip() if lead_id is not None else ""
    if lid:
        return f"id:{lid}"
    raise ValueError("a recipient needs an email or a lead id")


def new_batch_id() -> str:
    return "b-" + secrets.token_hex(6)


# -- the one decision ----------------------------------------------------------------------------


def decide(states: Mapping[str, str], wanted: Iterable[str]) -> Decision:
    """May a batch for ``wanted`` recipients proceed, given the current state of each recipient?

    Pure. Only the CLOSED list grants: a recipient with no record at all, or one explicitly ``released``.
    Anything else — including a blank or unheard-of state word — blocks, because a row we cannot read is
    exactly the case where a send must not guess. The WHOLE batch is refused if any recipient blocks,
    naming the overlap, so a half-sent list is never created by accident. ``outcome_unknown`` wins over
    ``already_dispatched`` when both are present, because the human then has something to resolve.
    """
    overlap = tuple(k for k in dict.fromkeys(wanted) if k in states and states[k] != RELEASED)
    if not overlap:
        return Decision(True)
    all_dispatched = all(states[k] == DISPATCHED for k in overlap)
    return Decision(False, overlap, "already_dispatched" if all_dispatched else "outcome_unknown")


def refusal_message(
    code: str, scope: str, overlap: Sequence[str], *, count: int, label: str | None
) -> str:
    """Plain words for the operator: what happened, and exactly what to do next. No keys, no hashes."""
    if code == "outcome_unknown":
        if scope.startswith("publish:"):
            platform = scope.split(":", 1)[1].replace("linkedin", "LinkedIn")
            first = (label or "this post").strip().splitlines()[0][:80] if label else "this post"
            return (
                f"Not sent: I can't tell whether the {platform} post «{first}» already went out. "
                "Open the platform and check, then tell me:"
            )
        n = count if count else len(overlap)
        return (
            f"Not sent: I can't tell whether this batch of {n} emails already went out. "
            "Open the sequence and check, then tell me:"
        )
    if code == "already_dispatched":
        shown = ", ".join(overlap[:5]) + (
            f" and {len(overlap) - 5} more" if len(overlap) > 5 else ""
        )
        what = "that post" if scope.startswith("publish:") else "those recipients"
        return f"Not sent: {what} already went out ({shown}). Nothing was sent again."
    if code == "on_hold":
        return (
            "Not sent: sending is on hold after a restore from backup, because the record of what "
            "already went out may be behind the sequencer. Run the reconcile step first "
            "(`dispatch-intents --reconcile`), then try again."
        )
    if code == "empty_batch":
        return "Not sent: there is nobody in this batch."
    return f"Not sent: {code}."


# -- replaying the ledger ---------------------------------------------------------------------------


@dataclass
class _Batch:
    scope: str
    tool: str
    recipients: list[str]
    ts: str
    label: str | None
    outcome: str | None = None  # None until recorded


class Replay:
    """The ledger rows folded, in order, into per-recipient state and per-batch facts."""

    def __init__(self, rows: Iterable[dict]) -> None:
        self.state: dict[tuple[str, str], str] = {}
        self.owner: dict[tuple[str, str], str] = {}
        self.batches: dict[str, _Batch] = {}
        self.hold: str | None = None
        for r in rows:
            getattr(self, "_" + str(r.get("kind")), self._ignore)(r)

    def _ignore(self, _r: dict) -> None:  # an unknown future kind is not an error
        return

    def _claim(self, r: dict) -> None:
        scope, bid = r["scope"], r["batch_id"]
        recs = [str(x) for x in r["recipients"]]
        self.batches[bid] = _Batch(scope, r.get("tool", ""), recs, r.get("ts", ""), r.get("label"))
        for rec in recs:
            self.state[(scope, rec)] = INTENT
            self.owner[(scope, rec)] = bid

    def _own(self, bid: str) -> list[tuple[str, str]]:
        b = self.batches.get(bid)
        if b is None:
            return []
        return [
            (b.scope, rec)
            for rec in b.recipients
            if self.owner.get((b.scope, rec)) == bid
            and self.state.get((b.scope, rec)) in (INTENT, UNKNOWN)
        ]

    def _outcome(self, r: dict) -> None:
        b = self.batches.get(r.get("batch_id", ""))
        if b is None:
            return
        b.outcome = r["state"]
        new = {"dispatched": DISPATCHED, "failed": RELEASED, "unknown": UNKNOWN}[r["state"]]
        for key in self._own(r["batch_id"]):
            self.state[key] = new

    def _resolve(self, r: dict) -> None:
        bid = r.get("batch_id", "")
        b = self.batches.get(bid)
        if b is None:
            return
        res = r["resolution"]
        sent = set(r.get("sent") or ())
        for key in self._own(bid):
            if res == "sent" or (res == "sent_recipients" and key[1] in sent):
                self.state[key] = DISPATCHED
            else:
                self.state[key] = RELEASED
        b.outcome = "resolved"

    def _removed(self, r: dict) -> None:
        for rec in r.get("recipients", []):
            self.state[(r["scope"], str(rec))] = RELEASED

    def _hold(self, r: dict) -> None:
        self.hold = r.get("reason") or "on hold"

    def _hold_cleared(self, _r: dict) -> None:
        self.hold = None

    def states_for(self, scope: str, recipients: Iterable[str]) -> dict[str, str]:
        return {rec: self.state[(scope, rec)] for rec in recipients if (scope, rec) in self.state}

    def open_batches(self) -> list[OpenBatch]:
        out = []
        for bid, b in self.batches.items():
            owned = [
                self.state.get((b.scope, rec))
                for rec in b.recipients
                if self.owner.get((b.scope, rec)) == bid
            ]
            live = [s for s in owned if s in (INTENT, UNKNOWN)]
            if live:
                state = UNKNOWN if (b.outcome == "unknown" or INTENT in live) else live[0]
                out.append(OpenBatch(bid, b.scope, b.tool, len(live), state, b.ts, b.label))
        return out


# -- the file store ------------------------------------------------------------------------------------


class FileIntentStore:
    """``content/<profile>/dispatch-intents.jsonl`` through :class:`gtm_core.ledgers.Ledgers`.

    Methods are ``async`` only so both stores share one call shape; the file work is local and brief.
    """

    def __init__(self, ledgers: Any) -> None:
        self._led = ledgers

    # reads ---------------------------------------------------------------------------------------
    def _replay(self) -> Replay:
        try:
            return Replay(self._led.read_dispatch_rows())
        except IntentLedgerUnreadable:
            raise
        except Exception as exc:  # noqa: BLE001 — any read problem means we cannot authorise a send
            raise IntentLedgerUnreadable(f"{type(exc).__name__}: {exc}") from exc

    async def unresolved(self) -> list[OpenBatch]:
        return self._replay().open_batches()

    async def hold_reason(self) -> str | None:
        return self._replay().hold

    # writes --------------------------------------------------------------------------------------
    async def claim(
        self,
        scope: str,
        recipients: Sequence[str],
        *,
        tool: str,
        label: str | None = None,
        ignore_hold: bool = False,
    ) -> ClaimResult:
        """Claim ``recipients`` for one outbound call, or refuse. ``ignore_hold`` is for the reconcile step
        ONLY — the step whose job is to lift the hold must be able to write through it."""
        wanted = list(dict.fromkeys(recipients))
        if not wanted:
            return ClaimResult(
                False,
                None,
                "empty_batch",
                (),
                refusal_message("empty_batch", scope, (), count=0, label=label),
            )
        batch_id = new_batch_id()
        record = {
            "kind": "claim",
            "batch_id": batch_id,
            "scope": scope,
            "tool": tool,
            "recipients": wanted,
            "label": label,
        }

        def guard(rows: list[dict]) -> ClaimResult | None:
            replay = Replay(rows)
            if replay.hold is not None and not ignore_hold:
                return ClaimResult(
                    False,
                    None,
                    "on_hold",
                    (),
                    refusal_message("on_hold", scope, (), count=len(wanted), label=label),
                )
            d = decide(replay.states_for(scope, wanted), wanted)
            if d.ok:
                return None
            msg = refusal_message(d.code or "", scope, d.overlap, count=len(wanted), label=label)
            owners = tuple(
                dict.fromkeys(
                    replay.owner[(scope, k)]
                    for k in d.overlap
                    if replay.state.get((scope, k)) in (INTENT, UNKNOWN)
                )
            )
            return ClaimResult(False, None, d.code, d.overlap, msg, owners)

        try:
            refused, _row = self._led.claim_dispatch(guard, record)
        except IntentLedgerUnreadable:
            raise
        except ValueError as exc:  # a corrupt line surfaced by the strict reader
            raise IntentLedgerUnreadable(str(exc)) from exc
        if refused is not None:
            return refused
        return ClaimResult(True, batch_id, None, (), "")

    async def record_outcome(self, batch_id: str, state: str, detail: str | None = None) -> None:
        if state not in OUTCOMES:
            raise ValueError(f"unknown outcome {state!r}")
        self._led.append_dispatch(
            {"kind": "outcome", "batch_id": batch_id, "state": state, "detail": detail}
        )

    async def resolve(
        self,
        batch_id: str,
        resolution: str,
        *,
        sent_recipients: Sequence[str] | None = None,
        actor: str | None = None,
    ) -> dict[str, int]:
        """Record a human's answer. ``actor`` ("cli", "cockpit:<chat>", "user:<id>") is stamped on the row and
        in history: this is the one act that can re-enable a send, so who did it must be on the record."""
        if resolution not in RESOLUTIONS:
            raise ValueError(f"unknown resolution {resolution!r}")
        replay = self._replay()
        if batch_id not in replay.batches:
            raise KeyError(batch_id)
        before = {k: v for k, v in replay.state.items() if replay.owner.get(k) == batch_id}
        self._led.append_dispatch(
            {
                "kind": "resolve",
                "batch_id": batch_id,
                "resolution": resolution,
                "sent": list(sent_recipients or ()),
                "by": actor,
            }
        )
        try:
            self._led.append_history(
                {
                    "event": "dispatch_resolved",
                    "batch_id": batch_id,
                    "resolution": resolution,
                    "by": actor,
                }
            )
        except Exception:  # noqa: BLE001  # nosec B110 — the audit line must never undo the resolution
            pass
        after = self._replay().state
        sent = sum(1 for k in before if after.get(k) == DISPATCHED and before[k] != DISPATCHED)
        released = sum(1 for k in before if after.get(k) == RELEASED and before[k] != RELEASED)
        return {"sent": sent, "released": released}

    async def mark_removed(
        self, scope: str, recipients: Sequence[str], actor: str | None = None
    ) -> None:
        self._led.append_dispatch(
            {"kind": "removed", "scope": scope, "recipients": list(recipients), "by": actor}
        )

    async def set_hold(self, reason: str) -> None:
        self._led.append_dispatch({"kind": "hold", "reason": reason})

    async def clear_hold(self) -> None:
        self._led.append_dispatch({"kind": "hold_cleared"})

    async def states(self, scope: str, recipients: Sequence[str]) -> dict[str, str]:
        return self._replay().states_for(scope, recipients)
