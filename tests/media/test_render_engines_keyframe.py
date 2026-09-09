"""C10 — the keyframe capability on the render-engine registry.

PRD test ids C10-T4 (no keyframe engine can leak into the presenter lane), C10-T6 (the audio
fields are declared, not inferred) and C10-T7 (the audit can propose a keyframe candidate and
still writes nothing). Plus the guard that makes a typo'd `requires` key fail loudly instead of
refusing every engine with a message naming a field that does not exist.

These run over the COMMITTED registry, not a fixture, wherever the property is about what ships.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_core.render_engines import (
    EngineError,
    EngineSpec,
    EngineUnavailable,
    audit_registry,
    load_registry,
    resolve_engine,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY = REPO_ROOT / "gtm_core" / "render_engines.toml"


# ── C10-T4: the presenter ban survives the new capability ─────────────────────────────────────


def test_no_engine_that_accepts_an_end_frame_can_reach_the_presenter_role():
    """A keyframe engine interpolates between stills; it holds no likeness and syncs no mouth.

    Asserted over the whole registry rather than over the one engine C10 happened to flag, so a
    later engine added with `accepts_end_frame = true` cannot quietly become a talking head.
    """
    data = load_registry(REGISTRY)
    keyframe_engines = {
        name for name, table in data["engines"].items() if table.get("accepts_end_frame")
    }
    assert keyframe_engines, "the registry declares no keyframe engine — this test proves nothing"

    for disclosed in (True, False):
        try:
            spec = resolve_engine("presenter", disclosed=disclosed, registry_path=REGISTRY)
        except EngineUnavailable:
            continue  # refused outright is the strongest possible pass
        assert spec.name not in keyframe_engines, (
            f"presenter resolved {spec.name!r}, which is a keyframe engine — the August 2026 "
            "face-drift failure just became representable again"
        )


def test_the_presenter_role_still_demands_native_lip_sync_and_a_faithful_identity():
    """The requirements C10 must not have loosened while adding a key beside them."""
    requires = load_registry(REGISTRY)["roles"]["presenter"]["requires"]
    assert requires["lip_sync"] == "native", "presenter stopped requiring native lip sync"
    assert requires["identity_faithful"] is True, "presenter stopped requiring a real identity"


# ── the keyframe role itself ──────────────────────────────────────────────────────────────────


def test_the_keyframe_role_resolves_and_requires_the_capability_it_depends_on():
    """The role exists to make the requirement CHECKED, which is only true if it states it."""
    requires = load_registry(REGISTRY)["roles"]["keyframe_broll"]["requires"]
    assert requires["accepts_end_frame"] is True, (
        "keyframe_broll does not require accepts_end_frame — rebinding b-roll to a start-frame-"
        "only model would then silently drop every end frame instead of failing at resolve time"
    )
    assert resolve_engine("keyframe_broll", registry_path=REGISTRY).accepts_end_frame is True


def test_rebinding_the_keyframe_role_to_a_start_frame_only_engine_fails_at_resolve_time(tmp_path):
    """The whole point of the role, exercised: a capability regression is caught before spend."""
    src = REGISTRY.read_text(encoding="utf-8").replace(
        "accepts_end_frame = true           # live catalog 2026-09-07, see notes",
        "accepts_end_frame = false",
    )
    broken = tmp_path / "render_engines.toml"
    broken.write_text(src, encoding="utf-8")

    with pytest.raises(EngineUnavailable, match="accepts_end_frame"):
        resolve_engine("keyframe_broll", registry_path=broken)
    # Positive control: plain b-roll places no such requirement and still resolves.
    assert resolve_engine("broll", registry_path=broken).name


# ── C10-T6: audio is declared, never inherited ────────────────────────────────────────────────


def test_every_engine_declaring_an_audio_default_also_names_the_toggle_that_turns_it_off():
    """`audio_default = true` with no toggle is a clip that sings and cannot be silenced."""
    for name, table in load_registry(REGISTRY)["engines"].items():
        if table.get("audio_default"):
            assert str(table.get("audio_toggle", "")).strip(), (
                f"engine {name!r} scores by default and names no way to stop it"
            )


# ── the unknown-requires-key guard ────────────────────────────────────────────────────────────


def test_a_typo_in_a_requires_key_is_refused_at_resolve_rather_than_refusing_every_engine(tmp_path):
    """Before the guard, `getattr(spec, key, None)` compared against None and refused everything.

    That is a fail-CLOSED outcome, so it was never a security hole — it was a MISLEADING one: the
    message named a capability the engine "lacked" that no engine has ever had.
    """
    src = REGISTRY.read_text(encoding="utf-8").replace(
        'requires = { output = "video", accepts_end_frame = true }',
        'requires = { output = "video", acepts_end_frame = true }',
    )
    typo = tmp_path / "render_engines.toml"
    typo.write_text(src, encoding="utf-8")

    with pytest.raises(EngineError, match="unknown engine capability key"):
        resolve_engine("keyframe_broll", registry_path=typo)


def test_every_requires_key_in_the_committed_registry_is_a_real_engine_field():
    """Positive control for the guard, run over what actually ships."""
    known = set(EngineSpec.__dataclass_fields__)
    for role, table in load_registry(REGISTRY)["roles"].items():
        unknown = set(table.get("requires", {}) or {}) - known
        assert not unknown, f"role {role!r} requires non-existent field(s) {sorted(unknown)}"


# ── C10-T7: the audit proposes, and writes nothing ────────────────────────────────────────────


def _catalog(*items: dict) -> dict:
    return {"items": list(items)}


def test_the_audit_reads_accepts_end_frame_off_the_catalogs_declared_media_roles():
    """Both roles are needed: a start frame alone animates forward, it does not arrive anywhere."""
    from gtm_core.render_engines_audit import _catalog_capabilities

    both = {"output_type": "video", "medias": [{"roles": ["start_image", "end_image"]}]}
    start_only = {"output_type": "video", "medias": [{"roles": ["start_image"]}]}
    assert _catalog_capabilities(both)["accepts_end_frame"] is True
    assert _catalog_capabilities(start_only)["accepts_end_frame"] is False, (
        "a start-frame-only model was read as keyframe-capable"
    )


def test_the_audit_never_writes_to_the_registry(tmp_path):
    """Read-only is the audit's whole contract; a proposal that edits is not a proposal."""
    working = tmp_path / "render_engines.toml"
    working.write_text(REGISTRY.read_text(encoding="utf-8"), encoding="utf-8")
    before = working.read_bytes()

    audit_registry(
        _catalog(
            {"id": "wan2_7", "output_type": "video", "medias": [{"roles": ["start_image"]}]},
            {"id": "soul_2", "output_type": "image", "medias": []},
        ),
        registry_path=working,
    )
    assert working.read_bytes() == before, "the audit modified the registry it was reading"


def test_an_unreachable_probe_reports_unknown_rather_than_an_all_clear():
    """The one thing an audit must never do is read a failed probe as "nothing changed"."""
    findings = audit_registry(None, registry_path=REGISTRY)
    assert [f for f in findings if f.kind == "unknown"], "a failed probe read as clean"
