"""The in-page staleness banner: a hidden card the browser unhides when the figures have aged.

``--check-fresh`` convicts an old page only when somebody asks. A page opened a week after it
was built has nothing on it to say its sending figures are old, so the one fact that depends on
the day it is READ is decided in the browser: Python emits the figures instant and the limit as
attributes plus a hidden card carrying both sentences, and ``banner.js`` writes the day count
and unhides it (:func:`stale_banner`).

What it does and does not cover:

* It compares the browser clock to the **figures instant**, not the build date — the figures are
  what go stale; the page is always built now. ``data-figures`` carries the exact moment the
  figures were fetched, normalised to UTC by the same parse every age on the page uses
  (``health.figures_instant``); the sentence prints that stamp's ISO date.
* **A static capture will not show it** (a saved-as-text copy, an email preview, anything that
  does not run the page's script): the card is emitted ``hidden``, so a reader without
  JavaScript sees nothing rather than a wrong age. ``--check-fresh`` is what covers that case.
* It is emitted only when there is an age to count down: a snapshot already past the limit at
  build time has the page-wide warnings strip (static, no script needed), and one with no
  usable date has that strip too, so a second copy here would only repeat it.
* It **fails closed** in the browser: an unreadable date or limit shows the "no usable date"
  sentence instead of staying quiet (see ``banner.js``).

Every sentence is rendered here, never in the JS (§R14's lint reads Python only), and the
limit is ``config.FIGURES_MAX_AGE_DAYS`` through ``health.figures_state`` — never a typed number.
"""

from __future__ import annotations

from pathlib import Path

from .format import _e

_BANNER_JS = (Path(__file__).parent / "banner.js").read_text(encoding="utf-8")


def stale_banner(m: dict) -> str:
    """The hidden card and its script, or ``""`` when the strip already covers the page."""
    fig = m.get("figures") or {}
    if fig.get("over_limit", True):
        return ""
    date = _e(str(fig.get("date") or ""))
    # The browser measures from the exact instant, not the calendar date (see `figures_instant`);
    # the sentence still prints the ISO date. A model built without an instant falls back to the
    # date alone, which `banner.js` reads as UTC midnight.
    instant = _e(str(fig.get("instant") or fig.get("date") or ""))
    limit = int(fig["limit"])
    ask = "Ask for the latest before relying on it."
    return (
        f'<div class="card" id="stale-banner" data-figures="{instant}" data-limit="{limit}" hidden>'
        f"<p data-known hidden>The sending figures on this page are from {date}, "
        f"<span data-age></span> days ago. {ask}</p>"
        f"<p data-unknown hidden>The sending figures on this page carry no date this page can "
        f"read, so their age is unknown. {ask}</p>"
        "</div>"
        f"<script data-stale-banner>{_BANNER_JS}</script>"
    )
