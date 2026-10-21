"""R2.4 / SCHEMA: an observation shard that cannot be read refuses the view, and nothing attests.

Verification audit 2026-10-02, Critical 6: a retraction lives in a shard like any other line. If
that shard could not be read, the reader dropped the retraction along with the shard and the
membership it withdrew came back and could vouch for an account. A corrupt or newer-version shard
can hold a line about any account, so while one cannot be read no observation is trusted for the
run (the legacy fields are untouched), and the view says why instead of looking merely empty.
"""

from __future__ import annotations

import json

from gtm_core import signal_view
from gtm_core.hook_coverage.premise import Premise
from gtm_core.hook_coverage.source_attest import source_attests
from gtm_core.signal_obs import observations as obs
from unit.test_signal_view import ROW, TODAY, _ctx, world  # noqa: F401 (the fixture)

PREMISE = Premise(
    key="agents-in-operation", min_distinct=1, terms=frozenset(), attested_by_source=True
)


def _retraction(w, membership: dict, writer="bo") -> dict:
    return obs.make_observation(
        kind="retracted",
        product=w.product,
        source_id=membership["source_id"],
        source_url=membership["source_url"],
        capture_sha256=membership["capture_sha256"],
        account_key=membership["account_key"],
        observed="2026-10-01",
        observed_basis="operator_decision",
        role=membership["role"],
        premise_at_write=membership["premise_at_write"],
        writer=writer,
        supersedes=membership["obs_id"],
    )


def _write_shard(w, name: str, lines: list[str]) -> None:
    (w.obs_dir / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_the_baseline_world_attests(world):  # noqa: F811
    ctx = _ctx(world)
    assert ctx.refused == () and source_attests(ROW, PREMISE, ctx) is True


def test_a_readable_retraction_withdraws_the_membership(world):  # noqa: F811
    member = world.observations()[0]
    _write_shard(world, "bo-2026-10.jsonl", [json.dumps(_retraction(world, member))])
    ctx = _ctx(world)
    assert ctx.refused == () and source_attests(ROW, PREMISE, ctx) is False


def test_the_audit_case_a_retraction_in_a_shard_that_cannot_be_read_does_not_bring_the_member_back(
    world,  # noqa: F811
):
    member = world.observations()[0]
    newer = json.dumps({"schema": 2, "kind": "something_new"})
    _write_shard(world, "bo-2026-10.jsonl", [json.dumps(_retraction(world, member)), newer])
    ctx = _ctx(world)
    assert source_attests(ROW, PREMISE, ctx) is False
    assert ctx.observations == ()
    assert [p["shard"] for p in ctx.refused] == ["bo-2026-10.jsonl"]


def test_a_shard_cut_off_mid_write_also_refuses_every_observation(world):  # noqa: F811
    (world.obs_dir / "cy-2026-10.jsonl").write_text('{"schema": 1, "kind": "sou', encoding="utf-8")
    ctx = _ctx(world)
    assert source_attests(ROW, PREMISE, ctx) is False
    assert ctx.refused[0]["shard"] == "cy-2026-10.jsonl"


def test_the_view_says_refused_and_keeps_the_legacy_fields(world):  # noqa: F811
    (world.obs_dir / "cy-2026-10.jsonl").write_text("not json\n", encoding="utf-8")
    row = {**ROW, "signal_observed": "2026-09-20", "why_now": "Northwind launched an agent pilot."}
    v = signal_view.derive(row, _ctx(world), premise=PREMISE, today=TODAY)
    assert v.view_basis == "refused"
    assert v.timing_kind == "news_event" and v.timing_observed == "2026-09-20"
    assert v.relevance_line and v.premise_via != "source"


def test_the_refusal_names_the_shard_and_a_newer_version_says_to_upgrade(world):  # noqa: F811
    _write_shard(world, "zed-2026-10.jsonl", [json.dumps({"schema": 2, "kind": "x"})])
    lines = signal_view.refusal_lines(_ctx(world))
    assert len(lines) == 1
    assert "zed-2026-10.jsonl" in lines[0] and "pull and upgrade" in lines[0]
    assert signal_view.refusal_lines(None) == []


def test_a_readable_world_has_no_refusal_lines(world):  # noqa: F811
    assert signal_view.refusal_lines(_ctx(world)) == []


def test_explain_names_a_refused_shard_on_stderr_and_reports_the_refused_basis(world, capsys):  # noqa: F811
    _write_shard(world, "zed-2026-10.jsonl", [json.dumps({"schema": 2, "kind": "x"})])
    rc = signal_view.main(
        [
            "explain",
            "--profile",
            world.profile,
            "--product",
            world.product,
            "--domain",
            ROW["domain"],
            "--premise",
            "agents-in-operation",
        ],
        profiles_root=world.profiles_root,
        content_root=world.content_root,
    )
    out = capsys.readouterr()
    assert rc == 0
    assert json.loads(out.out)["view_basis"] == "refused"
    assert "zed-2026-10.jsonl" in out.err and "pull and upgrade" in out.err
