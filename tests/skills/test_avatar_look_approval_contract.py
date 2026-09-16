"""Contract: the operator's approved avatar look is expressible, asked for, and never assumed.

Three surfaces have to agree for V-3 to hold, and each fails differently on its own:

* ``gtm_core.brandkit`` — without a writable key the kit **cannot express** "use this look", which
  is where the gap started.
* ``video-router`` — the ask happens at routing time, **every run**, before any spend. A stored
  look that applied itself would reproduce the 2026-08-29 failure exactly: the render was not
  wrong because the look was badly chosen, it was wrong because nobody was asked.
* ``video-avatar`` — the stored look is the **default** and the orientation/resolution procedure
  is the **tiebreak**. That is inverted from what shipped, where the procedure optimised pixels
  with no notion of whether the operator approves of how they look.
* ``identity-kit`` — the one place the pick is recorded, through the brandkit CLI.

These are body-contract tests: the skill is a prompt, so the artefact under test is the prose an
agent will actually follow.

Design: ``PENDING.md`` V-3 (operator decisions (a)/(b)/(c), 2026-08-29).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

ROUTER = REPO / "plugin" / "skills" / "video-router" / "body_template.md"
AVATAR = REPO / "plugin" / "skills" / "video-avatar" / "body_template.md"
IDENTITY_KIT = REPO / "plugin" / "skills" / "identity-kit" / "body_template.md"

# video-avatar and video-router are `oss = "private"` (gtm_core/gating.toml) — the OSS carve
# stubs their body_template.md out, so tests reading their bodies have nothing to check in
# that distribution.
_avatar_stubbed = pytest.mark.skipif(
    not AVATAR.exists(), reason="video-avatar body_template.md not present (paid-tier stub)"
)
_router_stubbed = pytest.mark.skipif(
    not ROUTER.exists(), reason="video-router body_template.md not present (paid-tier stub)"
)


def _flat(path: Path) -> str:
    """Body text with runs of whitespace collapsed — markdown hard-wraps, so any phrase these
    tests look for can straddle a newline."""
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


# --- (a) the kit can express it, per orientation --------------------------------------------------


def test_the_brand_kit_can_express_an_approved_look_per_orientation():
    """One key cannot serve both a 16:9 master and a 9:16 cut — `video-avatar`'s own dual-ratio
    rule says so, and a single key forces the padding that shipped on 2026-08-27."""
    from gtm_core import brandkit as bk

    assert "heygen_look_landscape" in bk._WRITABLE_IDENTITY_KEYS
    assert "heygen_look_portrait" in bk._WRITABLE_IDENTITY_KEYS


def test_an_approved_look_is_a_plain_string_not_a_list():
    """A look is ONE id per orientation. Both halves are asserted so this cannot pass on a
    brandkit that has never heard of the keys — absent from a set is not the same as scalar."""
    from gtm_core import brandkit as bk

    for key in ("heygen_look_landscape", "heygen_look_portrait"):
        assert key in bk._WRITABLE_IDENTITY_KEYS
        assert key not in bk._LIST_VALUED_KEYS


def test_writing_an_approved_look_round_trips_through_the_cli(tmp_path: Path):
    """Kit writes go through `set_identity_value` only — never an Edit/Write on the TOML."""
    from gtm_core import brandkit as bk

    kit = tmp_path / "BRAND.toml"
    bk.set_identity_value(kit, "identity.heygen_look_landscape", "look-l-1", create=True)
    bk.set_identity_value(kit, "identity.heygen_look_portrait", "look-p-1", create=True)

    loaded = bk._load_toml(kit)["identity"]
    assert loaded["heygen_look_landscape"] == "look-l-1"
    assert loaded["heygen_look_portrait"] == "look-p-1"


# --- (b) the router asks at routing time, every run -----------------------------------------------


@_router_stubbed
def test_the_router_asks_about_the_look_before_any_spend():
    body = _flat(ROUTER).lower()
    assert "heygen_look_landscape" in body and "heygen_look_portrait" in body, (
        "the router body never names the per-orientation look keys, so the decision it is "
        "supposed to surface has no source"
    )


@_router_stubbed
def test_the_router_does_not_present_a_stored_look_as_a_silent_default():
    """The ask survives even when both keys are set.

    Storage exists to make the ask CHEAP — a one-line confirm naming the look and its native
    pixels — not to skip it. The body has to say so in words an agent cannot read past.
    """
    body = _flat(ROUTER).lower()

    assert "every run" in body, (
        "the router body does not say the look is asked about EVERY RUN — without that, a stored "
        "look reads as a setting that has already answered the question"
    )
    assert "never a silent default" in body or "not a silent default" in body, (
        "the router body does not state that a stored look is a proposal rather than a default"
    )
    assert "confirm" in body, "the body does not describe the set-keys case as a one-line confirm"


@_router_stubbed
def test_the_router_names_the_look_in_the_menu_before_spend():
    """Step 1 is the last point where a lane can be changed for free, so it is where the look has
    to be visible — naming it only inside `video-avatar` puts it after the routing decision."""
    body = ROUTER.read_text(encoding="utf-8")
    step1 = re.sub(r"\s+", " ", body.split("## Step 1")[1].split("## Step 2")[0]).lower()
    assert "approved look" in step1 or "look approval" in step1, (
        "the Step 1 menu never names the approved avatar look, so the operator picks a lane "
        "before the one decision that lane's output hangs on"
    )


@_router_stubbed
def test_the_router_does_not_fetch_previews_or_pre_select_a_look():
    """(c) — the pick is the operator's aesthetic call about their own face. A router that fetched
    previews and proposed a favourite would be making it for them, one step earlier."""
    body = _flat(ROUTER).lower()
    assert "do not pre-select" in body or "never pre-select" in body, (
        "the router body does not forbid pre-selecting a look on the operator's behalf"
    )
    assert "media_fetch" not in body, (
        "the router body reaches for a preview fetch; Step 0/0.5 are free reads and the pick is "
        "not the router's to make"
    )


@_router_stubbed
def test_the_missing_look_is_a_decision_not_a_gate_in_the_body_too():
    """The module property has a prose half: a lane is never blocked for lacking a stored look.

    Scoped to the sentence that actually says it — the body is full of unrelated decision/gate
    language, so a bare keyword check here would pass on prose about the caption preset.
    """
    body = _flat(ROUTER).lower()
    assert re.search(r"never blocked[^.]{0,90}look|look[^.]{0,90}never blocks?\b", body), (
        "the router body does not state that a lane is never blocked for lacking an approved "
        "look, which is the decision/gate boundary this step sits on"
    )


# --- video-avatar: stored look is the default, geometry is the tiebreak ---------------------------


@_avatar_stubbed
def test_the_avatar_body_starts_from_the_stored_look():
    body = _flat(AVATAR)
    assert "identity.heygen_look_landscape" in body and "identity.heygen_look_portrait" in body, (
        "video-avatar never reads the approved-look keys, so an operator's recorded preference "
        "has no effect on the render it was recorded for"
    )


@_avatar_stubbed
def test_the_avatar_body_makes_the_pixel_procedure_the_tiebreak():
    """Inverted from what shipped. Orientation-and-resolution used to be the whole selection
    procedure; it is now what decides among candidates when nothing is approved yet."""
    body = _flat(AVATAR).lower()
    assert re.search(r"tie-?break[^.]{0,140}(approved|stored|operator)", body) or re.search(
        r"(approved|stored|operator)[^.]{0,140}tie-?break", body
    ), (
        "video-avatar does not describe the orientation/resolution procedure as the tiebreak "
        "BENEATH the operator's stored look — the word alone already meant something else here "
        "(resolution as the tiebreak under orientation), so the inversion has to be explicit"
    )


@_avatar_stubbed
def test_the_avatar_body_keeps_the_dual_ratio_rule_and_ties_it_to_the_two_keys():
    """The reason storage is per-orientation in the first place.

    A preservation guard with teeth: the rule already exists, and rewriting Step 2 around a
    stored look is exactly the edit that could drop it — so it is pinned together with the two
    keys it justifies, which is the half that does not exist yet.
    """
    body = _flat(AVATAR)
    assert "two decisions, not one" in body.lower()
    assert "identity.heygen_look_landscape" in body and "identity.heygen_look_portrait" in body


@_avatar_stubbed
def test_the_avatar_body_confirms_an_unconfirmed_look_before_spending():
    """`video-avatar` can be entered directly, without the router's ask. A stored look must not
    become a silent default by the side door."""
    body = _flat(AVATAR).lower()
    assert re.search(r"confirm[^.]{0,160}approved look|approved look[^.]{0,160}confirm", body), (
        "video-avatar never says to confirm the APPROVED look when it was entered without the "
        "router's ask, which is the side door a stored default walks through. A bare "
        "confirm/look pair is not enough — the body already says to confirm a look's pixels "
        "against `list_avatar_looks`, which is a geometry check, not an operator's yes."
    )


# --- identity-kit: the one place the pick is recorded ---------------------------------------------


def test_identity_kit_has_a_step_where_the_operator_picks_the_look():
    body = _flat(IDENTITY_KIT)
    assert "heygen_look_landscape" in body and "heygen_look_portrait" in body, (
        "identity-kit cannot record an approved look, so the ask has nowhere to land and the "
        "operator is re-asked from scratch every run"
    )
    assert "list_avatar_looks" in body


def test_identity_kit_writes_the_look_through_the_brandkit_cli():
    body = _flat(IDENTITY_KIT)
    assert "--set identity.heygen_look_landscape" in body or re.search(
        r"--set identity\.<[^>]*heygen_look_landscape", body
    ), "identity-kit does not show the CLI write for the approved look"


def test_identity_kit_does_not_pick_the_look_on_the_operators_behalf():
    body = _flat(IDENTITY_KIT).lower()
    assert "do not pre-select" in body or "never pre-select" in body, (
        "identity-kit does not forbid choosing the look for the operator — the one thing V-3 "
        "says must never be guessed"
    )


def test_identity_kit_audits_both_look_keys():
    """An audit that reported one orientation would make the other invisible, which is how a
    single-key assumption creeps back."""
    body = _flat(IDENTITY_KIT).lower()
    assert "per orientation" in body or "per-orientation" in body
