# Static-Email Mode Reference Guide

When an operator provides pre-written, fixed email copy and an audience list (such as a strategy pilot or an executive campaign), the engine executes **Static-Email Mode** to enforce safety, delivery windows, and exclusion hygiene without requiring multi-step AI research passes.

## 1. Single-Path Exclusion Filter

Every static audience passes through `gtm_core.static_pipeline.filter_static_audience` which enforces:
1. **Suppression Ledger (`.pool/suppression.csv`)**: Excludes any listed email, and every address at a listed `company_domain` — stricter than the main pipeline, which blocks a whole company only for company-level reasons (reason `suppressed`).
2. **Provider Do-Not-Contact list**: Not read here. Saleshandy refuses to send to anyone on its own list, and opt-outs are copied into the suppression ledger (step 1), which is read.
3. **Account status (`latest.json`)**: Excludes everyone at an account that opted out, is do-not-contact, disqualified, closed, held, or already in conversation (replied, meeting, customer…) — reason `account-<status>`. This is the enrollment gate's own check (`gtm_core.enrollment_gate`), which a static list never otherwise reaches because it is not enrolled with `--require-verdict`; a ledger that is present but unreadable stops the run.
4. **Already-Contacted / Already-Enrolled**: Excludes anyone already emailed (`sequences/*.csv` rows marked `SENT` and every `sequences/contacted-*.csv` row; reason `already-contacted`), then anyone on a list registered in `cells.toml` for a live sequence (reason `already-enrolled`). `DRAFT-*` ids and sequences `history.jsonl` records as deleted do not count as enrolled; a deleted sequence that sent is protected by the contacted roster instead. Both sets come from the lane router's loaders (`gtm_core.lanes.context`), so static mode and the router agree.
5. **Existing Customer (`knowledge/outreach-case-studies.txt`)**: Excludes any company on the case-study / customer roster, matched as a whole word on company name, company domain and email domain (reason `existing-customer`); a roster that is present but unreadable stops the run.
6. **Regulator / Competitor Classifier (`gtm_core.account_relation`)**:
   - Refuses central banks, financial regulators, government domains, and direct competitors.
   - Holds adjacent peers and market infrastructure for human approval.
7. **One Person Per Company**: Automatically deduplicates by company domain / token.

## 2. Copy Capture & Follow-up Rules

Static copy is captured verbatim with named lint waivers (e.g. word length or punctuation).
To avoid triggering automated mailbox security scanner unsubscribes:
- **Step 1**: Operator's fixed subject and body.
- **Step 2+ (Follow-ups)**: Must default to starting its **own new thread** (non-blank subject, e.g. "agent governance in your deals") rather than a threaded `Re:`.
- **Gaps**: Minimum wait between steps is **5 days** (default: day 1, day 6, day 12).

## 3. Regional Send Windows

Saleshandy enforces one schedule per sequence. To prevent emailing prospects in their overnight hours:
- The audience is mapped against `knowledge/send-windows.toml`.
- Prospects are partitioned into separate sequence copies by region (e.g., US business hours vs. SG/APAC business hours).

## 4. Staging, Pilot Capping, and Verification

1. **Pilot Batch**: Each regional sequence is capped at a pilot size (default: 25 prospects).
2. **Staged PAUSED**: Sequences are created in Saleshandy with real sequence IDs and staged with `active: false`.
3. **History Logging**: Every stage and enrollment is recorded to `history.jsonl`.
4. **Pilot Evaluation**: The remaining prospects (`rest`) are held until the pilot run completes its step 1 and step 2 safety reads using `python -m gtm_core.sequence_health`.
