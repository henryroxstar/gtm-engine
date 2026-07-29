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
    """Stable sha256 over the exact content. Binds an approval to its draft and
    serves as the idempotency key (same content ⇒ same key ⇒ publishes once)."""
    h = hashlib.sha256()
    h.update(b"hermes-publish-v1\n")
    h.update(post.encode("utf-8"))
    h.update(b"\n--media--\n")
    h.update("\n".join(media_urls).encode("utf-8"))
    return h.hexdigest()
