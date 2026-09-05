# LinkedIn Engagers — record schema + CSV mapping

How a captured engager maps onto the per-engager record and the HubSpot CSV. The CSV columns and
import settings are owned by `${CLAUDE_PLUGIN_ROOT}/skills/prospect/references/hubspot-csv-map.md` —
this file only maps engagement fields onto them. **One row per person.**

## Per-engager record (JSON sidecar)

| Field | Source | Notes |
|---|---|---|
| `name` | reactions modal / comment author | full name as shown |
| `headline` | line under the name | role/title + sometimes company |
| `company` | parsed from `headline` | null if not clearly present |
| `engagement_type` | which list / reaction tab | `like\|celebrate\|support\|love\|insight\|funny\|comment` |
| `comment_text` | the comment | verbatim, only for commenters; untrusted data |
| `profile_url` | the name's link | null if not captured |
| `source_url` | the post | provenance — always set |
| `captured_at` | now (ISO-8601 UTC) | provenance — always set |
| `icp_match` | match `headline` vs `icp-personas.md` | persona name or `none` |
| `tier` | qualification | `A` clear target · `B` adjacent · `none` |

A commenter who also reacted = one record, `engagement_type: comment` (stronger signal), comment kept.

## Record → HubSpot CSV column

| CSV column | From record | Notes |
|---|---|---|
| `First Name` / `Last Name` | split `name` | |
| `Email` | enrichment only | **blank** on manual/web path — don't guess |
| `Job Title` | `headline` | trim trailing company if separable |
| `LinkedIn Bio URL` | `profile_url` | |
| `Company Name` | `company` | |
| `GTM_Source` | constant | `Warm-Event: LinkedIn <post-slug>` |
| `GTM_Why_Now` | engagement | `<engagement_type> on <post-slug> <YYYY-MM-DD> – <source_url>` (≤200 chars) |
| `GTM_Persona_Tier` | `icp_match` | the matched persona name |
| `GTM_Tier` | `tier` | `A` / `B` |
| `GTM_Run_Date` | today | `YYYY-MM-DD` |

Leave `Phone Number`, `Company Domain Name`, `City`, `Country/Region`, `Number of Employees`,
`GTM_Score`, `GTM_Case_Study`, `GTM_Rubric_Version` blank unless enrichment fills them. UTF-8,
comma-delimited, header row first; quote any field containing a comma.

## Identity & dedup (owned by `gtm_core.people`)

The master record lives in `content/<active>/prospects/people.json`; the CLI owns identity and dedup —
don't hand-merge it. A person's `id` = normalized `profile_url` (no scheme/www/query/trailing slash),
else `slug(name)-slug(company)`. On `upsert`, an engagement is deduped on `(type, post_url, date)`,
tags are unioned, and `engagement_count` + `first/last_seen` refresh. A repeat engager on a *new* post
merges into the same record and their `engagement_count` grows (re-engagement is a strong intent
signal). Filter later with `python -m gtm_core.people query --profile <active> --tag <tag>` /
`--status <s>` / `--engaged-min <n>` / `--list-urls`.
