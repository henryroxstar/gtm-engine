"""Deterministic source-coverage collection for the ``voice-of-customer`` skill.

Company-agnostic building block: :mod:`gtm_core.voc.collect` enumerates the six
voice-of-customer inputs for a profile, resolves the latest artifact per source,
and emits a coverage manifest. Its load-bearing field is the **speaker** each
source belongs to — ``customer-voice`` (what the market says/does),
``bd-focus`` (what our commercial org believes and targets), ``expert-lens``
(real named practitioners' published frameworks, synthesized + hedged by us), or
``mixed`` — assigned in code, keyed only on which source a file came from. That
mechanical separation is why the generated brief can never silently merge market
demand with BD's assumptions.

Counts and freshness are read from the filesystem + structured JSON, never
summarized from untrusted free text — injected prose in a news item or a listening
match can't move a number. Pure stdlib + :mod:`gtm_core.paths`, SDK-free.
"""
