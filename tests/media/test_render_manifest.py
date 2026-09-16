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


# --- the validator can load what the skill actually writes, and runs the cost gate on it --------
#
# Observed 2026-09-03 on a live tenant. `write_render_manifest` refused an unmetered synthetic spend, but
# the video lane never called the writer: it hand-authored `render-<ratio>.json` and then ran
# `render_manifest validate`, the step its own body_template mandates. That path did two things
# wrong at once — the loader crashed with a `TypeError` on every multi-shot manifest (so the check
# never ran), and even when it loaded, `validate_render_file` did not check cost at all. A 405-
# credit film passed the mandated gate by never reaching it.


def _companion(tmp_path, **fields):
    """A manifest in the shape the video-render skill actually writes: no top-level
    profile/slug/asset_path, the asset named under `stitched_asset`, per-shot detail alongside."""
    d = tmp_path / "content" / "acme" / "video" / "2026-09-03-launch-film"
    d.mkdir(parents=True)
    (d / "joined.mp4").write_bytes(b"x")
    payload = {
        "ratio": "9:16",
        "identity_used": ["element"],
        "stitched_asset": {
            "path": "content/acme/video/2026-09-03-launch-film/joined.mp4",
            "duration_s": 30.0,
        },
        "shots": [{"n": 1, "prompt": "Static camera, slow push-in."}],
    }
    payload.update(fields)
    path = d / "render-9x16.json"
    path.write_text(json.dumps(payload))
    return path


def test_a_companion_shaped_manifest_loads_instead_of_raising_typeerror(tmp_path):
    """The regression itself: `RenderManifest.__init__() missing 3 required positional arguments`
    out of the loader. A validator that cannot parse its input is not a gate."""
    path = _companion(tmp_path)
    m = rm.load_render(path)
    assert m.profile == "acme"
    assert m.slug == "2026-09-03-launch-film"
    assert m.asset_path.endswith("joined.mp4")


def test_a_manifest_underdetermined_even_by_its_path_fails_readably(tmp_path):
    """Outside the conventional layout there is nothing to recover from, and the caller gets a
    ManifestError naming what is missing — never a stack trace out of a dataclass constructor."""
    path = tmp_path / "render-9x16.json"
    path.write_text(json.dumps({"identity_used": ["element"]}))
    with pytest.raises(rm.ManifestError, match="missing"):
        rm.load_render(path)


def test_the_loader_recovers_identity_but_never_cost(tmp_path):
    """The load-bearing asymmetry. Recovering profile/slug/asset_path from the path is repair;
    inferring credits from a free-text note would be laundering, and would hand a pass to exactly
    the manifests this gate exists to catch."""
    path = _companion(
        tmp_path,
        credits_spent={"video_renders": 9, "per_render": 45, "video_total": 405},
    )
    m = rm.load_render(path)
    assert m.cost_credits == 0.0
    assert m.cost_source == ""


def test_validate_runs_the_cost_gate_on_a_companion_shaped_manifest(tmp_path, monkeypatch):
    """End to end on the reference case: a synthetic film that spent real credits and names no
    ledger row is REFUSED by the command the skill body tells the operator to run."""
    monkeypatch.chdir(tmp_path)
    path = _companion(tmp_path, prompt="Static camera, slow push-in.")
    with pytest.raises(rm.ManifestError, match="cost_source"):
        rm.validate_render_file(path, synthetic=True, repo_root=tmp_path)


def test_validate_accepts_a_companion_manifest_that_did_meter_its_spend(tmp_path):
    """The positive control — without it the test above would pass on a validator that refuses
    everything. Same shape, cost fields filled in from `ledger_cli meter-render`."""
    path = _companion(
        tmp_path,
        prompt="Static camera, slow push-in.",
        cost_credits=405.0,
        cost_source="post-hoc-balance-delta",
        cost_ledger_ts="2026-09-03T09:15:00Z",
    )
    rm.validate_render_file(path, synthetic=True, repo_root=tmp_path)


def test_validate_leaves_real_footage_exempt_from_the_cost_gate(tmp_path):
    """Symmetric with the writer: uncaptured footage costs nothing to generate, so --synthetic
    off must not start demanding a ledger row for it."""
    path = _companion(tmp_path)
    rm.validate_render_file(path, synthetic=False, repo_root=tmp_path)


# --- finish manifest: pre-burned captions and the kit-declared route ----------------------------


def _finish(**kw) -> rm.FinishManifest:
    base = {
        "profile": "acme",
        "slug": "s",
        "ratio": "9x16",
        "asset_path": "would-be-final.mp4",
        "stages": ("normalize", "encode"),
        "executed": False,
        "caption_route": "local",
    }
    base.update(kw)
    return rm.FinishManifest(**base)


def test_a_finish_manifest_written_before_the_preburned_fields_existed_still_loads(tmp_path):
    legacy = tmp_path / "finish-9x16.json"
    legacy.write_text(
        json.dumps(
            {
                "profile": "acme",
                "slug": "s",
                "ratio": "9x16",
                "asset_path": "x.mp4",
                "stages": ["normalize"],
                "caption_route": "none",
            }
        )
    )
    fm = rm.load_finish(legacy)
    assert fm.captions_preburned is False and fm.overlays is None


def test_preburned_captions_and_overlays_round_trip_through_write_and_load(tmp_path):
    captions = {"frame": [1080, 1920], "screens": [{"index": 0, "start_s": 0.0, "end_s": 1.0}]}
    overlays = [
        {"kind": "k", "start_s": 0.0, "end_s": 1.0, "box": {"x": 0, "y": 0, "w": 1, "h": 1}}
    ]
    out = rm.write_finish_manifest(
        _finish(captions=captions, captions_preburned=True, overlays=overlays),
        out_dir=tmp_path,
        repo_root=tmp_path,
    )
    fm = rm.load_finish(out)
    assert fm.captions_preburned is True
    assert fm.captions == captions and fm.overlays == overlays


def test_lint_suppressions_round_trip_through_write_and_load_as_a_tuple_of_dicts(tmp_path):
    """#1: video_finish.execute carries suppressions forward as plain dicts (never a Suppression
    dataclass — that type lives in gtm_core.video_lint, and render_manifest must not depend on
    it); this pins that load_finish hands them back exactly as written, as a tuple of dicts."""
    suppressions = (
        {"tier": "V1", "asset": "cut-9x16-final.mp4", "reason": "resolution is intentionally low"},
        {"tier": "V10", "asset": "cut-9x16-final.mp4", "reason": "silence is a deliberate beat"},
    )
    out = rm.write_finish_manifest(
        _finish(lint_suppressions=suppressions), out_dir=tmp_path, repo_root=tmp_path
    )
    fm = rm.load_finish(out)
    assert fm.lint_suppressions == suppressions
    assert isinstance(fm.lint_suppressions, tuple)
    assert all(isinstance(s, dict) for s in fm.lint_suppressions)


def test_lint_suppressions_default_to_an_empty_tuple_when_omitted(tmp_path):
    out = rm.write_finish_manifest(_finish(), out_dir=tmp_path, repo_root=tmp_path)
    fm = rm.load_finish(out)
    assert fm.lint_suppressions == ()


def test_a_kit_declared_local_route_needs_no_suppression_beside_a_resolving_preset(tmp_path):
    """Mirror of plan()'s rule at the manifest gate: the two must agree, or a plan that was
    accepted fails at write time with an asset already encoded."""
    with pytest.raises(rm.ManifestError, match="caption_route_suppression"):
        rm.write_finish_manifest(
            _finish(), out_dir=tmp_path, repo_root=tmp_path, preset_resolved=True
        )
    out = rm.write_finish_manifest(
        _finish(),
        out_dir=tmp_path,
        repo_root=tmp_path,
        preset_resolved=True,
        declared_route="local",
    )
    assert out.is_file()
    with pytest.raises(rm.ManifestError, match="caption_route_suppression"):
        rm.write_finish_manifest(
            _finish(),
            out_dir=tmp_path,
            repo_root=tmp_path,
            preset_resolved=True,
            declared_route="reap",
        )
