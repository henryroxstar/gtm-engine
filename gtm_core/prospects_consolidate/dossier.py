from __future__ import annotations

from pathlib import Path

from ..account_folder import AmbiguousFolder, resolve
from ..slugify import slug
from .confidence import org_token
from .io import _load_master
from .paths import _accounts_dir, _pool_dir

# Per-variant glob patterns — public, and the ONE place each dossier variant's file
# naming is defined. gtm_core.account_integrity.dossier_depth() imports these three
# directly rather than keeping its own copy: a second hand-maintained list is exactly
# how the "research pack" variant below went unrecognized for a day after it shipped.
DOSSIER_GLOB_FULL = ("account-dossier-*",)
DOSSIER_GLOB_ONEPAGER = ("*-onepager-*",)
DOSSIER_GLOB_BRIEF = (
    "prospecting-brief-*",
    # the "research pack, no visuals" variant (see account-dossier/SKILL.md §"Research
    # pack variant") — a markdown-only dossier used for bulk coverage where a
    # docx+PDF+images pass per account isn't affordable. Named `dossier-<slug>-<date>.md`
    # to stay visually distinct from the docx `account-dossier-*` full variant. Added
    # 2026-08-13 after this pattern list caused a false `no-dossier` reading on all 439
    # accounts dossiered this way on 2026-08-12 — the files existed, this glob just
    # never looked for them.
    "dossier-*.md",
)
_DOSSIER_GLOB_PATTERNS = DOSSIER_GLOB_FULL + DOSSIER_GLOB_ONEPAGER + DOSSIER_GLOB_BRIEF


def _folder_has_dossier(folder: Path) -> bool:
    if not folder.is_dir():
        return False
    return any(any(folder.glob(pat)) for pat in _DOSSIER_GLOB_PATTERNS)


def dossier_folder(
    profile: str, company: str, company_domain: str = "", content_root: Path | None = None
) -> tuple[bool, str, str]:
    """``(has_dossier, folder_name, reason)`` for this account, answered from the ONE
    folder :func:`gtm_core.account_folder.resolve` says is this account's — the same
    resolver every skill writes a dossier through. ``folder_name`` is ``""`` when no
    dossier was found; ``reason`` is ``"folder-ambiguous: <candidate>, <candidate>"``
    when more than one existing folder could be the account, else ``""``.

    Until 2026-09-25 this ran its own fuzzy match over every folder name, so the gate
    READ a different folder than the skills WROTE (AF1). Its worst shape: the account's
    own folder exists and is empty, while a parent sharing its domain has a dossier —
    and the gate passed on the parent's research. Hence, deliberately:

    * resolved existing folder → has a dossier iff that folder holds one;
    * no existing folder (``resolve`` would mint a new one) → no dossier. A shorter
      legacy folder that merely begins the name is not borrowed;
    * ambiguous → no dossier (fail closed): picking one candidate is the same guess
      ``resolve`` refuses to make.
    """
    if not slug(company):
        # ``resolve`` raises ValueError on an empty name; checked here rather than caught
        # there, so a ValueError from the profile-segment guard or a malformed ledger
        # still propagates instead of reading as "no dossier".
        return False, "", ""
    try:
        folder, _rung = resolve(company, profile, company_domain, content_root)
    except AmbiguousFolder as e:
        return False, "", f"folder-ambiguous: {', '.join(e.candidates)}"
    if _folder_has_dossier(_accounts_dir(profile, content_root) / folder):
        return True, folder, ""
    return False, "", ""


def account_has_dossier(
    profile: str, company: str, company_domain: str = "", content_root: Path | None = None
) -> tuple[bool, str]:
    """True if this account's own folder — the one :func:`gtm_core.account_folder.resolve`
    returns — holds a dossier of any kind. See :func:`dossier_folder`, which also says
    why a lookup came back empty (``folder-ambiguous``).

    Public — the dossier-existence primitive :mod:`gtm_core.account_integrity` reuses for
    its ``no-dossier`` gate. The folder question has one owner, ``account_folder``;
    widening what counts as this account's folder belongs there, never here — a second
    matcher is how the gate came to pass on another company's research.

    Returns ``(has_dossier, matched_folder_name)`` — ``matched_folder_name`` is ``""``
    when no dossier was found.
    """
    has, folder, _reason = dossier_folder(profile, company, company_domain, content_root)
    return has, folder


def accounts_needing_dossier(
    profile: str, content_root: Path | None = None, *, tier: str | None = "A"
) -> list[dict]:
    """Accounts in ``master-list.csv`` with no dossier yet — the candidate list the
    ``prospect`` skill's dossier sweep loops over to auto-generate a prospecting brief +
    outreach draft for each. One entry per account (deduped by the same ``org_token``
    identity used throughout this module), not per person.

    ``tier="A"`` (default) preserves the original Tier-A-only sweep. Pass ``tier=None``
    to check every account regardless of tier — the wider check the sweep now runs
    before a bulk sequence load, since a Tier-B/bulk-sourced account reaching a merge
    sequence with zero research behind its Why Now clause is exactly the gap that let
    439 of 496 contacts ship with no dossier on 2026-08-12.

    Idempotent by construction: re-running this after the sweep generates a dossier
    for an account removes that account from the list on its own — no separate state
    file needed, the filesystem is the source of truth (see ``account_has_dossier``).
    """
    master = _load_master(_pool_dir(profile, content_root) / "master-list.csv")
    seen_tokens: set[str] = set()
    out: list[dict] = []
    for r in master:
        if tier is not None and (r.get("tier") or "").strip().upper() != tier.upper():
            continue
        token = org_token(r.get("company_domain", ""), r.get("company", ""))
        if not token or token in seen_tokens:
            continue
        seen_tokens.add(token)
        has_dossier, _ = account_has_dossier(
            profile, r["company"], r.get("company_domain", ""), content_root
        )
        if has_dossier:
            continue
        out.append(
            {
                "company": r["company"],
                "company_domain": r.get("company_domain", ""),
                "canonical_slug": slug(r["company"]),
                "why_now": r.get("why_now", ""),
                "cohort": r.get("cohort", ""),
                "top_intent_score": r.get("top_intent_score", ""),
            }
        )
    return out


def tier_a_needing_dossier(profile: str, content_root: Path | None = None) -> list[dict]:
    """Back-compat wrapper — Tier-A only. See :func:`accounts_needing_dossier`."""
    return accounts_needing_dossier(profile, content_root, tier="A")
