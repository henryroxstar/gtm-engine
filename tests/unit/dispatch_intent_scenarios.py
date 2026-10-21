"""Store-agnostic F4 scenarios (always-on hardening test plan F4-1, F4-2, F4-4, F4-7, F4-8, F4-9).

ONE list of scenarios, run against BOTH stores: the VPS file ledger (tests/unit/test_dispatch_intents.py)
and the backend Postgres table (tests/backend/test_dispatch_intents_db.py, live ``dbtest`` tier). The point
of "one decision function, two stores" is that the two cannot disagree; running the same scenarios against
both is how that stays true. Recipients come from ``gtm_core.fictionalize`` shapes — never real people.
"""

from __future__ import annotations

import asyncio

from gtm_core.dispatch_intents import recipient_key

SEQ = "seq-fictional-001"
OTHER_SEQ = "seq-fictional-002"


def keys(*emails: str) -> list[str]:
    return [recipient_key(e) for e in emails]


async def repeat_is_refused_after_dispatched(store) -> None:
    first = await store.claim(SEQ, keys("a@acme.example", "b@acme.example"), tool="import")
    assert first.ok and first.batch_id
    await store.record_outcome(first.batch_id, "dispatched")
    again = await store.claim(SEQ, keys("a@acme.example", "b@acme.example"), tool="import")
    assert not again.ok and again.code == "already_dispatched"
    assert set(again.overlap) == {"a@acme.example", "b@acme.example"}
    assert again.batch_id is None


async def overlap_not_identity_refuses_whole_batch_and_names_the_recipient(store) -> None:
    a = await store.claim(SEQ, keys("pat@acme.example"), tool="import")
    await store.record_outcome(a.batch_id, "dispatched")
    # case change, surrounding whitespace -> same recipient; the rest of the batch is fine on its own
    overlapping = await store.claim(
        SEQ, keys("  PAT@Acme.Example ", "fresh@acme.example"), tool="import"
    )
    assert not overlapping.ok and overlapping.overlap == ("pat@acme.example",)
    # refused WHOLE: the non-overlapping recipient was not claimed either
    disjoint = await store.claim(SEQ, keys("fresh@acme.example"), tool="import")
    assert disjoint.ok


async def id_form_is_a_distinct_but_stable_key(store) -> None:
    ids = [recipient_key(lead_id=101), recipient_key(lead_id=" 101 ")]
    assert ids[0] == ids[1] == "id:101"
    a = await store.claim(SEQ, [ids[0]], tool="leads")
    await store.record_outcome(a.batch_id, "dispatched")
    assert not (await store.claim(SEQ, [ids[1]], tool="leads")).ok


async def failed_outcome_releases_the_recipients(store) -> None:
    a = await store.claim(SEQ, keys("x@acme.example"), tool="import")
    await store.record_outcome(a.batch_id, "failed")
    retry = await store.claim(SEQ, keys("x@acme.example"), tool="import")
    assert retry.ok


async def unknown_outcome_blocks_until_a_human_resolves_it(store) -> None:
    a = await store.claim(SEQ, keys("u1@acme.example", "u2@acme.example"), tool="import")
    await store.record_outcome(a.batch_id, "unknown")
    blocked = await store.claim(SEQ, keys("u1@acme.example"), tool="import")
    assert not blocked.ok and blocked.code == "outcome_unknown"
    open_batches = await store.unresolved()
    assert [b.batch_id for b in open_batches] == [a.batch_id]
    assert open_batches[0].count == 2 and open_batches[0].state == "unknown"


async def a_claim_with_no_outcome_reads_as_unknown(store) -> None:
    """The process died between the intent and the outcome (F4-3 b/c)."""
    a = await store.claim(SEQ, keys("dead@acme.example"), tool="import")
    assert a.ok  # ... and no record_outcome ever arrives
    again = await store.claim(SEQ, keys("dead@acme.example"), tool="import")
    assert not again.ok and again.code == "outcome_unknown"
    assert (await store.unresolved())[0].batch_id == a.batch_id


async def resolve_sent_keeps_it_blocked_and_not_sent_releases_it(store) -> None:
    a = await store.claim(SEQ, keys("s1@acme.example"), tool="import")
    await store.record_outcome(a.batch_id, "unknown")
    await store.resolve(a.batch_id, "sent")
    assert (
        await store.claim(SEQ, keys("s1@acme.example"), tool="import")
    ).code == "already_dispatched"
    assert await store.unresolved() == []

    b = await store.claim(SEQ, keys("n1@acme.example"), tool="import")
    await store.record_outcome(b.batch_id, "unknown")
    await store.resolve(b.batch_id, "not_sent")
    assert (await store.claim(SEQ, keys("n1@acme.example"), tool="import")).ok


async def partial_send_resolves_per_recipient(store) -> None:
    emails = [f"person{i:03d}@acme.example" for i in range(351)]
    a = await store.claim(SEQ, keys(*emails), tool="import")
    await store.record_outcome(a.batch_id, "unknown")
    sent = keys(*emails[:200])
    await store.resolve(a.batch_id, "sent_recipients", sent_recipients=sent)
    assert not (await store.claim(SEQ, keys(emails[0]), tool="import")).ok  # sent: blocked
    assert not (await store.claim(SEQ, keys(emails[199]), tool="import")).ok
    assert await store.unresolved() == []  # resolved: nothing is left open
    assert (await store.claim(SEQ, keys(emails[200], emails[350]), tool="import")).ok  # released


async def a_deliberately_removed_recipient_can_be_enrolled_again(store) -> None:
    a = await store.claim(SEQ, keys("gone@acme.example", "stay@acme.example"), tool="import")
    await store.record_outcome(a.batch_id, "dispatched")
    await store.mark_removed(SEQ, keys("gone@acme.example"))
    assert (await store.claim(SEQ, keys("gone@acme.example"), tool="import")).ok
    assert not (await store.claim(SEQ, keys("stay@acme.example"), tool="import")).ok


async def sequences_are_independent_scopes(store) -> None:
    """The same person in ANOTHER sequence is not merged (that is load_prior_contacts' job)."""
    a = await store.claim(SEQ, keys("same@acme.example"), tool="import")
    await store.record_outcome(a.batch_id, "dispatched")
    assert (await store.claim(OTHER_SEQ, keys("same@acme.example"), tool="import")).ok


async def plus_addressing_is_a_documented_non_merge(store) -> None:
    a = await store.claim(SEQ, keys("lee@acme.example"), tool="import")
    await store.record_outcome(a.batch_id, "dispatched")
    assert (await store.claim(SEQ, keys("lee+x@acme.example"), tool="import")).ok  # stated residual


async def concurrent_claims_yield_exactly_one_winner(store) -> None:
    results = await asyncio.gather(
        *[
            store.claim(SEQ, keys("race@acme.example", f"r{i}@acme.example"), tool="import")
            for i in range(8)
        ]
    )
    assert sum(1 for r in results if r.ok) == 1


async def empty_batches_are_refused_not_vacuously_allowed(store) -> None:
    r = await store.claim(SEQ, [], tool="import")
    assert not r.ok and r.code == "empty_batch"


async def an_open_hold_blocks_every_claim_until_cleared(store) -> None:
    await store.set_hold("restored from backup")
    r = await store.claim(SEQ, keys("held@acme.example"), tool="import")
    assert not r.ok and r.code == "on_hold"
    await store.clear_hold()
    assert (await store.claim(SEQ, keys("held@acme.example"), tool="import")).ok


SCENARIOS = [
    repeat_is_refused_after_dispatched,
    overlap_not_identity_refuses_whole_batch_and_names_the_recipient,
    id_form_is_a_distinct_but_stable_key,
    failed_outcome_releases_the_recipients,
    unknown_outcome_blocks_until_a_human_resolves_it,
    a_claim_with_no_outcome_reads_as_unknown,
    resolve_sent_keeps_it_blocked_and_not_sent_releases_it,
    partial_send_resolves_per_recipient,
    a_deliberately_removed_recipient_can_be_enrolled_again,
    sequences_are_independent_scopes,
    plus_addressing_is_a_documented_non_merge,
    concurrent_claims_yield_exactly_one_winner,
    empty_batches_are_refused_not_vacuously_allowed,
    an_open_hold_blocks_every_claim_until_cleared,
]
