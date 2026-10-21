from __future__ import annotations

import re

from .names import _has_letters

# --- title ---------------------------------------------------------------

# A LinkedIn headline pasted into the job-title column: "Vice President | Compliance".
# The segments are real information, so they are JOINED, not truncated away — dropping
# everything after the first pipe would silently lose half of someone's role.
_TITLE_PIPE_RE = re.compile(r"\s*[|•·]\s*")
# A LinkedIn *summary* captured into the title field: "Deputy director, treasury, 20 years in
# institutional portfolio management & strategic funding". Detected on the tenure boast, not on
# length — plenty of real C-suite titles run past 70 characters ("Chief Information Security
# Officer & Vice President of Information Security"), so length alone only produces noise.
_TITLE_BIO_RE = re.compile(r"\b\d+\+?\s*(?:years?|yrs?)\b", re.IGNORECASE)


def clean_title(title: str) -> str:
    """Normalize a job title for ``{{Job Title}}`` rendering.

    Converts headline pipe separators to commas ("Vice President | Compliance" ->
    "Vice President, Compliance") and collapses whitespace. Deliberately does NOT
    re-case: job titles arrive in wildly mixed case across sources and guessing at
    capitalization ("head of emea private assets") is a copy decision, not hygiene.
    """
    raw = (title or "").strip()
    if not raw:
        return ""
    out = _TITLE_PIPE_RE.sub(", ", raw)
    out = re.sub(r"\s+", " ", out).strip(" ,")
    return out if _has_letters(out) else raw
