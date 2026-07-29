# Prospect — HubSpot CSV column map

> From routine v2.3 §12.5. **No live CRM connection** (your CRM policy may disallow live sync) — this CSV is for **manual import**. Output file: `prospects-YYYYMMDD-hubspot.csv`. One row per **contact** (not per account): a Tier-A account with 2 enriched contacts = 2 rows. HubSpot dedupes on `Email`, so re-importing is safe (existing records update, not duplicate).

## Required columns (HubSpot standard properties)

| CSV Column | Source | Notes |
|---|---|---|
| `First Name` | Persona enrichment | split from full name |
| `Last Name` | Persona enrichment | split from full name |
| `Email` | Persona enrichment (verified) | required for dedup; on web-only runs, leave blank if unverified rather than guess |
| `Phone Number` | Persona enrichment | optional |
| `Job Title` | Persona enrichment | |
| `LinkedIn Bio URL` | Persona enrichment | full LinkedIn profile URL |
| `Company Name` | Firmographic | |
| `Company Domain Name` | Firmographic (website) | e.g. `salesforce.com` |
| `City` | Firmographic (HQ city) | |
| `Country/Region` | Firmographic (HQ country) | full name, e.g. `United States`, `Singapore` |
| `Number of Employees` | Firmographic (range midpoint) | e.g. `7500` for 5001–10000 |

## Custom columns (create once in HubSpot as custom contact properties)

| CSV Column | Value | Notes |
|---|---|---|
| `GTM_Segment` | `Enterprise` / `Startup` | |
| `GTM_Score` | e.g. `12` | numeric, no denominator |
| `GTM_Tier` | `A` / `B` | |
| `GTM_Persona_Tier` | e.g. `Champion`, `Economic Buyer` | |
| `GTM_Why_Now` | signal text + date, ≤200 chars | e.g. `Product X GA 2026 – example.com/news/...` |
| `GTM_Case_Study` | e.g. `<reference-customer>` | |
| `GTM_Source` | `Cold` / `Warm-Event: [name]` | |
| `GTM_Run_Date` | `YYYY-MM-DD` | |
| `GTM_Rubric_Version` | `2026-05-15` | |
| `GTM_Heat` | `0`–`3` | intent heat axis (Number property) |
| `GTM_Intent_Feeds` | e.g. `vibe-topic;rr-news` | semicolon-joined; blank if none |
| `GTM_Top_Intent_Score` | e.g. `86` | raw 0–100 Bombora/topic-intent score behind `GTM_Heat`'s bucketed 0–3 (Number property); blank if no intent signal |
| `GTM_Intent_Topics` | e.g. `agentic ai:86;mlops:72` | `topic:score` pairs, highest first, semicolon-joined — which signal(s) actually fired, the field reps use to personalize outreach |
| `GTM_Industry` | e.g. `Commercial Banking` | firmographic industry/vertical (NAICS description or LinkedIn category) |
| `GTM_Revenue_Range` | e.g. `$1B-$10B` | firmographic annual revenue band |
| `GTM_New_In_Role` | `Yes` / blank | contact new in seat ≤6 months (🆕) |
| `GTM_Qualification_Path` | e.g. `intent-only-relaxed` | blank under a normal full-gate qualification; set only when a gate was relaxed for the run (e.g. a bulk-mode run per `discovery-and-budget.md` §"Bulk mode"), so a rep working straight from HubSpot still sees the caveat |

## Import settings

- Object type: **Contacts**
- Unique identifier: **Email**
- On duplicate: **Update existing record**
- Associate with company: **Yes** (HubSpot matches on Company Domain Name)

## One-time setup (per colleague's HubSpot)

Create the `GTM_*` custom properties before first import: Settings → Properties → Create property (type: Single-line text, or Number for `GTM_Score`).

## CSV writing notes

- UTF-8, comma-delimited, header row first. Quote any field containing a comma (e.g. `GTM_Why_Now`).
- Write the standard columns in the order above, then the `GTM_*` custom columns.
