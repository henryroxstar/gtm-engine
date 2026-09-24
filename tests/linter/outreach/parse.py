"""Parsers, the merge-tag renderer, and the profile-file loaders — one home for
"turn bytes into a pack or a spec", so no rule module re-derives a shape."""

from __future__ import annotations

import re
from pathlib import Path

from .model import EmailBlock, PackMeta, Touch
from .text import ROLE_INBOX_SENTINEL, UNRESOLVED_SENTINEL

_BLOCK_RE = re.compile(
    r"^###\s+(?P<idx>\d+)\.\s+(?P<header>.+?)\s*\n"
    r"\*\*To:\*\*\s*(?P<to>\S+)\s*\n"
    r"\*\*Subject:\*\*\s*(?P<subject>.+?)\s*\n"
    r"(?P<body>.*?)(?=\n---\s*\n|\n###\s+\d+\.|\Z)",
    re.DOTALL | re.MULTILINE,
)


def parse_pack(text: str) -> tuple[str | None, list[EmailBlock]]:
    """Return (rules_version, blocks)."""
    mv = re.search(r"^Rules-Version:\s*(\S+)", text, re.MULTILINE)
    version = mv.group(1) if mv else None
    blocks: list[EmailBlock] = []
    for m in _BLOCK_RE.finditer(text):
        header = m.group("header").strip()
        first = header.split()[0] if header.split() else ""
        company = header.split(",")[-1].strip() if "," in header else ""
        blocks.append(
            EmailBlock(
                index=int(m.group("idx")),
                header=header,
                first=first,
                company=company,
                to=m.group("to").strip().lower(),
                subject=m.group("subject").strip(),
                body=m.group("body").strip(),
            )
        )
    return version, blocks


# Two written forms of the same ladder line are in the corpus and both are correct markdown:
#     **Touch 2 — Day 2 — email:** ...
#     **Touch 2 (Day 2, email):** ...
# Matching only the dash form made every pack written in the paren form parse as ZERO touches,
# so `touch-count` and `channel-order` were inert on them — a silent blind spot rather than a
# reported one, which is the same shape as the `## Email (touch 1)` heading fix above.
_TOUCH_RE = re.compile(
    r"^[-*\s]*\*\*Touch\s+(?P<n>\d+)\s*(?:[—–-]\s*Day\s+(?P<day>\d+)\s*[—–-]\s*"
    r"|\(\s*Day\s+(?P<day2>\d+)\s*,\s*)(?P<channel>\w+)",
    re.MULTILINE,
)
# `## Sequence: 4 touches / 2 channels over ~12 days` and `## Sequence — 4 touches` both
# declare the same count; so does `## Sequence: manual, 2 touches — NOT sequencer-eligible`.
_SEQ_HEADER_RE = re.compile(r"^##\s*Sequence\s*[:—–-][^\n]*?(?P<n>\d+)\s+touches", re.MULTILINE)
_STATED_WORDS_RES = (
    re.compile(r"\*\*Word count:\*\*\s*~?\s*(\d+)"),
    re.compile(r"\((\d+)\s*words?\)"),
)

# The touch-1 heading is written several ways across the corpus:
#   ## Email (touch 1)
#   ## Email (touch 1, to Jordan Vance)        <- supplement packs name the recipient
#   ## Email (touch 1) — formal/standard register  <- register annotation after the paren
# Matching the bare literal `## Email (touch 1)` made two real packs unparseable — and therefore
# silently UNLINTED, reported only as a `parse` error that read like a malformed file rather than
# a linter blind spot. Both the in-paren suffix and any trailing annotation must be tolerated;
# anchoring the end of the line instead was a stricter rule than the literal it replaced.
_EMAIL_HEADING_RE = re.compile(r"^##[ \t]*Email[ \t]*\(touch[ \t]*1[^)\n]*\)[^\n]*$", re.MULTILINE)


def _strip_quote(text: str) -> str:
    """Drop one level of markdown blockquote markers."""
    return "\n".join(re.sub(r"^\s*>\s?", "", ln) for ln in text.splitlines()).strip()


def _quoted_body(text: str) -> str:
    """The contiguous blockquote that is the email itself.

    Stops at the first non-quoted line so trailing annotations — `(88 words)`,
    `**Word count:** 90 / 100` — stay out of the body they are reporting on.
    """
    lines = text.splitlines()
    out: list[str] = []
    started = False
    for ln in lines:
        if ln.lstrip().startswith(">"):
            started = True
            out.append(ln)
        elif started and not ln.strip():
            out.append("")
        elif started:
            break
    return _strip_quote("\n".join(out))


def _strip_meta_lines(body: str) -> str:
    """Drop drafter annotations that trail the body but are not part of the sent email."""
    body = re.split(r"\n\s*\*\*Word count:\*\*", body)[0]
    return re.sub(r"\n\s*\(\s*\d+\s*(?:words?|characters?)\s*\)\s*$", "", body).strip()


# Honorifics are not first names: "Dr Meilin Zhao" greets Meilin, not "Dr".
_HONORIFICS = {"dr", "mr", "mrs", "ms", "mx", "prof", "professor", "sir", "dame"}


def _first_name(raw: str) -> str:
    """First name from a persona/recipient field, or the sentinel when unresolved."""
    raw = raw.strip()
    if UNRESOLVED_SENTINEL in raw:
        return UNRESOLVED_SENTINEL
    if ROLE_INBOX_SENTINEL in raw:
        return ROLE_INBOX_SENTINEL
    head = re.split(r"\s*[—–,|(]", raw, maxsplit=1)[0].strip()
    toks = head.split()
    while toks and toks[0].lower().rstrip(".") in _HONORIFICS:
        toks.pop(0)
    return toks[0] if toks else ""


def parse_ladder(text: str) -> list[tuple[int, int, str]]:
    """``(touch number, day, channel)`` for every touch a pack enumerates, in touch order.

    The one home for reading a pack's ladder. The status page needs the *days* to say when a
    hand-sent 1:1 arc actually finishes, and a second regex for that would be a second answer
    to "how many emails does this pack send, and over how long" — the failure the pack/spec
    parsers are deliberately shared to avoid.
    """
    out = [
        (int(m.group("n")), int(m.group("day") or m.group("day2") or 0), m.group("channel"))
        for m in _TOUCH_RE.finditer(text)
    ]
    out.sort(key=lambda p: p[0])
    return out


def _parse_sequence(text: str) -> tuple[int | None, int, tuple[str, ...]]:
    """(declared touches, enumerated touches, channel per touch in order)."""
    m = _SEQ_HEADER_RE.search(text)
    declared = int(m.group("n")) if m else None
    touches = parse_ladder(text)
    return declared, len(touches), tuple(c for _, _d, c in touches)


def _stated_words(text: str) -> int | None:
    for rx in _STATED_WORDS_RES:
        m = rx.search(text)
        if m:
            return int(m.group(1))
    return None


def parse_draft_outreach_pack(text: str) -> tuple[str | None, list[EmailBlock], PackMeta]:
    """Parse the single-email `email-<account>-<date>.md` shape (draft-outreach output).

        # Cold email — Acme — 2026-07-03
        - **To:** Dana Rivera, Head of Platform — jordan@meridians.example
        - **Why-now:** ...
        ---
        **Subject:** agent audit trail
        Hi Dana,
        ...
        Alex
        ---
        **Word count:** 85 · **Voice check:** ...

    Only touch 1 is linted; the `## Follow-up` section below it is a later touch and the
    first-touch rules (no attachment, no time-ask) do not apply to it.
    """
    version = (lambda m: m.group(1) if m else None)(
        re.search(r"^Rules-Version:\s*(\S+)", text, re.MULTILINE)
    )
    head = text.split("**Subject:**", 1)[0]
    to_raw = (lambda m: m.group(1) if m else "")(re.search(r"\*\*To:\*\*\s*(.+)", head))
    attach = (lambda m: m.group(1).strip() if m else None)(
        re.search(r"\*\*(?:Attach|Attachment|Gift artifact):\*\*\s*(.+)", head)
    )
    company = (lambda m: m.group(1).strip() if m else "")(
        re.search(
            r"^#\s*Cold email\s*[—–-]\s*(.+?)\s*[—–-]\s*\d{4}-\d{2}-\d{2}", text, re.MULTILINE
        )
    )
    email = (lambda m: m.group(0).lower() if m else "")(
        re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", to_raw)
    )

    m = re.search(
        r"\*\*Subject:\*\*\s*(?P<subject>.+?)\s*\n(?P<body>.*?)(?=\n---|\Z)", text, re.DOTALL
    )
    blocks: list[EmailBlock] = []
    if m:
        blocks.append(
            EmailBlock(
                index=1,
                header=to_raw.strip(),
                first=_first_name(to_raw),
                company=company,
                to=email or company.lower(),
                subject=m.group("subject").strip(),
                body=_strip_meta_lines(_strip_quote(m.group("body"))),
            )
        )
    meta = PackMeta(
        fmt="draft-outreach",
        label=blocks[0].label if blocks else "PACK",
        attach=attach,
        stated_words=_stated_words(text.split("## Follow-up", 1)[0]),
    )
    return version, blocks, meta


#: The Tier-A pack heading, in BOTH shapes it has been authored in: `# Outreach Pack — Acme
#: — 2026-07-19` (the template's) and `# Outreach Pack: Acme (2026-09-04)`. A parser that
#: accepts only one does not fail — it yields an empty company, which reaches the judge as
#: missing context rather than as an error. On 2026-09-04 all six packs of one campaign
#: parsed with `company == ""` for exactly that reason.
_PACK_HEADING_RE = re.compile(
    r"^#\s*Outreach Pack\s*[—–:-]\s*(?P<company>.+?)\s*(?:[—–-]|\()\s*\d{4}-\d{2}-\d{2}\)?\s*$",
    re.MULTILINE,
)


def parse_prospect_pack(text: str) -> tuple[str | None, list[EmailBlock], PackMeta]:
    """Parse the `prospects-<date>-outreach-<account>.md` shape (prospect skill Tier-A pack).

    # Outreach Pack — Acme — 2026-07-19
    **Tier:** A | **Score:** 6/12 | ...
    **Primary persona:** Dana Rivera — Head of Platform
    **Gift artifact:** teaser one-pager
    ## LinkedIn DM (≤ 280 chars)
    > ...
    ## Email (touch 1)
    **Subject:** agent audit trail
    > Hi Dana,
    > ...
    > Alex
    (88 words)
    ## Sequence — 4 touches / 2 channels over ~12 days
    - **Touch 1 — Day 0 — LinkedIn:** ...
    """
    version = (lambda m: m.group(1) if m else None)(
        re.search(r"^Rules-Version:\s*(\S+)", text, re.MULTILINE)
    )
    # Supplement packs write `**Persona:**`; without the fallback they parse with an empty name
    # and the greeting check silently passes on nothing.
    persona = (lambda m: m.group(1) if m else "")(
        re.search(r"\*\*(?:Primary persona|Persona):\*\*\s*(.+)", text)
    )
    company = (lambda m: m.group("company").strip() if m else "")(_PACK_HEADING_RE.search(text))
    # The sendable address, as a FIELD rather than prose. Without one the judge's record has
    # no join key and `to` falls back to the company slug, which is a folder name, not a
    # recipient. Read only from the front block (above the first `## `) so a mailbox quoted
    # in a later notes section can never be mistaken for the addressee.
    front = text.split("\n## ", 1)[0]
    contact_raw = (lambda m: m.group(1) if m else front)(
        re.search(r"\*\*Contact:\*\*\s*(.+)", front)
    )
    contact = (lambda m: m.group(0).lower() if m else "")(
        re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", contact_raw)
    )
    # NOTE: `**Gift artifact:**` is deliberately NOT read as an attachment. In this template it
    # specifies the asset touch 1 *offers* and touch 2 delivers — describing it is the rule being
    # followed. Only a literal `Attach:` field (the draft-outreach shape) ships a payload.
    section = _EMAIL_HEADING_RE.split(text, maxsplit=1)
    blocks: list[EmailBlock] = []
    stated = None
    if len(section) == 2:
        after = section[1].split("\n## ", 1)[0]
        m = re.search(
            r"\*\*Subject:\*\*\s*(?P<subject>.+?)\s*\n(?P<body>.*?)(?=\n---|\Z)", after, re.DOTALL
        )
        if m:
            blocks.append(
                EmailBlock(
                    index=1,
                    header=persona.strip(),
                    first=_first_name(persona),
                    company=company,
                    to=contact or company.lower(),
                    subject=m.group("subject").strip(),
                    body=_quoted_body(m.group("body")),
                )
            )
        stated = _stated_words(after)

    declared, enumerated, channels = _parse_sequence(text)
    meta = PackMeta(
        fmt="prospect-pack",
        label=blocks[0].label if blocks else "PACK",
        stated_words=stated,
        declared_touches=declared,
        enumerated_touches=enumerated,
        touch_channels=channels,
        has_linkedin_dm="## LinkedIn DM" in text,
    )
    return version, blocks, meta


def parse_followup(text: str) -> tuple[str, str] | None:
    """(subject, body) of a `## Follow-up` section, when the pack carries one.

    Follow-ups are not linted as first touches — different rules apply (no greeting, it is a
    reply). But they are still text that ships, and the 2026-07-03 batch reused one follow-up
    skeleton across 17 files while every touch-1 body was already differentiated. A batch check
    that reads only touch 1 cannot see that.
    """
    m = re.search(r"##\s*Follow-up[^\n]*\n(.*)", text, re.DOTALL)
    if not m:
        return None
    section = m.group(1)
    sm = re.search(r"\*\*Subject:\*\*\s*(.+?)\s*\n(.*)", section, re.DOTALL)
    if not sm:
        return None
    return sm.group(1).strip(), _strip_meta_lines(_strip_quote(sm.group(2))).strip()


def detect_format(text: str) -> str:
    if re.search(r"^###\s+\d+\.", text, re.MULTILINE):
        return "tier-a-manual"
    if _EMAIL_HEADING_RE.search(text):
        return "prospect-pack"
    return "draft-outreach"


# `load_capability_rules` and the `CapabilityRules` alias were deleted 2026-09-24 with
# `capability-unargued`, `offer-does-their-work` and `offer-not-a-solution-overview`, the three
# rules that read them. The tenant file they read, `knowledge/capability-argument.toml`, was
# deleted the same day once the zero-reader grep came back empty; its capability groups live on
# as the `group` field of `claims.toml`, read by `gtm_core.messaging.registry`.


def _load_bans(path: str | None) -> tuple[str, ...]:
    """Every non-comment line of a tenant ban file, lowercased — the WHOLE file, flat.

    **The §1 narrowing was refused, 2026-09-24, and this is the argument.** PRD §3A keeps
    `banned-word` but narrows it to "tenant `voice-bans.txt` §1 regulatory overclaim only".
    Implementing that as written would have been a silent regression in three ways, each
    checkable:

    1. **There is no §1 to narrow to.** `voice-bans.txt` carries prose separators (`# ---`) and
       no numbered sections at all. A "§1 only" reader would have had to invent the section
       boundary, and whatever it guessed would be the new ban list.
    2. **It would un-ban what FR0 had just banned.** FR0's third checkbox ("make the
       retirements *true*") added `happy to` and the rule-4 invitation phrases to the tenant's
       ban file precisely so this cross-tenant gate would refuse what the voice had retired.
       Those entries sit below the separators and are reachable ONLY because this loader is
       flat. Narrowing would have deleted an enforcement FR0 had just bought, with no test
       going red — which is the exact shape of failure the PRD is written against.
    3. **The test plan contradicts it.** §5B UAT still asks for "no retired phrase
       (`voice-bans.txt` §4/§4b/§5)". A narrowing that makes its own acceptance test
       unsatisfiable is not a narrowing.

    `voice-rules.toml` `[bans].error_sections = ["1"]` remains UNIMPLEMENTED and is now
    knowingly so. Implementing sections while keeping §4/§4b/§5 at ERROR — the only version
    that does not regress — would build a section parser whose only configuration is "all
    sections", i.e. machinery that changes nothing. The key belongs with the tenant's file, and
    trimming it is the tenant's edit to make (`profiles/` is not this task's to write).
    `test_the_ban_file_is_read_whole_not_by_section` is the regression that holds this.
    """
    if not path:
        return ()
    p = Path(path)
    if not p.exists():
        return ()
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line.lower())
    return tuple(out)


# Merge tags a provider field list is expected to expose. Keep in sync with
# Saleshandy's `list_fields` labels; override per-run with --fields.
DEFAULT_FIELD_LABELS = (
    "First Name",
    "Last Name",
    "Email",
    "Company",
    "Company Domain",
    "Company Website",
    "Company Industry",
    "Job Title",
    "Industry",
    "Department",
    "City",
    "State",
    "Country",
    "Phone Number",
    "LinkedIn",
    "Website",
    # Custom fields this repo's sequences rely on. A custom field must exist in the
    # provider before import; --fields with the live label list is the real check.
    "Why Now",
)

# CSV column each merge tag reads from. A tag with no mapping here can still be valid
# for the provider; it is reported as unrenderable-from-this-CSV instead.
TAG_TO_COLUMN = {
    "First Name": "first",
    "Last Name": "last",
    "Email": "email",
    "Company": "company",
    "Company Domain": "company_domain",
    "Job Title": "title",
    "City": "city",
    "Country": "country",
    # NOT why_now: the raw research column is a multi-fact operator note. Only the
    # reduced, fail-closed clause is safe to render.
    "Why Now": "signal_clause",
}

_STEP_RE = re.compile(
    r"^\*\*Step\s+(?P<n>\d+)\s*[—\-–]\s*Day\s+(?P<day>\d+)\*\*(?P<rest>.*?)$",
    re.MULTILINE,
)
_SUBJECT_RE = re.compile(r"Subject:\s*`(?P<subject>[^`]*)`")
_MERGE_TAG_RE = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")


def parse_spec(text: str) -> list[Touch]:
    """Pull the touches out of a sequence spec.

    A touch is a ``**Step N — Day D**`` header (optionally carrying ``Subject: `...` ``)
    followed by the body as a markdown blockquote. Everything outside a blockquote
    between headers is prose about the touch and is ignored.
    """
    touches: list[Touch] = []
    matches = list(_STEP_RE.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[m.end() : end]
        sm = _SUBJECT_RE.search(m.group("rest"))
        body_lines: list[str] = []
        started = False
        for line in block.splitlines():
            if line.startswith(">"):
                started = True
                body_lines.append(line[1:].removeprefix(" "))
            elif started and not line.strip():
                body_lines.append("")
            elif started:
                break
        body = "\n".join(body_lines).strip("\n")
        body = re.sub(r"\n{3,}", "\n\n", body)
        if body:
            touches.append(
                Touch(
                    number=int(m.group("n")),
                    day=int(m.group("day")),
                    subject=(sm.group("subject").strip() if sm else ""),
                    body=body,
                )
            )
    return touches


def render(template: str, row: dict) -> str:
    """Substitute every ``{{Tag}}`` from ``row`` via :data:`TAG_TO_COLUMN`.

    An unmapped tag is left in place so ``lint_email``'s placeholder rule reports it —
    an unresolved tag reaching a prospect is exactly the failure this linter exists for.
    """

    def sub(m: re.Match[str]) -> str:
        col = TAG_TO_COLUMN.get(m.group(1).strip())
        if col is None:
            return m.group(0)
        return (row.get(col) or "").strip()

    return _MERGE_TAG_RE.sub(sub, template)


def _load_premise_vocab(profile: str) -> dict:
    """Load the profile's premise vocabulary, or ``{}`` if it ships none.

    Deferred import: ``gtm_core.hook_coverage`` imports ``parse_spec`` from THIS module, so a
    top-level import would be a cycle — the same reason ``lint_hook_cell`` defers its own.
    """
    from gtm_core.hook_coverage import load_premise_vocab

    return load_premise_vocab(profile)


def _load_domain_aliases(profile: str) -> set:
    """The profile's declared parent/subsidiary domain pairs. Deferred import, same cycle
    reason as :func:`_load_premise_vocab`."""
    from gtm_core.account_integrity import load_domain_aliases

    return load_domain_aliases(profile)
