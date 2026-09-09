"""Canonical publish content hash — the single source of truth for the idempotency key.

Two components need the *same* hash over a publish's bytes:

  - :mod:`agent.publish` (the cockpit publisher) — to dedupe a live publish, so a
    double-click or a restart cannot double-post (``content_hash`` feeds the
    ``Idempotency-Key`` header and the in-memory/durable dedupe checks).
  - :mod:`gtm_core.ledger_cli` (the manual-publish recorder) — to stamp the durable
    ``published`` event with the ``content_sha256`` that
    :meth:`gtm_core.ledgers.Ledgers.published_content_hashes` reads back. Without a
    matching hash, a post made by hand would be invisible to the durable idempotency
    ledger, and later enabling automated publish could re-send the same bytes.

It lives in ``gtm_core`` (the shared engine layer, bundled into the plugin's local
``lib/`` runtime) rather than in ``agent`` so the recorder can import it even where
the ``agent`` package is not present (local/bundled skill execution). ``agent.publish``
re-exports it, so ``agent.publish.content_hash`` is unchanged for existing callers.

Pure stdlib (``hashlib`` only). The ``hermes-publish-v1`` prefix is a version tag: do
NOT change the algorithm or the prefix without bumping it — any change re-hashes every
prior publish and would silently re-enable a duplicate post.
"""

from __future__ import annotations

import hashlib


def content_hash(post: str, media_urls: tuple[str, ...] | list[str]) -> str:
    """Stable sha256 over the exact content. The IDEMPOTENCY key.

    Deliberately does **not** cover a schedule time. "Publish at most once" is a
    statement about content: scheduling the same bytes for two different times is a
    double-post, and mixing the time in here would let both through. Use
    :func:`approval_hash` for the separate job of binding an operator's approval.
    """
    h = hashlib.sha256()
    h.update(b"hermes-publish-v1\n")
    h.update(post.encode("utf-8"))
    h.update(b"\n--media--\n")
    h.update("\n".join(media_urls).encode("utf-8"))
    return h.hexdigest()


def approval_hash(
    post: str,
    media_urls: tuple[str, ...] | list[str],
    scheduled_at: str | None = None,
) -> str:
    """Stable sha256 binding an approval to *everything the operator saw*.

    Two hashes, because they answer two different questions, and collapsing them
    breaks in one direction or the other:

    - :func:`content_hash` — "have we already sent these bytes?" Content only.
      Include the time and the same post scheduled twice sends twice.
    - :func:`approval_hash` — "is this exactly what the human approved?" Content
      **and** time. Omit the time and a draft re-staged for a different hour keeps a
      matching hash, so the changed time dispatches without anyone reviewing it.

    Backward compatible by construction: with no ``scheduled_at`` this returns
    ``content_hash(post, media_urls)`` unchanged, so every existing caller that
    staged a content hash keeps working and no prior approval is re-hashed.
    """
    base = content_hash(post, media_urls)
    if not scheduled_at:
        return base
    h = hashlib.sha256()
    h.update(b"hermes-approval-v1\n")
    h.update(base.encode("utf-8"))
    h.update(b"\n--schedule--\n")
    h.update(scheduled_at.encode("utf-8"))
    return h.hexdigest()
