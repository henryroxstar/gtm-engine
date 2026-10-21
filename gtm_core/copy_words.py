"""The words a recipient reads in an email step, and a digest of them.

Two things were wanted from one definition of "the same copy":

* ``agent/email_dispatch.py`` reads a paused sequence back from Saleshandy and refuses to load
  anyone unless it says word for word what the operator approved. Saleshandy re-wraps HTML it
  stores, so the comparison is on words, not bytes: tags dropped, entities decoded, whitespace
  collapsed, plus every link and image address (dropping tags would otherwise hide a changed
  ``href``).
* A digest of those same words, recorded when a sequence is staged, lets the dispatcher notice
  that the copy changed AFTER it was checked (``gtm_core.load_preconditions``).

They live here, not in the dispatcher, because the digest has to be computable by whatever
records it at staging, and ``gtm_core`` may not import ``agent`` (tests/contracts/test_layering.py).
Stdlib only, and importable on the system Python a hook may be run with.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from typing import Any

#: The name recorded beside a ``step_sha256`` list (``step_sha256_algo``) so a reader knows which
#: definition of "the same copy" produced it. A list with no name, or another name, is not
#: comparable and is never treated as a mismatch.
COPY_DIGEST_ALGO = "copy-v1"

_BREAK_TAG = re.compile(r"<\s*/?\s*(?:br|p|div|li|ul|ol|tr|h[1-6])\b[^>]*>", re.IGNORECASE)
_ANY_TAG = re.compile(r"<[^>]*>")
_LINK = re.compile(r"""\b(?:href|src)\s*=\s*(["'])(.*?)\1""", re.IGNORECASE | re.DOTALL)


def normalise_copy(text: Any) -> str:
    """The words a recipient reads: tags dropped (block tags and ``<br>`` as spaces),
    entities decoded, whitespace collapsed. Saleshandy re-wrapping approved HTML is not a
    difference; any change to the wording is."""
    text = _ANY_TAG.sub("", _BREAK_TAG.sub(" ", text if isinstance(text, str) else ""))
    return " ".join(html.unescape(text).split())


def variant_words(payload: Any) -> tuple:
    """What a variant says: its subject, body and preheader wording, plus every link and image
    address in the body — dropping tags would otherwise hide a changed ``href``."""
    payload = payload if isinstance(payload, dict) else {}
    content = payload.get("content") if isinstance(payload.get("content"), str) else ""
    links = tuple(sorted(html.unescape(m.group(2)).strip() for m in _LINK.finditer(content)))
    words = tuple(normalise_copy(payload.get(key)) for key in ("subject", "content", "preheader"))
    return (*words, links)


def copy_digests(steps: Any) -> list[str]:
    """One 16-hex digest per step, in order, over the words of its variants.

    A step's digest ignores the order of its variants (a sequencer may list them differently)
    and sees a variant added or dropped. Raises ``ValueError`` for a shape it cannot digest:
    a smaller answer from unreadable input would read as a match.
    """
    if not isinstance(steps, list) or not steps:
        raise ValueError("steps must be a non-empty list")
    digests: list[str] = []
    for position, step in enumerate(steps, start=1):
        variants = step.get("variants") if isinstance(step, dict) else None
        if not isinstance(variants, list) or not variants:
            raise ValueError(f"step {position} carries no variants to digest")
        words = sorted(json.dumps(variant_words(v), ensure_ascii=False) for v in variants)
        digests.append(hashlib.sha256("\n".join(words).encode("utf-8")).hexdigest()[:16])
    return digests
