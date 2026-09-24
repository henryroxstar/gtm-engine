"""Datatypes, the one ``RULES_VERSION``, and the rule inventory. The lowest layer:
nothing here imports another module of this package."""

from __future__ import annotations

from dataclasses import dataclass

# The 2026-09-24 merge collapsed two versions into one and DELIBERATELY kept the pack
# linter's, because it is the one that GATES: `rules-version-stale` compares a pack header
# (and a tracker CSV's `rules_version`) against it, so a new date would mark every fresh pack
# on disk stale in a commit that changed no rule — which is how a real staleness signal gets
# acknowledged into silence. "2026-08-20" gated nothing: it printed in `_report`'s header and
# the CLI description only. The next real rule change moves this; that is when it means
# something.
#
# NOT moved by the 2026-09-24 retirement, deliberately, and this is the one place to argue it
# back. FR3 retires 25 rules and adds 3, which by the note above is exactly the "next real rule
# change" — so the honest date is 2026-09-24 and every pack on disk IS stale, because a pack
# reviewed against `specificity` / `cta-question` / `hedge-missing` was reviewed against a gate
# that no longer exists. It is held back because the specs have not yet migrated to the registry
# shape (Tasks 3.5/3.7 rewrite the controls and the skills): stamping the date now turns the
# whole fleet red for a re-draft the drafting skills cannot yet produce, which is the "a gate
# nobody can ship through" failure this file keeps re-learning. Move it in the commit that lands
# the migrated skills, not before.
RULES_VERSION = "2026-09-04"


@dataclass
class Violation:
    level: str  # "ERROR" | "WARN"
    email: str  # recipient label or "PACK"
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.email}: {self.rule} — {self.detail}"


@dataclass
class EmailBlock:
    index: int
    header: str  # "First Last · Title, Company"
    first: str
    company: str
    to: str
    subject: str
    body: str  # plain text, ends with sign-off line

    @property
    def label(self) -> str:
        return f"#{self.index} {self.to}"


@dataclass
class PackMeta:
    """Format-specific fields that live OUTSIDE the email body.

    The per-email rules in `lint_email` judge the text that gets sent. These are the things a
    reader of the body alone cannot see — the attachment the header promises, the word count the
    drafter claimed, the sequence shape planned underneath — and every one of them was a real
    2026-08-17 audit finding that no body-level rule could have caught.
    """

    fmt: str  # "tier-a-manual" | "draft-outreach" | "prospect-pack"
    label: str = "PACK"
    attach: str | None = None  # an Attach:/Gift-artifact header field, if the format has one
    stated_words: int | None = None  # the drafter's self-reported count
    declared_touches: int | None = None  # what the sequence header claims
    enumerated_touches: int = 0  # how many touches are actually written out
    touch_channels: tuple[str, ...] = ()
    has_linkedin_dm: bool = False


@dataclass
class Touch:
    number: int
    day: int
    subject: str  # "" for a same-thread follow-up
    body: str


# --------------------------------------------------------------------------- what retired
#
# THE 2026-09-24 RETIREMENT, AND WHERE EACH RULE'S INTENT WENT.
#
# The outbound fact registry replaces copy-as-knowledge with facts-as-knowledge, and the
# design that did so is dated 2026-09-24. Twenty-five rules retire here. They are not
# deleted because they were wrong; they are deleted because they were the WRONG INSTRUMENT —
# a regex judging whether a sentence is persuasive, where the registry can instead answer
# whether the sentence is *derivable* from a statused fact. Two levers, one source.
#
# Nothing may be dropped silently, so every retired id is listed below with the one place its
# intent now lives. Five are honest losses and say so.
#
#   retired rule                     intent now lives in
#   ------------------------------   -----------------------------------------------------
#   hedge-missing                    card question `fact_earns_its_place`
#   credit-is-verdict                card question `fact_earns_its_place`
#   specificity                      card question `fact_earns_its_place`
#   signal-cell-mismatch             card question `fact_earns_its_place`
#   cta-overclaim                    card question `claim_within_status`
#   cta-unstaged-artifact            card question `claim_within_status`
#   cta-omits-gap                    card question `bridge_depends_on_fact`
#   cta-unanchored                   card question `bridge_depends_on_fact`
#   offer-does-their-work            card question `frame_fits_seat`
#   offer-not-a-solution-overview    card question `frame_fits_seat`
#   stakes-unattested                card question `frame_fits_seat`
#   seat-stakes-missing              card question `frame_fits_seat`
#   seat-stakes-not-in-problem       card question `frame_fits_seat`
#   problem-asserts-internals        card question `frame_fits_seat`   (it judged whether a
#                                    claim was pitched at a class or asserted about one
#                                    reader's build — a framing question, not a fact one)
#   signal-column-unknown            card question `corrupted_scrape`
#   signal-column-unrecorded         card question `corrupted_scrape`
#   capability-unargued              STRUCTURAL: `slot-attribution` + `claim-status`. The body
#                                    now cites the claim id it argues, so "declares X, argues
#                                    Y" is a derivation mismatch rather than a word count.
#   stakes-missing                   STRUCTURAL: stakes are derived from the angle
#                                    (`hook_coverage.declared.derived_fields`), never declared.
#   hook-cell-missing                `angle-missing`, which raises `declared.UndeclaredAngle`
#   hook-cell-unknown                `angle-unknown`, which raises `declared.UnknownAngle`
#
# Naming an exception type was NOT enough, and that is the 2026-09-24 review's finding 2: for
# one day these two lines pointed at `declared.py` classes that no production caller could
# reach, four documents told the drafter "a spec with no `angle:` is an ERROR", and
# `grep -rn "angle-missing" --include='*.py'` returned two comments. A retirement that hands a
# rule's intent to something nothing invokes is a deletion wearing a forwarding address. The
# successors are now rule ids in the catalogue above, with the severities their measurement
# supports (WARN for absence while 0 of 47 live specs declare one; ERROR for a wrong id).
#
# FIVE HONEST LOSSES — recorded, not disguised:
#
#   hedge-stem-repeat        NO HOME. Batch-scoped ("the same hedge frames N touches"); no card
#                            question is batch-scoped, the card is per row. The other batch
#                            rules (`*-template-share`, `same-company-*`) are KEPT, so this is
#                            an inconsistency the PRD created rather than a decision.
#   signal-column-undeclared NO HOME. The column axis is abolished — 0 of 43 specs ever
#                            declared one — so nothing inherits it.
#   cta-bundled              `voice-rules.toml` `[cta].max_per_touch = 1`, WITH NO READER.
#   cta-question             `voice-rules.toml` `[cta].must_be_question = true`, NO READER.
#   antithesis               `voice-rules.toml` `[banned_construction]`, NO READER.
#   (the three above)        A reader for those keys was considered and NOT built. Building one
#                            re-instates a retired style rule under a tenant-configurable name,
#                            and re-inflates exactly the surface FR3 exists to shrink; the PRD's
#                            decision is that shape judgements move to the card and the judge,
#                            not back into the linter as data-driven regex. Recorded so the next
#                            reader knows it was a decision. Six `voice-rules.toml` tables
#                            (`hedge`, `cta`, `offer`, `altitude`, `gap_shape`, `optional_beat`)
#                            now have no consumer at all and should be trimmed with the tenant.
#
# The card itself is `gtm_core/messaging/card.py` (Task 3.4), which another task owns. This
# block is the mapping's home; the card is the mapping's destination.


#: Every check this gate can make, with a plain-English description of what it protects.
#: The QA record used to list only the rules that FIRED, so a clean run looked like a thin
#: one and "never checked" was indistinguishable from "checked and clean". Publishing the
#: full catalogue alongside the findings is what makes a PASS mean something to a reader
#: who does not have the source open.
# This dict is documentation, not a source of truth the pipeline reads, so it drifts silently:
# corrected 2026-08-20 (P0.5) after it advertised a "43-rule inventory" that was neither the
# count nor an accurate map — 5 catalogued ids never fired and 26 emitted ones were absent.
# Every entry below is proven to fire by ``test_merge_render_mutation_suite.py``, which fails
# closed on a catalogued rule with no mutation. The OTHER direction is `ALL_RULE_IDS`: the
# catalogue is the mutation-covered subset, not the whole emittable surface.
#
# 2026-09-24 (outbound fact registry, FR3): -24 retired (see the block above; the 25th,
# `capability-unargued`, was uncatalogued), +3 derivation rules, and the four
# `score-*`/`segment-*` ids promoted OUT of `UNCATALOGUED_RULES` — they are raised by
# `gtm_core.merge_hygiene.check_row`, which the pre-merge catalogue backstop never walked, and
# a row-data rule with no catalogue entry is a rule `rule_lifecycle_report` has no denominator
# for.
RULE_CATALOGUE: dict[str, tuple[str, str]] = {
    # (category, what it protects against)
    # Added 2026-09-24 by the FR3 invariant review. The three rules below all rest on one
    # declaration, and until these ids existed a spec whose declaration did not resolve
    # switched all three OFF and reported nothing — `declared_angle`'s validating form was
    # never called from production, so its three refusal types were unreachable. A gate that a
    # single mistyped word turns off is not a gate. See `rules_derivation.lint_angle`.
    "angle-missing": (
        "derivation",
        "The spec declares no `angle:`, so it is not registry-derived and the three rules "
        "below cannot check it — WARN while the fleet migrates, and it names what it silenced",
    ),
    "angle-unknown": (
        "derivation",
        "The spec declares an angle id angles.toml does not hold — a typo in a migrated spec "
        "claims conformance to an argument nobody wrote",
    ),
    "angle-conflict": (
        "derivation",
        "A legacy `hook_cell` / `capability` / `premise` / `stakes` copy contradicts what the "
        "declared angle derives — one fact with two declarations can be wrong in one of them",
    ),
    "claim-status": (
        "derivation",
        "The body rests on a claim this tenant has not verified, or uses a phrasing the "
        "claim's own `do_not_say` list forbids — the overclaim the registry exists to stop",
    ),
    "proof-status": (
        "derivation",
        "A figure in the body has no `measured` proof behind it, leans on a `disputed` one, "
        "or cites another market's regulatory anchor at this reader",
    ),
    "slot-attribution": (
        "derivation",
        "A body slot names no source id, or names one that contradicts the declared angle — "
        "copy whose provenance cannot be checked is copy nobody can stand behind",
    ),
    "persona-lead-mismatch": (
        "relevance",
        "Email leads on a different seat's pain than the recipient holds",
    ),
    "premise-missing": (
        "relevance",
        "Spec does not declare the premise its body requires, so no row can be checked against it",
    ),
    "premise-unknown": (
        "relevance",
        "Spec declares a premise the profile's premise-vocab.toml does not define — an "
        "invented requirement wearing a declaration",
    ),
    "premise-unsupported": (
        "relevance",
        "The row's own researched evidence cannot establish what the body then claims — "
        "the body asserts plurality, the fact attests one thing",
    ),
    "premise-thin": (
        "relevance",
        "Most rows attest the declared premise on a single common word — a word-presence "
        "check wearing an entailment check's name",
    ),
    "signal-off-topic": (
        "relevance",
        "Opening line is about a different subject than the body's claim — the two paragraphs "
        "visibly do not connect",
    ),
    "signal-not-an-event": (
        "relevance",
        "Opening line is a standing description of the company, not a dated trigger",
    ),
    "signal-stray-digit": (
        "relevance",
        "Opening line carries a date or metric — an opener says what happened, not when or "
        "how much (a digit inside the company's own name is fine)",
    ),
    "touch-not-personalised": (
        "substance",
        "A follow-up that opens a new thread varies only by company name — every recipient "
        "gets near-identical copy",
    ),
    "signal-contradicts-pitch": (
        "substance",
        "The row's own researched fact announces the capability the body claims is missing — "
        "the send refutes itself",
    ),
    "signal-agent-homonym": (
        "substance",
        "The clause's 'agents' are people (insurance/real-estate), not AI agents — the body "
        "does not apply to this company",
    ),
    "thread-sentence-repeat": (
        "substance",
        "The same sentence runs in two touches landing in ONE thread — the reader sees it "
        "twice in the same window",
    ),
    "opener-undated": (
        "substance",
        "Touch 1's opener carries no year or month — opt-in, for a profile whose specs have "
        "migrated to the dated-referent shape",
    ),
    "thread-reply-prefix": (
        "substance",
        "A touch that opens a thread carries a Re:/Fwd: subject — it claims a conversation "
        "the recipient never had",
    ),
    "word-count": ("substance", "Body is outside the length band the format allows"),
    "sentence-length": (
        "substance",
        "A sentence is too long to skim — or the body's mean sentence runs long",
    ),
    "question-count": (
        "substance",
        "Body asks more questions than the reader can answer in one reply",
    ),
    "empty-merge-tag": (
        "merge",
        "A merge field is blank for some rows — renders a broken sentence",
    ),
    "unknown-merge-tag": ("merge", "Copy uses a merge tag the provider does not define"),
    "merge-tag-not-in-csv": ("merge", "Copy uses a merge tag the enrolment list has no column for"),
    "unused-signal-column": ("merge", "Rows carry research the copy never uses"),
    "first-name-unrenderable": ("merge", "First name cannot be rendered into the greeting"),
    "greeting": ("merge", "Greeting is malformed"),
    "greeting-not-a-name": ("merge", "Greeting resolves to something that is not a person's name"),
    "unresolved-contact": (
        "merge",
        "Contact name never resolved — the sentinel is the correct greeting until it does",
    ),
    "placeholder": (
        "merge",
        "Copy carries an unresolved bracket placeholder ([INSERT], [TBD]) or a stray merge tag",
    ),
    "spintax": ("merge", "Copy carries unresolved spintax ({a|b}) instead of a chosen variant"),
    "last-name-initial": ("data", "Last name is a bare initial"),
    "last-name-symbols": ("data", "Last name carries symbols that will render badly"),
    "last-name-credentials": (
        "data",
        "Last name carries post-nominals (MD, PhD) that read wrong inline",
    ),
    "company-headline": ("data", "Company field holds a headline, not a company"),
    "company-empty": ("data", "No company name at all"),
    "company-placeholder": ("data", "Company field holds a placeholder value (n/a, none, unknown)"),
    "company-trademark-glyph": (
        "data",
        "Company name carries a ®/™/©/℗ glyph that renders wrong mid-sentence",
    ),
    "company-sentence-punct": (
        "data",
        "Company name carries a ! or ? that reads as shouting mid-sentence",
    ),
    "company-trailing-period": ("data", "Company name ends in a stray period"),
    "company-transaction-entity": (
        "data",
        "Company field holds a deal/shell-entity name (a SPAC vehicle), not the operating company",
    ),
    "company-domain-junk": (
        "data",
        "Company domain is an enrichment artifact (localhost, geo-blocked, a social profile URL), "
        "not a real site",
    ),
    "first-name-empty": ("data", "No usable given name"),
    "first-name-placeholder": (
        "data",
        "Given name is a placeholder value (n/a, team, admin)",
    ),
    "email-malformed": ("data", "Email address does not parse as an address at all"),
    "email-role-address": ("data", "Email is a role/shared inbox (info@, sales@), not a person"),
    "email-freemail": (
        "data",
        "Recipient's email is a free consumer domain (gmail.com etc.), not a work address",
    ),
    "title-headline": (
        "data",
        "Job title field holds a pipe/bullet-delimited headline, not a title",
    ),
    "title-bio-fragment": (
        "data",
        "Job title carries a bio fragment ('12+ years...') rather than a title",
    ),
    # Promoted out of `UNCATALOGUED_RULES` 2026-09-24. `check_row` raises all four, and until
    # the merge nothing walked `check_row` for rule ids, so `--list-rules` under-reported the
    # gate by exactly these. They are row-data rules by every reading of PRD §3A and are KEPT.
    "score-not-numeric": (
        "data",
        "The row's GTM score is not a number — a scale comparison downstream will silently "
        "compare a string",
    ),
    "score-out-of-range": (
        "data",
        "The row's GTM score is outside the scale it is recorded on, so the tier derived "
        "from it is meaningless",
    ),
    "segment-noncanonical": (
        "data",
        "The row's segment is spelled in a form the segment grid does not use, so every "
        "segment-scoped check silently skips this row",
    ),
    "segment-unknown": (
        "data",
        "The row's segment is not one this profile defines — the row belongs to no grid",
    ),
    "possessive-sibilant": ("grammar", "{{Company}}'s produces a double sibilant"),
    "article-collision": ("grammar", "'the {{Company}}' doubles the article"),
    "subject-length": ("deliverability", "Subject is too long for the inbox preview"),
    "subject-lowercase": ("deliverability", "Subject casing looks automated"),
    "subject-placeholder": ("deliverability", "Subject carries a merge tag or bracket placeholder"),
    "no-links": ("deliverability", "First touch carries a URL — first touch must be link-free"),
    "em-dash": (
        "deliverability",
        "Copy contains an em dash — banned outright, not just above a density threshold",
    ),
    "sign-off": ("brand", "Sign-off is missing or not the expected name"),
    "banned-word": ("brand", "Copy uses a phrase this profile has banned"),
    "banned-stem": ("brand", "Copy uses a banned word stem"),
    "named-case-study": ("brand", "Copy names one of our own case-study companies"),
    "time-ask": (
        "brand",
        "CTA asks for a specific block of time (a call, a meeting) instead of offering an artifact",
    ),
    "same-company-identical-copy": ("dedupe", "Two people at one company get identical bodies"),
    "email-domain-mismatch": (
        "risk",
        "Recipient's email domain is a different organisation than the copy describes — a job-change artefact",
    ),
    "company-allcaps": ("cosmetic", "Company renders in all caps"),
    # Stays WARN. Promotion to ERROR was requested 2026-08-23 after the operator flagged a
    # ragged line on an eval sheet as a "formatting error", and was REFUSED on evidence:
    #   * it does not detect what was flagged — the flagged row's company was 28 chars, under
    #     this rule's 32 threshold, and the longest ragged line on that list came from a
    #     22-char company. The rule and the defect are barely correlated;
    #   * the defect is not real at the recipient. The live Saleshandy variant payload stores
    #     each paragraph as ONE unbroken line (`\n\n` between paragraphs, no mid-paragraph
    #     wrap), so the spec's ~90-char authoring wrap is de-wrapped at staging and never
    #     ships. The raggedness exists only in the linter's and the labeling sheet's render.
    # Promoting it would therefore have blocked a sendable row for a defect the reader never
    # sees. The real fix was to the review surface, not the gate. Left WARN as a cheap
    # cosmetic signal for the human reading a spec, with its rationale corrected below.
    "company-long": (
        "cosmetic",
        "Company name is long enough to wrap awkwardly in a spec's hard-wrapped source — a "
        "reviewing-a-draft signal only; staging de-wraps, so this never reaches a recipient",
    ),
    "company-legal-suffix": (
        "cosmetic",
        "Company carries a legal suffix (Inc, Pte Ltd) that reads stiff inline",
    ),
    "company-parenthetical": (
        "cosmetic",
        "Company carries a parenthetical that reads badly inline",
    ),
    "first-name-initial": ("cosmetic", "First name is a bare initial"),
}

#: Rule ids the gate CAN raise that ``RULE_CATALOGUE`` does not describe.
#:
#: `RULE_CATALOGUE` is the MUTATION-COVERED set (one `test_<rule>` per entry, enforced), so
#: it under-reported what the gate emits — which is how a PRD counted the rule surface from
#: it and got it wrong. Keeping the two facts apart fixes the count without weakening the
#: mutation contract, and `test_list_rules_count_equals_what_the_linter_can_raise` re-derives
#: the emitted set from the AST so neither can drift. Shrinking this tuple, by writing the
#: mutation and promoting the id, is the wanted direction — four were promoted on 2026-09-24.
#:
#: Every id left here is a PACK-DOCUMENT diagnostic or a batch-duplication rule reachable only
#: from the pack entry points, and each one has a discriminating pair in
#: `test_outreach_pack_linter.py` (`test_kept_pack_rules_each_have_a_discriminating_pair`).
UNCATALOGUED_RULES: tuple[str, ...] = (
    # Pack-format / staleness diagnostics — they judge the pack document, not the copy.
    "attachment-on-touch1",
    "channel-order",
    "duplicate-to",
    "empty",
    "not-an-outreach-pack",
    "parse",
    "rules-version-missing",
    "rules-version-stale",
    "touch-count",
    "word-count-mismatch",
    # Batch-duplication rules, catalogued only on the render path today.
    "body-template-share",
    "same-company-overlap",
    "same-company-subject",
    "subject-template-share",
    "template-share",
)

#: Every rule id this linter can raise. What ``--list-rules`` prints.
ALL_RULE_IDS: tuple[str, ...] = tuple(sorted(set(RULE_CATALOGUE) | set(UNCATALOGUED_RULES)))
