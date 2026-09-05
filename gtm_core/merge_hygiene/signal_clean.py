from __future__ import annotations

import re

from .names import _has_letters

# --- signal clause -------------------------------------------------------

# A `why_now` that is really an intent-topic score, not an event: "machine learning &
# artificial intelligence (intent score 81)". A topic score is a targeting input, never a
# thing to tell a prospect you noticed — quoting it back reads as surveillance and says
# nothing dated or specific.
EM_DASH = "\u2014"

_INTENT_LABEL_RE = re.compile(r"\(\s*intent score\s*\d+\s*\)\s*$", re.IGNORECASE)

# Research that explicitly records the ABSENCE of a signal, or one still unverified.
# "No dated funding round or launch event confirmed in research" is a note to ourselves;
# rendering it would open a cold email by telling the prospect we found nothing about them.
_NO_SIGNAL_RE = re.compile(
    r"\b(no dated|no verifiable|not verifiable|no confirmed|no funding|no signal|"
    r"none found|unconfirmed|to confirm|confirm at outreach|tbd|unknown)\b",
    re.IGNORECASE,
)

# The date research stamps on the end of a `why_now`. Freshness lives in the row's own
# `signal_observed` column, and the clause contract forbids a date in the opener — an
# opener states what happened, not when. Leaving it in is what `signal-stray-digit` fails,
# and it failed 41 of 41 send-verdict clauses on 2026-08-29 while every one of them was a
# real, well-sourced trigger. Dropping a trailing stamp is a REDUCTION (this function is
# allowed to drop trailing facts), never a rewording: the date is not lost, it is already
# recorded in the column that is checkable. Only a trailing stamp is taken — a mid-clause
# metric ("$27M Series A") stays, and those rows still fail the gate, correctly.
_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
_TRAILING_DATE_RE = re.compile(
    r"\s*[-–(\[]?\s*(?:(?:as of|since|dated|on)\s+)?"
    r"(?:\d{4}-\d{2}-\d{2}|\d{4}-\d{2}|"
    # "11 May 2026" and "May 11, 2026" — the DAY has to come off with the rest. Matching
    # only "May 2026" strips the month and year and leaves the day behind as a bare
    # integer ("Circle Agent Stack launched 11"), which reads as debris and still trips
    # `signal-stray-digit`. A half-stripped date is worse than an unstripped one.
    rf"\d{{1,2}}\s+{_MONTH}\s+\d{{4}}|"
    rf"{_MONTH}\s+\d{{1,2}},?\s+\d{{4}}|"
    rf"{_MONTH}\s+\d{{4}}|"
    r"\d{1,2}/\d{1,2}/\d{2,4}|"
    r"Q[1-4]\s+\d{4})\s*[)\]]?\s*[.;,]?\s*$",
    re.IGNORECASE,
)

# A trailing citation: " - businesswire.com", " - https://…". Provenance, and it is already
# recorded in the row's own `signal_source_url` column, so dropping it from the opener loses
# nothing and keeps a source URL out of the first line of a cold email. Verified 2026-08-29
# against six live rows: all six carried a populated `signal_source_url` AND `signal_observed`.
_TRAILING_SOURCE_RE = re.compile(
    r"\s*[-–|(\[]\s*(?:https?://\S+|(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/\S*)?)\s*[)\]]?\s*$",
    re.IGNORECASE,
)


def _drop_trailing_provenance(text: str) -> str:
    """Strip a trailing source citation and/or date stamp — both, in that order.

    Source first because it sits outermost ("eng signal 2026-06-10 - https://…"). Both are
    REDUCTIONS, which this module is allowed to do, and both facts survive in their own
    checkable columns. Anything left too short to open on is rejected downstream by the
    length gate, which is the correct outcome for a value that was only ever a research note.
    """
    out = _TRAILING_SOURCE_RE.sub("", text or "").strip(" ;,.-")
    return _TRAILING_DATE_RE.sub("", out).strip(" ;,.-")


# Top-level joins between independent facts. A `why_now` packs several facts for the
# operator; an email opens on ONE. Split only outside parentheses so a date range or an
# investor list inside brackets is never cut in half.
_SEGMENT_JOINS = (" + ", " — ", "; ", " then ", ", as ", ", and now ", ", on ")

SIGNAL_MIN_CHARS, SIGNAL_MAX_CHARS = 12, 110
# A segment shorter than this is usually a bare product name ("MediPath Fusion") with the
# actual news in the NEXT segment. Prefer the first substantive segment over the first one.
SIGNAL_SUBSTANCE_CHARS = 25


def _split_top_level(text: str) -> list[str]:
    """Split on :data:`_SEGMENT_JOINS`, ignoring any join inside parentheses."""
    parts: list[str] = []
    depth = 0
    current = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if depth == 0:
            hit = next((j for j in _SEGMENT_JOINS if text.startswith(j, i)), None)
            if hit:
                parts.append(current)
                current = ""
                i += len(hit)
                continue
        current += ch
        i += 1
    parts.append(current)
    return [p.strip() for p in parts if p.strip()]


def signal_clause(why_now: str) -> str:
    """Reduce a research ``why_now`` to a clause safe to render in a cold email, or ``""``.

    Returns the empty string whenever the value cannot open an email — an intent-topic
    score, a note that no signal was found, an unverified marker, a truncated source, or
    anything that will not fit. **Fail-closed on purpose:** a row with no usable clause
    belongs in the generic sequence, and sending a mangled or absent "why now" is worse
    than sending none.

    The clause is a VERBATIM span of the research, never a paraphrase. Reducing is allowed
    to drop trailing facts; rewording is not, because a reworded claim is a new claim about
    a real company that nobody verified. A trailing date stamp is one of those trailing
    facts — see :data:`_TRAILING_DATE_RE`.
    """
    original = re.sub(r"\s+", " ", (why_now or "").strip())
    if not original:
        return ""
    if _INTENT_LABEL_RE.search(original) or _NO_SIGNAL_RE.search(original):
        return ""
    raw = _drop_trailing_provenance(original)
    if not raw:
        return ""

    def _fits(s: str) -> bool:
        return SIGNAL_MIN_CHARS <= len(s) <= SIGNAL_MAX_CHARS

    # Keep the whole value when it already fits — splitting a value that needed no split
    # is how "MediPath Fusion, agentic AI-powered RCM platform unveiled at HIMSS" gets
    # cut down to a bare product name with the actual news thrown away.
    clause = raw.strip(" ;,.")
    if not _fits(clause):
        segments = [s.strip(" ;,.") for s in _split_top_level(raw)]
        lead = segments[0]
        second = segments[1] if len(segments) > 1 else ""
        if len(lead) < SIGNAL_SUBSTANCE_CHARS and _fits(second):
            # A short lead is a bare product name ("MediPath Fusion") and the actual news
            # is the next segment.
            clause = second
        elif _fits(lead):
            clause = lead
        else:
            # The lead fact is merely verbose. Trim a trailing parenthetical citation to
            # make it fit — but never fall through to a later segment, which is
            # elaboration and yields a mid-sentence fragment ("scaling LuLu").
            clause = re.sub(r"\s*\([^()]*\)\s*$", "", lead).strip(" ;,.")

    # Again after segment selection: a stamp can sit at the end of the chosen segment
    # rather than at the end of the whole value.
    clause = _drop_trailing_provenance(clause)

    if not _fits(clause):
        return ""
    # An em dash is banned in outreach copy, and a retained segment should never carry one.
    if EM_DASH in clause:
        return ""
    if clause.count("(") != clause.count(")") or clause.count('"') % 2:
        return ""
    if not _has_letters(clause):
        return ""
    # A source cut off mid-thought: no terminal punctuation, no closing bracket, and the
    # reduction changed nothing, so there was no complete lead fact to take. Compared
    # against the value as research wrote it, not against the date-stripped one — dropping
    # a stamp IS a reduction, and measuring against its own output would read every
    # date-stamped fact as untouched and then judge the letter the stamp used to follow.
    if clause == original and not re.search(r"[.)\"'\d]$|[a-z]$", original):
        return ""
    return clause
