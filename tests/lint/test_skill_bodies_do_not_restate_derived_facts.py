"""Contract: prose may not restate a capability fact that code owns.

The [restated-numbers-go-stale](../../CLAUDE.md) class. A fact copied out of the module that owns
it into a skill body or a profile doc drifts the moment the module changes, and nothing notices —
the copy keeps reading as authoritative long after it stopped being true.

The demonstrated case, and why this file exists: on 2026-08-20 `heygen_avatar` was bound to the
`presenter` role in `gtm_core/render_engines.toml` for DISCLOSED renders. Nine months of prose
across four skill surfaces and one tenant profile went on asserting the opposite — "no engine is
bound to the `presenter` role", "a synthetic talking head CANNOT be rendered" — so an agent
planning against those docs plans against a capability fact that has been false for over a week.

The rule is not "never mention the presenter role". It is **never assert what the registry
answers**. A skill body may truthfully say *this skill does not render presenters*; it may not say
*nothing can*. The registry is one command away and it is free:

    uv run python -m gtm_core.render_engines --role presenter --speaks [--disclosed]

Design: the 2026-08-29 video-router hardening note, item C3d.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: Claims that the `presenter` role has NO engine at all, or that a synthetic talking head is
#: unrenderable as such. Each is a statement about the registry, which is the registry's to make.
#:
#: Scoped statements are deliberately NOT matched: "this skill does not render presenters" and
#: "the lane has no presenter node" are true, useful, and say nothing about what the registry
#: binds. The pattern below keys on the *unbound / no engine / cannot be rendered* assertion, not
#: on the word "presenter".
_UNBOUND_CLAIMS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"no engine is bound to the .?.?presenter", re.IGNORECASE),
        "asserts the presenter role has no engine; heygen_avatar has been bound since 2026-08-20",
    ),
    (
        re.compile(r"presenter.{0,12}role.{0,12}unbound", re.IGNORECASE | re.DOTALL),
        "calls the presenter role unbound; it is bound to heygen_avatar for disclosed renders",
    ),
    (
        re.compile(r"talking head\b.{0,60}?cannot be rendered", re.IGNORECASE | re.DOTALL),
        "asserts a synthetic talking head is unrenderable; a DISCLOSED one renders on HeyGen",
    ),
)


def _docs_that_may_not_restate_the_registry() -> list[Path]:
    """Skill surfaces plus tenant profile docs.

    ``profiles/`` is absent from a public OSS carve, so the glob simply yields nothing there and
    the skill half of the census still runs — no ``private_tree`` marker needed.
    """
    paths = sorted((REPO / "plugin" / "skills").rglob("*.md"))
    paths += sorted((REPO / "profiles").glob("*/PROFILE.md"))
    return paths


def test_no_skill_body_claims_the_presenter_role_is_unbound() -> None:
    """C3d's regression test.

    A tenant profile asserted a capability fact that has been false since 2026-08-20, and the
    same false claim had propagated into three skill surfaces. A doc that
    tells an agent a lane cannot exist is worse than one that says nothing: it stops the agent
    looking, so the drift is self-concealing.
    """
    offenders: list[str] = []
    for path in _docs_that_may_not_restate_the_registry():
        text = path.read_text(encoding="utf-8")
        for pattern, why in _UNBOUND_CLAIMS:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                rel = path.relative_to(REPO)
                offenders.append(f"{rel}:{line} — {why} (matched {match.group(0)!r})")

    assert not offenders, (
        "prose asserts a `presenter`-role capability the render-engine registry owns:\n  "
        + "\n  ".join(offenders)
        + "\n\nSay what is true and scoped ('this skill does not render presenters'), and defer "
        "the capability question to `python -m gtm_core.render_engines --role presenter --speaks "
        "[--disclosed]`. One home per fact."
    )


# --- publish / Tier-A thresholds -----------------------------------------------------
#
# Same class, different fact. Scoring thresholds are TENANT data: they live in the active
# profile's `knowledge/icp-personas.md` and they differ per segment. A numeral for one carried
# into the company-agnostic plugin is a fact the plugin cannot keep true.
#
# The demonstrated case: on 2026-09-04 a new top-level section in `gates-and-scoring.md` — a peer
# of "Order of operations (every tenant)", so read as machinery — asserted "Publish is >=6 and
# Tier-A is >=8". The profile it would be applied to had said Publish >=5 / Tier-A >=6 for
# enterprise since 2026-07-19, and >=5 / >=6 for the builder segment added that same day. Two of
# three segments wrong, in the file an agent reads to learn how tiering works.
#
# The rule is not "never write a number". It is: a threshold numeral may appear ONLY inside a
# section whose heading marks it as frozen illustration, where a reader has been told not to carry
# it into a run. Per-run distribution aims ("Tier-A aim: >=3 of 10") are a different fact that the
# plugin does own, and are deliberately not matched.
_THRESHOLD_RE = re.compile(
    r"\b(?P<what>Publish|Tier-A)\s*(?:threshold\s*)?(?:is\s*)?(?:≥|>=)\s*\d",
)
_ILLUSTRATIVE_RE = re.compile(r"^#{2,3}\s+.*(illustrat|example)", re.IGNORECASE | re.MULTILINE)


def _enclosing_heading(text: str, pos: int) -> str:
    """The nearest ``##``/``###`` heading above ``pos``, or ``""`` at top level."""
    heads = [m for m in re.finditer(r"^#{2,3}\s+.*$", text, re.MULTILINE) if m.start() < pos]
    return heads[-1].group(0) if heads else ""


def test_no_plugin_doc_states_a_publish_or_tier_a_threshold_outside_an_illustrative_section() -> (
    None
):
    """Thresholds are the active profile's; the plugin may point at them, never restate them."""
    offenders: list[str] = []
    for path in sorted((REPO / "plugin" / "skills").rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        for match in _THRESHOLD_RE.finditer(text):
            heading = _enclosing_heading(text, match.start())
            if _ILLUSTRATIVE_RE.match(heading):
                continue
            line = text.count("\n", 0, match.start()) + 1
            rel = path.relative_to(REPO)
            offenders.append(
                f"{rel}:{line} — states a {match.group('what')} threshold "
                f"({match.group(0)!r}) under {heading.strip() or '(top level)'!r}"
            )

    assert not offenders, (
        "a company-agnostic plugin doc states a scoring threshold that belongs to the active "
        "profile:\n  "
        + "\n  ".join(offenders)
        + "\n\nThresholds differ per tenant AND per segment. Point at "
        "`knowledge/icp-personas.md` § 'Gates, scoring rubric & thresholds' instead, or move the "
        "numeral under a heading marked as frozen illustration."
    )
