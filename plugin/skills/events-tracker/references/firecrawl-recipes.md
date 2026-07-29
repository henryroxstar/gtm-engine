# Firecrawl Recipes — Events Tracker

Ready-to-use patterns for scraping event platforms (Luma, Eventbrite, Meetup) into structured rows. All use structured JSON extraction — far more reliable than parsing raw markdown. The schema is in `event_schema.json`.

**Firecrawl is optional and metered.** Always estimate credit cost and check against `per_run_cap_usd` from PROFILE before running. If not connected or cap would be exceeded, use the browser fallback (navigate → get_page_text) — which is always available and always free.

---

## Credit cost estimates (for budget guard)

| Call type | Typical credits |
|---|---|
| `scrape` — single page | 1–3 credits |
| `search` — web search | 0.1–0.5 credits |
| `map` — URL listing | ~1 credit |
| `extract` — multi-URL LLM extraction | 2–5 credits |

A typical 6–8 source run costs approximately 8–20 credits (~$0.25–$0.65 at standard Firecrawl pricing). Always estimate before starting and surface the estimate: "Estimated: ~N credits (~$X) | Cap: $Y/run."

**If estimated cost > `per_run_cap_usd`: switch to browser fallback immediately.**

---

## Firecrawl MCP usage (primary path when connected)

When Firecrawl is connected via the plugin's `.mcp.json`, use the Firecrawl MCP tools directly:
- `firecrawl_scrape` — scrape a single URL (pass `formats: ["json"]` + schema for structured data)
- `firecrawl_search` — web search with optional content scraping
- `firecrawl_map` — list URLs on a site cheaply

Pass `waitFor: 5000` for JS-rendered calendars. Set `onlyMainContent: false` for event list/grid pages.

---

## Python script (alternative path)

The `firecrawl_scrape.py` script in this references folder provides the same capabilities as a CLI or importable module. It reads the key from the `FIRECRAWL_API_KEY` environment variable — never hardcoded.

```bash
# Check the key works (~1 credit)
python firecrawl_scrape.py self-check

# Structured extraction from a Luma calendar
python firecrawl_scrape.py scrape "https://luma.com/genai-ny" \
    --schema event_schema.json \
    --prompt "Extract every event: name, date, start/end time, venue, city, in-person/online, ticket price, and link." \
    --wait 5000 --full-page --out luma_events.json
```

---

## Luma — calendar or city page

Luma calendars (e.g., `luma.com/genai-ny`, `luma.com/the-ai-agents-community`) list events as rendered cards. Always use structured extraction with `waitFor` ≥ 4000 ms and `only_main_content=False`.

```
MCP call: firecrawl_scrape
  url: "https://luma.com/{calendar-slug}"
  formats: ["json"]
  jsonOptions:
    schema: {contents of event_schema.json}
    prompt: "Extract every event card: name, date, start/end time, venue, city, in-person/online, ticket price, and the direct event link."
  waitFor: 5000
  onlyMainContent: false
```

**Tips:**
- Luma dates are rendered dynamically — always pass `waitFor` ≥ 4000.
- Individual event pages (`luma.com/<slug>`) carry the exact date/time and address — scrape the specific event page when a calendar card's date or venue is ambiguous.
- To enumerate all event URLs on a calendar before scraping, use `firecrawl_map` first.

---

## Eventbrite — search results

```
MCP call: firecrawl_scrape
  url: "https://www.eventbrite.com/d/{city-slug}/agentic-ai/"
  formats: ["json"]
  jsonOptions:
    schema: {contents of event_schema.json}
    prompt: "Extract each event: title, date, time, venue, city, price (or Free), and link."
  waitFor: 4000
  onlyMainContent: false
```

Or discover first, then scrape the promising URLs:
```
MCP call: firecrawl_search
  query: "agentic AI OR AI agents event {city} {month} {year} site:eventbrite.com"
  limit: 10
```

---

## Meetup — group upcoming events

```
MCP call: firecrawl_scrape
  url: "https://www.meetup.com/{group-slug}/events/"
  formats: ["json"]
  jsonOptions:
    schema: {contents of event_schema.json}
    prompt: "Extract upcoming events: name, date, time, venue, city, price, and link."
  waitFor: 4000
  onlyMainContent: false
```

Individual event pages (`/<group>/events/<id>/`) carry the firm date and RSVP count — scrape the specific page when the group listing is ambiguous.

---

## Multiple sources at once — extract()

When scraping several specific event URLs in one call:

```
MCP call: firecrawl_extract
  urls:
    - "https://luma.com/{calendar-1}"
    - "https://nyc.aitinkerers.org/"
    - "https://www.meetup.com/{group}/events/"
  prompt: "Extract every upcoming agentic-AI event with date, venue, city, price, and link."
  schema: {contents of event_schema.json}
```

---

## Browser fallback (always free)

When Firecrawl is not connected or the per-run cap would be exceeded, use Claude in Chrome browser tools:

```
1. navigate(url)
2. get_page_text() or read_page()
3. Parse the returned content for event fields
```

For JS-rendered pages: navigate, wait a moment (use `wait` action), then get_page_text. Luma and Eventbrite render in-browser; the browser tool sees the full rendered content.

For web discovery: use `WebSearch` with queries like:
```
agentic AI meetup {city} {month} {year}
AI agents conference {city} site:luma.com OR site:eventbrite.com OR site:meetup.com
```

---

## Filtering to the active company's focus topics

Extraction returns all events on the page. Filter afterward to events whose name or description matches the active company's `content_pillars` (from `profiles/<active>/PROFILE.md`) — for an agentic-AI focus that means agents, agent frameworks, orchestration, agent eval/observability, or autonomous workflows. The full topic criteria (and how content_pillars map to event keywords) are in `sources-and-criteria.md`. Drop anything already in the spreadsheet (match on Name + Date + Link).

---

## Luma guest lists — warm prospecting signals

If upcoming events (within 21 days) have publicly visible guest lists, they are high-strength warm prospecting signals — stronger than a job post. **Do not scrape guest lists automatically.** Surface the opportunity to the colleague and let them decide.

If the colleague wants to proceed:

**Step 1 — Check if the guest list is public**

Navigate to the event page and look for a "Guests" or "Attendees" tab. If visible and not behind login, proceed.

**Step 2 — Extract attendees**

```
MCP call: firecrawl_scrape
  url: "https://lu.ma/{event-slug}/guests"
  formats: ["json"]
  jsonOptions:
    schema:
      type: object
      properties:
        attendees:
          type: array
          items:
            type: object
            properties:
              name: {type: string}
              title: {type: string}
              company: {type: string}
              profile_url: {type: string}
    prompt: "Extract every attendee's name, job title, company, and Luma profile URL."
  waitFor: 5000
  onlyMainContent: false
```

**Step 3 — Filter to ICP-matching companies**

Run company names through the ICP gates in `profiles/<active>/knowledge/icp-personas.md`. Keep roles signaling buying authority: Founder/CEO/CTO/CPO/CISO, Head of AI, VP Engineering, Director, Principal Engineer/Architect.

**Step 4 — Feed into the prospecting run**

These accounts enter the prospecting run tagged as `[event | <event-date> | <event-URL> | H]` — strength always H (attending a curated agentic-AI event is Tier-1 "why now"). They skip the web-search signal sweep but still go through scoring gates and persona enrichment.

**Step 5 — Luma direct messages (require approval before sending)**

If an attendee has a Luma profile visible, Luma allows direct messages to fellow event-goers. Stage drafts for colleague approval — never auto-send. Luma DMs are external sends and require explicit approval per the GTM approval workflow.

> In the frames below, `[brand]` is the active company's `brand_name` and `[wedge]` is its
> positioning line (`wedge`) — both from `profiles/<active>/PROFILE.md`. Never hardcode a company name.

Pre-event frame (≤ 200 chars):
```
Hey [Name] — saw you're coming to [event]. We're building [wedge] at [brand].
Would love to compare notes on [use case] for 5 min there.
```

Post-event frame:
```
Great to see [event] — [one thing from agenda]. Building [wedge] at [brand] —
what you're doing at [company] maps closely. 10-min call this week?
```

**Step 6 — Speakers**

Speaker affiliations are public (event page + LinkedIn). Speakers are warmer than attendees — scrape the speaker section of the event page for name/title/company/social link. Speaker opening hook: "Caught your talk at [event] on [topic] — [one specific thing]. At [brand] we're solving [related thing]. Worth 15 minutes?" (`[brand]` = active company `brand_name` from `profiles/<active>/PROFILE.md`.)
