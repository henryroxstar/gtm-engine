"""``promote`` / ``retire`` — the fifth key-scoped writer for ``profiles/``.

``profiles/`` is read-only at runtime with a small, enumerated set of key-scoped exceptions
(``docs/RULES.md``, "The writer set for ``profiles/``, stated once"). This is the fifth. It
changes **one** ``[[angle]]``'s ``status`` and appends **one** dated history comment inside
that same block. Nothing else in the file may move.

**Nothing here deletes, ever.** ``retire`` sets a status. A retired angle is the record of
what was tried and why it stopped; removing it invites the next session to re-derive it from
scratch and reach the same dead end, this time without the evidence.

**Why a text edit and not a serialiser.** Round-tripping the tenant's TOML through a writer
would also change the status — and would silently reformat every comment, blank line and key
ordering the tenant put there. The diff then reads as a reformat nobody reviews, which is how
a "key-scoped" writer quietly rewrites a file. So the one ``status = "..."`` line is located
by id and replaced **in place**, preserving even its own trailing comment, and the candidate
text is re-parsed and compared against the original before anything reaches disk: the edit
must yield the intended status *and* leave every other parsed value identical, or it raises
and the file is untouched. Same posture as :func:`gtm_core.brandkit.set_identity_value` and
:func:`gtm_core.funnel.record_actuals`.

**Why promotion needs evidence, and retirement does not.** ``live`` is what makes an angle
sendable, so the direction that starts traffic is the one that has to cite a measurement;
retiring only ever stops traffic, and a rule that made *stopping* harder than *starting*
would be the wrong way round.

**Why the evidence is numbers (§R5, PRD §4A).** A dashboard's per-angle outcome cell is
computed from provider stats and from reply text, and reply text is untrusted input. So the
cell is parsed into integers against the ledger's **own** count vocabulary — read from
:func:`gtm_core.outcomes.summarize`, never retyped here — and anything that is not such a
count is refused rather than coerced. What lands in the tenant's file is then *regenerated*
from those integers, so no byte an operator pasted crosses into ``profiles/``.

**Why a ``live`` angle on a non-verified claim is unrepresentable.** ``promote`` is the only
path to ``live``, and it refuses when the angle's claim is not ``verified``. A design-target
capability stated in an email as fact is indistinguishable, in an inbox, from a true one.

Stdlib only. This module opens no socket and makes no paid call (§R6).
"""

from __future__ import annotations

import re
import tomllib
from datetime import UTC, datetime
from pathlib import Path

from ..fsio import atomic_write_text
from ..outcomes import summarize
from ..paths import resolve_knowledge_file, resolve_profiles_root
from .registry import ANGLES_FILE, VERIFIED, load

#: The two statuses this writer can reach. ``draft`` is where an angle is mined; it is not a
#: destination, so there is no verb that returns one and no way to un-retire by accident.
LIVE = "live"
RETIRED = "retired"

#: Every history line starts with this, so a reader (and a test) can find them without
#: parsing TOML, and so they can never be mistaken for a key.
HISTORY_PREFIX = "# angle-status "

_ANGLE_HEADER_RE = re.compile(r"^\[\[angle\]\][ \t]*$", re.MULTILINE)
#: Any TOML section header — the end of the block we are editing.
_SECTION_HEADER_RE = re.compile(r"^\[\[?[^\]\n]+\]\]?[ \t]*$", re.MULTILINE)
#: A ``status`` line whose value is a simple single-line string. ``lead`` and ``tail`` are
#: captured rather than rebuilt so the tenant's spacing and their own trailing comment
#: survive the edit by construction — there is no code path that could drop them.
_STATUS_LINE_RE = re.compile(
    r"^(?P<lead>[ \t]*status[ \t]*=[ \t]*)"
    r"(?P<value>\"[^\"\n]*\"|'[^'\n]*')"
    r"(?P<tail>[ \t]*(?:#[^\n]*)?)$",
    re.MULTILINE,
)

#: One ``field=<count>`` token of a dashboard cell. Non-negative integers only: a rate is a
#: derived figure, not a count, and a minus sign is not a measurement of anything.
_EVIDENCE_TOKEN_RE = re.compile(r"^(?P<field>[a-z_]+)=(?P<count>\d+)$")
#: What separates two cells on the page — whitespace, the page's own middot, a comma or a pipe.
_EVIDENCE_SPLIT_RE = re.compile(r"[\s·,|]+")

#: Gate-marker delimiters, defanged before an untrusted token is echoed in a refusal. Same
#: treatment and same reason as :data:`gtm_core.messaging.cli._MARKER_DELIMS`: this message
#: reaches a run header, where markers are read.
_MARKER_DELIMS = {"⟦": "[", "⟧": "]"}


def _ledger_counts() -> tuple[str, ...]:
    """The ledger's count fields, in its own order, read from the aggregator that owns them.

    :func:`gtm_core.outcomes.summarize` is what the per-angle dashboard cells are computed
    from (``by_tag["angle:<id>"]``), so this is the renderer's vocabulary one rung upstream
    of the rendering. Deriving it means a counter the ledger gains is a counter this command
    accepts, rather than one it starts refusing.

    The derived **rates** fall out for free: ``_finalize`` leaves them ``None`` when there is
    no denominator, so they are not integers and never enter the set. That is asserted in
    ``tests/unit/test_messaging_angle_status.py`` rather than re-checked here — a second
    guard that cannot fire reads as a rule while being untestable.
    """
    totals = summarize([])["totals"]
    return tuple(
        name
        for name, value in totals.items()
        if isinstance(value, int) and not isinstance(value, bool)
    )


#: The count names an evidence cell may carry, in the order a history line renders them.
EVIDENCE_ORDER = _ledger_counts()
EVIDENCE_FIELDS = frozenset(EVIDENCE_ORDER)


class AngleStatusError(ValueError):
    """A status move that must not happen, stated as one line an operator can act on.

    Raised **before** anything is written, always. Every message names the file and the
    angle id, in the same shape :class:`gtm_core.messaging.registry.RegistryError` uses.
    """


def _echo(token: str) -> str:
    """One untrusted token, safe to put in a message: defanged and length-capped."""
    out = token[:40]
    for bad, safe in _MARKER_DELIMS.items():
        out = out.replace(bad, safe)
    return out


def parse_evidence(cell: str) -> dict[str, int]:
    """A dashboard outcome cell as integers, or :class:`AngleStatusError`.

    Refuses rather than coerces. The tempting alternative — the dashboard's own ``_i``,
    which returns 0 for anything it cannot read — would turn a pasted sentence into a
    confident "0 replies" and promote an angle on a cell nobody measured.
    """
    tokens = [t for t in _EVIDENCE_SPLIT_RE.split(cell.strip()) if t]
    if not tokens:
        raise AngleStatusError(
            "--evidence: empty — cite the angle's cell from the dashboard, e.g. `sent=42 replies=5`"
        )

    counts: dict[str, int] = {}
    for token in tokens:
        match = _EVIDENCE_TOKEN_RE.match(token)
        if match is None:
            raise AngleStatusError(
                f"--evidence: `{_echo(token)}` is not a count — a cell is `field=<whole number>` "
                "and nothing else, because its figures derive from reply text (§R5)"
            )
        field = match.group("field")
        if field not in EVIDENCE_FIELDS:
            raise AngleStatusError(
                f"--evidence: `{_echo(field)}` is not a ledger count — one of "
                f"{', '.join(EVIDENCE_ORDER)}"
            )
        counts[field] = int(match.group("count"))

    if not counts.get("sent"):
        raise AngleStatusError(
            "--evidence: no `sent` count — a cell with no denominator is not evidence, it is a "
            "numerator with nothing under it"
        )
    return {name: counts[name] for name in EVIDENCE_ORDER if name in counts}


def _reparse(text: str) -> dict:
    """``tomllib.loads``, behind a name of its own so the verify has a seam.

    A verify nobody can make fail on purpose is indistinguishable from no verify at all
    (§R18), and this one is the difference between a targeted edit that landed and one that
    missed. A test replaces this function to prove the refusal reaches the caller and the
    file stays byte-identical.

    Deliberately not wrapped in a ``try``: both callers have already had these exact bytes
    accepted by ``registry.load``, and the substitution this module makes is a closed-set
    word in quotes plus a comment line. If that produced invalid TOML it would be a bug
    here, not something an operator can fix — and a bug rendered as advice is a bug nobody
    files (``cli._OPERATOR_FIXABLE``).
    """
    return tomllib.loads(text)


def _fingerprint(doc: dict, index: int) -> tuple:
    """Everything the document says EXCEPT the one status this writer is allowed to move.

    Compared before and after, this is what makes "key-scoped" a verified property rather
    than a claim about the regex: any other value the edit disturbed shows up here.
    """
    angles = doc.get("angle") or []
    rest = {key: value for key, value in doc.items() if key != "angle"}
    stripped = [
        {k: v for k, v in block.items() if not (i == index and k == "status")}
        for i, block in enumerate(angles)
    ]
    return rest, stripped


def _history_line(old: str, new: str, counts: dict[str, int] | None, date: str) -> str:
    """The dated audit line. Regenerated from integers — never the operator's own string."""
    line = f"{HISTORY_PREFIX}{date} {old} -> {new}"
    if counts:
        line += "  evidence: " + " ".join(f"{k}={v}" for k, v in counts.items())
    return line


def _rewrite(text: str, angle_id: str, new_status: str, history: str) -> str:
    """``text`` with exactly one angle's status moved and one history line added.

    Raises rather than returning a best effort. Every refusal here is a case where the
    writer cannot prove which bytes it would be changing.
    """
    doc = _reparse(text)
    blocks = doc.get("angle") or []
    headers = list(_ANGLE_HEADER_RE.finditer(text))
    if len(headers) != len(blocks):
        raise AngleStatusError(
            f"{ANGLES_FILE}: {len(headers)} `[[angle]]` header line(s) for {len(blocks)} parsed "
            "block(s) — this writer edits one line in place and will not guess which"
        )

    matches = [
        i for i, block in enumerate(blocks) if str(block.get("id", "")).strip().lower() == angle_id
    ]
    if len(matches) != 1:
        raise AngleStatusError(
            f"{ANGLES_FILE}: {angle_id} — {len(matches)} blocks carry this id; this writer "
            "edits exactly one"
        )
    index = matches[0]

    body_start = headers[index].end() + 1
    following = _SECTION_HEADER_RE.search(text, body_start)
    body_end = following.start() if following else len(text)
    line = _STATUS_LINE_RE.search(text, body_start, body_end)
    if line is None:
        raise AngleStatusError(
            f'{ANGLES_FILE}: {angle_id} — no simple `status = "..."` line to edit in this block'
        )

    replacement = f'{line.group("lead")}"{new_status}"{line.group("tail")}'
    new_text = text[: line.start()] + replacement + "\n" + history + text[line.end() :]

    # Verify BEFORE writing. A bad edit must leave the file exactly as it was.
    updated = _reparse(new_text)
    new_blocks = updated.get("angle") or []
    if len(new_blocks) != len(blocks) or new_blocks[index].get("status") != new_status:
        raise AngleStatusError(
            f"{ANGLES_FILE}: {angle_id} — post-write verify failed (the file is unchanged): "
            f"the edit did not read back as `{new_status}`"
        )
    if _fingerprint(updated, index) != _fingerprint(doc, index):
        raise AngleStatusError(
            f"{ANGLES_FILE}: {angle_id} — post-write verify failed (the file is unchanged): "
            "the edit changed something other than this angle's `status`"
        )
    return new_text


def _move(
    profile: str,
    *,
    angle_id: str,
    target: str,
    evidence: str | None,
    profiles_root: Path | None,
    product: str | None,
    now: str | None,
) -> Path:
    """The one code path that writes ``angles.toml``. Returns the path it wrote.

    No ``overlay`` argument, and that absence is the point: an experiment overlay lasts one
    run by definition (CLAUDE.md), so a promotion decided under one has nowhere durable to
    land. Making it unrepresentable beats checking for it.
    """
    root = profiles_root or resolve_profiles_root()
    # Fail-closed on the whole registry first: a status move decided against a tenant whose
    # claims do not load is a decision made on data nobody could read.
    registry = load(profile, profiles_root=root, product=product)

    key = angle_id.strip().lower()
    angle = registry.angles.get(key)
    if angle is None:
        raise AngleStatusError(f"{ANGLES_FILE}: {_echo(key)} — no such angle; nothing was written")

    counts = parse_evidence(evidence) if evidence is not None else None
    if target == LIVE:
        if counts is None:
            raise AngleStatusError(
                f"{ANGLES_FILE}: {key} — `promote` needs --evidence <dashboard cell>, e.g. "
                "`sent=42 replies=5`; a status set on a hunch reads exactly like one set on a "
                "result"
            )
        # `angle.claim` is guaranteed to resolve: `load` refuses a dangling reference, so
        # this cannot KeyError on a registry it just returned.
        claim = registry.claims[angle.claim]
        if claim.status != VERIFIED:
            raise AngleStatusError(
                f"{ANGLES_FILE}: {key} — claim `{claim.id}` is `{claim.status}`, not "
                f"`{VERIFIED}`; a live angle may not rest on one we have not verified"
            )

    path = resolve_knowledge_file(root, profile, ANGLES_FILE, product=product)
    date = now or datetime.now(UTC).strftime("%Y-%m-%d")
    new_text = _rewrite(
        path.read_text(encoding="utf-8"),
        key,
        target,
        _history_line(angle.status, target, counts, date),
    )
    atomic_write_text(path, new_text)
    return path


def promote(
    profile: str,
    *,
    angle_id: str,
    evidence: str | None,
    profiles_root: Path | None = None,
    product: str | None = None,
    now: str | None = None,
) -> Path:
    """Move one angle to ``live``, citing the dashboard cell that earned it.

    ``evidence`` is keyword-required and checked here as well as on the CLI. The CLI's
    ``--evidence`` is ``required=True``, so the two are belt and braces on purpose: this
    function is the public surface another caller would reach for, and it must not be
    promotable without a cell just because argparse was not in the loop.
    """
    return _move(
        profile,
        angle_id=angle_id,
        target=LIVE,
        evidence=evidence,
        profiles_root=profiles_root,
        product=product,
        now=now,
    )


def retire(
    profile: str,
    *,
    angle_id: str,
    evidence: str | None = None,
    profiles_root: Path | None = None,
    product: str | None = None,
    now: str | None = None,
) -> Path:
    """Move one angle to ``retired``. The angle stays in the file; only its status changes.

    ``evidence`` is optional and, when given, is parsed exactly as ``promote`` parses it —
    the cell that justified stopping is worth recording, but requiring one would make
    stopping harder than starting.
    """
    return _move(
        profile,
        angle_id=angle_id,
        target=RETIRED,
        evidence=evidence,
        profiles_root=profiles_root,
        product=product,
        now=now,
    )
