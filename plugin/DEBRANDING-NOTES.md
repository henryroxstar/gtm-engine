# plugin/ — the GTM engine (gtm-engine)

This holds the company-agnostic plugin (skills + knowledge). **Phase 0, step 1:** import the existing `gtm-engine/` plugin here (`.claude-plugin/plugin.json`, `skills/`, `knowledge/`, `PROFILE.template.md`), then de-brand it per spec §4A.

**De-branding rule (CI-enforced):** no hardcoded company facts in `skills/` — `grep -ri "<company-name>" plugin/skills/` must return nothing. All company-specific content lives in `../profiles/<company>/knowledge/`.

**Skill buckets (spec §4A):**
- Engine-pure (carousels, build-deck, setup, deck-research mechanics) — read brand from active profile.
- Knowledge-bound (market-scan, prospect, draft-outreach, call-prep, account-plan, gtm-planning, events-tracker) — move company facts into the profile knowledge pack.
- Product-bound (solution-design, gateway-runbook) — gate via `requires_product`.

**New Content OS skills (build company-agnostic from day one):** content-radar, content-plan, content-research, content-studio, podcast-studio, content-publish, content-os.

Plugin version target: **v0.7.0**.
