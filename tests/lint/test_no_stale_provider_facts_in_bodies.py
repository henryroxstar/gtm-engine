"""Cross-cutting: no dated provider snapshot survives in prose, and the lane list agrees with itself.

Two drift checks the hardening PRD calls out separately from the per-change tests, because both
describe a whole CLASS of staleness rather than one instance.

**A credit balance in prose is stale the moment it is written.** `video-router` Step 0.5 carried
"600 media credits with zero used" as of 2026-08-19. `get_plan_usage` is free and returns the live
figure, so the snapshot bought nothing and expired immediately — and a *confidently wrong* balance
is worse than none, because it is the number someone plans a batch against.

**The manifest description and the body's routing table must name the same lanes.** The 08-28 PRD
found the manifest advertising four lanes while the body carried five, so the skill's own
description misrepresented what it could do — and the description is what the agent reads when
deciding whether to invoke it at all.

Design: the 2026-08-29 video-router hardening note, item C3c, cross-cutting.
"""

from __future__ import annotations

import re
from pathlib import Path

from gtm_core.gating import stub_list

REPO = Path(__file__).resolve().parents[2]

VIDEO_SKILLS = (
    "video-router",
    "video-script",
    "video-storyboard",
    "video-render",
    "video-avatar",
    "video-finish",
    "video-restyle",
    "video-clip",
    "video-score",
)

#: A BALANCE — how much of an allowance is left right now. That is what goes stale on every call,
#: and it is free to read live.
#:
#: A PRICE is deliberately not matched. "10 credits vs seedance's 36", "1 media credit per billed
#: minute", "~23 credits/min" are rate-card facts: they change on a vendor release cadence, they
#: are what a cost ESTIMATE is built from, and banning them would delete C5. The discriminator is
#: a balance word next to the figure, not the figure itself.
_BALANCE_WORDS = r"(?:remaining|unused|used|left|available|consumed)"
_BALANCE_CLAIM = re.compile(
    rf"\d[\d,]*\s*(?:of\s*\d[\d,]*\s*)?(?:media\s+|ai\s+)?credits?\b[^.\n]{{0,40}}?\b{_BALANCE_WORDS}\b"
    rf"|\b{_BALANCE_WORDS}\b[^.\n]{{0,40}}?\d[\d,]*\s*(?:media\s+|ai\s+)?credits?\b",
    re.IGNORECASE,
)


def test_no_body_hardcodes_a_provider_credit_balance() -> None:
    # Bodies AND manifest descriptions: the description is prose too, and it is what an agent
    # reads before it ever opens the body, so a stale figure there is read more often, not less.
    # A stub-carved skill ships no body in the public cut (`gtm_core.gating stub-list`); every
    # other body must still exist, so only a withheld one may be absent without failing loudly.
    withheld = stub_list()
    paths = [
        p
        for n in VIDEO_SKILLS
        if (p := REPO / "plugin" / "skills" / n / "body_template.md").exists() or n not in withheld
    ]
    paths += [REPO / "gtm_core" / "skills" / f"{n.replace('-', '_')}.py" for n in VIDEO_SKILLS]

    offenders: list[str] = []
    for path in paths:
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not _BALANCE_CLAIM.search(line):
                continue
            offenders.append(f"{path.relative_to(REPO)}:{i} — {line.strip()[:100]}")

    assert not offenders, (
        "these lines state a provider credit BALANCE as prose. A balance is stale on write and "
        "the live read is free (`get_plan_usage`, `balance`):\n  "
        + "\n  ".join(offenders)
        + "\n\nRead it live and quote the result, or make the point without the figure — the "
        "durable lesson ('a paid route sat unused while we hand-rolled') survives the number "
        "going out of date, and dating the snapshot does not rescue it: a dated balance is the "
        "defect, not an excuse for it."
    )


def test_video_router_body_and_manifest_describe_the_same_lanes() -> None:
    """The exact drift the 08-28 PRD found: manifest said four lanes, the body had five."""
    body = (REPO / "plugin" / "skills" / "video-router" / "body_template.md").read_text(
        encoding="utf-8"
    )
    manifest = (REPO / "gtm_core" / "skills" / "video_router.py").read_text(encoding="utf-8")
    # The ROUTABLE lanes, from the module that owns the lane matrix — not every graph file in the
    # pack. `cross-modal-campaign` is a CALLER of this skill, not a lane it offers, and counting
    # it would make the test fail for naming the caller correctly.
    from gtm_core.video_preflight import LANE_REQUIREMENTS

    lanes = set(LANE_REQUIREMENTS)
    in_body = {g for g in lanes if g in body}
    in_manifest = {g for g in lanes if g in manifest}

    assert in_body, "the router body names no creator-pack variant at all"
    assert in_body == in_manifest, (
        "the router body and its manifest description name different lane sets — "
        f"body-only: {sorted(in_body - in_manifest)}, manifest-only: {sorted(in_manifest - in_body)}. "
        "The description is what an agent reads when deciding whether to invoke this skill, so a "
        "lane missing from it is a lane that is effectively unreachable."
    )
