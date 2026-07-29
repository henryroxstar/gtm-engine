"""Generic community-signal analysis — deterministic scoring + HTML rendering.

Company-agnostic building blocks for the ``community-signal-analysis`` skill:

  - :mod:`gtm_core.community_signal.model` — the signal-model contract: palette, color
    and HTML-escaping helpers, and lightweight validation.
  - :mod:`gtm_core.community_signal.score` — deterministic aggregation of raw Syften
    match pulls into the quantitative parts of a signal model (counts, per-filter noise,
    share-of-voice, platform mix, momentum). The numbers are computed in code from
    Syften's structured verdict fields, never summarized from untrusted free text — so
    injected prose in a match body can never move a metric (§R5 hardening).
  - :mod:`gtm_core.community_signal.render` — a validated signal model → a single,
    self-contained, theme-aware HTML dashboard (zero external assets; every
    match-derived string HTML-escaped).

Nothing here names a vendor, product, or company — the taxonomy (categories, tracked
entities, audience playbooks) is supplied by the caller / the active profile.
"""
