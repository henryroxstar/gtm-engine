"""Re-export shim — implementation lives in gtm_core.radar.

``python -m agent.radar`` continues to work; skills can also call
``python -m gtm_core.radar`` (preferred going forward).
"""

from gtm_core.radar import (  # noqa: F401
    cluster_and_score,
    dedupe,
    load_pillars,
    main,
    news_from_rows,
    render_digest,
    seen_ids_from_history,
)

if __name__ == "__main__":
    raise SystemExit(main())
