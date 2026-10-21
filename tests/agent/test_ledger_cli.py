"""Unit tests for the ledger CLI (agent.ledger_cli) — SDK-INDEPENDENT.

The CLI is the skills' write path to the ledgers. It must shape the same
``content/<profile>/`` tree the Ledgers class does, stamp ``ts``, and report the
monthly total (with a scriptable over-cap exit code). We drive it with
``--repo-root tmp_path`` so all state lands under a throwaway dir.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("agent.ledger_cli", reason="agent.ledger_cli not built yet")

from agent.ledger_cli import main as ledger_main  # noqa: E402

PROFILE = "example"


def _run(tmp_path: Path, *argv: str) -> int:
    return ledger_main(["--repo-root", str(tmp_path), *argv])


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_append_history_and_cost_write_jsonl(tmp_path):
    rc = _run(
        tmp_path,
        "append-history",
        "--profile",
        PROFILE,
        "--json",
        json.dumps({"event": "asset_ready", "item_id": "ci-1"}),
    )
    assert rc == 0
    rc = _run(
        tmp_path,
        "append-cost",
        "--profile",
        PROFILE,
        "--json",
        json.dumps({"tool": "deepseek-worker", "cost_usd": 0.12}),
    )
    assert rc == 0

    base = tmp_path / "content" / PROFILE
    hist = _read_jsonl(base / "history.jsonl")
    costs = _read_jsonl(base / "costs.jsonl")
    assert hist[0]["event"] == "asset_ready"
    assert "ts" in hist[0], "CLI must stamp ts via Ledgers"
    assert costs[0]["cost_usd"] == 0.12


def test_write_run_manifest_lands_under_runs(tmp_path):
    manifest = {
        "run_id": "r-1",
        "trigger": "telegram",
        "stages": [{"name": "radar", "status": "ok", "outputs": ["d.md"]}],
    }
    rc = _run(tmp_path, "write-run-manifest", "--profile", PROFILE, "--json", json.dumps(manifest))
    assert rc == 0
    path = tmp_path / "content" / PROFILE / "runs" / "r-1.json"
    assert path.is_file()
    assert json.loads(path.read_text())["run_id"] == "r-1"


def test_month_total_over_cap_exit_code(tmp_path, capsys):
    _run(
        tmp_path,
        "append-cost",
        "--profile",
        PROFILE,
        "--json",
        json.dumps({"tool": "x", "cost_usd": 7.0}),
    )
    # Under cap → exit 0.
    assert _run(tmp_path, "month-total", "--profile", PROFILE, "--cap", "10") == 0
    # At/over cap → exit 2 (scriptable hard stop).
    rc = _run(tmp_path, "month-total", "--profile", PROFILE, "--cap", "5")
    assert rc == 2
    out = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(out)
    assert payload["over_cap"] is True
    assert payload["total_usd"] == pytest.approx(7.0)


def test_month_units_sums_a_dict_shaped_units_record(tmp_path):
    _run(
        tmp_path,
        "append-cost",
        "--profile",
        PROFILE,
        "--json",
        json.dumps({"tool": "reap", "cost_usd": 0, "units": {"media_credits": 15}}),
    )
    _run(
        tmp_path,
        "append-cost",
        "--profile",
        PROFILE,
        "--json",
        json.dumps({"tool": "reap", "cost_usd": 0, "units": {"media_credits": 5}}),
    )
    assert (
        _run(
            tmp_path,
            "month-units",
            "--profile",
            PROFILE,
            "--tool",
            "reap",
            "--unit",
            "media_credits",
        )
        == 0
    )


def test_month_units_over_cap_exit_code(tmp_path, capsys):
    _run(
        tmp_path,
        "append-cost",
        "--profile",
        PROFILE,
        "--json",
        json.dumps({"tool": "reap", "cost_usd": 0, "units": {"media_credits": 600}}),
    )
    rc = _run(
        tmp_path,
        "month-units",
        "--profile",
        PROFILE,
        "--tool",
        "reap",
        "--unit",
        "media_credits",
        "--cap",
        "600",
    )
    assert rc == 2
    out = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(out)
    assert payload["over_cap"] is True
    assert payload["total"] == pytest.approx(600.0)
    assert payload["tool"] == "reap"
    assert payload["unit"] == "media_credits"
    assert payload["cap"] == pytest.approx(600.0)


def test_month_units_does_not_mix_pools_on_the_same_tool(tmp_path, capsys):
    """media_credits and ai_credits are TWO pools on ONE tool — must never merge."""
    _run(
        tmp_path,
        "append-cost",
        "--profile",
        PROFILE,
        "--json",
        json.dumps(
            {"tool": "reap", "cost_usd": 0, "units": {"media_credits": 400, "ai_credits": 10}}
        ),
    )
    assert (
        _run(
            tmp_path, "month-units", "--profile", PROFILE, "--tool", "reap", "--unit", "ai_credits"
        )
        == 0
    )
    out = capsys.readouterr().out.strip().splitlines()[-1]
    assert json.loads(out)["total"] == pytest.approx(10.0)


def test_bad_json_payload_errors(tmp_path):
    with pytest.raises(SystemExit):
        _run(tmp_path, "append-history", "--profile", PROFILE, "--json", "{not json")


# ── record-manual-publish (durable idempotency for a hand-posted item) ────────


def test_record_manual_publish_stamps_matching_content_hash(tmp_path, capsys):
    """A manual publish must record the SAME content_sha256 the auto-publisher uses,
    so published_content_hashes() sees it and a later auto-publish dedupes."""
    from gtm_core.ledgers import Ledgers
    from gtm_core.paths import PathConfig
    from gtm_core.publish_hash import content_hash

    asset = tmp_path / "ci-1.asset.json"
    asset.write_text(json.dumps({"platform": "linkedin", "body": "Shipped a thing."}))

    rc = _run(
        tmp_path,
        "record-manual-publish",
        "--profile",
        PROFILE,
        "--asset",
        str(asset),
        "--item-id",
        "ci-1",
        "--source",
        "journey",
        "--url",
        "https://linkedin.com/p/abc",
    )
    assert rc == 0
    printed = capsys.readouterr().out.strip().splitlines()[-1]

    expected = content_hash("Shipped a thing.", ())
    assert printed == expected, "CLI prints the recorded content_sha256"

    base = tmp_path / "content" / PROFILE
    rec = _read_jsonl(base / "history.jsonl")[0]
    assert rec["event"] == "published"
    assert rec["content_sha256"] == expected
    assert rec["manual"] is True
    assert rec["source"] == "journey"
    assert rec["url"] == "https://linkedin.com/p/abc"
    assert "ts" in rec, "CLI must stamp ts via Ledgers"

    # The durable idempotency ledger must now see this hash.
    led = Ledgers(PathConfig.from_env(repo_root=tmp_path), PROFILE)
    assert expected in led.published_content_hashes()


def test_record_manual_publish_with_media_hashes_media_too(tmp_path, capsys):
    """Omitting --media for a post that shipped with media would record a content_sha256
    the automated publisher never would have — --media must feed the same content_hash."""
    from gtm_core.publish_hash import content_hash

    asset = tmp_path / "ci-2.asset.json"
    asset.write_text(json.dumps({"platform": "linkedin", "body": "Shipped with media."}))

    rc = _run(
        tmp_path,
        "record-manual-publish",
        "--profile",
        PROFILE,
        "--asset",
        str(asset),
        "--item-id",
        "ci-2",
        "--media",
        "https://cdn.example/a.png",
        "--media",
        "https://cdn.example/b.png",
        "--ref",
        "urn:li:share:123",
    )
    assert rc == 0
    printed = capsys.readouterr().out.strip().splitlines()[-1]

    expected = content_hash(
        "Shipped with media.", ("https://cdn.example/a.png", "https://cdn.example/b.png")
    )
    assert printed == expected
    assert expected != content_hash("Shipped with media.", ()), (
        "sanity: media must actually change the hash, or this test proves nothing"
    )

    rec = _read_jsonl(tmp_path / "content" / PROFILE / "history.jsonl")[0]
    assert rec["content_sha256"] == expected
    assert rec["media"] == ["https://cdn.example/a.png", "https://cdn.example/b.png"]
    assert rec["ref"] == "urn:li:share:123"


def test_record_manual_publish_rejects_asset_without_body(tmp_path):
    asset = tmp_path / "ci-x.asset.json"
    asset.write_text(json.dumps({"platform": "x", "tweets": ["a"]}))  # no 'body'
    with pytest.raises(SystemExit):
        _run(
            tmp_path,
            "record-manual-publish",
            "--profile",
            PROFILE,
            "--asset",
            str(asset),
            "--item-id",
            "ci-x",
        )


# ── record-schedule-void (F5) ────────────────────────────────────────────────


def test_record_schedule_void_by_content_sha256(tmp_path, capsys):
    from gtm_core.ledgers import Ledgers
    from gtm_core.paths import PathConfig

    led = Ledgers(PathConfig.from_env(repo_root=tmp_path), PROFILE)
    led.append_history({"event": "scheduled", "content_sha256": "abc123"})
    assert "abc123" in led.published_content_hashes()

    rc = _run(
        tmp_path,
        "record-schedule-void",
        "--profile",
        PROFILE,
        "--content-sha256",
        "abc123",
        "--note",
        "operator cancelled in the scheduler UI",
    )
    assert rc == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "abc123"

    rec = _read_jsonl(tmp_path / "content" / PROFILE / "history.jsonl")[-1]
    assert rec["event"] == "schedule_voided"
    assert rec["content_sha256"] == "abc123"
    assert rec["note"] == "operator cancelled in the scheduler UI"

    led2 = Ledgers(PathConfig.from_env(repo_root=tmp_path), PROFILE)
    assert "abc123" not in led2.published_content_hashes(), (
        "a voided schedule must free the hash for re-approval"
    )


def test_record_schedule_void_by_post_text_derives_the_same_hash(tmp_path):
    from gtm_core.publish_hash import content_hash

    expected = content_hash("Cancel this one.", ("https://cdn.example/a.png",))
    rc = _run(
        tmp_path,
        "record-schedule-void",
        "--profile",
        PROFILE,
        "--post",
        "Cancel this one.",
        "--media",
        "https://cdn.example/a.png",
    )
    assert rc == 0
    rec = _read_jsonl(tmp_path / "content" / PROFILE / "history.jsonl")[-1]
    assert rec["content_sha256"] == expected


def test_record_schedule_void_never_unblocks_an_already_published_hash(tmp_path):
    """A voided-after-published event must not re-enable sending the same live
    bytes a second time — voiding only ever clears a `scheduled` state."""
    from gtm_core.ledgers import Ledgers
    from gtm_core.paths import PathConfig

    led = Ledgers(PathConfig.from_env(repo_root=tmp_path), PROFILE)
    led.append_history({"event": "published", "content_sha256": "live456"})

    rc = _run(tmp_path, "record-schedule-void", "--profile", PROFILE, "--content-sha256", "live456")
    assert rc == 0

    led2 = Ledgers(PathConfig.from_env(repo_root=tmp_path), PROFILE)
    assert "live456" in led2.published_content_hashes(), (
        "a schedule_voided event must never clear a hash that already went live"
    )


def test_record_schedule_void_requires_exactly_one_of_content_sha256_or_post(tmp_path):
    with pytest.raises(SystemExit):
        _run(tmp_path, "record-schedule-void", "--profile", PROFILE)
    with pytest.raises(SystemExit):
        _run(
            tmp_path,
            "record-schedule-void",
            "--profile",
            PROFILE,
            "--content-sha256",
            "a",
            "--post",
            "b",
        )


# --- meter-render: the credits→USD→ledger bridge -------------------------------------------------
#
# The step that did not exist. A Higgsfield render bills in credits, the monthly cap is in USD,
# and nothing converted between them — so no honest row could be written and none was. Twice a
# finished film left `month-total` reading $0.00 over real money (~580 credits August 2026; 405 on
# one tenant 2026-09-03). These hold the two properties that make the verb worth having: it lands in
# the SAME ledger the cap reads, and it writes NOTHING when it cannot price the spend.


def _declare_plan(tmp_path: Path, **kw) -> None:
    d = tmp_path / "content" / PROFILE
    d.mkdir(parents=True, exist_ok=True)
    (d / "settings.json").write_text(json.dumps(kw))


def test_meter_render_writes_a_dollar_row_the_monthly_cap_can_see(tmp_path, capsys):
    _declare_plan(tmp_path, higgsfield_plan="ultra", higgsfield_billing="monthly")
    rc = _run(
        tmp_path,
        "meter-render",
        "--profile",
        PROFILE,
        "--credits",
        "405",
        "--op",
        "video_render",
        "--model",
        "seedance_2_0",
        "--slug",
        "2026-09-03-launch-film",
    )
    assert rc == 0
    written = json.loads(capsys.readouterr().out)
    assert written["cost_usd"] == 17.415
    assert written["units"] == 405.0 and written["unit_kind"] == "credits"

    rows = _read_jsonl(tmp_path / "content" / PROFILE / "costs.jsonl")
    assert rows[0]["cost_usd"] == 17.415
    # The whole point: the spend now reaches the cap it was invisible to.
    rc = _run(tmp_path, "month-total", "--profile", PROFILE, "--cap", "10")
    assert rc == 2, "a $17.42 spend must trip a $10 cap"


def test_meter_render_hands_back_the_ts_the_manifest_has_to_cite(tmp_path, capsys):
    """`cost_ledger_ts` is the row's natural key. Printing "ok" — as `append-cost` used to —
    threw away the one value the caller needed next, at the moment it was created."""
    _declare_plan(tmp_path, higgsfield_plan="ultra", higgsfield_billing="monthly")
    _run(tmp_path, "meter-render", "--profile", PROFILE, "--credits", "45", "--op", "video_render")
    written = json.loads(capsys.readouterr().out)
    rows = _read_jsonl(tmp_path / "content" / PROFILE / "costs.jsonl")
    assert written["ts"] == rows[0]["ts"]


def test_meter_render_writes_nothing_when_the_profile_declares_no_rate(tmp_path, capsys):
    """Fail-closed. A $0 row for an unpriceable spend is the defect, not the fallback — it is
    indistinguishable from a free call and it is what the cap would then believe."""
    _declare_plan(tmp_path, monthly_budget_usd=25.0)
    rc = _run(tmp_path, "meter-render", "--profile", PROFILE, "--credits", "405", "--op", "render")
    assert rc == 1
    assert "higgsfield_plan" in capsys.readouterr().err
    assert not (tmp_path / "content" / PROFILE / "costs.jsonl").exists()


def test_append_cost_returns_the_row_as_written_not_ok(tmp_path, capsys):
    rc = _run(
        tmp_path,
        "append-cost",
        "--profile",
        PROFILE,
        "--json",
        json.dumps({"tool": "higgsfield", "cost_usd": 1.5}),
    )
    assert rc == 0
    written = json.loads(capsys.readouterr().out)
    assert written["cost_usd"] == 1.5 and written["ts"]
