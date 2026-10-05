"""The shared regulator / competitor classifier: what is this account to us?

Three steps used to answer that question three ways. The router matched seven government domain
endings and a model-written label; the enrolment gate read only the label; the pool carried
whatever a model had written, or nothing. A central bank was emailed because the only rule that
could have recognised it was a list of endings it does not use, and the one step that read its
name was a model that never ran.

One function now answers it, from tenant files, for every caller::

    index = load_index(profile)                      # regulators.toml + competitors.toml
    index.classify(company, company_domain, email)   # -> Relation | None

**Matching is on an explicit identity, never a first domain label** (a key is a claim about
identity, as detailed in the design docs):

* a listed domain matches itself and everything UNDER it. Its parent, its siblings and the same
  name on another ending do not;
* an ending (``.test``) matches on a dot boundary — ``a.test`` and ``test`` hit,
  ``nottest`` does not;
* a company name or alias matches as an exact ``org_token``, never as a substring;
* a row's own email host is an identity, and a free-mail host never is.

**It can only add.** :func:`classify_rows` never lowers a ``category_relation`` the model wrote.
A deterministic answer that could remove a block would turn a missing file into a permission.

**One policy table** (:data:`POLICY`) says what each kind does at the router and at the gate, and
both read it, so the two cannot drift:

======================================  ==========  ==========
kind                                    router      gate
======================================  ==========  ==========
central-bank, regulator, government     hold        ERROR
exchange, clearing, standards,
self-regulatory, public-health          hold        WARN
competitor-direct                       exclude     ERROR
competitor-adjacent                     hold        WARN
======================================  ==========  ==========

Files and loader: :mod:`gtm_core.account_relation_load`. Gate findings:
:mod:`gtm_core.account_relation_gate`. Audit: :mod:`gtm_core.account_relation_audit`
(``python -m gtm_core.account_relation audit --profile P``).

Pure: no I/O, no model, no network, no ledger.
"""

from __future__ import annotations

import datetime as _dt
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from functools import cached_property

from .competitor_index import CompetitorHit, competitor_lookup, host_chain, row_hosts
from .prospects_consolidate import org_token

__all__ = [
    "ALL_KINDS",
    "BODY_KINDS",
    "COMPETITOR_ADJACENT",
    "COMPETITOR_DIRECT",
    "FLOOR_ENDINGS",
    "HOLD_KINDS",
    "POLICY",
    "REFUSE_KINDS",
    "Action",
    "Allow",
    "Body",
    "Ending",
    "Regulators",
    "Relation",
    "RelationConfigError",
    "RelationIndex",
    "StampResult",
    "classify",
    "classify_rows",
    "main",
]

#: The closed set a ``[[body]]`` may be. A word outside it refuses the load.
BODY_KINDS = (
    "central-bank",
    "regulator",
    "government",
    "exchange",
    "clearing",
    "standards",
    "self-regulatory",
    "public-health",
)
#: Bodies a cold pitch is refused at the gate. They write, supervise or run the rules the pitch
#: appeals to, and a sales email to one reads as lobbying.
REFUSE_KINDS = frozenset({"central-bank", "regulator", "government"})
#: Bodies a human decides about: held at the router, a WARN at the gate.
HOLD_KINDS = frozenset(BODY_KINDS) - REFUSE_KINDS
COMPETITOR_DIRECT = "competitor-direct"
COMPETITOR_ADJACENT = "competitor-adjacent"
ALL_KINDS = (*BODY_KINDS, COMPETITOR_DIRECT, COMPETITOR_ADJACENT)

#: Strongest first. ``classify`` returns the first; ``classify_all`` returns them in this order.
_SEVERITY = (
    COMPETITOR_DIRECT,
    "central-bank",
    "regulator",
    "government",
    "exchange",
    "clearing",
    "standards",
    "self-regulatory",
    "public-health",
    COMPETITOR_ADJACENT,
)
#: Among equal kinds, the more specific evidence first: a listed domain, a name, a stem, an ending.
_SPECIFICITY = {"domain": 0, "name": 1, "stem": 2, "ending": 3}

#: What every tenant gets without a ``regulators.toml``, and what no ``regulators.toml`` can
#: remove: the endings the router has always held. The file adds to them.
FLOOR_ENDINGS = (".gov", ".mil", ".gov.uk", ".gov.sg", ".gov.au", ".gc.ca", ".europa.eu")

_KIND_WORDS = {
    "central-bank": "central bank",
    "regulator": "regulator",
    "government": "government body",
    "exchange": "exchange operator",
    "clearing": "clearing or settlement body",
    "standards": "standards body",
    "self-regulatory": "self-regulatory body",
    "public-health": "public health body",
}


class RelationConfigError(ValueError):
    """A ``regulators.toml`` that is present and wrong. Raised at load, naming the rule.

    A safety list that is partly read is a regulator that gets emailed, so the load refuses as a
    whole; the cost is one fix.
    """


@dataclass(frozen=True)
class Action:
    """What a kind does at each caller. ``router``: exclude | hold. ``gate``: error | warn."""

    router: str
    gate: str


POLICY: dict[str, Action] = {
    **{k: Action("hold", "error") for k in sorted(REFUSE_KINDS)},
    **{k: Action("hold", "warn") for k in sorted(HOLD_KINDS)},
    COMPETITOR_DIRECT: Action("exclude", "error"),
    COMPETITOR_ADJACENT: Action("hold", "warn"),
}


@dataclass(frozen=True)
class Relation:
    """What an account is to us, and the evidence."""

    kind: str
    #: The body's or competitor's name, or the ending that matched.
    entry: str
    #: ``domain:<listed host>`` | ``name:<token>`` | ``stem:<label>`` | ``ending:<.gov.xx>``.
    matched_on: str
    #: One line, used verbatim by the router as the hold detail and by the gate in its finding.
    reason: str
    source: str = ""
    tier: str = ""
    #: The host the match rested on: the listed host, or the row's host for an ending. Blank for a
    #: name match.
    domain: str = ""

    @property
    def router_action(self) -> str:
        return POLICY[self.kind].router

    @property
    def gate_action(self) -> str:
        return POLICY[self.kind].gate

    @property
    def is_body(self) -> bool:
        return self.kind in BODY_KINDS

    @property
    def rank(self) -> tuple[int, int]:
        return _SEVERITY.index(self.kind), _SPECIFICITY[self.matched_on.partition(":")[0]]


@dataclass(frozen=True)
class Body:
    name: str
    kind: str
    domains: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    note: str = ""
    verified: bool | None = None


@dataclass(frozen=True)
class Ending:
    #: Always starts with a dot. ``.test`` matches ``test`` and ``a.test``, never ``nottest``.
    ending: str
    kind: str = "government"
    source: str = "regulators.toml"


@dataclass(frozen=True)
class Allow:
    """A dated, reasoned override. It lets the GATE pass one body; the router still holds."""

    domain: str
    reason: str
    decided: _dt.date
    expires: _dt.date
    email: str = ""

    def active(self, today: _dt.date) -> bool:
        return self.decided <= today <= self.expires


def _under(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _ends_with(host: str, ending: str) -> bool:
    return host == ending.lstrip(".") or host.endswith(ending)


def _stronger(kind: str, than: str) -> bool:
    return _SEVERITY.index(kind) < _SEVERITY.index(than)


@dataclass(frozen=True)
class Regulators:
    """The parsed ``regulators.toml`` plus the built-in endings.

    ``Regulators()`` is the empty, ending-less value (a hand-built router context). A tenant with
    no file gets ``load_regulators`` -> the floor endings only.
    """

    bodies: tuple[Body, ...] = ()
    endings: tuple[Ending, ...] = ()
    allows: tuple[Allow, ...] = ()
    reviewed: str = ""

    @cached_property
    def _by_domain(self) -> dict[str, Body]:
        out: dict[str, Body] = {}
        for body in self.bodies:
            for d in body.domains:
                if d not in out or _stronger(body.kind, out[d].kind):
                    out[d] = body
        return out

    @cached_property
    def _by_name(self) -> dict[str, Body]:
        out: dict[str, Body] = {}
        for body in self.bodies:
            for n in (body.name, *body.aliases):
                tok = org_token("", n)
                if tok and (tok not in out or _stronger(body.kind, out[tok].kind)):
                    out[tok] = body
        return out

    def with_endings(self, endings: Iterable[str], kind: str = "government") -> Regulators:
        """This value plus ``endings`` (normalised, de-duplicated). Used for the floor and for
        the deprecated ``[hold] regulated_domains``."""
        from .account_relation_load import normalise_ending

        have = {e.ending for e in self.endings}
        extra = []
        for raw in endings:
            norm = normalise_ending(str(raw), where="an ending")
            if norm not in have:
                have.add(norm)
                extra.append(Ending(norm, kind, "built-in"))
        return replace(self, endings=(*self.endings, *extra))

    def match(self, company: str, company_domain: str, email: str) -> list[Relation]:
        """Every body or ending this row matches, unsorted."""
        out: list[Relation] = []
        hosts = row_hosts(company_domain, email)
        for host in hosts:
            for cand in host_chain(host):
                if body := self._by_domain.get(cand):
                    if not any(r.matched_on == f"domain:{cand}" for r in out):
                        out.append(
                            self._relation(body, f"domain:{cand}", cand, f"its domain {cand}")
                        )
                    break
        tok = org_token("", company or "")
        if tok and (body := self._by_name.get(tok)) and not any(r.entry == body.name for r in out):
            out.append(self._relation(body, f"name:{tok}", "", "its name"))
        for host in hosts:
            hits = [e for e in self.endings if _ends_with(host, e.ending)]
            if hits:
                best = max(hits, key=lambda e: (len(e.ending), -_SEVERITY.index(e.kind)))
                out.append(
                    Relation(
                        kind=best.kind,
                        entry=best.ending,
                        matched_on=f"ending:{best.ending}",
                        reason=f"domain {host} matches {best.ending}",
                        source=best.source,
                        domain=host,
                    )
                )
        return out

    @staticmethod
    def _relation(body: Body, matched_on: str, domain: str, how: str) -> Relation:
        return Relation(
            kind=body.kind,
            entry=body.name,
            matched_on=matched_on,
            reason=f"{body.name} ({_KIND_WORDS[body.kind]}) — matched {how}",
            source="regulators.toml",
            domain=domain,
        )

    def override_for(
        self, relation: Relation, *, company_domain: str, email: str, today: _dt.date
    ) -> Allow | None:
        """The active ``[[allow]]`` that lets the GATE pass this row, or ``None``.

        Only a refused kind can be allowed (a warned kind needs no override and a competitor is
        never an allow's business). An allow covers a row when one of the row's hosts is the
        allowed domain or under it, narrowed to one address when the allow names one. It is dated
        on both ends: before ``decided`` and after ``expires`` it does nothing.
        """
        if relation.kind not in REFUSE_KINDS:
            return None
        hosts = row_hosts(company_domain, email)
        who = (email or "").strip().lower()
        for allow in self.allows:
            if not allow.active(today):
                continue
            if allow.email and allow.email != who:
                continue
            if any(_under(h, allow.domain) for h in hosts):
                return allow
        return None

    def expired_allows(self, today: _dt.date) -> list[Allow]:
        return [a for a in self.allows if a.expires < today]


@dataclass(frozen=True, eq=False)
class RelationIndex:
    regulators: Regulators
    competitors: Mapping[str, CompetitorHit] = field(default_factory=dict)

    def classify_all(self, company: str, company_domain: str, email: str) -> list[Relation]:
        """Every relation this row has, strongest first (see :data:`_SEVERITY`)."""
        rels = self.regulators.match(company or "", company_domain or "", email or "")
        found = competitor_lookup(
            company or "", company_domain or "", self.competitors, email=email or ""
        )
        if found:
            hit, how = found
            rels.append(
                Relation(
                    kind=COMPETITOR_DIRECT if hit.direct else COMPETITOR_ADJACENT,
                    entry=hit.entry or hit.summary.partition(" (")[0],
                    matched_on=how,
                    reason=hit.summary,
                    source="competitors.toml",
                    tier=hit.tier,
                    domain=how.partition(":")[2] if how.startswith("domain:") else "",
                )
            )
        return sorted(rels, key=lambda r: r.rank)

    def classify(self, company: str, company_domain: str, email: str) -> Relation | None:
        rels = self.classify_all(company, company_domain, email)
        return rels[0] if rels else None


def classify(
    company: str, company_domain: str, email: str, index: RelationIndex
) -> Relation | None:
    """The strongest relation of one row, or ``None``. ``None`` means nothing matched; a row with
    no company, domain or email cannot be identified at all and :func:`classify_rows` counts it."""
    return index.classify(company, company_domain, email)


# --- stamping the pool -------------------------------------------------------------------------

#: How hard each ``category_relation`` word blocks. Blank, ``unclear`` and ``prospect`` carry no
#: adverse relation here; ``partner`` and ``adjacent`` warn; ``competitor`` and ``regulator`` stop.
_LABEL_RANK = {
    "": 0,
    "unclear": 0,
    "prospect": 0,
    "partner": 1,
    "adjacent": 1,
    "competitor": 2,
    "regulator": 2,
}
#: The label a kind implies, for the kinds that are allowed to write one. The others (a hold kind,
#: an adjacent competitor) never rewrite a label: stamping ``adjacent`` over a blank would turn a
#: row the gate blocks (``relation-unresolved``) into one it merely warns about.
_IMPLIED = {**dict.fromkeys(sorted(REFUSE_KINDS), "regulator"), COMPETITOR_DIRECT: "competitor"}


@dataclass
class StampResult:
    rows: list[dict]
    #: Rows by their strongest relation's kind. A row with no relation is not counted.
    by_kind: dict[str, int] = field(default_factory=dict)
    #: Rows whose ``category_relation`` the classifier set.
    stamped: int = 0
    #: Rows with no company, no domain and no email: nothing could be matched, which is not the
    #: same as "clean", and is reported as its own number.
    unclassifiable: int = 0


def classify_rows(rows: Iterable[dict], index: RelationIndex) -> StampResult:
    """Stamp ``category_relation`` and ``relation_source`` on COPIES of ``rows``.

    ``category_relation`` is only ever RAISED: it takes the classifier's word when that word blocks
    harder than what the row carries, and keeps the model's word otherwise. ``relation_source``
    says where the label came from:

    * ``classifier:<kind>:<entry>`` — the classifier set the label;
    * ``classifier-agrees:<kind>:<entry>`` — the model's label already says the same;
    * ``classifier-note:<kind>:<entry>`` — a hit that does not rewrite a label (a hold kind, an
      adjacent competitor, or a stronger model word of a different kind).

    This is the function the pool build calls; applying it is idempotent. Consolidate's call site
    is a one-line follow-up held by another change (PRD §1).
    """
    out = StampResult(rows=[])
    for src in rows:
        row = dict(src)
        company, domain, email = (
            str(row.get(k) or "").strip() for k in ("company", "company_domain", "email")
        )
        if not (company or domain or email):
            out.unclassifiable += 1
            out.rows.append(row)
            continue
        rel = index.classify(company, domain, email)
        if rel is None:
            out.rows.append(row)
            continue
        out.by_kind[rel.kind] = out.by_kind.get(rel.kind, 0) + 1
        label = str(row.get("category_relation") or "").strip().lower()
        implied = _IMPLIED.get(rel.kind)
        tag = f"{rel.kind}:{rel.entry}"
        if implied is None:
            source = f"classifier-note:{tag}"
        elif label == implied:
            source = f"classifier-agrees:{tag}"
        elif _LABEL_RANK.get(label, 0) >= _LABEL_RANK[implied]:
            source = f"classifier-note:{tag}"
        else:
            row["category_relation"] = implied
            out.stamped += 1
            source = f"classifier:{tag}"
        if str(row.get("relation_source") or "") == f"classifier:{tag}":
            source = f"classifier:{tag}"  # a second pass over a stamped row changes nothing
        row["relation_source"] = source
        out.rows.append(row)
    return out


# --- CLI ------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """``python -m gtm_core.account_relation audit --profile P [--product S]``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] != "audit":
        print(
            "usage: python -m gtm_core.account_relation audit --profile P [--product S]",
            file=sys.stderr,
        )
        return 2
    from .account_relation_audit import main as audit_main

    return audit_main(args[1:])


if __name__ == "__main__":
    sys.exit(main())
