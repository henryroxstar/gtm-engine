"""Per-email copy rules: ``lint_email`` and the argument rules it calls — deliverability,
merge hygiene, voice bans, readability and seat lead. Everything here judges ONE body;
batch-scoped rules live in ``rules_batch``.

**What left on 2026-09-24 (outbound fact registry, FR3).** This module used to hold the
argument gate as well: `hedge-missing`, `credit-is-verdict`, six `cta-*` shape rules,
`specificity`, `antithesis`, the two `offer-*` rules, `capability-unargued`, both
`seat-stakes-*` rules and `problem-asserts-internals`. Every one of them asked a regex whether
a sentence was *persuasive*. That question now belongs to the quality card and the judge; what
a body may ASSERT is checked in ``rules_derivation`` against the fact registry instead. The
retired-id → card-question map lives in ``model.py`` — nothing was dropped silently.
"""

from __future__ import annotations

import re

from .model import EmailBlock, Violation
from .roles import _load_vocabulary, seat_of
from .text import (
    _NAME_RE,
    ROLE_INBOX_SENTINEL,
    UNRESOLVED_SENTINEL,
    _sentences,
)

# Under 100 words, hard. The old 110 ceiling predates the six-beat shape and let drafts sprawl to
# 104 once stakes and frontier beats were added; the fix is denser beats, not a wider ceiling.
WORDS_HARD_MIN, WORDS_HARD_MAX = 50, 99
# Soft band widened 2026-08-21 from 70-95. Measured against the copy the band is meant to
# describe: the H4 rewrite pins touch 1 at base + clause under a hard cap of 99, and the four
# re-cut specs render 69-96. The old band therefore fired at BOTH ends by exactly one word —
# 26 exec + 48 builder T3 renders at 69, three security T1 renders at 96 — none of which is a
# defect anyone would act on. A band that disagrees with the copy it governs is the band's bug,
# and every one of those 77 warnings was budget spent on nothing. 65 keeps a real floor (a
# truncated 30-word body still fires). The ceiling stays strictly BELOW WORDS_HARD_MAX on
# purpose: setting them equal would empty the warning shoulder entirely, so a body would go
# from "fine" to ERROR with no "approaching the cap" step — silently deleting a signal rather
# than recalibrating it. 97 clears the observed 96 and leaves 98-99 as that shoulder.
WORDS_SOFT_MIN, WORDS_SOFT_MAX = 65, 97

# Sentence-level readability. Boomerang's 5.3M-message analysis found third-grade-level copy
# replied ~36% better than college-level (~17% better than high-school), and Lavender scores cold
# openers to grade 3-5. Neither is gated here as a Flesch-Kincaid score, and that omission is
# deliberate rather than lazy: FK's syllable term is dominated by this ICP's unavoidable
# vocabulary (attribution, authorisation, compliance) and — worse for a MERGE linter — by the
# rendered company name, so one template would score a different grade row by row for reasons no
# writer can act on. Sentence length is the half of the formula the writer controls, so it is the
# half that is gated. Evidence: docs/cold-email-craft-evidence.md 5.5.
#
# Calibrated 2026-08-26 against 103 real first touches parsed out of content/ (40-130 words):
# mean sentence length p50 18.8 / p75 20.8 / p90 24.2 / max 31.7; longest sentence p50 33 /
# p75 37 / p90 39 / max 61. The corpus runs LONG — a 33-word sentence inside a 95-word body is a
# third of the email in one breath. Thresholds catch the TAIL, not the ideal: setting them at the
# evidence-ideal (mean ~14, longest ~25) would fail ~95% of existing copy, and a gate nobody can
# ship through has the same effect as a gate that never ran.
#
# The thresholds are ALSO set clear of merge-driven noise, which is what decided both numbers.
# Measured across 69 real touch templates rendered against a live CSV: the merged fields move a
# body's MEAN sentence length by 0.0-2.2 words row to row and its LONGEST by 0-6, purely from how
# long the company name and signal clause happen to be. A threshold inside that band fires on a
# subset of rows for a reason the writer cannot act on — the exact failure the WORDS_SOFT comment
# above documents (77 warnings spent on nothing). The observed data has a natural gap between a
# 39-word longest and the next template at 42, so:
#
#   HARD_MAX 40   sits in that gap. Fires on 8% of packs and on 2 of 69 templates (longest 42-45
#                 and 47-50) — both on EVERY row, so they collapse to one template-wide line.
#                 This is the live rule, and its message is directly actionable.
#   SOFT_MEAN 26  clears the entire observed template range (max mean 24.0) plus the maximum
#                 observed spread. It therefore fires on NOTHING today and is a regression guard,
#                 not a catch — stated plainly rather than implied to be finding something. It
#                 earns its place by covering a failure HARD_MAX cannot see: uniformly long-winded
#                 prose with no single wall. An earlier draft put it at 22, which straddled three
#                 live templates (21.8-22.5, 22.5-23.2, 22.5-24.0) and was pure noise.
#
# Ratchet both down as the corpus improves; the p-values above are the baseline to measure against.
SENTENCE_HARD_MAX = 40
SENTENCE_SOFT_MEAN = 26

# One CTA means one question. Boomerang put the useful range at 1-3 questions (~50% response
# lift); past that the reader must decide WHICH to answer. On the same 103-email corpus this
# ceiling is PROPHYLACTIC, not remedial: the observed distribution is 0 questions x5, 1 x90,
# 2 x8 — nothing reaches 3, so this rule catches no present defect and is registered honestly as
# a regression guard. It earns its place because the failure it prevents is silent: extra
# questions read as thoroughness while splitting the reply.
#
# It is the LAST surviving `cta-*`-adjacent rule after the 2026-09-24 retirement, and it survives
# because it counts something rather than judging something: `cta-question`, `cta-bundled`,
# `cta-unanchored` and `cta-omits-gap` each asked a regex whether an ask was any good.
QUESTIONS_MAX = 3
SUBJECT_MAX_WORDS = 4
#: The one correct opening for a role inbox — voice.md §5b, and draft-outreach's body template
#: ("use `Hi team,` for a role inbox"). NOT an exemption from `greeting`: the rule still asserts
#: an exact opening, it just knows which one this pack owes.
ROLE_INBOX_GREETING = "Hi team,"

EM_DASH = "—"
URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
SPINTAX_RE = re.compile(r"\{[^{}]*\|[^{}]*\}")
PLACEHOLDER_RE = re.compile(r"\[(INSERT|TBD|TODO|PLACEHOLDER|LINK|ONE LINK)", re.IGNORECASE)
#: Valedictions that sit above the bare first name. Part of the sign-off block, never the body.
VALEDICTIONS = frozenset({"regards", "best", "best regards", "thanks", "cheers", "kind regards"})
MERGE_TAG_RE = re.compile(r"\{\{[^}]+\}\}")

BANNED_WORDS = (
    "excited to",
    "thrilled to",
    "reach out",
    "touch base",
    "synergy",
    "circle back",
    "i hope this finds you well",
    "i came across your profile",
    "i'd love to connect",
    "delve",
    "seamless",
    "robust",
    "elevate",
    "pivotal",
    "foster",
)
# "leverage" only as a verb-ish use; crude but effective: flag "leverage " + noun-phrase
LEVERAGE_RE = re.compile(r"\bleverag(e|ing|es|ed)\b", re.IGNORECASE)

# Mail-merge "stems" (a pack's signature sentences — recurrence means the draft fell back
# into the skeleton) and case-study company names are TENANT-SPECIFIC. They are supplied at
# call time via --stem-file / --case-study-file (one entry per line, `#` comments allowed;
# see _load_bans), so the shipped defaults are EMPTY and the linter enforces neither until an
# operator provides their own — mirroring content_linter's tenant denylist. A case study must
# be cited by company TYPE ("a regulated-FI compliance team"), never the logo (an unknown name
# confuses more than it credits) — the named-case-study rule flags any file-listed name.
DEFAULT_BANNED_STEMS: tuple[str, ...] = ()
DEFAULT_CASE_STUDY_NAMES: tuple[str, ...] = ()

TIME_ASK_TOKENS = (
    "15 minutes",
    "20 minutes",
    "quick call",
    # A meeting ask wearing an adjective. "Worth a short call?" shipped in the 2026-07-19 run
    # because every listed token assumed the word "quick" or a calendar noun.
    "short call",
    "brief call",
    "intro call",
    "introductory call",
    "quick chat",
    "short chat",
    "hop on a call",
    "jump on a call",
    "calendly",
    "calendar link",
    "grab time",
    "find time",
    "book a",
    "schedule a",
    "meet next week",
    "chat this week",
    "minutes this week",
    "a call this",
)

# Word-boundary matched, not substring: a plain `t in low` check for "book a" matched
# inside "excess and surplus book and workbench..." (found 2026-08-12 on an insurer's
# row) because "book a" is literally a substring of "book and". `\bTOKEN\b`
# requires a non-word character (or string edge) on both sides, so "book a" no longer
# matches when the very next character is "n" — a real time-ask still matches, since
# it is followed by whitespace/punctuation, not glued to another word.
_TIME_ASK_RE = tuple((t, re.compile(r"\b" + re.escape(t) + r"\b")) for t in TIME_ASK_TOKENS)


def lint_persona_lead(b: EmailBlock, profile: str | None = None) -> list[Violation]:
    """Fail an email that leads on a different seat's pain than its recipient holds.

    Only fires when the seat is recognised AND the body carries security-seat stakes vocabulary
    AND carries none of its own seat's. An unrecognised title says nothing — silence is the safe
    default, exactly as with the city gazetteer in email_compliance.
    """
    vocab = _load_vocabulary(profile)
    seat = seat_of(b.header, profile)
    if seat is None or seat == "security":
        return []
    low = b.body.lower()
    borrowed = [
        t for t in vocab.security_only if re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", low)
    ]
    if not borrowed:
        return []
    own = vocab.stakes_for(seat)
    if any(re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", low) for w in own):
        return []
    return [
        Violation(
            "ERROR",
            b.label,
            "persona-lead-mismatch",
            f"{seat} seat led on security-seat pain ({', '.join(borrowed)}) with none of its own "
            f"— see voice.md persona-axis table",
        )
    ]


def lint_email(
    b: EmailBlock,
    extra_bans: tuple[str, ...] = (),
    signoff: str = "Alex",
    case_studies: tuple[str, ...] = DEFAULT_CASE_STUDY_NAMES,
    banned_stems: tuple[str, ...] = DEFAULT_BANNED_STEMS,
) -> list[Violation]:
    v: list[Violation] = []
    low = b.body.lower()
    plain = re.sub(r"\s+", " ", b.body)

    # An unresolved contact suppresses the NAME rules only — never the content rules. Returning
    # early here (the original shape) meant 105 of 390 packs in the 2026-07-19 run received a
    # single warning and skipped every check on the copy itself: word count, banned words,
    # persona lead. That is fail-open — the defects were still there, waiting to surface the
    # moment someone filled the name in, which is exactly when nobody re-lints.
    unresolved = b.first == UNRESOLVED_SENTINEL or UNRESOLVED_SENTINEL in b.body
    if unresolved:
        v.append(
            Violation(
                # ERROR since 2026-08-27. The message already said "before this pack can
                # send" and the rule below calls the sentinel the way to keep a pack
                # "blocked from sending" — but as a WARN it exited 0, so a pack still
                # carrying the sentinel in its greeting linted green and read as sendable.
                # A gate whose own text says "blocked" must actually block.
                "ERROR",
                b.label,
                "unresolved-contact",
                f"{UNRESOLVED_SENTINEL} in greeting — resolve the name before this pack can send",
            )
        )

    # Subject
    subj_words = b.subject.split()
    if not (1 <= len(subj_words) <= SUBJECT_MAX_WORDS):
        v.append(
            Violation("ERROR", b.label, "subject-length", f"{len(subj_words)} words: {b.subject!r}")
        )
    if b.subject != b.subject.lower():
        v.append(Violation("ERROR", b.label, "subject-lowercase", repr(b.subject)))
    if MERGE_TAG_RE.search(b.subject) or PLACEHOLDER_RE.search(b.subject):
        v.append(Violation("ERROR", b.label, "subject-placeholder", repr(b.subject)))

    # Greeting / sign-off. The greeting and name-shape rules are the only ones an unresolved
    # contact legitimately suppresses — the sentinel IS the correct greeting until a name lands.
    # A role inbox is the other half of that: not a name we are missing, but a name that does
    # not exist. It takes its own expected greeting rather than a suppression — the rule still
    # asserts an exact opening, so a borrowed name in a role-inbox pack is still an error.
    role_inbox = b.first == ROLE_INBOX_SENTINEL
    if unresolved:
        pass
    elif role_inbox and not b.body.startswith(ROLE_INBOX_GREETING):
        v.append(
            Violation(
                "ERROR",
                b.label,
                "greeting",
                f"role-inbox pack must start {ROLE_INBOX_GREETING!r} — there is no name to greet",
            )
        )
    elif role_inbox:
        pass
    elif not b.body.startswith(f"Hi {b.first},"):
        v.append(Violation("ERROR", b.label, "greeting", f"must start 'Hi {b.first},'"))
    # ...and the name itself must be a NAME. The check above compares the greeting against the
    # persona field it was derived from, so a broken persona validates its own broken greeting:
    # a field of "*name unconfirmed to publish*" yields first="*name" and "Hi *name," passes.
    # Thirteen packs in the 2026-07-19 run were one send away from "Hi *unresolved,". A gate that
    # reads only its own field is not a gate; this is the independent witness.
    elif b.first != UNRESOLVED_SENTINEL and not _NAME_RE.match(b.first):
        v.append(
            Violation(
                "ERROR",
                b.label,
                "greeting-not-a-name",
                f"greeting name {b.first!r} is not a person's name — resolve it, or write "
                f"{UNRESOLVED_SENTINEL} so the pack is blocked from sending",
            )
        )
    lines = [ln.strip() for ln in b.body.splitlines() if ln.strip()]
    # Hoisted above the sign-off check because two rules downstream need it: `sentence-length`
    # and `question-count` read all the sentences. The sign-off is excluded from both — a bare
    # name is not a sentence, and counting it drags the mean down by ~4 words on a 5-sentence
    # body. The sign-off is a BLOCK, not a line: "Regards" above the name is part of it.
    _sig = lines[:-1] if lines and lines[-1] == signoff else lines
    if _sig and _sig[-1].rstrip(",").strip().lower() in VALEDICTIONS:
        _sig = _sig[:-1]
    body_wo_signoff = "\n".join(_sig) if lines and lines[-1] == signoff else b.body
    sentences = _sentences(body_wo_signoff)
    if not lines or lines[-1] != signoff:
        v.append(Violation("ERROR", b.label, "sign-off", f"last line must be bare {signoff!r}"))

    # Length
    wc = len(plain.split())
    if not (WORDS_HARD_MIN <= wc <= WORDS_HARD_MAX):
        v.append(
            Violation(
                "ERROR",
                b.label,
                "word-count",
                f"{wc} words (hard {WORDS_HARD_MIN}-{WORDS_HARD_MAX})",
            )
        )
    elif not (WORDS_SOFT_MIN <= wc <= WORDS_SOFT_MAX):
        v.append(
            Violation(
                "WARN",
                b.label,
                "word-count",
                f"{wc} words (target {WORDS_SOFT_MIN}-{WORDS_SOFT_MAX})",
            )
        )

    # Readability. Word count says how long the email is; this says whether it can be SKIMMED,
    # which is the property that actually matters against a ~9-second executive read. Graded like
    # word-count: a hard cap on the worst single sentence, a soft cap on the mean.
    sent_lens = [len(x.split()) for x in sentences]
    if sent_lens:
        longest = max(sent_lens)
        mean_len = sum(sent_lens) / len(sent_lens)
        if longest > SENTENCE_HARD_MAX:
            v.append(
                Violation(
                    "ERROR",
                    b.label,
                    "sentence-length",
                    f"longest sentence is {longest} words (hard max {SENTENCE_HARD_MAX}) — "
                    f"{longest * 100 // max(wc, 1)}% of the body in one breath; split it",
                )
            )
        elif mean_len > SENTENCE_SOFT_MEAN:
            v.append(
                Violation(
                    "WARN",
                    b.label,
                    "sentence-length",
                    f"mean sentence {mean_len:.1f} words across {len(sent_lens)} "
                    f"(target under {SENTENCE_SOFT_MEAN}); longest {longest}",
                )
            )

    # Mechanical hygiene
    if URL_RE.search(b.body):
        v.append(Violation("ERROR", b.label, "no-links", "first touch is link-free"))
    if EM_DASH in b.body:
        v.append(Violation("ERROR", b.label, "em-dash", "em dash present"))
    if SPINTAX_RE.search(b.body):
        v.append(Violation("ERROR", b.label, "spintax", SPINTAX_RE.search(b.body).group(0)))
    if PLACEHOLDER_RE.search(b.body) or MERGE_TAG_RE.search(b.body):
        v.append(Violation("ERROR", b.label, "placeholder", "unresolved placeholder/merge tag"))

    # Voice bans. Read from the WHOLE tenant file, not a numbered section of it — see
    # `parse._load_bans` for why the PRD's "§1 regulatory overclaim only" narrowing was
    # refused rather than implemented.
    for ban in BANNED_WORDS + tuple(x.lower() for x in extra_bans):
        if ban and re.search(r"(?<!\w)" + re.escape(ban) + r"(?!\w)", low):
            v.append(Violation("ERROR", b.label, "banned-word", ban))
    if LEVERAGE_RE.search(b.body):
        v.append(Violation("ERROR", b.label, "banned-word", "leverage (verb)"))

    # Mail-merge stems
    for stem in banned_stems:
        if stem in low:
            v.append(Violation("ERROR", b.label, "banned-stem", stem))

    # Named case studies (proof by company type, never the logo)
    for name in case_studies:
        if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", low):
            v.append(
                Violation(
                    "ERROR",
                    b.label,
                    "named-case-study",
                    f"{name}: name the company type + outcome, not the case-study logo",
                )
            )

    # The ask must be answerable in one reply. `cta-question` (was the last sentence an offer?)
    # retired 2026-09-24; counting the questions did not, because it measures rather than judges.
    n_questions = sum(1 for x in sentences if x.endswith("?"))
    if n_questions > QUESTIONS_MAX:
        v.append(
            Violation(
                "ERROR",
                b.label,
                "question-count",
                f"{n_questions} questions (max {QUESTIONS_MAX}) — the ask has to be the only "
                f"thing they must decide",
            )
        )
    for t, pat in _TIME_ASK_RE:
        if pat.search(low):
            v.append(Violation("ERROR", b.label, "time-ask", t))

    # Persona lead pain: the seat sets what the email leads on (voice.md persona-axis table).
    v.extend(lint_persona_lead(b))

    return v
