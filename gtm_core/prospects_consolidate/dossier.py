from __future__ import annotations

import re
from pathlib import Path

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


# Geographic qualifiers only — a suffix naming WHERE a company operates, never WHICH
# part of it. "vertex-chartered-singapore" is the same account as "Vertex Chartered";
# a "…-ventures"/"…-capital" arm is a separate entity with its own buyers and its own
# research. Adding a business-unit word here would let the ``no-dossier`` gate accept a
# subsidiary's dossier in place of the parent's — the failure mode this list exists to
# avoid. Keep it geographic.
_GEO_QUALIFIERS = frozenset(
    {
        "apac",
        "asia",
        "americas",
        "anz",
        "australia",
        "brazil",
        "canada",
        "china",
        "emea",
        "europe",
        "france",
        "germany",
        "global",
        "hong kong",
        "india",
        "indonesia",
        "international",
        "italy",
        "japan",
        "korea",
        "malaysia",
        "mena",
        "mexico",
        "middle east",
        "netherlands",
        "new zealand",
        "nordics",
        "philippines",
        "saudi arabia",
        "sea",
        "singapore",
        "south africa",
        "south korea",
        "spain",
        "sweden",
        "switzerland",
        "taiwan",
        "thailand",
        "uae",
        "uk",
        "us",
        "usa",
        "united kingdom",
        "united states",
        "vietnam",
    }
)


def _drop_geo_suffix(name: str) -> str:
    """``vertex-chartered-singapore`` -> ``vertex chartered``; a name with no known
    geographic suffix comes back unchanged.

    Splits on word boundaries *before* tokenising, deliberately: matching the suffix
    against the collapsed org token would strip a name that merely *ends in* those
    letters ("Nexus" -> "Nex" via ``us``, "Undersea" -> "Under" via ``sea``). Only one
    qualifier is dropped, and never the whole name.
    """
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", name) if p]
    for width in (2, 1):  # two-word qualifiers first ("hong kong", "united kingdom")
        if len(parts) > width and " ".join(parts[-width:]).lower() in _GEO_QUALIFIERS:
            return " ".join(parts[:-width])
    return name


def account_has_dossier(
    profile: str, company: str, company_domain: str = "", content_root: Path | None = None
) -> tuple[bool, str]:
    """True if this account already has a dossier of any kind, checked two ways: the
    canonical slug path, and a fuzzy match against every existing account folder name
    (via the same org-token identity used elsewhere in this pipeline for account dedup)
    — so an account hand-dossiered under a differently-spelled legacy folder (before the
    canonical slug existed) is never silently re-generated under a second, duplicate
    folder. The canonical slug is not retrofitted onto old folders; this check is what
    lets a new-vs-legacy folder name still resolve to the same account.

    The fuzzy pass compares a folder's token against **both** of the account's tokens —
    the domain-derived one and the name-derived one. Comparing only the domain-derived
    token (the shape this had until 2026-08-25) made the whole fallback inert for every
    account whose registered domain *abbreviates* its name, a very common shape for
    banks, airlines and industrials: ``org_token("vc.example", "Vertex Chartered")`` is
    ``vc`` and can never equal ``vertexchartered``, so for that entire class the promise
    in the paragraph above silently did not hold, and the sweep re-generated a second
    folder every time. A single trailing geographic qualifier is tolerated on one side
    (``vertex-chartered-singapore`` matches ``Vertex Chartered``) but never on both, so
    two genuinely distinct regional accounts still read as distinct.

    Exact token matches win over qualifier-tolerant ones, and folders are scanned in
    sorted order, so ``matched_folder_name`` is deterministic when several folders
    could match.

    Public — also the dossier-existence primitive :mod:`gtm_core.account_integrity`
    reuses for its ``no-dossier`` gate; keep this the one place that logic lives.
    Loosening it further trades a duplicate folder for a masked missing-research
    finding, which is the more expensive of the two errors.

    Returns ``(has_dossier, matched_folder_name)`` — ``matched_folder_name`` is ``""``
    when no dossier was found.
    """
    accounts_dir = _accounts_dir(profile, content_root)
    canonical = slug(company)
    if canonical and _folder_has_dossier(accounts_dir / canonical):
        return True, canonical

    domain_token = org_token(company_domain, "")
    name_token = org_token("", company)
    # The account's own name minus a geographic qualifier, for the mirror case where
    # the *company* carries the qualifier and the folder does not.
    company_base = org_token("", _drop_geo_suffix(company)) if name_token else ""
    if not (domain_token or name_token) or not accounts_dir.is_dir():
        return False, ""

    candidates = [f for f in sorted(accounts_dir.iterdir()) if f.name != canonical]
    exact = {t for t in (domain_token, name_token) if t}
    for folder in candidates:
        if org_token("", folder.name) in exact and _folder_has_dossier(folder):
            return True, folder.name
    if not name_token:
        return False, ""
    for folder in candidates:
        folder_token = org_token("", folder.name)
        folder_base = org_token("", _drop_geo_suffix(folder.name))
        # One side qualified, the other bare. Never base-vs-base: that would collapse
        # a "…-singapore" account into a "…-malaysia" one.
        if folder_base == name_token or (company_base and folder_token == company_base):
            if _folder_has_dossier(folder):
                return True, folder.name
    return False, ""


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
        has_dossier, matched = account_has_dossier(
            profile, r["company"], r.get("company_domain", ""), content_root
        )
        if has_dossier:
            continue
        out.append(
            {
                "company": r["company"],
                "company_domain": r.get("company_domain", ""),
                "canonical_slug": slug(r["company"]),
                "existing_legacy_folder": matched or None,
                "why_now": r.get("why_now", ""),
                "cohort": r.get("cohort", ""),
                "top_intent_score": r.get("top_intent_score", ""),
            }
        )
    return out


def tier_a_needing_dossier(profile: str, content_root: Path | None = None) -> list[dict]:
    """Back-compat wrapper — Tier-A only. See :func:`accounts_needing_dossier`."""
    return accounts_needing_dossier(profile, content_root, tier="A")
