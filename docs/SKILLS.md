<!-- GENERATED — DO NOT EDIT. The authoritative skill inventory.
     Regenerate: `python -m gtm_core.skills.codegen generate-all`.
     CI (tests/skills/test_codegen.py / skill_codegen_sync) fails if this drifts. -->

# Skill index

**58 skills**, generated from the manifests in `gtm_core/skills/`. This is the single source of truth for the skill inventory — other docs link here rather than restate it.

| Skill | Tier | Requires product capability | What it does |
|---|---|---|---|
| [`account-dossier`](../plugin/skills/account-dossier/SKILL.md) | core | — | Generate a short, on-brand account + buyer dossier as a Word (.docx) that lets a non-technical seller walk into a… |
| [`account-plan`](../plugin/skills/account-plan/SKILL.md) | core | — | Build a CRO-grade strategic account plan for one target company — deal stage + forecast category, ICP score, a… |
| [`airq-scan`](../plugin/skills/airq-scan/SKILL.md) | production | gateway | Run an AIRQ-aligned agent-security assessment of a target company's AI agent product — from a GitHub repo, a… |
| [`build-deck`](../plugin/skills/build-deck/SKILL.md) | core | — | Build an on-brand sales deck, one-pager, POC proposal, or partner brief for the active company. Trigger when the… |
| [`builder-evidence`](../plugin/skills/builder-evidence/SKILL.md) | core | — | Assembles the evidence pack for one builder-story build moment. Reads the chosen StoryCluster from… |
| [`builder-radar`](../plugin/skills/builder-radar/SKILL.md) | core | — | Scans the active profile's configured repo history — git commits and any project design docs — to surface… |
| [`builder-studio`](../plugin/skills/builder-studio/SKILL.md) | core | — | Drafts the asset bundle for one builder-story build moment in the founder's voice: a LinkedIn text post, a longer… |
| [`call-prep`](../plugin/skills/call-prep/SKILL.md) | core | — | Prepare a pre-meeting brief for a sales call — account snapshot, attendee persona mapping, matched case study… |
| [`campaign-plan`](../plugin/skills/campaign-plan/SKILL.md) | core | — | Build or refresh an executive-facing outbound program plan — a scaled, staged cross-org campaign grounded in the… |
| [`carousel-auto`](../plugin/skills/carousel-auto/SKILL.md) | production | — | Automate the weekly carousel pipeline from market-scan signals to publish-ready package. This skill should be used… |
| [`carousel-pdf`](../plugin/skills/carousel-pdf/SKILL.md) | core | — | Produce a LinkedIn 4:5 portrait carousel (PDF document post) from the active company's knowledge pack. This skill… |
| [`carousel-visuals`](../plugin/skills/carousel-visuals/SKILL.md) | production | — | Generate AI visuals for the active company's LinkedIn and Instagram carousels using Higgsfield — cinematic 4:5… |
| [`case-study`](../plugin/skills/case-study/SKILL.md) | core | — | Turn a solution design — or a delivered engagement — into a publishable 1-2 page customer story: Markdown, a… |
| [`community-signal-analysis`](../plugin/skills/community-signal-analysis/SKILL.md) | core | — | Turn a community social-listening feed (Syften) into a high-signal, highly-visual market briefing. Pulls recent… |
| [`consulting-partner-brief`](../plugin/skills/consulting-partner-brief/SKILL.md) | core | — | For a consulting partner — a systems integrator, dev shop, or consulting firm that both advises clients and builds… |
| [`content-outcomes-sync`](../plugin/skills/content-outcomes-sync/SKILL.md) | pipeline | — | Close the CONTENT learning loop for the active company. Pulls published-post performance from multiple sources in… |
| [`content-plan`](../plugin/skills/content-plan/SKILL.md) | core | — | Propose the week's content plan for the active company from the latest radar digests. Loads the last few… |
| [`content-publish`](../plugin/skills/content-publish/SKILL.md) | pipeline | — | Stage a reviewed LinkedIn text post (plus hosted image/video media when present) for human-approved publishing to… |
| [`content-radar`](../plugin/skills/content-radar/SKILL.md) | pipeline | — | News-driven content radar for the active company. Reads fresh PROD discovery_items via the read-only Postgres news… |
| [`content-research`](../plugin/skills/content-research/SKILL.md) | core | — | Research a planned content item into verifiable, citable material for the active company. For a given ContentItem… |
| [`content-studio`](../plugin/skills/content-studio/SKILL.md) | core | — | Draft and lint a publish-ready, platform-native asset for the active company from a researched content item… |
| [`deck-research`](../plugin/skills/deck-research/SKILL.md) | core | — | Research an account into a structured, reusable deck dossier that build-deck consumes to fill the account-specific… |
| [`demo-capture`](../plugin/skills/demo-capture/SKILL.md) | production | — | Turn LOCAL footage the operator already has — a phone recording of themselves, a camera file, a webinar export, or… |
| [`draft-outreach`](../plugin/skills/draft-outreach/SKILL.md) | core | — | Draft outreach for the active company's flagship product — LinkedIn DMs, cold emails, and follow-ups — in the… |
| [`email-quality`](../plugin/skills/email-quality/SKILL.md) | pipeline | — | Close the loop between reading emails and improving them. Runs the four modes of the email-quality program: judge… |
| [`email-sequence`](../plugin/skills/email-sequence/SKILL.md) | core | — | Turn composed outreach into a staged, multi-step email sequence in the connected sequencer — Saleshandy today… |
| [`events-tracker`](../plugin/skills/events-tracker/SKILL.md) | core | — | Weekly GTM events scan and travel-budget tracker. Scans Luma, Eventbrite, Meetup, and the open web for conferences… |
| [`format-router`](../plugin/skills/format-router/SKILL.md) | core | — | Cross-modal format dispatcher. Given an approved hook and pillar, decides which (format, platform) tuples to… |
| [`gateway-runbook`](../plugin/skills/gateway-runbook/SKILL.md) | core | gateway | Produce a parameterized, step-by-step gateway setup runbook tailored to a specific account, use case, and stack… |
| [`gtm-planning`](../plugin/skills/gtm-planning/SKILL.md) | core | — | Build or refresh the quarterly GTM plan for the colleague's market. This skill should be used when the user says… |
| [`identity-kit`](../plugin/skills/identity-kit/SKILL.md) | core | — | Audits, creates, and writes back the active profile's (or product's) render-identity handles in BRAND.toml… |
| [`inbound-triage`](../plugin/skills/inbound-triage/SKILL.md) | core | — | Read inbound replies from the connected inbox (Saleshandy today, Gmail via a per-profile inbound_source switch)… |
| [`infographic-data`](../plugin/skills/infographic-data/SKILL.md) | production | — | Render a finished, postable data-dense editorial infographic — a single image with a bold headline, numbered… |
| [`infographic-handwritten`](../plugin/skills/infographic-handwritten/SKILL.md) | production | — | Render a finished, postable handwritten-style infographic — a single image that looks like a real notebook page… |
| [`knowledge-refresh`](../plugin/skills/knowledge-refresh/SKILL.md) | pipeline | — | Refresh the active company's knowledge corpus on a cadence, safely. Reads which knowledge topics are DUE for review… |
| [`linkedin-engagers`](../plugin/skills/linkedin-engagers/SKILL.md) | core | — | Turn the people who engaged with a LinkedIn post — reactors (like/celebrate/support/love/insight/funny) and… |
| [`linkedin-reply`](../plugin/skills/linkedin-reply/SKILL.md) | core | — | Craft a soft-sell reply to a LinkedIn post — a value-first public comment (and an optional DM / connection note)… |
| [`market-harvest`](../plugin/skills/market-harvest/SKILL.md) | core | — | Pull the external market signal that the market-intelligence brief reads, across ten lanes — Syften social… |
| [`market-intelligence`](../plugin/skills/market-intelligence/SKILL.md) | core | — | Turn the field data the GTM engine already generates into an internal, educational intelligence brief for the… |
| [`market-scan`](../plugin/skills/market-scan/SKILL.md) | core | — | Weekly agentic-AI market signals sweep for the active company's GTM. Scans news, competitor moves, regulatory… |
| [`outcomes-sync`](../plugin/skills/outcomes-sync/SKILL.md) | pipeline | — | Close the GTM learning loop for the active company. Pulls campaign/outreach RESULTS — email replies via the… |
| [`product-partner-brief`](../plugin/skills/product-partner-brief/SKILL.md) | core | — | For a product company — a peer vendor that ships software, not a reseller and not a consultancy. Builds the… |
| [`profile-onboard`](../plugin/skills/profile-onboard/SKILL.md) | core | — | Reads source text about a company (a website crawl, an uploaded PDF, or pasted content) and emits a single… |
| [`prospect`](../plugin/skills/prospect/SKILL.md) | core | — | Run the active profile's prospecting routine — discover, qualify, score, and enrich ICP accounts, then output a… |
| [`reddit-reply`](../plugin/skills/reddit-reply/SKILL.md) | core | — | Run the active company's Reddit engagement motion end to end — pick the right subreddit and thread, then draft a… |
| [`setup`](../plugin/skills/setup/SKILL.md) | core | — | Guided one-time onboarding for the GTM engine plugin. This skill should be used when the user says "set me up"… |
| [`solution-design`](../plugin/skills/solution-design/SKILL.md) | core | solution-architecture | Turn a use case and requirements into a solution architecture — either mapped onto the active company's flagship… |
| [`solution-discovery`](../plugin/skills/solution-discovery/SKILL.md) | core | technical-discovery | Prepare for a technical deep-dive by profiling the account's engineering stack and gathering functional scope… |
| [`solution-scope-check`](../plugin/skills/solution-scope-check/SKILL.md) | core | — | The customer-facing Scope Check — a short, on-brand 2-page Word (.docx) worksheet the buyer marks up to confirm or… |
| [`video-avatar`](../plugin/skills/video-avatar/SKILL.md) | pipeline | — | Render the SPEAKING PRESENTER beats of a shot list as a synthetic talking head on HeyGen, the only engine the… |
| [`video-clip`](../plugin/skills/video-clip/SKILL.md) | production | — | Repurpose the operator's own long-form video into 1-20 short-form clips using Reap Video Studio's create_clips… |
| [`video-finish`](../plugin/skills/video-finish/SKILL.md) | pipeline | — | Finish a rendered short-form video variant into a shippable artifact: normalize, upscale to the target frame, grade… |
| [`video-render`](../plugin/skills/video-render/SKILL.md) | production | — | Render approved b-roll, product, environment and abstract shots for ONE target aspect ratio using Higgsfield… |
| [`video-restyle`](../plugin/skills/video-restyle/SKILL.md) | production | — | Restyle the operator's own real footage (4-120s) into an on-brand short using Higgsfield's shorts_studio_create and… |
| [`video-router`](../plugin/skills/video-router/SKILL.md) | core | — | A thin front-door skill that routes a 'make a video' request into the correct creator pack variant. First runs a… |
| [`video-score`](../plugin/skills/video-score/SKILL.md) | pipeline | — | Score the rendered short-form variants for the active company and recommend which one ships where. Runs Higgsfield… |
| [`video-script`](../plugin/skills/video-script/SKILL.md) | pipeline | — | Turn an approved short-form video item into a shot-by-shot script for the active company: cold open, hook line… |
| [`video-storyboard`](../plugin/skills/video-storyboard/SKILL.md) | production | — | Generate operator-reviewable storyboard still(s) for an approved video script before any video render spend. Reads… |
