"""Canonical manifest for the `reddit-reply` skill (engagement cohort, phase 3C).

Prompt body: plugin/skills/reddit-reply/body_template.md (verbatim).
SKILL.md is generated from this manifest by gtm_core.skills.codegen.
"""

from __future__ import annotations

from ..tiers import Tier
from .base import GTMSkill

SKILL = GTMSkill(
    name="reddit-reply",
    capability_tier=Tier.CORE,
    version="0.1.0",
    phase="3C",
    description="Run the active company's Reddit engagement motion end to end — pick the right "
    "subreddit and thread, then draft a disclosed, value-first comment that would earn its place even "
    "if the product didn't exist. Follows the Useful Redditor framework: triage the thread (is genuine "
    "help possible, is self-promo allowed here, is this a high-intent or already-ranking thread), "
    "answer the real problem as if the product doesn't exist, add lived specifics, and only bridge to "
    "what the company builds when it is the obvious answer AND the founder tie is disclosed up front. "
    "Shapes the reply so it also earns upvotes, ranks in search, and is quotable by AI answer engines "
    "(SEO/GEO) — because the genuinely helpful answer is exactly what ranks and gets cited. Reads the "
    "thread from pasted text, a screenshot, or a URL (degrades gracefully when blocked); loads voice, "
    "personas, target subreddits (social-tuning.md), and case studies from the active profile and runs "
    "a Reddit-native self-check (subreddit rules, disclosure, shill-radar, voice). This skill should be "
    'used when the user says "reply to this Reddit post/thread", "draft a Reddit comment", "answer this '
    'subreddit question", "engage on Reddit for [product]", "find Reddit threads to answer", "help me '
    'respond in r/[sub]", or shares a Reddit URL/screenshot and wants a reply. Drafts only — never '
    "posts, never uses multiple accounts, never fabricates a persona; the operator posts manually as "
    "themselves. For a cold first-touch with no thread to reference, use draft-outreach instead.",
)
