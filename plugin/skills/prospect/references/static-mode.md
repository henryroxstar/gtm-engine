# Static-Email Mode Reference Guide

When an operator provides pre-written, fixed email copy and an audience list (such as a strategy pilot or an executive campaign), the engine executes **Static-Email Mode** to enforce safety, delivery windows, and exclusion hygiene without requiring multi-step AI research passes.

## 1. Single-Path Exclusion Filter

Every static audience passes through `gtm_core.static_pipeline.filter_static_audience` which enforces:
1. **Suppression Ledger (`.pool/suppression.csv`)**: Excludes any email, domain, or company marked opted-out, bounced, or suppressed.
2. **Provider DNC List**: Checks Saleshandy global DNC cache.
3. **Colleague Holds & Status**: Excludes accounts in `latest.json` under active hold or conversation.
4. **Already-Enrolled**: Verifies against all active and paused sequence lists registered in `cells.toml`.
5. **Regulator / Competitor Classifier (`gtm_core.account_relation`)**:
   - Refuses central banks, financial regulators, government domains, and direct competitors.
   - Holds adjacent peers and market infrastructure for human approval.
6. **One Person Per Company**: Automatically deduplicates by company domain / token.

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
