# Per-Profile Settings (`settings.json`)

When running with a profile, custom runtime settings can be placed in `content/<profile>/settings.json`.
Use `settings.template.json` as a starting point.

## Fields

- **`monthly_budget_usd`** *(number, default: 50.0)*:
  Monthly spending limit in USD for paid external APIs and tools.
  Note: `monthly_tool_budget_usd` in `PROFILE.md` is the primary source of truth; this setting acts as a legacy fallback.

- **`generic_lane_share_cap`** *(number between 0.0 and 1.0, default: 0.5)*:
  Maximum share of candidates allocated to generic outreach lanes before gating.

- **`higgsfield_plan`** *(string, e.g. `"standard"`, `"pro"`, `"ultra"`)*:
  Higgsfield subscription plan tier used for video credit calculations.

- **`higgsfield_billing`** *(string, `"monthly"` or `"annual"`)*:
  Higgsfield billing frequency used for credit cost calculations.
