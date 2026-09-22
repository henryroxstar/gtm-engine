"""The deterministic hold and exclude triggers. Pure functions over (row, context, judge).

Each returns ``(trigger_id, detail)`` or ``None``. They never read the judge's verdict
WORD to decide an account-level question — only its normalised defect scope — and they
never look at the free-text reason column, which is mostly email-body text.
"""

from __future__ import annotations

from ..account_integrity import competitor_match
from ..adjudication import Adjudication
from ..adjudication.defects import defect_scope, normalize_defect_class
from ..prospects_consolidate.confidence import _person_key, _row_to_record, org_token
from ..prospects_state import _identity_keys
from ..signal_record import CategoryRelation
from .context import RouterContext

Hit = tuple[str, str] | None


def _email(row: dict) -> str:
    return (row.get("email") or "").strip().lower()


def _identity(row: dict) -> list[str]:
    return _identity_keys(
        {
            "domain": (row.get("company_domain") or "").strip().lower(),
            "id": row.get("id") or "",
            "company": row.get("company") or "",
            "account_id": row.get("account_id") or "",
        }
    )


# --- excludes ------------------------------------------------------------------------


def suppressed(row: dict, ctx: RouterContext) -> Hit:
    col = (row.get("suppression") or "").strip()
    if col:
        return "suppressed", col
    if ctx.suppression is not None and ctx.suppression.match(row):
        return "suppressed", "in the suppression ledger"
    return None


def optout(row: dict, ctx: RouterContext) -> Hit:
    return ("optout", "opt-out detected in history") if _email(row) in ctx.optouts else None


def already_enrolled(row: dict, ctx: RouterContext) -> Hit:
    cell = ctx.enrolled.get(_email(row))
    return ("already-enrolled", f"in registered list {cell}") if cell else None


def competitor_direct(row: dict, ctx: RouterContext) -> Hit:
    hit = competitor_match(
        row.get("company", ""),
        row.get("company_domain", ""),
        ctx.competitors,
        email=row.get("email", ""),
    )
    return ("competitor-direct", hit.summary) if hit and hit.direct else None


EXCLUDES = (suppressed, optout, already_enrolled, competitor_direct)


# --- holds ---------------------------------------------------------------------------


def competitor_adjacent(row: dict, ctx: RouterContext, judge: Adjudication | None) -> Hit:
    hit = competitor_match(
        row.get("company", ""),
        row.get("company_domain", ""),
        ctx.competitors,
        email=row.get("email", ""),
    )
    return ("competitor-adjacent", hit.summary) if hit and not hit.direct else None


def partner(row: dict, ctx: RouterContext, judge: Adjudication | None) -> Hit:
    rel = (row.get("category_relation") or "").strip().lower()
    return ("partner", f"category_relation={rel}") if rel == CategoryRelation.PARTNER else None


def regulator(row: dict, ctx: RouterContext, judge: Adjudication | None) -> Hit:
    rel = (row.get("category_relation") or "").strip().lower()
    if rel == CategoryRelation.REGULATOR:
        return "regulator", "category_relation=regulator"
    domain = (row.get("company_domain") or "").strip().lower()
    for suffix in ctx.regulated_suffixes:
        if domain and (domain == suffix.lstrip(".") or domain.endswith(suffix)):
            return "regulator", f"domain {domain} matches {suffix}"
    if ctx.regulated_industries:
        for key in _identity(row):
            industry = ctx.industries.get(key, "")
            if industry and any(term in industry for term in ctx.regulated_industries):
                return "regulator", f"industry {industry!r}"
    return None


def prior_contact(row: dict, ctx: RouterContext, judge: Adjudication | None) -> Hit:
    if _email(row) in ctx.prior_emails:
        return "prior-contact", "this address was already emailed"
    pk = _person_key(_row_to_record(row, "pool"))
    if pk and pk in ctx.prior_people:
        return "prior-contact", "this person was already emailed at another address"
    return None


def negative_reply(row: dict, ctx: RouterContext, judge: Adjudication | None) -> Hit:
    return (
        ("negative-reply", "a negative reply is on record") if _email(row) in ctx.negative else None
    )


def engaged_account(row: dict, ctx: RouterContext, judge: Adjudication | None) -> Hit:
    for key in _identity(row):
        status = ctx.statuses.get(key, "")
        if status in ctx.engaged_statuses:
            return "engaged-account", f"latest.json status={status}"
    return None


def strategic_account(row: dict, ctx: RouterContext, judge: Adjudication | None) -> Hit:
    tok = org_token(row.get("company_domain", ""), row.get("company", ""))
    return (
        ("strategic-account", "on strategic-accounts.toml")
        if tok and tok in ctx.strategic
        else None
    )


def judge_account_scope(row: dict, ctx: RouterContext, judge: Adjudication | None) -> Hit:
    if judge is None or judge.unscored or judge.verdict != "drop":
        return None
    if defect_scope(judge.defect_class) != "account":
        return None
    return "judge-account-scope", judge.note or judge.evidence or normalize_defect_class(
        judge.defect_class
    )


def researcher_drop(row: dict, ctx: RouterContext, judge: Adjudication | None) -> Hit:
    if (row.get("verdict") or "").strip().lower() != "drop":
        return None
    return "researcher-drop", (row.get("verdict_reason") or "").strip() or "research verdict drop"


def untraceable_number(row: dict, ctx: RouterContext, judge: Adjudication | None) -> Hit:
    if judge is None or "untraceable=" not in (judge.grounding or ""):
        return None
    flag = next(f for f in judge.grounding.split(";") if f.startswith("untraceable="))
    return "untraceable-number", flag.replace(
        "untraceable=", "numbers not in the case-study corpus: "
    )


#: Row-scoped holds, in :data:`gtm_core.lanes.model.HOLD_ORDER` — minus the two that need
#: the provisional lane (``tier-a-generic``) or the whole batch (``duplicate-contact``);
#: the router applies those in a second pass.
HOLDS = (
    competitor_adjacent,
    partner,
    regulator,
    prior_contact,
    negative_reply,
    engaged_account,
    strategic_account,
    judge_account_scope,
    researcher_drop,
    untraceable_number,
)


def first_exclude(row: dict, ctx: RouterContext) -> Hit:
    for fn in EXCLUDES:
        hit = fn(row, ctx)
        if hit:
            return hit
    return None


def first_hold(row: dict, ctx: RouterContext, judge: Adjudication | None) -> Hit:
    for fn in HOLDS:
        hit = fn(row, ctx, judge)
        if hit:
            return hit
    return None
