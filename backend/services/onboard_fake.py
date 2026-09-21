"""Dev-only scripted onboarding extraction (``GTM_FAKE_RUNS``): a ProfileDraft with no brain.

A local stack has no model key, so ``POST /v1/onboard`` cannot reach a draft there, and a
client developer cannot build the onboarding → promote → first-run flow. Under the SAME flag
and guard as scripted runs (:func:`backend.services.runs.fake.fake_runs_enabled` — the flag
AND ``ENV=development``; ``backend.main.check_fake_runs`` refuses to boot with the flag set
anywhere else) the two brain calls are swapped for this script. Everything around them stays
real: ingest, slugify, render, stage, the persisted ``.draft.json``, diff, promote and the
``profiles`` row.

Four properties hold by construction:

* **Unreachable outside development.** The router branches on ``fake_runs_enabled()`` at the
  single call site of each brain call; with the flag unset, or with ``ENV`` anything but
  ``development``, the real extractor runs.
* **No spend, no egress, no filesystem.** Nothing here calls a model, a provider, or the
  network, reads the onboarding spend ledger, or touches disk. Pinned by
  ``tests/backend/test_onboard_fake.py``, an AST check on this file's imports.
* **Never drifts from the real shape.** Every draft this returns has been through the real
  validator (``_parse_and_validate_draft``), so a scripted draft the real path would refuse
  cannot be staged.
* **Visibly scripted.** Every draft carries :data:`SCRIPTED_GAP` in ``gaps`` — the field a
  client already shows the operator as "what we could not find" — and each call logs one
  WARNING.

What a test controls: the company name is the first non-blank line of the text, stripped and
capped at :data:`MAX_NAME_LEN`. Everything else is fixed fictional copy. A text with no usable
first line (no letter or digit) is the caller's error, with the message the real ingest gives
for unreadable text.

A URL source is scripted too (:func:`fake_page_text`): no crawler runs and no
``FIRECRAWL_API_KEY`` is needed, so a client can walk the URL flow locally. The company name is
the host's first label, title-cased (``https://www.example.com`` → ``Example``).

Each scripted job waits :func:`fake_delay_s` seconds (``GTM_FAKE_ONBOARD_DELAY_S``, default
:data:`DEFAULT_DELAY_S`) before its draft is ready, so the pending → running → succeeded
polling flow is observable. It is a sleep, nothing else.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re

from agent.onboard.errors import OnboardingInputError
from agent.onboard.extract import _parse_and_validate_draft
from agent.onboard.slug import slugify

log = logging.getLogger(__name__)

#: The ``gaps`` entry that marks a draft as scripted.
SCRIPTED_GAP = "scripted by GTM_FAKE_RUNS: no model read this source"
MAX_NAME_LEN = 80
FAKE_PRODUCT_SLUG = "core-service"

_UNREADABLE = (
    "Ingested source has no readable text — the page may be blocked, JS-only, "
    "or an image-only PDF. Ask the founder to paste their About text or a deck."
)
_USABLE = re.compile(r"[A-Za-z0-9]")
# [userinfo@]host[:port]/..., matched after any "scheme://" is cut off.
_URL_HOST = re.compile(r"^(?:[^@/?#]*@)?([^:/?#]+)")

#: Seconds each scripted onboarding job waits before its draft is ready.
DELAY_ENV = "GTM_FAKE_ONBOARD_DELAY_S"
DEFAULT_DELAY_S = 1.0

_WARNING = (
    "GTM_FAKE_RUNS is ON — onboarding %s is SCRIPTED: no model read the source, no cost is "
    "recorded. Local development only."
)


def _company_name(raw_text: str) -> str:
    first = next((line.strip() for line in raw_text.splitlines() if line.strip()), "")
    if not _USABLE.search(first):
        raise OnboardingInputError(_UNREADABLE)
    return first[:MAX_NAME_LEN].strip()


def fake_delay_s() -> float:
    """``GTM_FAKE_ONBOARD_DELAY_S``, floored at 0; unset, empty or unparseable is the default."""
    try:
        return max(0.0, float(os.getenv(DELAY_ENV) or DEFAULT_DELAY_S))
    except ValueError:
        return DEFAULT_DELAY_S


def fake_page_text(url: str) -> str:
    """The scripted twin of URL ingest: page text whose first line names the host's company."""
    rest = url.strip()
    rest = rest.split("://", 1)[1] if "://" in rest else rest
    match = _URL_HOST.match(rest)
    host = match.group(1).lower() if match else ""
    labels = [label for label in host.split(".") if label]
    if labels and labels[0] == "www":
        labels = labels[1:]
    if not labels or not _USABLE.search(labels[0]):
        raise OnboardingInputError(f"not a URL with a host name: {url[:80]!r}")
    log.warning(_WARNING, "URL fetch")
    name = labels[0].replace("-", " ").title()
    return f"{name}\nScripted page text for {host}. No crawler fetched this URL.\n"


def _slug(name: str) -> str:
    try:
        return slugify(name)
    except ValueError as exc:  # a reserved name: the caller's to change, as a 422
        raise OnboardingInputError(f"company name cannot be a profile name: {exc}") from exc


def _product() -> dict:
    return {
        "slug": FAKE_PRODUCT_SLUG,
        "name": "Core Service",
        "description": "The scripted placeholder product of a scripted draft.",
        "capabilities": [],
        "use_cases": ["Placeholder use case written by the scripted extractor."],
        "references": [],
        "technical_notes": "Scripted placeholder. Replace it before use.",
    }


def fake_extract(raw_text: str) -> dict:
    """The scripted twin of ``agent.onboard.extract.extract``: a validated ProfileDraft."""
    name = _company_name(raw_text)
    log.warning(_WARNING, "extraction")
    draft = {
        # Every value below the company name is fixed scripted placeholder copy, but it is
        # SHAPED like the real thing on purpose: this draft goes through the real validator,
        # which since 2026-09-17 checks profile-draft.schema.json (issue #267). A scripted draft
        # that skipped a required field or an item type would have made the fake path pass where
        # the real path refuses — the exact drift this module's contract promises not to have.
        # The source value is not echoed back; it is marked scripted instead.
        "source": {"type": "text", "value": "scripted"},
        "confidence": "low",
        "company": {
            "name": name,
            "slug": _slug(name),
            "brand_name": name,
            "description": "A scripted placeholder description. Replace it before use.",
            "markets": ["Scripted placeholder market"],
            "social_handle": "",
        },
        "voice": {
            "tone": "Scripted placeholder tone. Replace it before use.",
            "principles": [
                "Scripted placeholder principle one.",
                "Scripted placeholder principle two.",
                "Scripted placeholder principle three.",
            ],
            "ban_list": [],
            "examples": [],
        },
        "icp": {
            "personas": [
                {
                    "title": "Scripted Placeholder Persona",
                    "pain_points": ["Scripted placeholder pain point."],
                    "goals": ["Scripted placeholder goal."],
                }
            ],
            "verticals": [],
            "company_size": "",
        },
        "competitors": [],
        "pillars": ["Scripted placeholder pillar one", "Scripted placeholder pillar two"],
        "products": [_product()],
        "brand": {"palette": ["#000000"]},
        "gaps": [SCRIPTED_GAP],
    }
    return _parse_and_validate_draft(json.dumps(draft))


def fake_extract_product(product_slug: str, text: str, draft: dict) -> dict:
    """The scripted twin of ``extract_product``: an unknown slug is the caller's error (422),
    as on the real path; a known one gains one scripted use case. Returns the updated draft."""
    products = draft.get("products", [])
    if not any(p.get("slug") == product_slug for p in products):
        raise OnboardingInputError(f"Product {product_slug!r} not found in draft")
    log.warning(_WARNING, "product re-extraction")
    updated = copy.deepcopy(draft)
    for product in updated["products"]:
        if product.get("slug") == product_slug:
            notes = product.setdefault("use_cases", [])
            notes.append(f"Scripted re-extraction #{len(notes) + 1}.")
    gaps = updated.setdefault("gaps", [])
    if SCRIPTED_GAP not in gaps:
        gaps.append(SCRIPTED_GAP)
    return _parse_and_validate_draft(json.dumps(updated))
