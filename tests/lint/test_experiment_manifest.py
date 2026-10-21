"""Lint: every overlay committed to the tree is one that could actually be admitted.

An overlay is refused at *admission*, which happens at 9am on the morning somebody wanted to
run it. That is the worst possible time to discover a typo'd slug or a file that may not be
overridden, so the same rules run here, at commit time, against everything on disk.

Deliberately NOT checked here: expiry. An expired overlay is a correct, committed artifact —
the record of an experiment that ended — and failing the build for one would train people to
delete their own history, or to keep pushing the date out to keep CI quiet. Expiry is a
run-time refusal only.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

from gtm_core.experiments import (
    MANIFEST_NAME,
    OverlayError,
    admit,
    list_overlays,
    parse_overlay_arg,
)

REPO = Path(__file__).resolve().parents[2]
PROFILES = REPO / "profiles"

#: Far enough in the past that no committed overlay is treated as expired by this lint.
#: Admission still enforces expiry at run time — see the module docstring.
_EPOCH = _dt.date(1970, 1, 1)


def _all_overlays() -> list[tuple[str, str]]:
    if not PROFILES.is_dir():  # pragma: no cover - OSS carve ships no profiles/
        return []
    return [
        (p.name, slug)
        for p in sorted(PROFILES.iterdir())
        if p.is_dir()
        for slug in list_overlays(p.name, PROFILES)
    ]


@pytest.mark.parametrize("profile,slug", _all_overlays())
def test_every_committed_overlay_would_admit(profile: str, slug: str, monkeypatch) -> None:
    """Manifest present and parseable, slug matches its directory, every file overridable.

    The kill switch is forced ON for this check only: whether overlays are *enabled on this
    box* is an operator decision, and it says nothing about whether the artifact on disk is
    well-formed. Those are different questions and conflating them would make the lint
    pass or fail based on a shell variable.
    """
    monkeypatch.setenv("GTM_EXPERIMENT_OVERLAY_ENABLED", "1")
    try:
        exp = admit(profile, slug, PROFILES, today=_EPOCH)
    except OverlayError as exc:
        pytest.fail(
            f"profiles/{profile}/experiments/{slug}/ could not be admitted:\n  {exc}\n"
            f"Fix it now — the alternative is discovering it at the moment someone tries to "
            f"run the experiment."
        )
    assert exp.files, f"{profile}/{slug} overrides nothing"


@pytest.mark.parametrize("profile,slug", _all_overlays())
def test_every_committed_overlay_carries_a_manifest(profile: str, slug: str) -> None:
    """Asserted separately from admission so the failure names the missing file directly."""
    manifest = PROFILES / profile / "experiments" / slug / MANIFEST_NAME
    assert manifest.is_file(), (
        f"{manifest} is missing. An experiment with no owner and no expiry is not an "
        f"experiment; it is an un-decided change to the live ICP."
    )


def test_a_comma_separated_overlay_is_rejected() -> None:
    """One overlay per run is a property, not a convention.

    Two overlays in one run is two budget checks collapsed into one, two attribution paths
    for a single reply, and two arms one person could land in.
    """
    assert parse_overlay_arg("one") == "one"
    for bad in ("a,b", "a b", "a, b"):
        with pytest.raises(OverlayError, match="ONE slug"):
            parse_overlay_arg(bad)


def test_an_overlay_slug_cannot_escape_the_profile() -> None:
    """Directory traversal here is the highest-risk tenant error (CLAUDE.md)."""
    for bad in ("../../etc", "..", "a/b", "a\\b", "$HOME", ""):
        with pytest.raises(ValueError):
            resolved = parse_overlay_arg(bad)
            # An empty string legitimately normalises to None; anything else must have raised.
            assert resolved is None
            raise ValueError("empty overlay normalises to None")
