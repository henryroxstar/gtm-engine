"""The retention sweep's campaign gate: ``--apply`` needs every campaign to be FINISHED.

The first cut of this gate asked "is any campaign active?" — the status page's definition of
open. That fails OPEN for the way campaigns are actually run: a sequence sits paused or in draft
for weeks, and neither word is "active", so the purge was permitted in the middle of a campaign.
The product owner's rule is the other way round — *nothing is purged if we haven't finished the
customer email campaign* — so the gate is a closed list of words that mean finished, and every
other state blocks, including no status, an unknown word, and no manifest at all.

All fixtures are fictional and live under ``tmp_path``; the clock is injected.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gtm_core.retention_campaign_gate import FINISHED_STATUSES, campaign_gate
from gtm_core.retention_sweep import UnfinishedCampaignRefusal, main, sweep_stale_pii

PROFILE = "test-tenant"
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


def _aged_export(root: Path) -> Path:
    path = root / PROFILE / "prospects" / "prospects-20260801-hubspot.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("first,last,email\nAvery,Quill,avery.quill@northwind.example\n", "utf-8")
    stamp = NOW.timestamp() - 10 * 86400
    os.utime(path, (stamp, stamp))
    return path


def _campaign(root: Path, slug: str, status_line: str) -> None:
    """``status_line`` is raw TOML, so a test can omit the key or write it oddly."""
    folder = root / PROFILE / "plans" / "campaigns"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{slug}.campaign.toml").write_text(f'slug = "{slug}"\n{status_line}\n', "utf-8")


def _sequence_export(root: Path, name: str = "autumn-outbound-hubspot.csv") -> Path:
    path = root / PROFILE / "prospects" / "sequences" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("email\njules.marsh@tailspinhealth.example\n", encoding="utf-8")
    return path


def _tree(root: Path) -> dict[str, bytes]:
    base = root / PROFILE
    return {
        str(p.relative_to(base)): p.read_bytes() for p in sorted(base.rglob("*")) if p.is_file()
    }


# --------------------------------------------------------------------------- unfinished blocks


@pytest.mark.parametrize(
    ("status_line", "shown"),
    [
        ('status = "draft"', "draft"),
        ('status = "paused"', "paused"),
        ('status = "active"', "active"),
        ('status = ""', "no status declared"),
        ("", "no status declared"),
        ('status = "wrapping-up"', "wrapping-up"),
    ],
    ids=["draft", "paused", "active", "blank", "missing", "unknown-word"],
)
def test_a_campaign_that_is_not_explicitly_finished_blocks_apply(
    tmp_path: Path, status_line: str, shown: str
) -> None:
    export = _aged_export(tmp_path)
    _campaign(tmp_path, "autumn-outbound", status_line)
    before = _tree(tmp_path)

    with pytest.raises(UnfinishedCampaignRefusal) as exc:
        sweep_stale_pii(PROFILE, apply=True, content_root=tmp_path, now=NOW)

    assert exc.value.slugs == ("autumn-outbound",)
    assert f"autumn-outbound (status: {shown})" in str(exc.value)
    assert 'status = "closed"' in str(exc.value), "the refusal says how to close a campaign"
    assert export.exists() and _tree(tmp_path) == before


@pytest.mark.parametrize(
    "status", ["closed", "Completed ", " FINISHED", "done", "Archived"], ids=repr
)
def test_an_explicitly_finished_campaign_permits_apply(tmp_path: Path, status: str) -> None:
    export = _aged_export(tmp_path)
    _campaign(tmp_path, "spring-outbound", f'status = "{status}"')

    res = sweep_stale_pii(PROFILE, apply=True, content_root=tmp_path, now=NOW)

    assert len(res.archived) == 1
    assert not export.exists()


def test_the_finished_vocabulary_is_a_closed_list() -> None:
    """Pinned, because widening it is a policy change: every word added here is a state in
    which prospect data may be purged."""
    assert FINISHED_STATUSES == {"closed", "completed", "finished", "done", "archived"}


def test_one_finished_and_one_draft_blocks_and_names_only_the_draft(tmp_path: Path) -> None:
    export = _aged_export(tmp_path)
    _campaign(tmp_path, "spring-outbound", 'status = "closed"')
    _campaign(tmp_path, "autumn-outbound", 'status = "draft"')

    with pytest.raises(UnfinishedCampaignRefusal) as exc:
        sweep_stale_pii(PROFILE, apply=True, content_root=tmp_path, now=NOW)

    assert exc.value.slugs == ("autumn-outbound",)
    assert "spring-outbound" not in str(exc.value)
    assert export.exists()


# --------------------------------------------------------------------------- no manifest at all


def test_no_manifest_but_a_sequence_export_blocks_apply(tmp_path: Path) -> None:
    """Campaign work exists and nothing says it is finished — a campaign run without a manifest
    must not be the one case the gate cannot see."""
    export = _aged_export(tmp_path)
    _sequence_export(tmp_path)
    before = _tree(tmp_path)

    with pytest.raises(UnfinishedCampaignRefusal) as exc:
        sweep_stale_pii(PROFILE, apply=True, content_root=tmp_path, now=NOW)

    message = str(exc.value)
    assert exc.value.slugs == ()
    assert "no campaign manifest" in message and "1 export" in message
    assert "nothing says it is finished" in message
    assert 'status = "closed"' in message
    assert export.exists() and _tree(tmp_path) == before


def test_a_linked_sequence_export_counts_without_being_followed(tmp_path: Path) -> None:
    export = _aged_export(tmp_path)
    target = tmp_path / "elsewhere.csv"
    target.write_text("email\n", encoding="utf-8")
    link = tmp_path / PROFILE / "prospects" / "sequences" / "linked-hubspot.csv"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)

    with pytest.raises(UnfinishedCampaignRefusal):
        sweep_stale_pii(PROFILE, apply=True, content_root=tmp_path, now=NOW)

    assert export.exists()


def test_a_linked_sequences_folder_blocks_rather_than_reading_as_empty(tmp_path: Path) -> None:
    """The folder is never followed, so its contents are unknown — and "could not look" must
    not be reported as "nothing there"."""
    export = _aged_export(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / PROFILE / "prospects" / "sequences").symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(UnfinishedCampaignRefusal):
        sweep_stale_pii(PROFILE, apply=True, content_root=tmp_path, now=NOW)

    assert export.exists()


def test_no_manifest_and_no_sequence_export_permits_apply(tmp_path: Path) -> None:
    """Nothing campaign-shaped exists, so there is nothing to finish."""
    export = _aged_export(tmp_path)
    (tmp_path / PROFILE / "prospects" / "sequences" / ".pool").mkdir(parents=True)

    res = sweep_stale_pii(PROFILE, apply=True, content_root=tmp_path, now=NOW)

    assert len(res.archived) == 1
    assert not export.exists()


# --------------------------------------------------------------------------- unclaimed exports
#
# Review finding M3 (2026-09-21): the exports rule was consulted only with ZERO manifests, so one
# old `closed` manifest switched it off for the whole profile. A finished manifest speaks only for
# the files its `roster_globs` claim.


def _claims(*globs: str) -> str:
    return 'status = "closed"\nroster_globs = [' + ", ".join(f'"{g}"' for g in globs) + "]"


def test_a_finished_manifest_does_not_cover_an_export_it_does_not_claim(tmp_path: Path) -> None:
    """Last season's closed campaign says nothing about this season's list."""
    export = _aged_export(tmp_path)
    _sequence_export(tmp_path, "autumn-outbound-hubspot.csv")
    _campaign(tmp_path, "spring-outbound", 'status = "closed"')
    before = _tree(tmp_path)

    with pytest.raises(UnfinishedCampaignRefusal) as exc:
        sweep_stale_pii(PROFILE, apply=True, content_root=tmp_path, now=NOW)

    message = str(exc.value)
    assert exc.value.slugs == ()
    assert "autumn-outbound-hubspot.csv" in message, "the refusal names the unclaimed file"
    assert "roster_globs" in message and "close the campaign" in message, "and says how to proceed"
    assert export.exists() and _tree(tmp_path) == before


def test_a_finished_manifest_that_claims_every_export_permits_apply(tmp_path: Path) -> None:
    """Positive control for the refusal above: same tree, plus the claim."""
    export = _aged_export(tmp_path)
    _sequence_export(tmp_path, "autumn-outbound-hubspot.csv")
    _campaign(tmp_path, "spring-outbound", _claims("sequences/autumn-outbound-hubspot.csv"))

    gate = campaign_gate(PROFILE, tmp_path)
    assert (gate.blocks_apply, gate.unclaimed_exports) == (False, ())
    sweep_stale_pii(PROFILE, apply=True, content_root=tmp_path, now=NOW)

    assert not export.exists()


def test_a_glob_claims_only_what_it_matches(tmp_path: Path) -> None:
    _aged_export(tmp_path)
    _sequence_export(tmp_path, "spring-sg-hubspot.csv")
    stray = _sequence_export(tmp_path, "autumn-au-hubspot.csv")
    _campaign(tmp_path, "spring-outbound", _claims("sequences/spring-*.csv"))

    gate = campaign_gate(PROFILE, tmp_path)

    assert gate.unclaimed_exports == (stray,)
    assert gate.blocks_apply is True


@pytest.mark.parametrize("name", ["ready-to-load.csv", "ready-to-load-sg-builders.csv"])
def test_a_ready_to_load_working_file_is_campaign_work_too(tmp_path: Path, name: str) -> None:
    """Every tenant has these; a load file is a campaign about to happen. Fail closed."""
    export = _aged_export(tmp_path)
    _sequence_export(tmp_path, name)
    _campaign(tmp_path, "spring-outbound", 'status = "closed"')

    with pytest.raises(UnfinishedCampaignRefusal) as exc:
        sweep_stale_pii(PROFILE, apply=True, content_root=tmp_path, now=NOW)

    assert name in str(exc.value)
    assert export.exists()


def test_an_export_claimed_only_by_an_unfinished_campaign_still_blocks(tmp_path: Path) -> None:
    _aged_export(tmp_path)
    _sequence_export(tmp_path, "autumn-outbound-hubspot.csv")
    _campaign(tmp_path, "spring-outbound", 'status = "closed"')
    _campaign(
        tmp_path,
        "autumn-outbound",
        'status = "paused"\nroster_globs = ["sequences/autumn-outbound-hubspot.csv"]',
    )

    with pytest.raises(UnfinishedCampaignRefusal) as exc:
        sweep_stale_pii(PROFILE, apply=True, content_root=tmp_path, now=NOW)

    assert exc.value.slugs == ("autumn-outbound",)
    assert "autumn-outbound (status: paused)" in str(exc.value)


def test_a_linked_sequences_folder_cannot_be_claimed_away(tmp_path: Path) -> None:
    """The folder was never looked inside, so no glob — not even one naming the folder — may
    declare its contents finished."""
    _aged_export(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "autumn-outbound-hubspot.csv").write_text("email\n", encoding="utf-8")
    (tmp_path / PROFILE / "prospects").mkdir(parents=True, exist_ok=True)
    (tmp_path / PROFILE / "prospects" / "sequences").symlink_to(elsewhere, target_is_directory=True)
    _campaign(tmp_path, "spring-outbound", _claims("sequences", "sequences/*.csv", "*"))

    assert campaign_gate(PROFILE, tmp_path).blocks_apply is True


@pytest.mark.parametrize(
    "globs_line",
    ['roster_globs = "sequences/*.csv"', 'roster_globs = ["/etc/*.csv"]', 'roster_globs = [""]'],
    ids=["not-a-list", "absolute", "empty"],
)
def test_a_malformed_claim_claims_nothing(tmp_path: Path, globs_line: str) -> None:
    _aged_export(tmp_path)
    stray = _sequence_export(tmp_path)
    _campaign(tmp_path, "spring-outbound", f'status = "closed"\n{globs_line}')

    assert campaign_gate(PROFILE, tmp_path).unclaimed_exports == (stray,)


# --------------------------------------------------------------------------- the plan says so


def test_the_plan_opens_with_each_campaigns_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    _aged_export(tmp_path)
    _campaign(tmp_path, "spring-outbound", 'status = "closed"')
    _campaign(tmp_path, "autumn-outbound", 'status = "paused"')

    assert main(["--profile", PROFILE], now=NOW) == 0

    out = capsys.readouterr().out
    assert "autumn-outbound (status: paused) — UNFINISHED" in out
    assert "spring-outbound (status: closed) — finished" in out
    assert "--apply would be REFUSED" in out
    assert out.index("autumn-outbound") < out.index("Export CSVs"), "the verdict comes first"


def test_the_plan_reports_a_profile_with_no_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    _aged_export(tmp_path)
    _sequence_export(tmp_path, "one-hubspot.csv")
    _sequence_export(tmp_path, "two-hubspot.csv")

    assert main(["--profile", PROFILE], now=NOW) == 0

    out = capsys.readouterr().out
    assert "no campaign manifest, 2 sequence export(s)" in out
    assert "--apply would be REFUSED" in out


def test_the_plan_names_the_exports_no_finished_campaign_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    _aged_export(tmp_path)
    _sequence_export(tmp_path, "spring-sg-hubspot.csv")
    _sequence_export(tmp_path, "ready-to-load.csv")
    _campaign(tmp_path, "spring-outbound", _claims("sequences/spring-*.csv"))

    assert main(["--profile", PROFILE], now=NOW) == 0

    out = capsys.readouterr().out
    assert "spring-outbound (status: closed) — finished" in out
    assert "2 export(s), 1 not claimed by a finished campaign's roster_globs" in out
    assert "unclaimed: ready-to-load.csv" in out
    assert "--apply would be REFUSED — 1 sequence export(s) not claimed" in out


def test_the_plan_says_when_apply_would_be_permitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    _aged_export(tmp_path)

    assert main(["--profile", PROFILE], now=NOW) == 0

    out = capsys.readouterr().out
    assert "no campaign manifest, 0 sequence export(s)" in out
    assert "--apply would be permitted" in out


def test_the_gate_is_stricter_than_the_status_pages_open_scope(tmp_path: Path) -> None:
    """The two answer different questions and must be ALLOWED to disagree: a paused campaign is
    not "open" on the status page, and it is not finished here."""
    from gtm_core.campaigns_dashboard import _load_manifests
    from gtm_core.email_campaign_dashboard.scope import OPEN_STATUSES

    _campaign(tmp_path, "autumn-outbound", 'status = "paused"')

    manifests = _load_manifests(PROFILE, tmp_path)
    assert [m["status"] in OPEN_STATUSES for m in manifests] == [False]
    assert campaign_gate(PROFILE, tmp_path).blocks_apply is True
