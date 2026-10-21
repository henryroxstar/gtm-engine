"""Plain-language status reporting for source lists (R2.7, R2.8, R1.2, R1.6, R6.3).

One function, :func:`record_lines`, feeds both the terminal prospect status report and the status
page, so the two cannot word the same fact two ways. The lines are plain words for the operator.
"""

from __future__ import annotations

import datetime
from pathlib import Path

from . import run_scope, signal_outcomes, signal_view
from .paths import resolve_content_root
from .signal_obs import registry, review, switch


def record_lines(
    profile: str,
    product: str | None = None,
    *,
    profiles_root: Path | None = None,
    content_root: Path | None = None,
    today: datetime.date | None = None,
) -> list[str]:
    """The plain lines about source lists that the record section shows."""
    if not switch.sources_enabled():
        return [f"Source lists: off. Switch on with {switch.SETTING}=1."]

    if not switch.view_routing_enabled():
        mode = f"Source lists: collecting only. Routing is off until {switch.VIEW_SETTING}=1."
    else:
        mode = "Source lists: used for choosing emails."

    try:
        scope = run_scope.require(profile, product, profiles_root=profiles_root)
        resolved_product = scope.product
    except run_scope.ScopeError:
        return [mode, "Name a product with --product to see source lists for it."]

    lines = [mode]
    root = content_root or resolve_content_root()

    ctx = signal_view.attest_context(
        profile,
        resolved_product,
        profiles_root=profiles_root,
        content_root=root,
        today=today,
    )
    if ctx is not None:
        lines.extend(signal_view.refusal_lines(ctx))

    reg, warning_sentence = registry.load_for_run(
        profile,
        resolved_product,
        profiles_root=profiles_root,
        content_root=root,
        today=today,
    )
    if warning_sentence:
        lines.append(warning_sentence)

    if reg is not None:
        try:
            waiting = review.waiting_count(
                profile,
                resolved_product,
                content_root=root,
                profiles_root=profiles_root,
                today=today,
            )
            if waiting == 1:
                lines.append(
                    "1 name found on a source list is waiting for a decision on which company it is."
                )
            elif waiting > 1:
                lines.append(
                    f"{waiting} names found on source lists are waiting for a decision on which company they are."
                )
        except (OSError, registry.RegistryError):
            pass

    if ctx is not None and not ctx.refused and not switch.view_routing_enabled():
        premises = list(
            signal_view.load_premise_vocab(profile, profiles_root, resolved_product).values()
        )
        rows = signal_view.ledger_rows(root, profile)
        count = signal_view.shadow_total(rows, premises, ctx, profile=profile, today=today)
        lines.append(signal_view.shadow_line(count))

    try:
        rep = signal_outcomes.report(root, profile)
        if rep.classes:
            lines.extend(signal_outcomes.plain_lines(rep))
            lines.extend(signal_outcomes.record_lines(rep))
    except (OSError, ValueError):
        pass

    return lines


__all__ = ["record_lines"]
