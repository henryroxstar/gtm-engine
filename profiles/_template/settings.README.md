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

## Stop lines for live sequences (read by `gtm_core.sequence_health`)

All optional. A key that is present but invalid stops the check (exit 3); it never falls back to the default.

- **`unsubscribe_stop_line`** *(fraction above 0 and at most 1, default: 0.05)*:
  Pause when unsubscribed ÷ contacted is strictly over this, per sequence and per step, once at least 20 people have been contacted at that level.

- **`bounce_stop_line`** *(fraction, default: 0.05)*:
  Pause when bounced ÷ sent is strictly over this, per sequence and per step, once at least 20 emails have gone.

- **`block_bounce_stop_line`** *(fraction, default: 0.02)*:
  Pause when emails refused outright by the receiving server ÷ sent is strictly over this. Per sequence only: the per-step feed carries no bounce type.

- **`mailbox_daily_cap`** *(whole number of 1 or more)*:
  Emails one mailbox may send in a day. With it set, a mailbox that sent more in one day (read from the sends, or from `mailbox_daily_volume`) is a breach.

- **`mailbox_daily_volume`** *(whole number of 0 or more)*:
  Normally not set: the peak is derived from the sends. Use it, or the `--mailbox-daily-volume` flag, to compare a figure you already hold against the cap.

Separately, three or more unsubscribes among a step's first 20 emails sent is always an early warning (no setting).
