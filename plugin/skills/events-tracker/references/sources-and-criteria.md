# Events Tracker — Sources & Criteria Framework

How the weekly scan decides what goes in the events spreadsheet. This is a market-aware framework — the SKILL reads the colleague's `home_base` and `target_cities` from PROFILE and applies these criteria to their geography. Nothing here is hardcoded for a specific city; adapt all city-specific URLs to the colleague's situation.

Edit this file to change scan behavior; the next Monday run picks up the changes.

---

## Goal

Surface conferences and meetups matching the active company's `content_pillars` (from `profiles/<active>/PROFILE.md`) worth attending from the colleague's home base, primarily in their local commute range, with secondary coverage of their `target_cities` and relevant marquee events. The example criteria below are tuned for an **agentic AI** focus; adapt the keyword set to whatever the active company's content pillars are.

---

## Topic filter — what counts (example: "agentic AI")

For a content pillar of agentic AI, **include** events whose focus is meaningfully about AI agents / agentic systems:
- Multi-agent orchestration
- Agent frameworks: LangChain/LangGraph, CrewAI, AutoGen, DSPy, and equivalents
- Tool use & function calling
- Agent evaluation & observability
- Autonomous workflows
- Agentic RAG
- Agent infrastructure (MCP, A2A, agent gateways, agent identity)
- Applied agent deployments in enterprise

**Include** general AI/LLM events **only if** they have a substantial agentic track or session (e.g., major cloud summits, AI Engineer World's Fair, Databricks/Snowflake summits often have agent content).

**Exclude** pure data-engineering, computer-vision, hardware, or generic "AI for business" events with no agent angle.

**Borderline calls:** include with a note in "Why relevant" so the colleague can judge.

---

## Geography & weighting

Scale to the colleague's PROFILE. The general priority order:

1. **Local / commutable (primary)** — events reachable by transit from `home_base` without overnight stay. The definition of "commutable" depends on the city (e.g., 90 min by train for a US suburban base; 60 min by MRT for Singapore; etc.).
2. **Secondary cities** — `target_cities` from PROFILE, weighted by business importance.
3. **Marquee / destination** — major global events in the colleague's field that warrant a flight regardless of city (AI Engineer, major cloud summits). Include these sparingly.
4. **Online** — virtual/free passes and streamed events, especially for marquee events the colleague may not travel to.

---

## Date window

Rolling: each run looks at **today → ~4 weeks ahead**, plus already-known marquee events through the colleague's travel end date (from PROFILE or context). Events that have passed get Status "Past" (auto-set on the "New" and "Interested" rows only); kept on the sheet for the record.

---

## Sources framework (adapt URLs to the colleague's cities)

### Luma (primary for local meetups)

Luma is the dominant platform for AI meetup communities. These types of calendars exist for most major tech cities — find the equivalent for the colleague's `home_base` and `target_cities`:

- City AI / GenAI community calendars (e.g., `luma.com/genai-ny`, `luma.com/genai-singapore`)
- Local AI meetup groups (e.g., `luma.com/nycai`, city-specific equivalents)
- Agentic-AI-focused groups (e.g., `luma.com/the-ai-agents-community`)
- Luma city-wide search for "agentic", "AI agents", "agent framework" filtered to the colleague's cities

**Note:** Luma is JavaScript-rendered — use Firecrawl or the browser tool, not a plain web fetch.

### Eventbrite

Search "agentic AI", "AI agents", "LLM" filtered to the colleague's cities and a date range. Eventbrite search URLs follow the pattern: `eventbrite.com/d/{city-slug}/{keyword}/`.

### Meetup.com

Find the equivalent Meetup groups for the colleague's cities. Search Meetup for "AI agents", "generative AI", "machine learning" groups in their cities. Look for groups with active monthly events and agent-relevant recent topics.

### Open web / conference directories

- Web search: `agentic AI conference {city} {month} {year}` and variations
- Directories known to track AI events: AI engineer events list, LangChain events, crossmint AI agent conference calendar
- Official conference sites for marquee events: ai.engineer, databricks.com/dataaisummit, snowflake.com/summit, and similar
- Market-specific directories: for Singapore → SGInnovate, AI Singapore events; for India → NASSCOM, iSpirt; for MENA → Hub71, GITEX AI track; add the relevant local innovation hubs for the colleague's market

---

## Data captured per event

Name · Type (Conference / Summit / Meetup / Workshop) · Topic focus · Date(s) · Day & time (flag if event ends after 8 PM) · City/Region · Venue · Format (In-person / Hybrid / Online) · Source · Link · Cheapest ticket tier + price · Why relevant

Then from the cost model (see `travel-policy-rates.md`): Transport · Local ground · Lodging (if triggered) · Meals · Incidentals · Total estimated cost · Within policy?

Tracking columns (colleague-controlled, never overwritten): Status · Priority · Date added · Last updated

---

## Update rules (one file, updated in place)

Each Monday the scan **updates the existing spreadsheet in place:**
- **Match key:** event Name + Date + Link. All three must match to be treated as a duplicate.
- **New events:** appended with Status "New" and today's date in Date Added.
- **Changed details:** if date, venue, or price changed, the row is refreshed and "Last updated" is set; a note records what changed.
- **Colleague edits preserved:** Status, Priority, and any notes the colleague typed are never overwritten.
- **Passed events:** Status set to "Past" only if currently "New" or "Interested". "Registered", "Skip", and other colleague-set values are preserved.
- **Summary tab:** recalculates totals each run (by city, by month, committed vs. estimated).

---

## Status values (colleague controls these)

`New` → just surfaced · `Interested` → considering · `Registered` → booked · `Skip` → not attending · `Past` → date has passed (auto on New/Interested rows)

---

## What the scan does NOT do

- Does not register for events or buy tickets — surfaces and budgets; colleague decides.
- Does not book travel or hotels.
- Does not scrape Luma guest lists automatically — surfaces which events have public lists and lets the colleague decide to run the attendee-scraping recipe.
