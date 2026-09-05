"""The approved avatar LOOK is a proposal the router asks about — never a silent default.

On 2026-08-29 a verification render used the pinned digital twin while 40+ photo-avatar looks —
six of them native 1920x1080 landscape — sat unexamined in the same avatar group at identical
cost. The operator saw the result only after it was paid for.

**The failure was "nobody was asked", not "the look was chosen badly."** That is the whole design
constraint here, and it is what these tests pin:

* Storage exists so the ask is CHEAP (one line naming a look), not so the ask can be skipped. A
  stored look that applied itself would reproduce the original failure exactly, so every
  proposal — stored or not — carries an ask.
* A missing look is a DECISION, not a gate. The same property as the C11c staleness caveat: a
  lane is never blocked for lacking an aesthetic preference nobody has recorded yet.
* Landscape and portrait are two separate decisions. One key cannot serve both a 16:9 master and
  a 9:16 cut, and a cross-orientation fallback is what produced the padded render of 2026-08-27.

Design: ``PENDING.md`` V-3 (operator decisions (a)/(b)/(c), 2026-08-29).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core import video_preflight as vp


def _kit(**identity: str) -> dict:
    return {"identity": dict(identity)} if identity else {}


@pytest.fixture
def profiles(tmp_path: Path) -> Path:
    """A profile with the creator pack active and every HeyGen handle EXCEPT a look."""
    root = tmp_path / "profiles"
    prof = root / "acme"
    (prof / "knowledge").mkdir(parents=True)
    (prof / "packs.toml").write_text('active = ["creator"]\n', encoding="utf-8")
    (prof / "knowledge" / "BRAND.toml").write_text(
        "\n".join(
            [
                "[disclosure]",
                'line = "Made with AI."',
                "[identity]",
                'heygen_avatar_id = "avatar-0001"',
                'heygen_voice_grade = "professional"',
            ]
        ),
        encoding="utf-8",
    )
    return root


def _write_looks(profiles: Path, *, landscape: str = "", portrait: str = "") -> None:
    kit = profiles / "acme" / "knowledge" / "BRAND.toml"
    kit.write_text(
        kit.read_text(encoding="utf-8")
        + f'\nheygen_look_landscape = "{landscape}"\nheygen_look_portrait = "{portrait}"\n',
        encoding="utf-8",
    )


# --- (a) storage is per-orientation, and the two never cross -------------------------------------


def test_a_landscape_target_never_resolves_the_portrait_key():
    """The 2026-08-27 defect in its purest form.

    A 608x1080 portrait look rendered for a 16:9 master fills 608 of 1920 columns and pads the
    rest. If a landscape lookup could fall back to the portrait key, that padding would come back
    the moment only one orientation had been approved.
    """
    kit = _kit(heygen_look_portrait="look-portrait-1")
    assert vp.resolve_look(kit, "landscape") == ""
    assert vp.resolve_look(kit, "portrait") == "look-portrait-1"


def test_a_portrait_target_never_resolves_the_landscape_key():
    kit = _kit(heygen_look_landscape="look-landscape-1")
    assert vp.resolve_look(kit, "portrait") == ""
    assert vp.resolve_look(kit, "landscape") == "look-landscape-1"


def test_each_orientation_reads_its_own_key():
    assert vp.LOOK_KEY_BY_ORIENTATION["landscape"] == "identity.heygen_look_landscape"
    assert vp.LOOK_KEY_BY_ORIENTATION["portrait"] == "identity.heygen_look_portrait"
    assert len(set(vp.LOOK_KEY_BY_ORIENTATION.values())) == 2


def test_an_unknown_orientation_is_refused_rather_than_guessed():
    """ "square", "4:5", "" — none of these is one of the two decisions. Guessing which key they
    mean is how a cross-orientation fallback gets reintroduced by accident."""
    with pytest.raises(ValueError):
        vp.resolve_look(_kit(heygen_look_landscape="x"), "square")


def test_an_empty_stored_look_counts_as_unset_not_as_a_value():
    """A product kit's present-but-empty value is an override to empty (see gtm_core.brandkit),
    so an empty look is a real gap and must ask with the candidate list, not confirm an empty id."""
    kit = _kit(heygen_look_landscape="")
    assert vp.resolve_look(kit, "landscape") == ""
    proposal = next(p for p in vp.look_proposals(kit) if p.orientation == "landscape")
    assert not proposal.stored


# --- (b) the stored value is a PROPOSAL, and the ask survives it ---------------------------------


def test_the_ask_survives_even_when_both_keys_are_set(profiles: Path):
    """The load-bearing test. Storage makes the ask CHEAP, never optional.

    With both looks recorded there is still an ask per orientation — a one-line confirm naming
    the look — because the 2026-08-29 failure was that nobody was asked, and a stored default
    that applies itself is that failure with a config file in front of it.
    """
    _write_looks(profiles, landscape="look-l-1", portrait="look-p-1")
    proposals = vp.preflight("acme", profiles_root=profiles).constraints.look_proposals

    assert len(proposals) == 2
    for proposal in proposals:
        assert proposal.stored, "both looks were written to the kit"
        assert proposal.ask.strip(), (
            f"the {proposal.orientation} look is stored and carries NO ask — a stored look that "
            "applies itself is exactly the 2026-08-29 failure"
        )
        assert proposal.look_id in proposal.ask, (
            "a confirm-shaped ask has to name the look it is proposing, or the operator is "
            "confirming an id they cannot see"
        )


def test_an_unset_look_asks_with_the_candidate_list_instead_of_a_confirm(profiles: Path):
    """Empty keys => ask with the candidates for the TARGET orientation. Different ask, same duty."""
    proposals = vp.preflight("acme", profiles_root=profiles).constraints.look_proposals
    assert proposals, "the preflight reports no look proposals at all"
    for proposal in proposals:
        assert not proposal.stored
        assert proposal.ask.strip(), "an unrecorded look must still produce an ask"
        assert "list_avatar_looks" in proposal.ask, (
            "the ask for an unset look must name the free call that produces the candidate list"
        )


def test_the_preflight_never_pre_selects_a_look_for_the_operator(profiles: Path):
    """(c) — the pick is the operator's aesthetic call about their own face.

    A preflight that proposed an id nobody recorded would be pre-selecting under another name.
    """
    proposals = vp.preflight("acme", profiles_root=profiles).constraints.look_proposals
    assert all(p.look_id == "" for p in proposals), (
        "the preflight invented a look id for a profile whose kit records none"
    )


def test_resolving_a_look_costs_no_provider_call(profiles: Path, monkeypatch):
    """Zero-spend, zero-write, zero-network — the whole premise of running this before creative
    work. Previews are deliberately not fetched (V-3 decision (c))."""
    import urllib.request

    def _boom(*_a, **_k):  # pragma: no cover - the assertion is that it never runs
        raise AssertionError("the look decision made a network call")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    _write_looks(profiles, landscape="look-l-1")
    assert vp.preflight("acme", profiles_root=profiles).constraints.look_proposals


# --- the decision/gate boundary ------------------------------------------------------------------


def test_a_lane_with_no_stored_look_is_ready_not_blocked(profiles: Path):
    """A DECISION, not a gate — the same property as the C11c staleness caveat.

    `presenter-video` has every handle it needs; no look has been approved. Blocking the lane
    would refuse work to punish a missing preference, and would push the ask behind the routing
    decision instead of in front of it.

    Both halves are asserted together on purpose. "Ready" alone would pass on a preflight that
    had never heard of looks at all, which proves nothing about the boundary — the property is
    that the preflight KNOWS the look is unrecorded, says so, and still marks the lane runnable.
    """
    pf = vp.preflight("acme", profiles_root=profiles)
    presenter = next(lane for lane in pf.lanes if lane.variant == "presenter-video")

    unrecorded = [p.orientation for p in pf.constraints.look_proposals if not p.stored]
    assert set(unrecorded) == {"landscape", "portrait"}, (
        "the preflight did not report the missing looks, so this test would pass on a preflight "
        "that never resolved them"
    )
    assert presenter.ready, f"presenter-video is blocked: {presenter.blocked_reason}"
    assert presenter.blocked_reason is None


def test_no_look_key_is_a_lane_precondition():
    """Structural: if a look key ever reaches LANE_REQUIREMENTS.handles it becomes a gate, and
    the decision above stops holding no matter what the readiness test says."""
    look_keys = set(vp.LOOK_KEY_BY_ORIENTATION.values())
    for variant, req in vp.LANE_REQUIREMENTS.items():
        assert not (look_keys & set(req.handles)), (
            f"{variant} lists an approved-look key as a required handle — that makes an aesthetic "
            "preference a hard blocker"
        )


def test_the_cli_reports_the_look_decision_in_its_text_output(profiles: Path, capsys):
    """It has to reach the router's Step 0.5 block, which reads this output."""
    _write_looks(profiles, landscape="look-l-1")
    vp.main(["--profile", "acme", "--profiles-root", str(profiles)])
    out = capsys.readouterr().out

    assert "look-l-1" in out
    assert "landscape" in out.lower() and "portrait" in out.lower()


def test_the_json_output_carries_the_look_proposals(profiles: Path, capsys):
    _write_looks(profiles, portrait="look-p-1")
    vp.main(["--profile", "acme", "--profiles-root", str(profiles), "--json"])
    payload = json.loads(capsys.readouterr().out)

    looks = {p["orientation"]: p for p in payload["constraints"]["look_proposals"]}
    assert looks["portrait"]["look_id"] == "look-p-1"
    assert looks["portrait"]["stored"] is True
    assert looks["landscape"]["look_id"] == ""
    assert looks["landscape"]["stored"] is False
    assert all(p["ask"].strip() for p in looks.values())
