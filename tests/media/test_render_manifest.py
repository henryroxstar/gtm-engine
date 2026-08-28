"""gtm_core.render_manifest — the writer §8 assumes exists (F5). Refuses a generated/restyled
asset with empty identity_used, a voice asset with no lip_sync_source, and a manifest naming a
path that does not exist on disk.
"""

from __future__ import annotations

import json

import pytest

from gtm_core import render_manifest as rm


@pytest.fixture(autouse=True)
def _approved_storyboard(tmp_path):
    """Every synthetic render must name a storyboard the operator approved at ⟦GATE:plan⟧.
    Tests exercising that rule itself override ``storyboard_path`` explicitly."""
    (tmp_path / "storyboard.json").write_text(json.dumps({"approved": True, "shots": []}))


def _mk(**kw):
    defaults = {
        "profile": "acme",
        "slug": "2026-08-15-test",
        "ratio": "9x16",
        "asset_path": "asset.mp4",
        # Phase 18: every synthetic asset must record its verbatim prompt.
        "prompt": "Static camera, slow push-in. He looks up. No text.",
        # The approved storyboard that authorised the spend.
        "storyboard_path": "storyboard.json",
        # W0.3: a synthetic render must say how it was costed and name the ledger row that
        # metered it — an unrecordable spend is an unshippable one.
        "cost_source": "preflight",
        "cost_credits": 10.0,
        "cost_ledger_ts": "2026-08-20T00:00:00Z",
    }
    defaults.update(kw)
    return rm.RenderManifest(**defaults)


def test_a_synthetic_asset_with_empty_identity_used_is_refused(tmp_path):
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk(identity_used=())
    with pytest.raises(rm.ManifestError, match="identity_used is empty"):
        rm.write_render_manifest(m, out_dir=tmp_path, synthetic=True, repo_root=tmp_path)


def test_a_non_synthetic_asset_with_empty_identity_used_is_accepted(tmp_path):
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk(identity_used=())
    out = rm.write_render_manifest(m, out_dir=tmp_path, synthetic=False, repo_root=tmp_path)
    assert out.exists()


def test_a_generated_asset_with_generated_in_identity_used_is_accepted(tmp_path):
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk(identity_used=("generated",))
    out = rm.write_render_manifest(m, out_dir=tmp_path, synthetic=True, repo_root=tmp_path)
    assert out.exists()


def test_an_unknown_identity_value_is_refused(tmp_path):
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk(identity_used=("bogus",))
    with pytest.raises(rm.ManifestError, match="unknown identity_used"):
        rm.write_render_manifest(m, out_dir=tmp_path, synthetic=False, repo_root=tmp_path)


def test_voice_identity_with_no_lip_sync_source_is_refused(tmp_path):
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk(identity_used=("voice",), lip_sync_source=None)
    with pytest.raises(rm.ManifestError, match="lip_sync_source is unset"):
        rm.write_render_manifest(m, out_dir=tmp_path, synthetic=True, repo_root=tmp_path)


@pytest.mark.parametrize("source", ["audio_references", "text_prompt_only"])
def test_voice_identity_with_a_valid_lip_sync_source_is_accepted(tmp_path, source):
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk(identity_used=("voice",), lip_sync_source=source)
    out = rm.write_render_manifest(m, out_dir=tmp_path, synthetic=True, repo_root=tmp_path)
    assert out.exists()


def test_an_invalid_lip_sync_source_string_is_refused(tmp_path):
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk(identity_used=("voice",), lip_sync_source="made_up")
    with pytest.raises(rm.ManifestError, match="lip_sync_source"):
        rm.write_render_manifest(m, out_dir=tmp_path, synthetic=True, repo_root=tmp_path)


def test_write_refuses_a_manifest_naming_a_missing_path(tmp_path):
    """Reproduces the live defect: render-9x16.json named …-final.mp4, which was never written."""
    m = _mk(asset_path="never-written-final.mp4")
    with pytest.raises(rm.ManifestError, match="does not exist"):
        rm.write_render_manifest(m, out_dir=tmp_path, synthetic=False, repo_root=tmp_path)


def test_a_written_manifest_round_trips_through_load_render(tmp_path):
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk(identity_used=("generated",))
    out = rm.write_render_manifest(m, out_dir=tmp_path, synthetic=True, repo_root=tmp_path)
    loaded = rm.load_render(out)
    assert loaded.identity_used == ("generated",)
    assert loaded.asset_path == "asset.mp4"


def test_validate_render_file_re_runs_the_same_rules(tmp_path):
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk(identity_used=("generated",))
    out = rm.write_render_manifest(m, out_dir=tmp_path, synthetic=True, repo_root=tmp_path)
    rm.validate_render_file(out, synthetic=True, repo_root=tmp_path)  # must not raise


def test_writer_and_validator_agree_on_what_a_relative_asset_path_means(tmp_path, monkeypatch):
    """Round-trip with BOTH sides on their defaults — the asymmetry this pins was real.

    ``write_render_manifest`` defaults repo_root to the CWD, so a manifest written by the documented
    path carries a repo-relative ``asset_path``. ``validate_render_file`` used to default to the
    manifest's OWN PARENT directory, which prepended that parent to an already-repo-relative path
    and made the ``validate`` CLI — the command ``video-render`` Step 5 tells the caller to run
    before reporting — fail on every manifest the writer produced. Both existing validate tests
    passed repo_root explicitly, so neither could see it. Fixed 2026-08-19.
    """
    nested = tmp_path / "content" / "acme" / "video" / "slug"
    nested.mkdir(parents=True)
    (nested / "asset.mp4").write_bytes(b"x")
    monkeypatch.chdir(tmp_path)

    m = _mk(
        identity_used=("generated",),
        asset_path="content/acme/video/slug/asset.mp4",
        storyboard_path="content/acme/video/slug/storyboard.json",
    )
    (nested / "storyboard.json").write_text('{"approved": true, "entries": [{"n": 1}]}')

    # Writer on its default (CWD).
    out = rm.write_render_manifest(m, out_dir=nested, synthetic=True)
    # Validator on ITS default — must resolve the same relative path the writer just accepted.
    rm.validate_render_file(out, synthetic=True)  # must not raise


def test_finish_manifest_with_executed_false_does_not_require_the_asset_to_exist(tmp_path):
    """ffmpeg-absent path: plan() succeeded, execute() didn't run — no partial mp4 to point at."""
    fm = rm.FinishManifest(
        profile="acme",
        slug="s",
        ratio="9x16",
        asset_path="would-be-final.mp4",
        stages=("normalize", "captions", "encode"),
        executed=False,
        caption_route="local",
    )
    out = rm.write_finish_manifest(fm, out_dir=tmp_path, repo_root=tmp_path)
    json_loaded = json.loads(out.read_text())
    assert json_loaded["executed"] is False


def test_finish_manifest_with_executed_true_requires_the_asset_to_exist(tmp_path):
    fm = rm.FinishManifest(
        profile="acme",
        slug="s",
        ratio="9x16",
        asset_path="missing-final.mp4",
        stages=("normalize",),
        executed=True,
        caption_route="none",
    )
    with pytest.raises(rm.ManifestError, match="does not exist"):
        rm.write_finish_manifest(fm, out_dir=tmp_path, repo_root=tmp_path)


# --- prompt + seed recording (Phase 18: engineered-prompt layer, R2) ----------------------


def test_a_synthetic_asset_with_an_empty_prompt_is_refused(tmp_path):
    """The gap the 2026-08-17 assessment found live: 0/3 shipped manifests carried the prompt,
    so the exact bytes sent to the provider were unrecoverable."""
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk(identity_used=("generated",), prompt="")
    with pytest.raises(rm.ManifestError, match="prompt is empty"):
        rm.write_render_manifest(m, out_dir=tmp_path, synthetic=True, repo_root=tmp_path)


def test_a_synthetic_asset_with_a_whitespace_prompt_is_refused(tmp_path):
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk(identity_used=("generated",), prompt="   ")
    with pytest.raises(rm.ManifestError, match="prompt is empty"):
        rm.write_render_manifest(m, out_dir=tmp_path, synthetic=True, repo_root=tmp_path)


def test_a_non_synthetic_asset_needs_no_prompt(tmp_path):
    """video-clip trimming the operator's own footage sent no prompt — nothing to record."""
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk(prompt="")
    out = rm.write_render_manifest(m, out_dir=tmp_path, synthetic=False, repo_root=tmp_path)
    assert out.exists()


def test_prompt_and_seed_round_trip_through_load_render(tmp_path):
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk(identity_used=("generated",), seed=424242)
    out = rm.write_render_manifest(m, out_dir=tmp_path, synthetic=True, repo_root=tmp_path)
    loaded = rm.load_render(out)
    assert loaded.prompt == "Static camera, slow push-in. He looks up. No text."
    assert loaded.seed == 424242


def test_a_null_seed_means_not_surfaced_and_is_accepted(tmp_path):
    """The connector path exposes no seed param today — null is honest, not a violation."""
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk(identity_used=("generated",), seed=None)
    out = rm.write_render_manifest(m, out_dir=tmp_path, synthetic=True, repo_root=tmp_path)
    assert rm.load_render(out).seed is None


def test_validate_render_file_enforces_the_prompt_rule_on_existing_manifests(tmp_path):
    """A hand-authored manifest (the only kind shipped to date) must fail --synthetic
    validation when it omits the prompt — this is the enforcement lever for prose-written
    manifests."""
    handwritten = tmp_path / "render-9x16.json"
    (tmp_path / "asset.mp4").write_bytes(b"x")
    handwritten.write_text(
        json.dumps(
            {
                "profile": "acme",
                "slug": "s",
                "ratio": "9x16",
                "asset_path": "asset.mp4",
                "identity_used": ["generated"],
            }
        )
    )
    with pytest.raises(rm.ManifestError, match="prompt is empty"):
        rm.validate_render_file(handwritten, synthetic=True, repo_root=tmp_path)


# --- draft-pool fields (Phase 16: K-variant reroll/keeper-rate budget) --------------------


def test_draft_pool_fields_omitted_together_is_accepted(tmp_path):
    """An N-K batch asset (never individually scored) omits all three fields — not a violation."""
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk()
    out = rm.write_render_manifest(m, out_dir=tmp_path, synthetic=False, repo_root=tmp_path)
    assert out.exists()


def test_draft_pool_fields_set_together_is_accepted(tmp_path):
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk(draft_pool_size=3, draft_rank=1, pool_predictor_score=72.0)
    out = rm.write_render_manifest(m, out_dir=tmp_path, synthetic=False, repo_root=tmp_path)
    assert out.exists()


def test_draft_pool_size_alone_is_refused(tmp_path):
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk(draft_pool_size=3)
    with pytest.raises(rm.ManifestError, match="set together or not at all"):
        rm.write_render_manifest(m, out_dir=tmp_path, synthetic=False, repo_root=tmp_path)


def test_draft_rank_alone_is_refused(tmp_path):
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk(draft_rank=1)
    with pytest.raises(rm.ManifestError, match="set together or not at all"):
        rm.write_render_manifest(m, out_dir=tmp_path, synthetic=False, repo_root=tmp_path)


def test_draft_rank_zero_is_out_of_range(tmp_path):
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk(draft_pool_size=3, draft_rank=0, pool_predictor_score=50.0)
    with pytest.raises(rm.ManifestError, match="out of range"):
        rm.write_render_manifest(m, out_dir=tmp_path, synthetic=False, repo_root=tmp_path)


def test_draft_rank_past_pool_size_is_out_of_range(tmp_path):
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk(draft_pool_size=3, draft_rank=4, pool_predictor_score=50.0)
    with pytest.raises(rm.ManifestError, match="out of range"):
        rm.write_render_manifest(m, out_dir=tmp_path, synthetic=False, repo_root=tmp_path)


def test_draft_pool_fields_round_trip_through_load_render(tmp_path):
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk(draft_pool_size=3, draft_rank=2, pool_predictor_score=61.5)
    out = rm.write_render_manifest(m, out_dir=tmp_path, synthetic=False, repo_root=tmp_path)
    loaded = rm.load_render(out)
    assert loaded.draft_pool_size == 3
    assert loaded.draft_rank == 2
    assert loaded.pool_predictor_score == 61.5


def test_pool_predictor_score_may_be_null_when_pool_membership_is_set(tmp_path):
    """Phase 17 (2026-08-17): the predictor is a recorded advisory, not a spend gate, and an
    unresolvable id or a scoring failure is a documented non-fatal degradation — a real pool
    member can land with no predictor reading at all."""
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk(draft_pool_size=3, draft_rank=1, pool_predictor_score=None)
    out = rm.write_render_manifest(m, out_dir=tmp_path, synthetic=False, repo_root=tmp_path)
    loaded = rm.load_render(out)
    assert loaded.draft_pool_size == 3
    assert loaded.draft_rank == 1
    assert loaded.pool_predictor_score is None


def test_pool_predictor_score_without_pool_membership_is_refused(tmp_path):
    """A predictor score with no pool to attach it to doesn't mean anything — refuse rather
    than accept an orphaned score."""
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk(pool_predictor_score=72.0)
    with pytest.raises(rm.ManifestError, match="pool membership"):
        rm.write_render_manifest(m, out_dir=tmp_path, synthetic=False, repo_root=tmp_path)


def test_cli_validate_exits_nonzero_on_a_refused_manifest(tmp_path, capsys):
    bad = tmp_path / "render-9x16.json"
    bad.write_text(
        json.dumps(
            {
                "profile": "acme",
                "slug": "s",
                "ratio": "9x16",
                "asset_path": "nope.mp4",
                "identity_used": [],
                "lip_sync_source": None,
                "provider": "",
                "provider_job_id": "",
                "cost_credits": 0.0,
                "duration_s": 0.0,
            }
        )
    )
    rc = rm.main(["validate", str(bad)])
    assert rc == 1
    assert "does not exist" in capsys.readouterr().err


# --- storyboard gate ---------------------------------------------------------
# 2026-08-18: a full synthetic render ran end to end without video-storyboard ever being
# invoked, so the operator never saw a frame until after the spend. The gate existed; only the
# skill's prose asked for it, and prose is what gets skipped.


def test_a_synthetic_render_with_no_storyboard_is_refused(tmp_path):
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk(identity_used=("generated",), storyboard_path="")
    with pytest.raises(rm.ManifestError, match="no storyboard_path"):
        rm.write_render_manifest(m, out_dir=tmp_path, synthetic=True, repo_root=tmp_path)


def test_real_footage_needs_no_storyboard(tmp_path):
    """Nothing was generated, so there was no frame to approve."""
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk(storyboard_path="")
    out = rm.write_render_manifest(m, out_dir=tmp_path, synthetic=False, repo_root=tmp_path)
    assert out.exists()


def test_a_storyboard_that_does_not_exist_is_refused(tmp_path):
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk(identity_used=("generated",), storyboard_path="nope.json")
    with pytest.raises(rm.ManifestError, match="does not exist"):
        rm.write_render_manifest(m, out_dir=tmp_path, synthetic=True, repo_root=tmp_path)


def test_an_unapproved_storyboard_is_refused(tmp_path):
    (tmp_path / "asset.mp4").write_bytes(b"x")
    (tmp_path / "pending.json").write_text(json.dumps({"approved": False, "shots": []}))
    m = _mk(identity_used=("generated",), storyboard_path="pending.json")
    with pytest.raises(rm.ManifestError, match="not marked approved"):
        rm.write_render_manifest(m, out_dir=tmp_path, synthetic=True, repo_root=tmp_path)


def test_an_unreadable_storyboard_is_refused_rather_than_assumed_approved(tmp_path):
    (tmp_path / "asset.mp4").write_bytes(b"x")
    (tmp_path / "broken.json").write_text("{not json")
    m = _mk(identity_used=("generated",), storyboard_path="broken.json")
    with pytest.raises(rm.ManifestError, match="unreadable"):
        rm.write_render_manifest(m, out_dir=tmp_path, synthetic=True, repo_root=tmp_path)


def test_an_approved_storyboard_lets_the_synthetic_render_through(tmp_path):
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk(identity_used=("generated",))
    out = rm.write_render_manifest(m, out_dir=tmp_path, synthetic=True, repo_root=tmp_path)
    assert json.loads(out.read_text())["storyboard_path"] == "storyboard.json"


# --- W0.3: a spend nothing metered is a spend the monthly cap never sees --------------------
#
# The August 2026 video programme spent ~580 credits that costs.jsonl records as $0. Generations
# drew a PRE-PURCHASED credit pool, so no code path was forced to meter them. Reading the ledger
# to answer "what did video cost" returned zero, and zero was wrong.


def test_a_synthetic_render_without_a_cost_source_is_refused(tmp_path):
    (tmp_path / "asset.mp4").write_bytes(b"x")
    (tmp_path / "storyboard.json").write_text(json.dumps({"approved": True, "shots": []}))
    m = _mk(identity_used=("generated",), cost_source="")
    with pytest.raises(rm.ManifestError, match="cost_source must be one of"):
        rm.write_render_manifest(m, out_dir=tmp_path, synthetic=True, repo_root=tmp_path)


def test_an_unknown_cost_source_is_refused(tmp_path):
    (tmp_path / "asset.mp4").write_bytes(b"x")
    (tmp_path / "storyboard.json").write_text(json.dumps({"approved": True, "shots": []}))
    m = _mk(identity_used=("generated",), cost_source="probably-about-ten-bucks")
    with pytest.raises(rm.ManifestError, match="cost_source must be one of"):
        rm.write_render_manifest(m, out_dir=tmp_path, synthetic=True, repo_root=tmp_path)


def test_a_costed_render_without_a_ledger_row_is_refused(tmp_path):
    """The exact August shape: credits were spent, nothing was metered."""
    (tmp_path / "asset.mp4").write_bytes(b"x")
    (tmp_path / "storyboard.json").write_text(json.dumps({"approved": True, "shots": []}))
    m = _mk(
        identity_used=("generated",), cost_source="preflight", cost_credits=361.8, cost_ledger_ts=""
    )
    with pytest.raises(rm.ManifestError, match="cost_ledger_ts is required"):
        rm.write_render_manifest(m, out_dir=tmp_path, synthetic=True, repo_root=tmp_path)


def test_a_provider_with_no_working_preflight_may_still_record_a_measured_cost(tmp_path):
    """sync_so's get_cost returns 422 on the params its own generation requires (observed live).

    That must not become a silently omitted cost field — it becomes an explicit, auditable
    balance-delta measurement.
    """
    (tmp_path / "asset.mp4").write_bytes(b"x")
    (tmp_path / "storyboard.json").write_text(json.dumps({"approved": True, "shots": []}))
    m = _mk(
        identity_used=("generated",),
        cost_source="post-hoc-balance-delta",
        cost_credits=22.5,
        cost_ledger_ts="2026-08-20T12:00:00Z",
    )
    assert rm.write_render_manifest(
        m, out_dir=tmp_path, synthetic=True, repo_root=tmp_path
    ).exists()


def test_a_free_path_needs_no_ledger_row_but_may_not_claim_a_cost(tmp_path):
    (tmp_path / "asset.mp4").write_bytes(b"x")
    (tmp_path / "storyboard.json").write_text(json.dumps({"approved": True, "shots": []}))
    ok = _mk(identity_used=("generated",), cost_source="free", cost_credits=0.0, cost_ledger_ts="")
    assert rm.write_render_manifest(
        ok, out_dir=tmp_path, synthetic=True, repo_root=tmp_path
    ).exists()

    contradictory = _mk(
        identity_used=("generated",), cost_source="free", cost_credits=12.0, cost_ledger_ts=""
    )
    with pytest.raises(rm.ManifestError, match="free path spends nothing"):
        rm.write_render_manifest(
            contradictory, out_dir=tmp_path, synthetic=True, repo_root=tmp_path
        )


def test_real_footage_is_exempt_from_the_cost_gate(tmp_path):
    """Nothing was generated, so there is no generation spend to meter."""
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk(identity_used=(), cost_source="", cost_credits=0.0, cost_ledger_ts="")
    assert rm.write_render_manifest(
        m, out_dir=tmp_path, synthetic=False, repo_root=tmp_path
    ).exists()


def test_append_cost_returns_the_stamped_row_so_a_manifest_can_cite_it(tmp_path):
    """The manifest's cost_ledger_ts has to come from somewhere; this is that somewhere."""
    from gtm_core.ledgers import Ledgers

    ledgers = Ledgers(profile="acme", cfg=_LedgerCfg(tmp_path))
    row = ledgers.append_cost({"tool": "higgsfield", "cost_usd": 0.68, "units": {"credits": 22.5}})
    assert row["ts"], "append_cost must return the row it stamped, ts included"
    assert row["prev_sha256"], "the returned row must be the chained one actually written"


class _LedgerCfg:
    """Minimal stand-in for the runtime config Ledgers reads its content root from."""

    def __init__(self, root):
        self.content_root = root


# --- W1.3: a retired engine may never reach a shipped asset --------------------------------


def test_a_manifest_naming_a_retired_engine_is_refused(tmp_path):
    (tmp_path / "asset.mp4").write_bytes(b"x")
    (tmp_path / "storyboard.json").write_text(json.dumps({"approved": True, "shots": []}))
    m = _mk(identity_used=("generated",), render_engine="higgsfield_lipsync_posthoc")
    with pytest.raises(rm.ManifestError, match="retired"):
        rm.write_render_manifest(m, out_dir=tmp_path, synthetic=True, repo_root=tmp_path)


def test_an_engine_the_registry_does_not_know_is_refused(tmp_path):
    (tmp_path / "asset.mp4").write_bytes(b"x")
    (tmp_path / "storyboard.json").write_text(json.dumps({"approved": True, "shots": []}))
    m = _mk(identity_used=("generated",), render_engine="whatever_was_handy")
    with pytest.raises(rm.ManifestError, match="not in gtm_core/render_engines.toml"):
        rm.write_render_manifest(m, out_dir=tmp_path, synthetic=True, repo_root=tmp_path)


def test_a_known_live_engine_is_accepted(tmp_path):
    (tmp_path / "asset.mp4").write_bytes(b"x")
    (tmp_path / "storyboard.json").write_text(json.dumps({"approved": True, "shots": []}))
    m = _mk(identity_used=("generated",), render_engine="higgsfield_i2v")
    assert rm.write_render_manifest(
        m, out_dir=tmp_path, synthetic=True, repo_root=tmp_path
    ).exists()


# --- native lip sync: the vocabulary, and the claim the engine has to back --------------------
#
# `audio_references` records an audio file passed to a general image-to-video model as a
# REFERENCE input — verified 2026-08-19 to leave the mouth closed in 9 of 10 sampled frames.
# HeyGen's avatar engine genuinely generates the mouth from a supplied audio track. Recording the
# second as the first would erase the distinction the whole August 2026 rearchitecture turned on,
# in the one file that is supposed to preserve it.


def test_a_heygen_render_records_native_audio_not_the_reference_input_token(tmp_path):
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk(
        identity_used=("soul", "voice"),
        lip_sync_source="native_audio",
        render_engine="heygen_avatar",
    )
    out = rm.write_render_manifest(m, out_dir=tmp_path, synthetic=True, repo_root=tmp_path)
    assert json.loads(out.read_text())["lip_sync_source"] == "native_audio"


def test_a_native_sync_claim_over_a_model_that_cannot_do_it_is_refused(tmp_path):
    """The August 2026 defect written from the audit end.

    Back then the render was wrong and the record said nothing. A record that can assert native
    sync over `wan2_7` would let the same failure ship while *looking* audited — which is worse,
    because a reviewer trusts the record instead of watching the frames.
    """
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk(
        identity_used=("soul", "voice"),
        lip_sync_source="native_audio",
        render_engine="higgsfield_i2v",
    )
    with pytest.raises(rm.ManifestError, match="claims the mouth was generated"):
        rm.write_render_manifest(m, out_dir=tmp_path, synthetic=True, repo_root=tmp_path)


def test_the_reference_input_token_still_records_honestly_against_that_model(tmp_path):
    """The positive control for the rule above: `audio_references` on `wan2_7` is the TRUE
    description of what that render was, and must stay writable — the cross-check refuses an
    unbacked claim, not the honest record of a disappointing one."""
    (tmp_path / "asset.mp4").write_bytes(b"x")
    m = _mk(
        identity_used=("soul", "voice"),
        lip_sync_source="audio_references",
        render_engine="higgsfield_i2v",
    )
    assert rm.write_render_manifest(
        m, out_dir=tmp_path, synthetic=True, repo_root=tmp_path
    ).exists()


def test_post_hoc_lip_sync_is_still_absent_from_the_vocabulary_and_refused(tmp_path):
    """Widening LIP_SYNC_SOURCES for the native cases must not have readmitted the retired one.

    Refused on BOTH paths, which are genuinely different code: `_validate_lip_sync` returns early
    when identity_used carries no "voice", so without this second case the retirement branch in
    `_validate_engine` would be the untested one — and it is the branch that catches a post-hoc
    render someone forgot to mark as voice-bearing.
    """
    assert "post_hoc" not in rm.LIP_SYNC_SOURCES
    (tmp_path / "asset.mp4").write_bytes(b"x")

    voiced = _mk(identity_used=("soul", "voice"), lip_sync_source="post_hoc")
    with pytest.raises(rm.ManifestError, match="post_hoc"):
        rm.write_render_manifest(voiced, out_dir=tmp_path, synthetic=True, repo_root=tmp_path)

    unvoiced = _mk(identity_used=("soul",), lip_sync_source="post_hoc")
    with pytest.raises(rm.ManifestError, match="retired"):
        rm.write_render_manifest(unvoiced, out_dir=tmp_path, synthetic=True, repo_root=tmp_path)
