# GTM Profile — <Your Name>

> **This is the template.** The `setup` skill copies it into your company profile bundle as **`profiles/<company>/PROFILE.md`** and fills it in with you. Every skill reads that file so the same plugin behaves for your market, your voice, your cadence, your budget.
>
> **Config only — never secrets.** No API keys, tokens, or passwords ever go in this file. Tools you connect (Vibe Prospecting, Firecrawl) keep their credentials in the app's secure store or your OS environment — see the plugin README, "Keys & privacy."
>
> Replace every `<…>` placeholder. Lines beginning with `#` are guidance and can stay or go.

## Identity

```
name:            <Your Name>
title:           <e.g. GTM Lead, [your product]>
email_signature: <how you sign off — e.g. just your first name, or a full block>
```

## Voice style  *(optional — overrides the built-in voice.md for outreach drafts)*

```
# Paste your voice guide here, or describe your communication style in a few sentences.
# The draft-outreach skill reads this first; if blank it falls back to the built-in voice.md.
#
# What to capture:
#   - Core philosophy (e.g. "efficiency as a virtue — get to the unlock immediately")
#   - Tone markers (e.g. "strategic informality: lowercase, relaxed syntax, peer not vendor")
#   - Structure preferences (e.g. "framework mindset — speak in playbooks, steps, systems")
#   - Close style (e.g. "collaborative inquiry: invite them into the process, not just ask for a meeting")
#   - Anything you'd tell a ghostwriter: banned words, must-haves, cadence quirks
#
# Example:
#   voice_style: |
#     direct and concise — no preamble, no fluff. strategic informality: lowercase where it
#     reads naturally, relaxed syntax, peer not vendor. framework mindset: frame problems as
#     system gaps, not pain points. close with collaborative inquiry — invite them into the
#     process ("want me to share the blueprint?") not a generic "let me know."
#     ban list: "excited to", "thrilled to", "synergy", "reach out", "touch base".
#
voice_style: <leave blank to use built-in voice.md, or paste/describe your style here>
```

## Markets

```
# Markets you sell into, primary first. The prospect skill scores accounts against these.
target_markets:  [<e.g. United States (primary), Singapore>]
target_cities:   [<e.g. New York, San Francisco, Singapore>]   # optional, sharpens density
language:        English        # optional, default English
```

## Home base & travel  *(used by events-tracker — Phase 2)*

```
home_base:       <City, Country — e.g. Hauppauge, NY>
nearest_hub:     <transit/airport anchor — e.g. Central Islip LIRR / JFK>
travel_policy:   <defaults are fine to start; editable>
                 # e.g. day-trips within 90 min by train; overnight only for Tier-A events
```

## ICP weighting  *(used by prospect, call-prep, account-plan)*

```
# Per-run segment mix. Routine default = 3 enterprise + 7 startup per run.
segment_mix:        70% startup / 30% enterprise
emphasize_personas: [<e.g. Head of AI Platform, CISO, CEO/Founder>]
emphasize_verticals:[<e.g. financial services, fintech/payments, healthcare, vertical SaaS>]
# Leave personas/verticals blank to use the full ICP from profiles/<active>/knowledge/icp-personas.md.
```

## Cadence  *(how often each motion runs)*

```
# Suggested defaults shown. setup can create scheduled tasks for the recurring ones.
prospect:        weekly (Mon)        # daily optional
market_scan:     weekly (Mon)        # Phase 2
events_tracker:  weekly (Mon)        # Phase 2
gtm_planning:    monthly + quarterly review   # Phase 4
```

## Budget  *(caps on metered tool calls — protects your own account)*

```
# Before any metered call, the skill estimates cost, shows it, and stops at the cap.
# Free paths (web search, browser) never count against budget.
monthly_tool_budget_usd: 50
per_run_cap_usd:         10
tools_metered:           [Vibe Prospecting, Firecrawl]
# Vibe reference: a Boost pack = 3,000 credits for ~$89.99 (one-time, no auto-renew).
# Routine targets <=100 credits/run (~$3); ~400 credits/month (~$12) at weekly cadence.
```

## Tools connected  *(names + status only — NEVER keys)*

```
vibe_prospecting: <connected | not connected>   # optional OAuth connector; web-search fallback if absent
firecrawl:        <connected | not connected>    # optional; key set as OS env var FIRECRAWL_API_KEY
# This section records THAT a tool is connected, never the credential itself.
```

## Output location

```
# Per-profile state tree (gitignored runtime state). Per-account deliverables go under
# <output_folder>/accounts/<account-slug>/ — one folder per account. See CLAUDE.md "Per-account outputs".
output_folder:   content/<profile>/
```

## Deck defaults  *(used by build-deck)*

```
# Who decks are attributed to. Default is the company BRAND, not your personal name —
# slides and the Slidev config.yml speaker block say the company brand, not "<Your Name>".
# Set to "name" (or leave the field and just tell build-deck "put my name on it") to use
# your personal identity from the Identity block above.
deck_byline:     brand          # brand = your company brand_name (default) | name = your personal name+title
deck_speaker:    <brand · product — e.g. your company brand_name + flagship product>   # the presenter line shown on decks when byline = brand
# Note: this only affects DECKS. Outreach still signs off with your email_signature.
```

## Deck research  *(used by deck-research → feeds build-deck)*

```
# deck-research builds a reusable account dossier (Layer 1 = account intel,
# Layer 2 = per-persona slot-fills) that build-deck consumes. Defaults are fine to start.
deck_research_depth:    standard      # standard = free web paths (default) | deep = metered opt-in
deck_research_metered:  off           # off (default) | on — only honoured if a metered tool is
                                      # connected AND within the budget caps above
emphasize_deck_personas: []           # optional — bias slot-fills toward these personas/templates
                                      #   (e.g. [A5, A6]); blank = infer from the named audience
# deep depth uses Firecrawl/Vibe for richer firmographics; budget caps above are a hard stop.
```

## Content OS  *(content-radar / -plan / -studio)*

```
# The three or so topic territories you post inside. Stay in lane — scattershot topics
# dilute the algorithm's sense of what you're authoritative on.
content_pillars: [<Pillar A>, <Pillar B>, <Pillar C>]
# One sentence: the seam where your pillars meet. Every post should ladder back to it.
wedge:           <e.g. the trust / identity layer for the agentic era>
# Where content is published FROM. Posting identity is usually the PERSON, not a brand page.
social_handle:   <e.g. https://www.linkedin.com/in/your-handle/>
```

Per-platform tuning (lead formats, posting clocks, the "never do this" list) lives in
`knowledge/social-tuning.md` — the generic platform method stays in `docs/*-optimization.md`.
