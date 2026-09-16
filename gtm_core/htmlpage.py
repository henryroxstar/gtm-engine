"""Embedding data in a self-contained HTML page, safely.

Both offline pages this repo ships — the hold sheet (:mod:`gtm_core.lanes.sheet`) and the
campaign dashboard (:mod:`gtm_core.email_campaign_dashboard`) — are one file with no server:
JSON goes into a ``<script>`` block, and the page's own JS reads it. That shape has exactly
one sharp edge, and it is the reason this module exists rather than a ``json.dumps`` at each
call site.
"""

from __future__ import annotations

import json


def script_json(value) -> str:
    """JSON that is safe to place inside a ``<script>`` block.

    ``</`` is escaped because an HTML parser ends a script at the first ``</script>`` **in
    the raw bytes**, without parsing the JSON around it — so a string value containing that
    sequence closes the block early and the remainder of the payload is reparsed as markup.
    Escaping the slash is invariant under ``JSON.parse``, so the value the page reads back is
    byte-identical to the one passed in.

    ``ensure_ascii=False`` keeps non-ASCII readable in the source (the page declares UTF-8),
    which matters because these pages are read by a person opening ``view-source`` as often
    as by the browser.

    Promoted here from ``lanes.sheet`` on 2026-09-10 when the dashboard grew a payload of its
    own. Deliberately NOT copied: the escape is the whole security property of the pattern,
    and two copies is two chances to fix one and forget the other.
    """
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")
