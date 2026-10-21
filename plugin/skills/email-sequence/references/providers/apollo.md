# Provider adapter — Apollo.io

> ⚠️ **Documented, not yet verified.** No Apollo connector is authenticated in this repo's runtime
> yet. This adapter records how the logical flow *maps* onto Apollo so enabling it later is a config
> change (`email_tool: apollo` + wiring the Apollo MCP), not a skill rewrite. **Verify every tool
> name + parameter against the live Apollo MCP before relying on this**, and stage a tiny test
> sequence first.

## The one rule (unchanged)

**Never start / launch / activate an Apollo sequence.** Apollo sequences send from the connected
mailbox once active; activation is the operator's. Build the sequence, add contacts, and stop.

## Concept mapping

Apollo's "sequences" (formerly "emailer campaigns") are the equivalent object:

| Logical step | Apollo concept | Notes |
|---|---|---|
| Find sending account | connected mailbox / linked email account | Confirm an active mailbox exists. |
| Create the sequence | create an **emailer campaign / sequence** | Starts in a draft/off state. |
| Add one step per touch | add **sequence steps** (`auto_email` / `manual_email` + wait steps) | Day offsets are expressed as inter-step delays; translate the spec's absolute day-offsets into deltas. |
| A/B variant | step template variants (where supported) | Confirm variant support on the live API. |
| Schedule | campaign sending schedule / working hours | Set days + window + timezone. |
| Enroll leads | **add contacts to the sequence** | Contacts must exist in Apollo (import first). PII egress — confirm first. |
| Read stats | sequence analytics (read-only) | Safe. |

## Watch-outs

- Apollo distinguishes **`auto_email`** (sends automatically when active) from **`manual_email`**
  (creates a task). For a hands-off cadence use auto steps — but that makes activation the *only*
  thing standing between staging and live sends, so the never-activate rule is even more load-bearing.
- Apollo enforces its own mailbox sending limits + email health; respect them.
- Cadence in Apollo is **relative** (delays between steps), unlike Saleshandy's absolute
  `absoluteDays` — convert the spec's day offsets to deltas.
