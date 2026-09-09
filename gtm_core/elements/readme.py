"""The element's own README — generated, never hand-written.

An element's README exists for the person who opens the folder in six months and has to decide
whether these sixteen stills are the ones they want. It is derived from ``element.toml`` in full,
so it can never disagree with what the render path actually reads; a hand edit is overwritten by
the next ``render-readme``, and the header says so.
"""

from __future__ import annotations

from .model import Element

__all__ = ["render_readme"]


def render_readme(element: Element) -> str:
    lines = [
        f"# {element.name}",
        "",
        "> **Generated** from `element.toml` by `python -m gtm_core.elements render-readme`.",
        "> Edit the TOML through the CLI and re-render; a change made here is lost on the next run.",
        "",
        f"- **slug** `{element.slug}`",
        f"- **kind** {element.kind}",
    ]
    if element.depicts:
        lines.append(f"- **depicts** {element.depicts}")
    if element.consent_ref:
        lines.append(f"- **consent** recorded at `{element.consent_ref}`")
    if element.provider_handles:
        for key, value in sorted(element.provider_handles.items()):
            lines.append(f"- **{key}** `{value}`")
    else:
        lines.append("- **provider handles** none — this element is local-only")
    lines.append(f"- **updated** {element.updated or 'unknown'}")
    if element.draft:
        lines += [
            "",
            "> ⚠️ **Draft.** Imported pose descriptions are placeholders. Fill each pose's `use` "
            "and clear the flag before a render is pointed here.",
        ]

    if element.constraints:
        lines += ["", "## Always", ""] + [f"- {c}" for c in element.constraints]
    if element.avoid:
        lines += ["", "## Never", ""] + [f"- {a}" for a in element.avoid]

    lines += [
        "",
        "## Poses",
        "",
        'Order is the contract — position 1 is "the first reference".',
        "",
    ]
    lines += ["| # | pose | file | ratio | animatable | use |", "|---|---|---|---|---|---|"]
    for i, pose in enumerate(element.poses, 1):
        lines.append(
            f"| {i} | `{pose.name}` | `{pose.file}` | {pose.ratio or '—'} | "
            f"{'yes' if pose.animatable else 'no'} | {pose.use or '—'} |"
        )
    return "\n".join(lines) + "\n"
