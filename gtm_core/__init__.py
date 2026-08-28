"""gtm_core — shared portable core for the GTM Content OS.

Stdlib-only, SDK-free. Usable by both:
  - Tier 2 (VPS harness): imported via agent/ re-export shims.
  - Tier 1 (local plugin): bundled under plugin/lib/gtm_core/.

Modules:
  paths       — path resolution with GTM_CONTENT_ROOT / GTM_PROFILES_ROOT env overrides.
  ledgers     — per-profile history / cost / run-manifest ledgers.
  ledger_cli  — CLI over Ledgers (skills shell out to this).
  locks       — cross-process advisory flock for the content/ volume.
  radar       — deterministic dedupe + cluster + score for content-radar.
  capabilities — runtime capability probe + tier resolver (Entitlement, RuntimeKind, resolve_effective).
  tiers       — capability tier enum (CORE/PIPELINE/PRODUCTION).
  tenancy     — data-spine types (WorkspaceId, Workspace, TenantContext) + StorageAdapter ABC.
  skills      — canonical GTMSkill manifests + SKILL.md codegen (source of truth for plugin skills).
"""
