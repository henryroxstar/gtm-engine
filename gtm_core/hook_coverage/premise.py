from __future__ import annotations

import re
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from ..paths import resolve_knowledge_file, resolve_profiles_root
from .config import EXEMPLARS, MAX_SPECS_PER_CAPABILITY
from .declared import _CAPABILITY_RE, _PREMISE_RE, _SIGNAL_COLUMN_RE, _STAKES_RE
from .matrix import _clean_cell, _sections, _split_row

# --- does the row's own fact ESTABLISH what the body claims? --------------------------
#
# The dominant defect class in the 2026-08-21 operator review, and the one nothing checked.
# Six of twelve rejections were a version of the same sentence:
#
#     "single internal tool (Navigator) - doesn't establish the multi-framework credential
#      pain the follow-on paragraph claims"
#     "single vendor product (a defence contractor's GEOINT software) - no multi-framework
#      signal"
#     "fact is one CAD vendor's product on one platform - doesn't establish the 'crosses
#      more than one platform' claim that follows"
#
# Note what these are NOT. They are not off-topic (`signal-off-topic` passes them — the fact
# is about exactly the right subject). They are not contradictions (`signal-contradicts-pitch`
# passes them — the fact does not announce the capability). They are not thin (`specificity`
# passes them — the render is full of anchors). The fact is true, relevant and specific, and
# the body's next paragraph asserts something ARITHMETICALLY larger than it: the body claims
# plurality, the evidence attests one thing. Two paragraphs that visibly do not connect, which
# is the fastest "this is generated" tell a recipient gets.
#
# So it becomes a declared property, the same move `hook_cell` already made: the spec states
# the premise its body requires, the profile states what attests each premise, and a row whose
# own recorded evidence cannot meet the bar is a type error rather than a judgement call.
#
# The vocabulary is the TENANT's (`knowledge/premise-vocab.toml`) — this module carries no
# premise text of its own, exactly as it carries no hook text. A missing file disables the
# check, the same convention `--case-study-file` and `competitors.toml` already use.

_PREMISE_VOCAB_FILE = "premise-vocab.toml"


@dataclass(frozen=True)
class Premise:
    """One entry from the tenant's ``premise-vocab.toml``.

    ``min_distinct`` is the arity the body's claim needs. A premise asserting that the
    recipient runs agents across SEVERAL frameworks needs two distinct framework names in
    evidence; a premise asserting they run agents at all needs one. That number is the whole
    check — everything else is term matching.
    """

    key: str
    min_distinct: int
    terms: frozenset[str]
    claim: str = ""

    def attested_by(self, text: str) -> set[str]:
        """Which of this premise's terms the text carries, as distinct terms."""
        low = (text or "").lower()
        hits = set()
        for term in self.terms:
            # Word-boundary match so "sap" does not fire inside "sapphire". Terms may be
            # multi-word ("copilot studio"), which \b still handles correctly at both ends.
            if re.search(rf"\b{re.escape(term)}\b", low):
                hits.add(term)
        return hits

    def met_by(self, text: str) -> bool:
        return len(self.attested_by(text)) >= self.min_distinct


def load_premise_vocab(
    profile: str,
    profiles_root: Path | None = None,
    product: str | None = None,
    overlay: str | None = None,
) -> dict[str, Premise]:
    """Read the tenant's premise vocabulary, or ``{}`` when it ships none.

    Schema (``schema = 1``)::

        [premise.multi-framework]
        claim        = "the recipient runs agents on more than one framework"
        min_distinct = 2
        terms        = ["langgraph", "autogen", "crewai", "bedrock", ...]

    Returns ``{}`` for a missing or malformed file rather than raising: this check is
    opt-in per profile, and a tenant that has not written the file simply does not get it.

    Resolved through :func:`resolve_knowledge_file`, the same rung ladder
    :func:`capability_vocab` uses two functions below. Until 2026-09-23 this one hand-built
    ``<root>/<profile>/knowledge/<file>`` instead, so it reached neither the product rung nor
    the overlay rung: a tenant with a product-level premise vocabulary was silently served the
    profile-level one, and the two readers in this module disagreed about where a tenant's
    knowledge lives. The fix is the resolver, never a third rung added by hand here.
    """
    try:
        path = resolve_knowledge_file(
            profiles_root or resolve_profiles_root(),
            profile,
            _PREMISE_VOCAB_FILE,
            product=product,
            overlay=overlay,
        )
    except (OSError, ValueError):
        return {}
    if not path.is_file():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    out: dict[str, Premise] = {}
    for key, entry in (data.get("premise") or {}).items():
        terms = frozenset(
            str(t).strip().lower() for t in (entry.get("terms") or []) if str(t).strip()
        )
        if not terms:
            continue
        out[key.strip().lower()] = Premise(
            key=key.strip().lower(),
            min_distinct=max(1, int(entry.get("min_distinct", 1))),
            terms=terms,
            claim=str(entry.get("claim") or "").strip(),
        )
    return out


def declared_premise(spec_text: str) -> str:
    """The premise id a spec declares, or ``""``. Same one-line front-block read as
    :func:`declared_cell`, so the field costs no new parser."""
    m = _PREMISE_RE.search(spec_text or "")
    return m.group("value").strip().lower() if m else ""


def declared_stakes(spec_text: str) -> str:
    """The consequence a spec claims its body attaches to the gap it names, or ``""``.

    Same one-line front-block read as :func:`declared_premise`, and it exists for the same
    reason that one does: to turn a property no regex can judge into one a gate can.

    ``voice.md`` job 4 says *"a gap with no consequence is trivia"*, and nothing enforced it.
    Both 2026-08-21 ship30 specs shipped a named gap with no cost attached, and both of their
    "what changed" sections **claimed the opposite** — security said "the stakes name a limit,
    not a feeling", exec said the why "explains why it is HARD". The operator rejected on
    exactly that, in the same words, for the second round running: *"we say what is missing
    but never what it costs them. Nothing is at stake, so there is no reason to reply."*

    A prose claim in §2 is unfalsifiable. A declared field is not: once the consequence is
    written down as ``stakes:``, ``lint_stakes`` can prove the body actually carries it, and
    the eval sheet can show the labeler the specific claim to judge rather than asking for a
    general verdict on the email. Unlike ``premise`` this value is free text, not a key into
    a vocabulary file — the consequence is per-argument and there is no fixed set of them.
    Lower-casing is deliberate: attestation is checked case-insensitively so re-capitalising
    a sentence in the body is not a lint failure.
    """
    m = _STAKES_RE.search(spec_text or "")
    return m.group("value").strip() if m else ""


def capability_slug(label: str) -> str:
    """``"Credentials & delegation"`` -> ``"credentials-delegation"``.

    One normaliser for both sides, so a spec may declare either the taxonomy's own label or
    its slug and still land on the same group.
    """
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", (label or "").lower())).strip("-")


def _knowledge_path(profile: str, profiles_root: Path | None, filename: str) -> Path | None:
    """One knowledge file's resolved path, or ``None`` when the profile cannot be resolved.

    The resolver is the source of truth for the rung ladder (CLAUDE.md); this only turns its
    refusals — an unsafe segment, an unreadable root — back into "this tenant has no such file",
    which is what every opt-in reader in this module already means by a missing path.
    """
    try:
        return resolve_knowledge_file(profiles_root or resolve_profiles_root(), profile, filename)
    except (OSError, ValueError):
        return None


def capability_vocab(profile: str, profiles_root: Path | None = None) -> dict[str, str]:
    """``slug -> label`` for the profile's capability groups.

    The vocabulary is **tenant knowledge**, never a list in this module — the same rule
    ``hook_cell`` follows against ``hook-matrix.md``.

    **Two rungs, in that order (FR2, 2026-09-24).** A tenant that ships ``claims.toml`` has
    stated its capability groups as *facts* (``[[claim]] group``), and that file is the one the
    outbound linter, the matrix and the angle resolver all derive from; reading a prose taxonomy
    beside it would be a second answer to "which groups exist". The first tenant migrated in FR1
    kept the group names its taxonomy already used, so the switch renames nothing there. A
    tenant with **no** ``claims.toml`` — most profiles in this repo — keeps the ``product.md``
    behaviour exactly, and a profile whose ``product.md`` has no taxonomy section returns ``{}``,
    which turns the check off rather than failing every spec: a tenant that has not written a
    taxonomy has not declared a violation.

    **Failure posture: a broken ``claims.toml`` RAISES.** ``registry.load`` is all-or-nothing, so
    it is the one caller here that can fail loudly, and it must. The alternatives are both worse
    in the same way: swallowing the error to ``{}`` disables ``capability-unargued`` and
    ``capability-unknown`` on every spec in the campaign — a gate that passes by finding nothing,
    the failure this repo has already paid for — and falling back to ``product.md`` answers from
    a file the tenant has stopped maintaining, silently. The one caller for which an advisory
    vocabulary really is optional (``agent/mcp/judge/server.py:judge_context``) already catches
    and returns ``{}``, so scoring is not blocked by a tenant data defect either way.
    """
    from ..messaging import registry as _registry

    claims_path = _knowledge_path(profile, profiles_root, _registry.CLAIMS_FILE)
    if claims_path is not None and claims_path.is_file():
        groups: dict[str, str] = {}
        for claim in _registry.load(profile, profiles_root=profiles_root).claims.values():
            slug = capability_slug(claim.group)
            if slug:
                groups.setdefault(slug, claim.group)
        return groups

    try:
        path = resolve_knowledge_file(
            profiles_root or resolve_profiles_root(), profile, "product.md"
        )
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return {}
    out: dict[str, str] = {}
    for title, body in _sections(text):
        if "capability taxonomy" not in title.lower():
            continue
        for line in body.splitlines():
            row = _split_row(line)
            if not row:
                continue
            label = _clean_cell(row[0])
            slug = capability_slug(label)
            # The header row ("Group") and the separator normalise to noise, not a group.
            if slug and slug != "group" and len(row) > 1:
                out.setdefault(slug, label)
    return out


def declared_capability(spec_text: str, vocab: dict[str, str] | None = None) -> str:
    """The capability group a spec declares, normalised to its slug, or ``""``.

    Raises :class:`ValueError` when ``vocab`` is supplied and the declared value is not in
    it. An unvalidated field cannot be counted: ``credentials_delegation`` and
    ``credentials-delegation`` are one group to a reader and two to a ``Counter``, so a typo
    would silently buy a spec an extra slot under the cap.
    """
    m = _CAPABILITY_RE.search(spec_text or "")
    if not m:
        return ""
    slug = capability_slug(m.group("value"))
    if vocab and slug not in vocab:
        known = ", ".join(sorted(vocab)[:EXEMPLARS])
        raise ValueError(
            f"capability {m.group('value').strip()!r} is not a group in product.md's "
            f"capability taxonomy (known: {known})"
        )
    return slug


def capability_monotone(
    capabilities: dict[str, str], cap: int = MAX_SPECS_PER_CAPABILITY
) -> list[str]:
    """Findings for capability groups more than ``cap`` specs in this campaign argue from.

    **Why this is a campaign-level rule and not a linter rule.** Every gate in
    ``merge_render_linter`` judges one spec against its own rows; none of them can see a
    sibling, so none of them can see the defect that matters most. On 2026-08-23 seven
    drafted specs each passed the per-email gate at zero errors while six of the seven made
    the *same* argument — the operator's verdict on the batch was that they all looked the
    same. Monotone is only visible from above.

    Specs that declare nothing are not counted. The field is new, so an undeclared spec is
    an unmigrated one, not a violation — :func:`audit_campaign` reports those separately as
    an advisory ``capability-undeclared``.
    """
    counts = Counter(slug for slug in capabilities.values() if slug)
    out = []
    for slug, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        if n > cap:
            specs = ", ".join(sorted(s for s, c in capabilities.items() if c == slug)[:EXEMPLARS])
            out.append(
                f"argument-monotone: {n} specs argue capability {slug!r} (cap {cap}) — "
                f"{specs}; one argument in {n} costumes reads as mass mail, "
                f"re-angle one onto a group product.md's pain->owner table gives this seat"
            )
    return out


@dataclass(frozen=True)
class PremiseFinding:
    email: str
    premise: str
    attested: tuple[str, ...]
    needed: int

    @property
    def detail(self) -> str:
        got = ", ".join(sorted(self.attested)) or "nothing"
        return (
            f"premise {self.premise!r} needs {self.needed} distinct attesting term(s); "
            f"this row's evidence carries {len(self.attested)} ({got})"
        )


def premise_attestation(
    rows: list[dict],
    premise: Premise,
    *,
    evidence_fields: tuple[str, ...] = ("signal_evidence", "signal_clause", "why_now"),
) -> Counter:
    """``{term -> how many rows attest the premise SOLELY via that term}``.

    The companion measurement to :func:`premise_unsupported`, and the one that says whether a
    pass means anything. A premise with ``min_distinct = 1`` is satisfied by a single word,
    so if that word is common the gate is a word-presence check wearing an entailment check's
    name — which is the error the premise rule was created to catch, committed by the rule.

    Measured 2026-08-23 on the shipped enterprise-security list: all 15 rows attested
    ``cross-org-agents`` and **11 of them on the bare word "partner"**, where it described a
    commercial relationship (an integrator *joining* a partner network, a hospital *selecting*
    a vendor) rather than agents crossing an organisational boundary. The premise's own claim
    is about agents crossing; nothing in a lone "partner" establishes that.

    Reported, never auto-corrected: the term list and ``min_distinct`` live in the tenant's
    ``premise-vocab.toml``, so tightening them is the operator's call about their own
    messaging, not a change code should make silently.
    """
    solo: Counter = Counter()
    for r in rows:
        text = " ".join(str(r.get(f) or "") for f in evidence_fields)
        # Same name-stripping and same `attested_by` the gate itself uses. A second
        # implementation of "what does this row attest" would drift from the first, and then
        # this function would be reporting on a check nobody runs.
        company = str(r.get("company") or "").strip()
        if company:
            text = re.sub(re.escape(company), " ", text, flags=re.IGNORECASE)
        hits = premise.attested_by(text)
        if len(hits) == 1:
            solo[sorted(hits)[0]] += 1
    return solo


def premise_unsupported(
    rows: list[dict],
    premise: Premise,
    *,
    evidence_fields: tuple[str, ...] = ("signal_evidence", "signal_clause", "why_now"),
) -> list[PremiseFinding]:
    """Rows whose own research record cannot carry the premise the spec declares.

    Reads the row's RECORDED evidence, never the rendered body: the question is whether the
    research found enough to justify the claim, and a body that asserts it regardless is
    precisely the defect. Scanning `signal_evidence` first (the verbatim source span) plus
    the clause and the raw notes gives the row every chance to attest before it fails —
    a false ERROR here deletes a good row, which is the expensive direction.
    """
    out: list[PremiseFinding] = []
    for r in rows:
        text = " ".join(str(r.get(f) or "") for f in evidence_fields)
        # Strip the account's own name before matching. A company literally called
        # "<X> Partners", "<X> Ecosystem" or "<X> Vendors" would otherwise attest a premise
        # on its letterhead — the evidence would carry the term without the FACT carrying it,
        # which is precisely the "relevance is not entailment" error this rule exists to catch,
        # committed by the rule itself. Caught 2026-08-21 by reading a render (a health system
        # with "Partners" in its name); that row's attestation turned out to be genuine (its why_now names an
        # announced enterprise partnership), so this changes no live verdict — it closes the
        # hazard before a row passes on its name alone.
        company = str(r.get("company") or "").strip()
        if company:
            text = re.sub(re.escape(company), " ", text, flags=re.IGNORECASE)
        hits = premise.attested_by(text)
        if len(hits) < premise.min_distinct:
            out.append(
                PremiseFinding(
                    email=(r.get("email") or "?").strip(),
                    premise=premise.key,
                    attested=tuple(sorted(hits)),
                    needed=premise.min_distinct,
                )
            )
    return out


def declared_signal_column(spec_text: str) -> str:
    """The signal CATEGORY a spec declares its beat 2 depends on, or ``""``.

    Same one-line front-block read as :func:`declared_premise`. Empty is a real answer and
    not an error: every spec written before 2026-09-22 predates the field, which is exactly
    what the `signal-column-undeclared` WARN is for.
    """
    m = _SIGNAL_COLUMN_RE.search(spec_text or "")
    return m.group("value").strip() if m else ""
