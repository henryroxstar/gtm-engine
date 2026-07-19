# Provider adapter — GMass

> ⚠️ **Documented, not yet verified.** No GMass connector is authenticated in this repo's runtime
> yet. Verify against the live GMass API before relying on this, and test with a tiny list first.

> ⚠️ **Structurally different from Saleshandy/Apollo.** GMass is a **Gmail mail-merge** tool: it
> sends *through the connected Gmail account* off a spreadsheet/list, and "sequences" are **auto
> follow-ups** attached to a campaign (sent to non-repliers on a schedule). There is no separate
> server-side sequence object the way Saleshandy/Apollo have one — the campaign *is* the unit, and it
> lives against the mailbox. Because sends originate from the user's own Gmail, deliverability and
> daily-send limits are Gmail's, and warmup/domain discipline from `docs/email-optimization.md`
> matters even more.

## The one rule (unchanged)

**Never trigger the send / schedule-to-send-now.** With GMass the "stage vs send" line is: build the
campaign as a **draft** (and define its auto-follow-up stages) but do **not** hit send or set an
immediate/active send schedule that the operator hasn't approved. The operator sends from Gmail/GMass.

## Concept mapping

| Logical step | GMass concept | Notes |
|---|---|---|
| Sending account | the connected Gmail account | Sends originate here — Gmail limits + reputation apply. |
| The sequence | a **campaign** + its **auto follow-up** stages | The campaign holds T1; follow-ups are stages sent to non-repliers after N days. |
| Steps / cadence | follow-up stages with day gaps + stop-on-reply | Map the spec's touches to the initial send + follow-up stages. |
| A/B | subject/content A/B testing (where supported) | Confirm on the live API. |
| Leads | the recipient list (spreadsheet / Google Sheet / list) | PII egress into Google/GMass — confirm first. |
| Stats | campaign reports (read-only) | Safe. |

## Watch-outs

- Everything ships **from the user's Gmail** — respect Gmail's daily send caps; do not size a blast
  that would trip them.
- A GMass draft that is scheduled can auto-send at the scheduled time — leave scheduling to the
  operator, or set no active schedule, so nothing goes out without a human action.
