"""The derivation rules: the three that read the registry, and the three that read the
declaration those three rest on — ``angle-missing``, ``angle-unknown``, ``angle-conflict``.

The only module of this package that imports :mod:`gtm_core.messaging`. It asks a different
question from every other rule here. The rest of the gate asks *is this sentence well-formed*;
these ask *is this sentence derivable from a fact the tenant has statused*. That is the whole
point of the 2026-09-24 retirement: twenty-five regexes that guessed at persuasiveness are
replaced by three that read the registry the drafting skill was handed.

**Two levers, one source.** ``gtm_core.messaging.resolve.angle_for`` picks what a body MAY say;
these rules check what it DID say. Both call ``registry.load`` — the same loader, the same
closed sets, the same fail-closed parse — so a drafting step and its gate cannot disagree about
what ``verified`` means. A second reader of ``claims.toml`` living here would be the drift the
registry exists to remove.

**§R5, and the two paths differ — read this before adding a rule here.** Everything that
SELECTS is read from the declaration surface (:func:`gtm_core.hook_coverage.declared.
declaration_surface`): which angle, and therefore which claim, proof and seat, plus the five
``slot_<id>:`` lines. That surface is a fenced ``Key: value`` front block wherever it sits, plus
bolded ``**Field:** value`` lines in the document's HEADER — above the first email section. A
rendered email body is on neither, so the copy under test reaches nothing the gate selects on,
on either path — the spec-side form of the property ``agent/publish.py`` holds by reading every
field outside the ``⟦POST⟧`` span. ``tests/injection/`` holds the standing proof.

**The residual is stated here exactly as ``declared.py`` states it, and never more strongly.**
A bolded field line forged into the HEADER block itself would still be read; the header is
author-written front matter, but one of its fields (``**Why-now:**`` in the Tier-A pack
template) carries the row's researched clause, so a multi-line paste there is the one remaining
way row text reaches this surface. Until the 2026-09-24 invariant review this paragraph instead
claimed a row "cannot reach any of it, on either path", while ``declared.py`` recorded a much
wider residual — two modules, two answers for one property, and the stronger claim sat in the
module that had just wired the pack path. It was not academic there: on that path ``spec_text``
is the whole document *including the rendered bodies*, so a pasted clause whose line read
``**slot_proof:** none`` SUPPRESSED the ``slot-attribution`` ERROR that names the missing slot,
and a ``**Angle:**`` in a body outranked a declaration written lower down. Narrowing the
unfenced rung to the header closed both; do not re-widen one module's claim without the other.

What differs is the COPY UNDER TEST. On the render path it is the touch TEMPLATES, so it is
the author's words and nothing else. On the pack path a 1:1 body is already *rendered*: the
drafter pasted the row's researched clause into the prose, and there is no template to fall
back to. So on that path the copy is a mix of author text and row-derived text, and the
guarantee is weaker and different — it is **one-directional**:

    row-derived text may only ADD a refusal here, never suppress one.

That holds by construction rather than by filtering, and every rule below is written to keep
it: ``claim-status`` iterates the registry's ``do_not_say`` phrases and appends a hit;
``proof-status`` iterates the figures found and appends when no ``measured`` proof carries one.
Neither has a branch where more text produces fewer findings. It is the same asymmetry
``declared._conflicts`` already records for the legacy fields, and it is what makes an
injection cost a loud error rather than a quiet pass. **A future rule that CLEARS a finding on
evidence found in the copy would break it, and must read the template only.**

The direction is not merely tolerable on the pack path, it is right: a 1:1 body is what
actually ships, so a scraped sentence quoting a figure the tenant cannot stand behind is the
overclaim this registry exists to stop, whoever typed it.

**Off unless a registry is passed.** Same opt-in shape as ``premise_vocab`` and
``domain_aliases`` — and the same hazard, which is how ``cta-unstaged-artifact`` sat inert for
months. Both CLIs therefore load the registry whenever ``--profile`` is given, rather than
behind a flag of their own.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .model import Violation

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gtm_core.messaging.registry import Angle, Registry

#: One piece of copy under test, as ``(subject, body)``. Deliberately neither ``Touch`` nor
#: ``EmailBlock``: the render path carries copy in the first and the pack path in the second,
#: and this module judges neither container — it judges the words in it. A signature naming one
#: of the two would have made the other entry point look like the odd one out, which is part of
#: why the pack path went unwired for a day and lost fourteen ERROR rules.
Copy = tuple[str, str]

#: The five body slots, in the order ``voice-rules.toml``'s ``[[slot]]`` table declares them.
#: Written down once, here, because the linter must not depend on a tenant file to know how
#: many slots a body has — a tenant that deleted a slot from its own copy of the table would
#: otherwise silently switch this rule off for itself.
SLOT_IDS: tuple[str, ...] = ("signal", "claim", "pain", "hedge", "proof")

#: ``slot_claim: audit-signed`` in a spec's front block. One line per slot, read over
#: :func:`gtm_core.hook_coverage.declared.declaration_surface` so a ``slot_claim:`` line quoted
#: inside a touch body is not a declaration — in the bolded ``**slot_claim:**`` form as much as
#: the bare one, which is the half that had to be closed on 2026-09-24 — the spec-side form of
#: the property ``agent/publish.py`` holds by reading every field outside the ``⟦POST⟧`` span.
_SLOT_RE = {
    slot: re.compile(rf"^\**slot[_ ]{slot}\**:\**\s*(?P<value>.+?)\s*$", re.MULTILINE | re.I)
    for slot in SLOT_IDS
}

#: A number, with thousands separators and an optional decimal. Never a figure on its own.
_NUM = r"\d[\d,]*(?:\.\d+)?"

#: The units and comparatives that turn a number into a magnitude. Longest alternative first,
#: so ``percentage points`` is not read as ``percent`` with a stray word behind it.
_UNIT_ALT = (
    r"percentage\s+points?|percent|pct|%|pp"
    r"|seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|months?|quarters?|years?"
)

#: A FIGURE: a number that carries a unit or a comparative — ``90%``, ``40 percent``, ``1.5x``,
#: ``$40k``, ``3 hours``, ``2 days``. Three alternatives: currency-prefixed, number-then-unit,
#: number-then-multiplier. Deliberately NOT extended to spelled-out numbers: "five-to-fifteen
#: days to minutes" is the one flagship figure this tenant can actually support, and it carries
#: no digit — so a rule that also caught word-numerals would have to carry an exemption for the
#: only clean case, which is a rule fitted to its own fixture.
#:
#: **What this replaces, and why (2026-09-24, §R18 review).** It shipped that morning as
#: ``\d[\d,.]*`` — any digit run — under a docstring that said "every digit in a body is a
#: magnitude claim". Measured on the live packs the same day: `proof-status` was 49 of 66
#: warnings (74.2%), past the line the finding budget draws for "a property of this list, not a
#: finding about its members", with `1`, `1.5` and `110` as its exemplars; and it convicted a
#: live pack at ERROR for "a 90-second demo recording?" because the numeral `90` matched the
#: retracted `90%` proof. A numeral is not a magnitude. A magnitude has a unit, and two numbers
#: under different units are different claims — which is why the comparison in :func:`_figures`
#: carries the unit through rather than stripping it.
#:
#: The **adjacency** rule is what separates the two halves: a unit counts when it touches the
#: number or sits one space from it. A hyphen does not join them, so ``90-second demo`` and
#: ``29-point gap`` are attributive compounds describing a thing, not quantities being claimed —
#: the form the false positive was written in. Two deliberate non-units: a bare scale word
#: (``35 million dollars`` — the second live false positive, a prospect's funding round pasted
#: into a 1:1 body) needs a noun to mean anything, and a plain plural noun (``8 agents per
#: user``, ``12 markets``) is a count, not a measurement.
_FIGURE_RE = re.compile(
    rf"(?P<cur>[$€£])\s?(?P<curnum>{_NUM})(?P<scale>k|m|bn|thousand|million|billion)?(?!\w)"
    rf"|(?<![\w.])(?P<num>{_NUM})\s?(?P<unit>{_UNIT_ALT})(?!\w)"
    rf"|(?<![\w.])(?P<xnum>{_NUM})(?P<mult>x|×)(?!\w)",
    re.IGNORECASE,
)

#: Unit spellings that mean the same magnitude, so a body's "40 percent" and a proof's "40%"
#: are one figure and not two. Everything else is singularised and lowercased.
_PERCENT_UNITS = frozenset({"%", "percent", "pct"})
_POINT_UNITS = frozenset({"pp", "percentage point", "percentage points"})
_TIME_ABBREV = {"sec": "second", "min": "minute", "hr": "hour"}
_SCALE_ABBREV = {"thousand": "k", "million": "m", "billion": "bn"}

#: Merge tags and the rendered artefacts around them are stripped before figures are counted:
#: ``{{Why Now}}`` is the ROW's text, and §R5 keeps the row out of this rule entirely.
_MERGE_TAG_RE = re.compile(r"\{\{[^}]*\}\}")


def _copy_under_test(copy: list[Copy]) -> str:
    """Every subject and body under test, merge tags removed.

    On the render path these are TEMPLATES — the author's own words — and stripping the merge
    tags is what keeps the row out of the rule entirely. On the pack path they are rendered
    bodies with no tags left to strip, and the module docstring's one-directional argument is
    what holds instead. Do not add a branch that reads a finding as *cleared* from what it
    finds here without first reading that argument.
    """
    joined = "\n".join(f"{subject}\n{body}" for subject, body in copy)
    return _MERGE_TAG_RE.sub(" ", joined)


def _phrase_re(phrase: str) -> re.Pattern[str]:
    """Word-bounded, whitespace-tolerant match for a registry phrase.

    Whitespace-tolerant because a spec hard-wraps its bodies at ~90 chars and that wrap does
    not survive staging, so a banned phrase must not be findable or missable according to where
    the author happened to break the line — the same normalisation `lint_email`'s hedge check
    learned on 2026-08-29, and `lint_stakes` used before it retired.
    """
    parts = [re.escape(tok) for tok in phrase.split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(parts) + r"(?!\w)", re.IGNORECASE)


def lint_angle(spec_text: str, registry: Registry) -> tuple[Angle | None, list[Violation]]:
    """The angle this spec declares, VALIDATED — plus the refusal when it does not resolve.

    ``angle-missing`` (WARN), ``angle-unknown`` (ERROR), ``angle-conflict`` (ERROR). Returns
    the resolved :class:`~gtm_core.messaging.registry.Angle` so the three rules below do not
    each re-derive it; a ``None`` angle means they fall back to their unmigrated branch, and
    the caller has already been told why in a finding.

    **The bug this function exists to close (2026-09-24 review, finding 1).** Until it did,
    the angle was resolved through ``declared_angle(spec)`` — the PURE read, with no registry
    — and ``registry.angles.get(<typo>)`` then returned ``None``. All three rules took the
    unmigrated branch: ``claim-status`` returned before the status check, ``proof-status``
    dropped its figure branch to WARN, ``slot-attribution`` returned ``[]``. Nothing reported
    the id. One mistyped word in a front block switched the registry gate off and the run went
    green. ``declared_angle``'s validating form already existed; nothing in production called
    it, so its three refusal types were unreachable.

    **Why the severities differ, and why that is a measurement.** On 2026-09-24 zero of the 47
    live specs with touches declare an ``angle:`` at all, so refusing *absence* hard would fail
    the whole fleet for a re-draft the drafting skills cannot yet produce — the "a gate nobody
    can ship through" failure. Absence is therefore a WARN that names what it switched off, so
    the silence is visible rather than invisible. An *unknown* id is the opposite case: a spec
    that claims to be registry-derived and names an argument nobody wrote. That is a defect in
    this spec, and it is an ERROR. Promote ``angle-missing`` when the fleet has migrated.

    **Why ``angle-conflict`` is its own id rather than folded into ``angle-unknown``.** It is a
    different operator action — a stale legacy line to delete, not an id to correct — and
    catching the exception family while dropping one member on the floor would re-create the
    finding-1 bypass one level down. The declared ``angle:`` stays authoritative (one declared
    id, everything else derived), so the angle is re-read and the three rules still run: a
    reported-and-then-skipped rule is the same silence in a louder shirt.
    """
    from gtm_core.hook_coverage.declared import (
        DerivedFieldConflict,
        UndeclaredAngle,
        UnknownAngle,
        declared_angle,
    )

    try:
        angle_id = declared_angle(spec_text or "", registry)
    except UndeclaredAngle:
        return None, [
            Violation(
                "WARN",
                "SPEC",
                "angle-missing",
                "no `angle:` in the declaration surface, so this spec is not registry-derived "
                "and `slot-attribution` did not run, `claim-status` checked only the "
                "`do_not_say` phrasings, and `proof-status` reported unbacked figures as WARN "
                f"rather than ERROR. The registry holds {len(registry.angles)} angles; "
                "`python -m gtm_core.messaging unused` lists them.",
            )
        ]
    except UnknownAngle as exc:
        return None, [Violation("ERROR", "SPEC", "angle-unknown", str(exc))]
    except DerivedFieldConflict as exc:
        # The id itself resolved — only a legacy copy of a derived field disagrees — so it is
        # read back with the pure form and the rules below still run against it.
        angle_id = declared_angle(spec_text or "")
        return registry.angles.get(angle_id), [
            Violation("ERROR", "SPEC", "angle-conflict", str(exc).replace("\n", "; "))
        ]
    return registry.angles.get(angle_id), []


def lint_claim_status(
    copy_pairs: list[Copy], angle: Angle | None, registry: Registry
) -> list[Violation]:
    """``claim-status`` (ERROR) — the body says something the registry has not verified.

    Two halves, and the first works on every spec whether or not it has migrated:

    * **A ``do_not_say`` phrase in the copy.** Each claim carries the wordings that overstate
      it — an append-only operator decision, made once, in the tenant's own file. "hash-chained"
      for a claim whose verified statement is "each audit entry is signed" is not a paraphrase,
      it is a different technical assertion, and the reason FR0 existed.
    * **Slot 2 resting on an unverified claim.** When the spec declares an angle, the claim
      underneath it must be ``verified``. ``conditional`` and ``design-target`` are legal to
      record and illegal to draft from — the same line ``resolve.angle_for`` refuses at, checked
      from the other side so the two levers cannot drift.

    ``angle`` is resolved once by :func:`lint_angle` and passed in, rather than re-derived here.
    A ``None`` angle means the spec named none, named one the registry does not hold, or could
    not be trusted to name one — all three already reported by that function, so the second
    half simply does not apply and the first half still does.
    """
    copy = _copy_under_test(copy_pairs)
    out: list[Violation] = []
    for claim_id, claim in sorted(registry.claims.items()):
        for phrase in claim.do_not_say:
            if _phrase_re(phrase).search(copy):
                out.append(
                    Violation(
                        "ERROR",
                        "SPEC",
                        "claim-status",
                        f"copy says {phrase!r}, which claim {claim_id!r} lists under "
                        f"`do_not_say` — the verified statement is {claim.statement!r}. "
                        f"Rephrase to what the claim actually holds, or change the claim.",
                    )
                )

    if angle is None:
        return out
    claim = registry.claims.get(angle.claim)
    if claim is not None and claim.status != "verified":
        out.append(
            Violation(
                "ERROR",
                "SPEC",
                "claim-status",
                f"angle {angle.id!r} rests on claim {claim.id!r}, which is {claim.status!r} — "
                f"only a `verified` claim may reach a body. Verify it (and record the "
                f"`source`), or route this seat to an angle that is.",
            )
        )
    return out


def _magnitude(unit: str) -> str:
    """One canonical spelling per unit — ``percent``/``pct``/``%`` all become ``%``."""
    u = re.sub(r"\s+", " ", unit.strip().lower())
    if u in _PERCENT_UNITS:
        return "%"
    if u in _POINT_UNITS:
        return "pp"
    u = u[:-1] if u.endswith("s") else u
    return _TIME_ABBREV.get(u, u)


def _figures(text: str) -> set[str]:
    """The magnitudes a piece of text quotes, each normalised as ``<number><unit>``.

    The UNIT is carried through, and that is the point rather than a detail. A number with no
    unit is not a magnitude claim at all and never enters this set; two numbers under different
    units are different claims, so ``90 day`` can never be matched against the retracted
    ``90%`` — the live ERROR this function was rewritten to stop.

    Spellings are folded so a body's "40 percent" and a proof's "40%" are one figure, and
    compared as WHOLE tokens rather than substrings: a first version asked ``figure in
    proof.statement`` and matched the body's "10" inside a proof's "100%" and its "3" inside
    "35%", so two live specs were told their numbers came from a disputed proof they had never
    quoted. A figure gate that convicts the wrong number is worse than none — the operator goes
    looking for a figure that is not there and stops believing the rule.

    No exemption list, deliberately. The line is drawn by the predicate in :data:`_FIGURE_RE`,
    which is one rule about what a magnitude IS; an exemption list is how a figure gate quietly
    stops gating, because every entry would have to be re-justified against a corpus nobody
    re-reads. Two known blind spots, recorded rather than patched: a range writes its unit once
    (``50–54%`` yields only ``54%``), and a currency written in words (``USD 40,000``) is not the
    same token as ``$40k``. Both under-report, which is the safe direction here — the rule may
    only ADD a refusal from copy it reads (see this module's docstring), never clear one.
    """
    out: set[str] = set()
    for m in _FIGURE_RE.finditer(text):
        if m.group("cur"):
            scale = (m.group("scale") or "").lower()
            number = m.group("curnum").replace(",", "")
            out.add(f"{m.group('cur')}{number}{_SCALE_ABBREV.get(scale, scale)}")
        elif m.group("mult"):
            out.add(f"{m.group('xnum').replace(',', '')}x")
        else:
            unit = _magnitude(m.group("unit"))
            joiner = "" if unit in ("%", "pp") else " "
            out.add(f"{m.group('num').replace(',', '')}{joiner}{unit}")
    return out


def lint_proof_status(
    copy_pairs: list[Copy], rows: list[dict], angle: Angle | None, registry: Registry
) -> list[Violation]:
    """``proof-status`` (ERROR) — a number with nothing behind it, or a foreign anchor.

    Three checks, all re-derived from the same ``registry.load`` ``resolve`` used:

    * **A figure with no ``measured`` proof.** A *figure* is a number that carries a unit or a
      comparative — ``90%``, ``1.5x``, ``$40k``, ``3 hours`` — and NOT every digit in a body: a
      bare numeral in prose ("a 90-second recording", "1 link", "8 agents per user") asserts no
      magnitude, and reading it as one is what made this rule 74% of the live warning volume and
      convicted a shipping pack for offering a demo video. See :data:`_FIGURE_RE` for the line
      and the measurement behind it. If no proof whose ``figure_kind`` is ``measured`` carries
      that figure in its statement, the copy is quoting a number the tenant cannot stand behind.
      This is the rule FR0's retraction of the 90% / 100% flagship figures was reaching for and
      nothing had.
    * **A ``disputed`` figure quoted anyway.** Matched WITH its unit, so a numeral shared with a
      retracted proof is not the retracted claim. Reported separately and first, because "we
      looked at this and could not confirm it" is a different operator action from "nobody has
      measured this yet".
    * **Another market's anchor.** An anchor is a statement about a jurisdiction. Where the
      registry HOLDS an anchor for the reader's market, pointing a different market's regulation
      at them is a data defect in the angle — the same split ``resolve._eligible_proof`` makes,
      and deliberately not a re-point: swapping the local anchor in would be this gate authoring
      messaging. Where it holds none, the absence was recorded on purpose and the offer ships
      without one, so nothing fires.

      The market comes from the ROWS, so on the pack path — where ``rows`` is empty because a
      1:1 pack carries no country column — this branch is silent by absence rather than by
      exemption. That is the honest answer: nothing in a pack document says which regulator
      the reader answers to, and inventing one would be this gate authoring messaging.
    """
    from gtm_core import email_compliance

    copy = _copy_under_test(copy_pairs)
    measured = {p.id: p for p in registry.proof.values() if p.figure_kind == "measured"}
    disputed = {p.id: p for p in registry.proof.values() if p.figure_kind == "disputed"}
    # The unmeasured-figure branch is graded by whether the spec has MIGRATED, and the grade
    # is a measurement rather than a preference: a rule that is rare is worth failing a send
    # over, and one that fires on half the fleet is describing the population rather than
    # screening it (`rule_lifecycle_report` draws that line at 0.40). Measured 2026-09-24 over
    # the live `sequences/spec-*.md` corpus — 37 specs with touches — BOTH ways, so the two
    # numbers are comparable to each other and not to the "47" an earlier draft of this comment
    # used: under the any-digit matcher it fired on 21 of 37 (57%), far over the line, which is
    # why it shipped as WARN until a spec declares an `angle:`, the same shape `premise-missing`
    # and the retired `signal-column-undeclared` settled on. Under the unit-bearing predicate
    # that replaced it the same day: 0 of 37. Nearly the whole rate was numerals — years, item
    # counts, a CFR section number — and the touch TEMPLATES quote no unbacked magnitude. The
    # severity
    # split is deliberately left alone here: it is now argued from a rate that would support
    # promoting the branch, and promoting it is a fleet decision, not a bug fix. The DISPUTED
    # branch stays ERROR on every spec regardless — it is precise, and it is the FR0 retraction
    # of the flagship figures made enforceable rather than written down again; it fires on 2 of
    # the 37 (5%), both quoting the retracted 100% audit figure. (Before the fix it "fired" on
    # 7, five of them on numerals that merely shared a digit with a disputed stat.) An earlier
    # draft of this comment typed "3 of 47 (6%)", which was wrong by 2.3x and mattered, because
    # the split is argued FROM the rate. Re-derive it rather than trusting this line.
    unmeasured_level = "ERROR" if angle is not None else "WARN"
    out: list[Violation] = []

    for figure in sorted(_figures(copy)):
        hit = next(
            (p for _, p in sorted(disputed.items()) if figure in _figures(p.statement)),
            None,
        )
        if hit is not None:
            out.append(
                Violation(
                    "ERROR",
                    "SPEC",
                    "proof-status",
                    f"copy quotes {figure!r}, which belongs to proof {hit.id!r} — recorded "
                    f"`disputed`. A disputed figure may be kept in the registry and may not be "
                    f"sent; cut it or replace it with a `measured` one.",
                )
            )
            continue
        if not any(figure in _figures(p.statement) for p in measured.values()):
            out.append(
                Violation(
                    unmeasured_level,
                    "SPEC",
                    "proof-status",
                    f"copy quotes {figure!r} and no `measured` proof in proof.toml carries it "
                    f"({len(measured)} measured of {len(registry.proof)}) — a figure with no "
                    f"measurement behind it is the overclaim this registry exists to stop."
                    + (
                        ""
                        if angle is not None
                        else " WARN because this spec declares no `angle:` and so predates the "
                        "registry; it is an ERROR on any spec that does."
                    ),
                )
            )

    if angle is None:
        return out
    proof = registry.proof.get(angle.proof)
    if proof is None or proof.kind != "anchor":
        return out
    anchor_market = email_compliance.normalize_market(proof.market)
    markets = {
        m
        for m in (email_compliance.normalize_market(str(r.get("country") or "")) for r in rows)
        if m
    }
    for market in sorted(markets - {anchor_market}):
        has_local = any(
            p.kind == "anchor" and email_compliance.normalize_market(p.market) == market
            for p in registry.proof.values()
        )
        if not has_local:
            continue  # recorded as having no anchor: the no-anchor offer shape, not a defect
        out.append(
            Violation(
                "ERROR",
                "SPEC",
                "proof-status",
                f"angle {angle.id!r} anchors on {proof.id!r} ({anchor_market or 'no market'}) "
                f"while this list carries {market} readers, and proof.toml HOLDS a {market} "
                f"anchor — point the angle at the reader's own regulator rather than borrowing "
                f"one.",
            )
        )
    return out


def lint_slot_attribution(spec_text: str, angle: Angle | None) -> list[Violation]:
    """``slot-attribution`` (ERROR) — a body slot whose source cannot be named.

    Five slots, five source ids, declared in the spec's front block as ``slot_<id>:``. A slot
    with no source is copy nobody can stand behind: it is the state every pre-registry spec was
    in, where "where did this sentence come from" had no answer and the reviewer had to guess.

    Presence alone would be a declaration nothing checks — the failure this repo keeps
    re-learning — so the three slots the angle DERIVES are cross-checked against it. ``signal``
    and ``hedge`` are presence-only by construction: the signal's source is the row (which
    varies per recipient) and the hedge's is the tenant's own cue table, and neither is a
    registry id.

    Silent when no angle resolved: the whole slot contract is the migrated shape, and the
    absent, unknown or untrustworthy declaration is :func:`lint_angle`'s finding to make, not
    this rule's to make five times over.

    Reads the declaration surface and nothing else, so this rule is identical on the render and
    the pack paths — a pack's rendered body carries row text and none of it is on this surface.
    """
    from gtm_core.hook_coverage.declared import declaration_surface

    if angle is None:
        return []

    surface = declaration_surface(spec_text or "")
    declared: dict[str, str] = {}
    out: list[Violation] = []
    for slot in SLOT_IDS:
        m = _SLOT_RE[slot].search(surface)
        value = m.group("value").strip() if m else ""
        declared[slot] = value
        if not value:
            out.append(
                Violation(
                    "ERROR",
                    "SPEC",
                    "slot-attribution",
                    f"slot {slot!r} names no source id — every slot of a body says where it "
                    f"came from, or the body cannot be checked against the registry at all. "
                    f"Add `slot_{slot}:` to the front block.",
                )
            )

    # The derivable three. `none` is the sanctioned value for `slot_proof` on the no-anchor
    # offer shape (four markets are recorded as having no anchor), so it is accepted there and
    # nowhere else.
    for slot, want in (("claim", angle.claim), ("pain", angle.seat), ("proof", angle.proof)):
        said = declared[slot].lower()
        if not said:
            continue
        if slot == "proof" and said == "none":
            continue
        if said != want.lower():
            out.append(
                Violation(
                    "ERROR",
                    "SPEC",
                    "slot-attribution",
                    f"slot {slot!r} cites {declared[slot]!r} but angle {angle.id!r} derives "
                    f"{want!r} — one declared id, everything else derived. Fix the slot or "
                    f"declare the angle the copy actually argues.",
                )
            )
    return out


def lint_derivation(
    spec_text: str,
    copy_pairs: list[Copy],
    rows: list[dict],
    registry: Registry | None,
) -> list[Violation]:
    """Every derivation rule, or nothing when no registry was supplied.

    The one entry point, called from BOTH ``driver.lint_merge_render`` (the render path, where
    ``copy_pairs`` are touch templates and ``rows`` are the enrolment list) and
    ``rules_batch.lint_pack`` / ``lint_formatted_pack`` (the pack path, where they are rendered
    email blocks and ``rows`` is empty). Having two callers is the point: before 2026-09-24 the
    pack path reached none of these rules at all, while the card documented ``claim-status`` as
    "authoritative there" — a surface claiming a check nothing ran.

    ``spec_text`` is the whole document, and only its declaration surface is read from it.
    """
    if registry is None:
        return []
    angle, out = lint_angle(spec_text, registry)
    return (
        out
        + lint_claim_status(copy_pairs, angle, registry)
        + lint_proof_status(copy_pairs, rows, angle, registry)
        + lint_slot_attribution(spec_text, angle)
    )
