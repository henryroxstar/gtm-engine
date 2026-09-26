"""Founder-facing terminology translation dictionary and helpers (PRD-041).

Maps internal pipeline and sales terminology to executive-friendly plain English:
- 'Wedge' -> 'Opening Angle' / 'Hook'
- 'Send Card' -> 'Ready to Send' / 'Approval Queue'
- 'Champion' -> 'Primary Advocate'
- 'Evaluator' -> 'Technical Reviewer'
"""

from __future__ import annotations

import re

TERMINOLOGY_MAP: dict[str, str] = {
    "wedge": "Opening Angle",
    "wedges": "Opening Angles",
    "opening angle": "Opening Angle",
    "opening angles": "Opening Angles",
    "hook": "Hook",
    "hooks": "Hooks",
    "send card": "Ready to Send",
    "send cards": "Ready to Send",
    "send_card": "Ready to Send",
    "send_cards": "Ready to Send",
    "approval queue": "Approval Queue",
    "champion": "Primary Advocate",
    "champions": "Primary Advocates",
    "primary advocate": "Primary Advocate",
    "primary advocates": "Primary Advocates",
    "evaluator": "Technical Reviewer",
    "evaluators": "Technical Reviewers",
    "technical reviewer": "Technical Reviewer",
    "technical reviewers": "Technical Reviewers",
}


def map_term(term: str, default: str | None = None) -> str:
    """Map internal pipeline and sales terminology to founder-friendly plain English.

    Case-insensitive, normalizes underscores and hyphens to spaces.
    If the term is not recognized, returns ``default`` if provided, else the input term.
    """
    if not term:
        return default if default is not None else term

    cleaned = str(term).strip().lower()
    if cleaned in TERMINOLOGY_MAP:
        return TERMINOLOGY_MAP[cleaned]

    # Normalize hyphens and underscores to single spaces
    normalized = re.sub(r"[_\s-]+", " ", cleaned).strip()
    if normalized in TERMINOLOGY_MAP:
        return TERMINOLOGY_MAP[normalized]

    return default if default is not None else term


def format_founder_label(key: str) -> str:
    """Convenience helper to translate a term or fall back to title-cased key."""
    return map_term(key, key.replace("_", " ").replace("-", " ").title())
