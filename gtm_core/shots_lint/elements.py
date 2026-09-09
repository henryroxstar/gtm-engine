"""A shot's `elements` — named library subjects the render should be pointed at.

A shot names elements by SLUG (`gtm_core.elements`), and the storyboard resolves each slug to
ordered pose files before any render. The rule here is narrow on purpose: it warns when a named
slug has no element under the profile's content root, because that is the one failure the
downstream steps cannot report usefully — `resolve` would refuse mid-run, after a script is
written and a gate is passed.

Inert without a resolvable content root. The CLI has no `--profile`, and guessing one is how a
linter starts reporting a tenant's elements as missing from another tenant's tree.
"""

from __future__ import annotations

__all__ = ["_lint_elements_exist"]


def _lint_elements_exist(
    shot: dict, prefix: str, warnings: list[str], *, known: set[str] | None
) -> None:
    """Warn when a shot names an element slug the profile's library does not hold."""
    if known is None:
        return  # no resolvable library in hand; guessing across tenants is worse than silence
    named = shot.get("elements")
    if not isinstance(named, list):
        return
    for slug in named:
        if isinstance(slug, str) and slug and slug not in known:
            warnings.append(
                f"{prefix}.elements names {slug!r}, and the profile's element library holds no "
                f"such slug ({sorted(known) or 'the library is empty'}). The storyboard resolves "
                "these to ordered pose files before any render, so this fails mid-run, after the "
                "script is written and the plan gate is passed. Define it "
                "(`python -m gtm_core.elements define`) or correct the slug."
            )
