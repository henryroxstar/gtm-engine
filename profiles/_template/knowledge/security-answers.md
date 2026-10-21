---
source: manual
refreshed: 2026-09-22
review: 90d
triggers: regulatory-update,product-release
---
# Security answers — the evidence pack

Replace this file with your own answers. It is the **only** source `security-review` may answer
from: a questionnaire row with no entry here is refused and routed to a named owner, never
answered from general knowledge about how systems like yours usually work. A plausible answer to a
security questionnaire is a written representation to a customer's risk function, and the person
who signs it is not the one who wrote it.

## How to write an entry

One entry per claim, not per question — a hundred questionnaires ask the same forty things in
different words. Each entry carries four fields, and the last two are what make it usable:

| Field | Why it is here |
|---|---|
| **Claim** | the answer, in one sentence, in your own words |
| **Evidence** | what a customer could be shown: a report, a policy document, a config, a test |
| **Last verified** | a date. An answer nobody has re-checked in two years is a memory, not a control |
| **Owner** | who answers a follow-up. Every entry needs one; "the team" is not a person |

## Status vocabulary — closed, on purpose

Use exactly one of these. They are not interchangeable, and the difference between the first two
is where questionnaires get companies into trouble:

- **Held** — the control exists and the evidence is current. This is the only status that answers
  a question as a yes.
- **In progress** — the work is underway and there is a date. Answer as "in progress, target
  `<date>`", never as a yes with a footnote.
- **Not held** — say so. A "no" with a compensating control named is a normal, survivable answer;
  a yes that turns out to be a no is a misrepresentation.
- **Not applicable** — and say *why* it does not apply. An unexplained N/A reads as evasion.

## Entries

### Certifications and audits

- **Claim:** …
  **Evidence:** …
  **Status:** Held / In progress / Not held / Not applicable
  **Last verified:** YYYY-MM-DD
  **Owner:** …

> Never claim a certification you do not hold, and never let an adjacent one stand in for it.
> "Aligned to", "built to", and "certified" are three different statements, and only one of them
> has an auditor behind it.

### Data handling and residency

- **Claim:** …

### Access control and authentication

- **Claim:** …

### Encryption

- **Claim:** …

### Logging, monitoring and incident response

- **Claim:** …

### Subprocessors and supply chain

- **Claim:** …

### Business continuity

- **Claim:** …

## Questions this pack cannot answer

Keep this list. It is the honest output of every questionnaire run, and it is what tells you which
entry to write next — a question that gets refused three times is an entry somebody should own.
