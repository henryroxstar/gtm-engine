"""The engine registry can be re-verified instead of trusted — and the audit never re-pins.

P2's defect is structural, not a stale value: **a capability ban outlives the probe that justified
it.** ``render_engines.toml`` says no video model on this provider can hold a real likeness or
lip-sync from audio. That was true when it was probed. Nothing in the file said *when*, and nothing
could re-ask.

So: every engine carries its own ``verified_on`` (C11a), and ``--audit`` diffs the registry against
a live model list (C11b). Two properties matter more than the diff itself:

* **The audit reports; it never edits.** A tool that re-pins models from a catalog would replace a
  human's capability judgement with a tag match — which is precisely how the August 2026 failures
  happened in the first place.
* **A fail-open probe reports "unknown", never "unchanged".** A silent all-clear from a probe that
  never reached the provider is the worst possible output, because it looks like verification.

The last test is the critical regression, and it encodes the whole of P2's reasoning.

Design: the 2026-08-29 video-router hardening note, item C11.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from gtm_core import render_engines

REPO = Path(__file__).resolve().parents[2]
REGISTRY = REPO / "gtm_core" / "render_engines.toml"


# --- fixtures -----------------------------------------------------------------------------------
#
# Model entries are shaped like a real `models_explore` item: an `id`, an `output_type`, and a
# `tags` list. Fabricated ids throughout — the point is the matching logic, not the catalog.


def _model(model_id: str, output_type: str = "video", **kw) -> dict:
    return {"id": model_id, "output_type": output_type, "tags": kw.pop("tags", []), **kw}


@pytest.fixture()
def registry_models() -> list[dict]:
    """A live catalog that still carries everything the shipped registry pins."""
    data = tomllib.loads(REGISTRY.read_text(encoding="utf-8"))
    return [
        _model(e["model"], "video" if e.get("output") == "video" else "image")
        for e in data["engines"].values()
    ]


def _write_registry(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "render_engines.toml"
    path.write_text(body, encoding="utf-8")
    return path


# --- C11a ---------------------------------------------------------------------------------------


def test_every_engine_declares_a_verified_on_date():
    """A capability claim is only as good as its probe date, and one `[meta] updated` for the whole
    file cannot say which engine was actually looked at."""
    data = tomllib.loads(REGISTRY.read_text(encoding="utf-8"))
    undated = [name for name, table in data["engines"].items() if not table.get("verified_on")]
    assert not undated, (
        f"engines {undated} carry no `verified_on`. Their capability claims — including the bans "
        "— have no probe date, so nothing can tell a fresh finding from a two-year-old one."
    )
    for name, table in data["engines"].items():
        spec = render_engines._spec(name, table)
        assert spec.verified_on, f"{name}: verified_on did not survive into the EngineSpec"


# --- C11b ---------------------------------------------------------------------------------------


def test_audit_reports_a_pinned_model_that_no_longer_exists(registry_models):
    """The plain drift case: the provider retires a model and the registry keeps pointing at it."""
    survivors = [m for m in registry_models if m["id"] != "wan2_7"]
    findings = render_engines.audit_registry(survivors)
    missing = [f for f in findings if f.kind == "pinned_model_missing"]
    assert [f.subject for f in missing] == ["higgsfield_i2v"], (
        f"a retired pinned model was not reported; findings were {findings}"
    )
    assert "wan2_7" in missing[0].detail

    # Positive control: with the full catalog, nothing is missing.
    assert not [
        f
        for f in render_engines.audit_registry(registry_models)
        if f.kind == "pinned_model_missing"
    ]


def test_audit_reports_a_new_model_satisfying_an_unservable_role(tmp_path):
    """The seedance case in miniature: a role marked unservable while a live model's advertised
    capabilities would satisfy it is REPORTED — a question for a human, never an auto-binding."""
    registry = _write_registry(
        tmp_path,
        """
[meta]
version = 1

[engines.only_stills]
provider = "fabricated"
model = "still_maker"
output = "image"
identity_faithful = true
lip_sync = "none"
verified_on = "2026-01-01"

[roles.motion]
engine = ""
requires = { output = "video" }
""",
    )
    live = [_model("still_maker", "image"), _model("newcomer_v1", "video")]
    findings = render_engines.audit_registry(live, provider="fabricated", registry_path=registry)
    candidates = [f for f in findings if f.kind == "candidate_for_unservable_role"]
    assert [f.subject for f in candidates] == ["motion"], f"findings were {findings}"
    assert "newcomer_v1" in candidates[0].detail
    assert "still_maker" not in candidates[0].detail, (
        "an image model was offered for a role requiring video output"
    )


def test_audit_is_read_only(registry_models):
    """The audit informs a human; it never re-pins. Checked by bytes, not by intent."""
    before = REGISTRY.read_bytes()
    render_engines.audit_registry(registry_models)
    render_engines.audit_registry(None)
    assert REGISTRY.read_bytes() == before, "the audit modified the registry"


def test_audit_reports_unknown_when_the_provider_is_unreachable():
    """Fail-open as *unknown*. A silent 'all clear' from a failed probe is the worst outcome,
    because it is indistinguishable from a real verification."""
    findings = render_engines.audit_registry(None)
    kinds = {f.kind for f in findings}
    assert kinds == {"unknown"}, (
        f"an unreachable provider produced {kinds or 'no findings at all'} — an empty finding list "
        "reads as 'nothing has changed', which is exactly the claim the audit cannot make here"
    )
    assert "unknown" in findings[0].detail.lower()


def test_the_presenter_ban_survives_an_audit_that_finds_identity_tagged_models(tmp_path):
    """**The critical regression.**

    The live catalog really does carry video models tagged ``identity`` — reference-driven
    consistency across shots. That is not the same capability as generating a mouth from an audio
    track, and the entire ``presenter`` ban rests on the difference (verified 2026-08-19: with
    audio_references passed, the mouth was closed in 9 of 10 sampled frames).

    The structural guarantee is that a model CATALOG cannot establish ``lip_sync`` at all — no
    provider advertises it as a capability flag — so a role requiring native lip sync can never
    acquire a candidate from tag matching. The ban is not defended by a denylist that a new model
    slips past; it is defended by the audit being unable to speak to the property.
    """
    registry = _write_registry(
        tmp_path,
        """
[meta]
version = 1

[engines.none_yet]
provider = "fabricated"
model = "placeholder"
output = "video"
identity_faithful = false
lip_sync = "none"
verified_on = "2026-01-01"

[roles.presenter]
engine = ""
requires = { identity_faithful = true, lip_sync = "native", output = "video" }
""",
    )
    tempting = [
        _model("placeholder"),
        _model("shiny_identity_v2", tags=["identity", "consistent", "reference", "audio"]),
        _model("shiny_identity_mini", tags=["identity", "audio-reference", "character", "sync"]),
    ]
    findings = render_engines.audit_registry(
        tempting, provider="fabricated", registry_path=registry
    )
    for f in findings:
        assert f.kind != "candidate_for_unservable_role" or f.subject != "presenter", (
            "the audit proposed a candidate for the presenter role from catalog tags. An "
            "`identity` tag is reference-driven consistency, not audio-driven lip sync, and the "
            f"ban rests on that distinction: {f.detail}"
        )
    explained = [f for f in findings if f.kind == "unestablishable_requirement"]
    assert any(f.subject == "presenter" and "lip_sync" in f.detail for f in explained), (
        "the audit silently found nothing for `presenter`. Silence here is wrong: the reason no "
        "candidate exists is that the catalog cannot speak to lip_sync at all, and a reader has "
        "to be told that rather than left to infer the models were checked and rejected."
    )
