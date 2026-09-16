"""The email-campaign status page — written for a reader who does not work the tooling.

Supersedes ``status.html`` (pipeline, rep-facing) and ``campaigns.html`` (portfolio,
CRO-facing). Those were split **by audience**, which is the wrong axis: both readers need
both halves, and splitting there is what let a reply rate live on one page while the list
quality that explains it lived on another. This page splits **by question**:

* **Who we're emailing** — how many, where, which seat, and what we actually know.
* **What we're saying** — the template, the per-seat message, every subject line, the
  signals it opens on, and every check the copy passed.
* **What we'll learn** — the hypothesis, the parameters, and which comparisons are
  readable versus confounded.
* **Operator notes** — the mechanics: what is loaded in the sending tool right now, what
  still has to be pushed, and what is blocking the start. Split off because the reader this
  page is written for is not the person who presses the buttons, and the re-push procedure
  was displacing the numbers the page is actually opened for.

Two rules govern the wording. **No tool nouns**: a reader who has never opened the
sequencer should not meet the word "sequencer", "enrolled", "merge tag" or "variant"
without a plain-English gloss. And **no bare counts**: every number says what it means for
whether an email can be sent to that person today.

Stdlib-only and provider-free. The live numbers come from a snapshot the agent layer
drops by hand, so the page **reconciles it against the ledger** and says so loudly when
they disagree, rather than rendering a confident page about sequences that no longer exist.

CLI::

    python -m gtm_core.email_campaign_dashboard --profile P \\
        [--scope {campaign,open,all}] [--campaign SLUGS] [--check-fresh] [--no-stubs]
"""

from __future__ import annotations

from .aggregate import _cadence_split, _scope_figures  # noqa: F401
from .cli import _cli  # noqa: F401

# Eager, complete re-export of the pre-split module surface (PRD §5 rule 1):
# every submodule is imported here, so module-level registrations run on
# `import <package>` exactly as they did on `import <module>`.
from .config import (  # noqa: F401
    BENCHMARKS,
    FUNNEL_GLOSS,
    PAGE_NAME,
    PERSONA_AXIS,
    PRIMARY_BENCHMARK,
    SEAT_COVERAGE,
    TABS,
    dashboard_path,
    input_globs,
    page_title,
)
from .forecast import _forecast_block, _lanes  # noqa: F401
from .format import _barlist, _e, _i, _pct, _rate_of, _seat_label, _stat  # noqa: F401
from .model import (  # noqa: F401
    _lint_records,
    _spec_copy,
    build_model,
    market_split,
    prospect_status_model,
    prospecting_runs,
    reconcile_snapshot,
    scope_to_campaign,
)
from .render import _stub, check_fresh, page_path, render_dashboard, render_html  # noqa: F401
from .scope import MODES, Scope, resolve  # noqa: F401
from .views_learn import _learn_view, _ops_view  # noqa: F401
from .views_samples import _samples_section  # noqa: F401
from .views_status import _status_view  # noqa: F401
from .views_what import _what_view  # noqa: F401
from .views_who import _intent_block, _who_view  # noqa: F401

__all__ = [
    "TABS",
    "PAGE_NAME",
    "PERSONA_AXIS",
    "SEAT_COVERAGE",
    "FUNNEL_GLOSS",
    "BENCHMARKS",
    "PRIMARY_BENCHMARK",
    "dashboard_path",
    "reconcile_snapshot",
    "market_split",
    "prospecting_runs",
    "build_model",
    "page_title",
    "render_html",
    "render_dashboard",
    "scope_to_campaign",
    "resolve",
    "Scope",
    "MODES",
    "page_path",
    "check_fresh",
    "input_globs",
]
