"""The shipped default role vocabulary — the values a tenant gets until it ships its own.

Split out of ``gtm_core/role_vocabulary.py`` on 2026-09-21 under the §R10 complexity
ratchet: the module was 830 lines, of which these ~440 are DATA. Data and the rules that
validate it are different things to read and different things to change, and a file that
mixes them makes both harder to review.

**Do not edit these to serve one tenant.** They are the generic B2B-SaaS fallback. A tenant
whose buyers are a different shape ships ``knowledge/role-vocabulary.toml`` instead — that
is the entire point of the mechanism, and editing here to fix one profile silently moves
every other profile that never opted in.

Every comment below travelled here verbatim from ``tests/linter/outreach_pack_linter.py``.
The incidents they record are why the values are what they are, so they move with the data
rather than being left behind pointing at literals that are no longer there.
"""

from __future__ import annotations

# =====================================================================================
# Defaults — moved verbatim from tests/linter/outreach_pack_linter.py on 2026-09-21.
# Every comment below is the original one; the incidents they record are why the values
# are what they are, and they travel with the data rather than being left behind.
# =====================================================================================

# --- the persona axis (one vocabulary, two views) ------------------------------------
#
# ONE ordered title vocabulary resolves a recipient to a PERSONA; a small map folds those
# personas into the coarser SEAT that ``lint_persona_lead`` reasons about. Defining it once
# is the point: before 2026-08-20 this module had three seats and
# ``gtm_core.hook_coverage`` had its own ten-persona list, and two cue lists for one
# question drift apart — the finer one already knew titles the coarser one did not
# ("Chief AI Officer" resolved to a seat but to no persona, and vice versa).
#
# ORDER IS LOAD-BEARING, and every omission below carries its reason:
#   * ``data-compliance`` precedes ``compliance`` — "Data / Compliance Leader" and
#     "CRO / Compliance" both contain "compliance" and are different buyers.
#   * ``ciso`` precedes ``cloud-architect`` — "chief information security" must not be
#     claimed by "chief information".
#   * every seat-bearing persona precedes ``ceo`` — "Senior Vice President, Chief
#     Technology Officer" is a CTO, not an exec. 70 of the 123 live titles containing
#     "president" are VICE-presidents; ordering resolves the ones carrying a seat marker
#     and ``_ANTI_CUES`` catches the rest.
#   * bare "cro" is absent (Chief Revenue vs Chief Risk sit in opposite seats), bare "coo"
#     is absent (substring of "coordinator"), bare "cio" is absent (substring of ordinary
#     words) — the prefixed forms that actually occur are listed instead.

#: Titles that disqualify a persona even when one of its cues matched. "Executive Vice
#: President, Engineering" is not a CEO; without this, the bare "president" cue makes every
#: VP an exec — the same substring trap as coo/coordinator, found 2026-08-20 mis-seating 28
#: CTOs into the exec bucket.
DEFAULT_ANTI_CUES: dict[str, tuple[str, ...]] = {
    "ceo": ("vice president", "vice-president", "evp", "svp", "avp"),
}

#: The exec cues that name the JOB rather than a seniority band. ``_PERSONA_RULES`` puts
#: ``ceo`` last on purpose, so a lower functional cue anywhere in a compound title wins the
#: whole title: measured 2026-09-09, "Founder, chief executive officer, head of ai
#: innovations" resolved to ``ai-platform`` and "Chief Operating Officer / Chief Compliance
#: Officer" to ``compliance``. Both are execs, and the tempting reading of the resulting
#: `seat-stakes-missing` is that the COPY is wrong. Across the 1,145-title content corpus
#: this reclaims 14 titles (548 occurrences), including "Chief Executive Officer (former
#: CTO, promoted 2026-05-20)" — seated by its own parenthetical.
#:
#: RANK cues stay out, and that is the whole design. "managing director" and "president"
#: are bands a functional chief also holds ("Group CISO Managing Director" is a CISO), so
#: promoting them would trade this mis-seat for a wider one. OWNERSHIP cues stay out too:
#: "founder" says who owns the company, not which copy is owed, and 33 pooled
#: "Co-Founder & CTO" rows are technical buyers whose current ``cto`` seat is correct.
DEFAULT_CEO_TITLE_CUES = ("ceo", "chief executive", "chief operating", "chief operations")

DEFAULT_PERSONA_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "data-compliance",
        (
            "chief data",
            "data governance",
            "data protection officer",
            "data / compliance",
            "data and compliance",
            "data privacy",
            "head of data",
            "chief analytics",
            "data & analytics",
            "data and analytics",
            "dpo",
        ),
    ),
    (
        "ciso",
        (
            "ciso",
            "chief information security",
            "chief security",
            "head of security",
            "head of infosec",
            "security officer",
            "vp security",
            "director of security",
            # Recovered by the word-boundary fix: "Director of Information Security" used
            # to be claimed by the bare "cto" cue inside the word "director" before any
            # security cue was reached. It is a CISO and always was.
            "information security",
            "security engineering",
            "head of cyber",
            "cyber security",
            "cybersecurity",
        ),
    ),
    (
        "compliance",
        (
            "cro / compliance",
            "chief risk",
            "chief compliance",
            "head of compliance",
            "head of risk",
            "compliance officer",
            "risk officer",
            "regulatory affairs",
            "head of audit",
            "internal audit",
            "financial crimes",
            "compliance",
        ),
    ),
    (
        "ai-platform",
        (
            "chief ai",
            "chief a.i.",
            "head of ai platform",
            "ai platform",
            "head of ai",
            "head of applied ai",
            "ai engineering",
            "machine learning platform",
            "ml platform",
            "head of ml",
            "vp of ai",
            "vp ai",
            "genai",
            "generative ai",
        ),
    ),
    # Resolver-only personas: recognised so they are never invisible, but mapped to NO
    # seat below, so no copy is owed to them. Measured 2026-08-20 across the whole
    # 870-title pool: finops 0 recipients, partnership 1. Writing an argument for a
    # persona nobody holds is how a message portfolio gets padded instead of aimed.
    (
        "partnership",
        (
            "ecosystem / partnership",
            "head of partnerships",
            "head of partnership",
            "head of ecosystem",
            "partner platform",
            "platform bd",
            "business development",
            "alliances",
        ),
    ),
    (
        "finops",
        (
            "finops",
            "fin ops",
            "cloud economics",
            "cloud cost",
            "cost optimisation",
            "cost optimization",
        ),
    ),
    # SPLIT from one ``cloud-architect`` persona on 2026-09-21. The single persona held
    # both the people who architect a system and the people who run an IT organisation,
    # and measured on the 1,132-title corpus the mix was **87 titles / 5,262 people CIO
    # or CDO against 3 titles / 100 people who are actually architects**. Nothing failed:
    # the ``architect`` SEAT read 10% of the pool and looked like healthy technical
    # coverage of the ICP's named "Influencer — Security architect / senior platform
    # engineer", while holding almost no architects at all. A label that reports a seat
    # you do not cover is worse than a label that reports nothing.
    #
    # Both halves still map to the SAME seat in :data:`_SEAT_RULES`, so no new copy is
    # owed and ``lint_persona_lead`` is unchanged by this split — verified by A/B over
    # that corpus, 0 seat changes. It is exactly the ``founder-operator`` -> ``ceo``
    # pattern, and for the same reason: the two stay distinct as PERSONAS so the
    # inference is countable, and share a SEAT because the stakes are.
    #
    # ORDER: ``cloud-architect`` precedes ``cio`` so that "Chief Information Architect"
    # (which carries BOTH a ``chief information`` cue and an ``architect`` cue) seats as
    # the architect it is. ``ciso`` still precedes both — "chief information security"
    # must not be claimed by "chief information".
    (
        "cloud-architect",
        (
            "cloud architect",
            "enterprise architect",
            "principal architect",
            "chief architect",
            "solutions architect",
            "solution architect",
            "head of architecture",
            # Bare ``architect`` is SAFE here where a bare ``director`` was not: tested
            # 2026-09-21 against the whole 1,132-title corpus, it matches exactly three
            # titles and every one is an architect. It earns its place by catching the
            # compound forms the fixed phrases above miss, not by widening the net.
            "architect",
        ),
    ),
    (
        "cio",
        (
            "chief information officer",
            "chief information & digital",
            "chief information and digital",
            "chief digital",
            "group cio",
            # "chief information" is safe here only because ``ciso`` is evaluated FIRST
            # and claims "chief information security" — order is load-bearing.
            "chief information",
        ),
    ),
    (
        "cto",
        (
            "cto",
            "chief technology",
            "technology officer",
            "head of technology",
            "founding engineer",
            "head of platform",
            "head of engineering",
            "vp engineering",
            "vp of engineering",
            "director of engineering",
            "head of infrastructure",
        ),
    ),
    (
        "cpo",
        (
            "cpo",
            "chief product",
            "head of product",
            "vp product",
            "vp of product",
            "director of product",
            "product management",
            "product lead",
        ),
    ),
    (
        "ceo",
        (
            "ceo",
            "chief executive",
            "founder",
            "co-founder",
            "cofounder",
            "managing director",
            "chief operating",
            "chief operations",
            "president",
            "owner",
        ),
    ),
    # LAST on purpose, so it only ever catches what the named seats did not. This is the
    # DEFAULT seat for an owner-operator title, not a seat of its own: below a certain
    # headcount there is no functional split, and the person who signed the company up is
    # the buyer, the architect and the security reviewer at once. It maps to the ``ceo``
    # SEAT below, so it is owed no new copy — the hook matrix's CEO / Founder row is
    # already the right argument for this reader.
    #
    # It exists as a distinct PERSONA rather than as more ``ceo`` cues so that "resolved
    # because the title says CEO" and "resolved because nothing else fit and this is an
    # SME" stay countable apart. The first is a fact about the person; the second is an
    # inference about the company, and only one of them should survive contact with an
    # enterprise list.
    (
        "founder-operator",
        (
            "executive director",
            "managing partner",
            "founding partner",
            "senior partner",
            "managing principal",
            "proprietor",
            "business owner",
            "co-owner",
        ),
    ),
)
# A BARE "Director" is deliberately absent. It is the owner at a ten-person agency and a
# mid-level manager at a bank, and as a cue it matched every functional director in the
# pool — "Sales Director" and "Director of Talent Acquisition" both resolved here, which is
# the same over-reach the "cto"-in-"director" bug was made of, arriving from the other
# direction. An ambiguous title stays unrecognised and is handled by
# :data:`SEGMENT_DEFAULT_PERSONA` at routing time, where the caller knows the segment and
# this vocabulary does not.

#: Titles that are recognisably NOT a buyer for this product — see :func:`non_buyer_of`.
#: Kept small and unambiguous on purpose: the cost of a missing entry is one wasted send,
#: and the cost of an over-broad one is a dropped account that was never given a chance.
DEFAULT_NON_BUYER_CUES: tuple[str, ...] = (
    "administrative assistant",
    "executive assistant",
    "personal assistant",
    "office manager",
    "receptionist",
    "intern",
    "recruiter",
    "talent acquisition",
    "office administrator",
)

#: Where a recipient goes when :func:`persona_of` cannot see their seat. The point of a
#: default is that the None bucket stops being silently handed whatever persona a spec
#: declared: on the 2026-09-04 SG-builder roster, 10 of 18 merge-lane recipients held a
#: title the matrix had no row for, and each was sent an argument written for a seat
#: nobody had checked they held.
DEFAULT_SEGMENT_PERSONA = "founder-operator"

#: ``(seat, personas it covers, the seat's own stakes vocabulary)``.
#:
#: Six seats, sized to where recipients actually are (measured on the 397 live rows,
#: 2026-08-20): ceo 113 · cto 106 · security 97 · cloud-architect 30 · cpo 11 ·
#: ai-platform 3. The old three-seat split put 156 people in one "exec" bucket that held
#: CEOs, CPOs, data leaders and — through the bare "president" cue — 28 CTOs.
#:
#: ``finops`` and ``partnership`` resolve as personas but map to NO seat, so
#: ``lint_persona_lead`` stays silent on them: they have 0 and 1 recipients respectively
#: in the entire pool, and a seat with no recipients is a copy obligation with no reader.
DEFAULT_SEAT_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "security",
        ("ciso", "compliance", "data-compliance"),
        (
            "auditor",
            "examiner",
            "regulator",
            "audit",
            "evidence",
            "policy",
            "breach",
            "incident",
            "control",
            "attestation",
        ),
    ),
    (
        "ceo",
        # `founder-operator` shares this seat deliberately: an SME owner reads the CEO /
        # Founder argument, so the default costs no new copy. The two stay distinct as
        # PERSONAS so the inference is countable; they are one SEAT because the stakes are.
        ("ceo", "founder-operator"),
        (
            "deal",
            "customer",
            "enterprise",
            "procurement",
            "buyer",
            "revenue",
            "review stalls",
            "security review",
            "sales",
            "contract",
            "adoption",
            "rollout",
        ),
    ),
    (
        "product",
        ("cpo",),
        (
            "roadmap",
            "feature",
            "ship",
            "backlog",
            "product",
            "launch",
            "customer",
            "adoption",
            "differentiat",
            "build",
        ),
    ),
    (
        "cto",
        ("cto",),
        (
            "engineer",
            "build",
            "rebuild",
            "integration",
            "framework",
            "stack",
            "per deployment",
            "per customer",
            "fragment",
            "maintain",
            "wire",
            "retrofit",
            "ship",
        ),
    ),
    (
        "architect",
        # ``cio`` shares this seat deliberately (2026-09-21 split): an IT organisation's
        # owner reads the same standardise-once / no-lock-in argument an architect does,
        # so the split costs no new copy. They stay distinct as PERSONAS so the 87:3 mix
        # is countable; they are one SEAT because the stakes are.
        ("cloud-architect", "cio"),
        (
            "standard",
            "standardise",
            "standardize",
            "platform",
            "estate",
            "multi-cloud",
            "portable",
            "lock-in",
            "interoperab",
            "topology",
            "reference architecture",
            "stack",
        ),
    ),
    (
        "ai-platform",
        ("ai-platform",),
        (
            "model",
            "agent",
            "framework",
            "orchestrat",
            "production",
            "observability",
            "guardrail",
            "evaluation",
            "latency",
            "pipeline",
        ),
    ),
)

# Vocabulary that belongs to security and reads as borrowed in any other seat's email.
DEFAULT_SECURITY_ONLY = ("auditor", "examiner", "audit trail", "attribution", "attributable")


#: Segment vocabulary EVERY downstream comparison normalises against. Moved here from
#: ``gtm_core.merge_hygiene.company`` on 2026-09-21 for the same reason as the rules above:
#: it is a tenant fact. Its own history is the argument — ``builder`` joined 2026-09-04 with
#: the third segment, and a segment a tenant can select into ``segment_mix`` but that this
#: tuple does not know is a segment whose rows are measured as noise, silently.
DEFAULT_SEGMENTS: tuple[str, ...] = ("enterprise", "startup", "builder", "unspecified")

#: The segment kept for a row nobody classified. Never selected into a run mix, never owed
#: a hook grid — see ``tests/lint/test_profile_targeting_invariants.py``.
UNSPECIFIED_SEGMENT = "unspecified"


# --- the seat's outbound-copy facts (added 2026-09-24, outbound fact registry) ----------
#
# A ``[[seat]]`` may carry the four copy facts an outbound email is built from — the pain it
# LEADS on, the gain it promises, the pains that belong to another seat and must never be
# fired at this one, and the register it reads in — plus the segments it is aimed at. They
# live on the seat because that is where "which argument does this person read" already
# lives; before this they were restated in prose tables across five knowledge files, each
# free to drift from the others with nothing able to see the drift.

#: The registers a seat may declare. CLOSED on purpose: a register selects a whole surface
#: of copy, so an unrecognised one has only two possible behaviours, and both are invisible
#: at send time — fall back silently to a register the tenant did not choose, or render
#: nothing. Refusing at load is the only outcome an operator can see.
SEAT_REGISTERS: frozenset[str] = frozenset({"standard", "technical", "executive"})

#: The shipped default declares NONE of them, and that is deliberate rather than pending.
#: Everything else in this file is a generic B2B-SaaS *vocabulary* — cues, seat names,
#: stakes words — which is reasonable to guess for a tenant that never customised. A lead
#: pain is not a vocabulary; it is a sentence claiming to know what a stranger's buyer loses
#: sleep over. Shipping one would put it in the mouth of every profile that never opted in,
#: and the tenant would have no way to tell an inherited guess from its own decision. A seat
#: with no lead pain reads as "" everywhere, which the registry can see and refuse.
DEFAULT_SEAT_LEAD_PAIN: dict[str, str] = {}
DEFAULT_SEAT_GAIN: dict[str, str] = {}
DEFAULT_SEAT_FORBIDDEN_PAINS: dict[str, tuple[str, ...]] = {}
DEFAULT_SEAT_REGISTER: dict[str, str] = {}
DEFAULT_SEAT_SEGMENTS: dict[str, tuple[str, ...]] = {}
