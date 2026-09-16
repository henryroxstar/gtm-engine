"""Fold every un-consolidated prospect-run export into one deliverability-gated,
DNC-clean master list.

Each prospect run emits its own ``prospects-<date>-<cohort>-hubspot.csv`` (see
:mod:`gtm_core.prospects_import`), but folding those into a loadable sequencer
list has always been a manual end-of-flow step — one the operator often can't
reach because a run gets interrupted or the flow is only run partway. Exports
then pile up un-consolidated and silently invisible: ``latest.json`` is
account/dashboard-level and never holds person+email rows, so nothing surfaces
the backlog. On 2026-07-23 that gap hid 370 real emails across 25+ exports
(and 26 live DNC violations inside them) behind a stale, hand-built master list.

This module is the plumbing half of the fix — it is deliberately MCP-free
(stdlib-only, matching the rest of gtm_core) so it can run standalone, on a
schedule, or mid-flow after a partial run. The judgment/MCP half (fetching the
*live* Saleshandy DNC list) belongs to the skill/agent layer, which dumps it to
a small cache file first — see ``dnc_cache_path`` below.

One-file-for-humans layout (the "make it simple" requirement): the operator was
drowning in near-identical lists. So there is exactly **one** file a human ever
looks at — ``sequences/ready-to-load.csv`` (the load-me list) — and everything
else (the full ``master-list.csv`` audit trail, the ``needs-verification.csv``
hold queue, snapshots, the blocked log) lives in a hidden ``sequences/.pool/``
dir. The mental model is then literally what the folder shows: one file.

Person-level identity, not just email (the "don't re-burn a contact" fix): the
email-only DNC/sent filter misses the same person re-resolved under a second
address (``sam@vertex.example`` on DNC, ``sortega@vertex.example`` in a later run) and
double-counts one person carrying two email formats (``robin.kraft@`` +
``rkraft@``). So the loadable outputs are additionally deduped by a person key
(normalized first+last + org token from the domain/company) — a row is dropped
if that person was already sent under *any* address, and same-person duplicates
collapse to the highest-confidence one. The full ``master-list.csv`` keeps every
address for audit; only the loadable files are person-unique.

Deliverability gate (the "make it the default" requirement): an email with no
verification signal is exactly the kind of unknown that tanks bounce rate
during mailbox ramp-up (see the ramp-up lessons in email-sequence skill docs).
So a row is classified into a confidence tier from whatever ``email_status``/
``conf`` signal its source export carried (RocketReach grade, "verified",
"account-folder-verified", "site-published", or nothing at all):

  * ``high``    — RocketReach A/A-, "verified", "account-folder-verified"; or
                  "site-published" (a generic inbox pulled directly off the
                  company's own official site, e.g. `hello@` on `acme.com`
                  found on `acme.com`'s own contact page) **when** the row's
                  ``segment`` is Builder or Startup **and** the email's domain
                  actually matches the company's own domain
                  -> written to ``ready-to-load.csv`` (safe to load today)
  * ``medium``  — RocketReach B, "found", ``conf`` in (high, med) with no
                  grade; or a domain-matched "site-published" row outside
                  Builder/Startup (an Enterprise "info@" reaches a mailroom,
                  not a buyer — worth a human glance, not a default trust)
                  -> written to ``needs-verification.csv`` (verify before load)
  * ``unknown`` — no signal at all (the common case for a fresh raw export);
                  or a "site-published" row whose email domain does NOT match
                  the company's own domain (the claim the status makes is
                  false on its face, regardless of segment)
                  -> also ``needs-verification.csv``
  * ``blocked`` — RocketReach F(pattern), ``conf`` "invalid"
                  -> excluded from every loadable file, logged to
                  ``.blocked-log.jsonl`` for audit, never silently dropped

Only ``high`` confidence rows land in ``ready-to-load.csv`` by default. The
"site-published" status is stamped by the `prospect` skill's web-fallback step,
never guessed by this module — it only ever adjudicates a status a source
export already carried (see ``plugin/skills/prospect/references/discovery-and-budget.md``
"Web-search fallback").
"""

from __future__ import annotations

from .accounts import (  # noqa: F401
    _AUTHORITATIVE_RECORD_COLUMNS,
    _INHERITED_RECORD_COLUMNS,
    _VERDICT_STRICTNESS,
    _account_id_index,
    _account_item_of,
    _account_key_of,
    _account_record_index,
    _disqualified_account_keys,
    _verdict_at_least_as_strict,
)
from .cli import _cli  # noqa: F401

# Eager, complete re-export of the pre-split module surface (PRD §5 rule 1):
# every submodule is imported here, so module-level registrations run on
# `import <package>` exactly as they did on `import <module>`.
from .columns import (  # noqa: F401
    _ALIASES,
    _ASSIGNED_COLUMNS,
    _COLUMN_NOTES,
    MASTER_COLS,
    column_value,
    csv_map_markdown,
)
from .confidence import (  # noqa: F401
    _BLOCKED_STATUS,
    _CONF_RANK,
    _HIGH_STATUS,
    _MEDIUM_STATUS,
    _SITE_PUBLISHED,
    _SITE_PUBLISHED_SEGMENTS,
    _get,
    _person_key,
    _row_to_record,
    _same_domain,
    _score_num,
    classify_confidence,
    org_token,
)
from .consolidate import consolidate  # noqa: F401
from .dossier import (  # noqa: F401
    _DOSSIER_GLOB_PATTERNS,
    _GEO_QUALIFIERS,
    DOSSIER_GLOB_BRIEF,
    DOSSIER_GLOB_FULL,
    DOSSIER_GLOB_ONEPAGER,
    _drop_geo_suffix,
    _folder_has_dossier,
    account_has_dossier,
    accounts_needing_dossier,
    tier_a_needing_dossier,
)
from .io import (  # noqa: F401
    _append_blocked_log,
    _atomic_write_csv,
    _atomic_write_csv_cols,
    _existing_master_path,
    _load_master,
    _snapshot,
)
from .paths import (  # noqa: F401
    _accounts_dir,
    _pool_dir,
    _prospects_dir,
    _sequences_dir,
    dnc_cache_path,
    hand_send_path,
    needs_verification_path,
    ready_to_load_path,
)
from .queues import (  # noqa: F401
    _print_banner,
    next_verification_batch,
    pool_status,
    split_by_signal,
)
from .suppression import (  # noqa: F401
    DNC_MAX_AGE_HOURS,
    DncSuppression,
    MarketGate,
    _load_dnc,
    _load_sent,
    _parse_fetched_at,
    _resolve_market_gate,
)

__all__ = [
    "MASTER_COLS",
    "classify_confidence",
    "org_token",
    "ready_to_load_path",
    "hand_send_path",
    "needs_verification_path",
    "dnc_cache_path",
    "DNC_MAX_AGE_HOURS",
    "DncSuppression",
    "MarketGate",
    "csv_map_markdown",
    "consolidate",
    "split_by_signal",
    "pool_status",
    "DOSSIER_GLOB_FULL",
    "DOSSIER_GLOB_ONEPAGER",
    "DOSSIER_GLOB_BRIEF",
    "account_has_dossier",
    "accounts_needing_dossier",
    "tier_a_needing_dossier",
    "next_verification_batch",
]
