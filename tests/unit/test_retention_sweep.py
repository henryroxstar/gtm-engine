"""The retention sweep's POLICY: manual, report-first, campaign-aware, exclusion-preserving.

The sweep was wired into the routine ``consolidate`` command and ran unconditionally, silently,
with its result discarded. The product owner's decision replaces that: nothing is purged while a
customer campaign is unfinished, and purging is something an operator asks for — never a side
effect. These tests pin that policy; ``tests/test_retention_sweep.py`` pins the CSV mechanics.

Every fixture is fictional and lives under ``tmp_path``. The clock is injected (``now=``) so no
assertion depends on the day the suite runs.
"""

from __future__ import annotations

import gzip
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gtm_core.locks import LockBusy, profile_lock
from gtm_core.retention_sweep import UnfinishedCampaignRefusal, main, sweep_stale_pii

PROFILE = "test-tenant"
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


def _age(path: Path, days: float) -> None:
    stamp = NOW.timestamp() - days * 86400
    os.utime(path, (stamp, stamp), follow_symlinks=False)


def _aged_account_file(root: Path, slug: str, days: float = 70, name: str = "dossier.md") -> Path:
    folder = root / PROFILE / "accounts" / slug
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("# Dossier\nSensitive PII notes\n", encoding="utf-8")
    _age(path, days)
    return path


def _aged_export(
    root: Path, name: str = "prospects-20260801-hubspot.csv", days: float = 10
) -> Path:
    folder = root / PROFILE / "prospects"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("first,last,email\nAvery,Quill,avery.quill@northwind.example\n", "utf-8")
    _age(path, days)
    return path


def _ledger(root: Path, items: list[dict]) -> Path:
    path = root / PROFILE / "prospects" / "latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"kind": "prospects", "profile": PROFILE, "items": items}), encoding="utf-8"
    )
    return path


def _campaign(root: Path, slug: str, status: str, extra: str = "") -> Path:
    folder = root / PROFILE / "plans" / "campaigns"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{slug}.campaign.toml"
    path.write_text(f'slug = "{slug}"\nstatus = "{status}"\n{extra}', encoding="utf-8")
    return path


def _tree(root: Path) -> dict[str, bytes]:
    """Every file under the profile, by relative path — the "nothing was written" oracle."""
    base = root / PROFILE
    return {
        str(p.relative_to(base)): p.read_bytes()
        for p in sorted(base.rglob("*"))
        if p.is_file() and not p.is_symlink()
    }


# --------------------------------------------------------------------------- report first


def test_the_default_is_a_plan_that_writes_nothing(tmp_path: Path) -> None:
    dossier = _aged_account_file(tmp_path, "northwind-robotics")
    export = _aged_export(tmp_path)
    _ledger(
        tmp_path, [{"id": "old", "company": "Old Co", "status": "new", "added_at": "2026-06-01"}]
    )
    before = _tree(tmp_path)

    res = sweep_stale_pii(PROFILE, content_root=tmp_path, include_ledger=True, now=NOW)

    assert res.applied is False
    assert res.archived == [export]
    assert res.archived_dossiers == [dossier]
    assert res.purged_accounts == ["Old Co"]
    assert _tree(tmp_path) == before, "a plan must not create, change or remove any file"


def test_apply_archives_aged_account_files(tmp_path: Path) -> None:
    dossier = _aged_account_file(tmp_path, "northwind-robotics")

    res = sweep_stale_pii(PROFILE, dossier_ttl_days=60, apply=True, content_root=tmp_path, now=NOW)

    assert res.applied is True
    assert len(res.archived_dossiers) == 1
    assert not dossier.exists()
    assert not dossier.parent.exists(), "an emptied account folder is pruned"
    archived = list(
        (tmp_path / PROFILE / "prospects" / ".archive" / "accounts" / "northwind-robotics").glob(
            "*.gz"
        )
    )
    assert len(archived) == 1
    with gzip.open(archived[0], "rt", encoding="utf-8") as fh:
        assert "Sensitive PII notes" in fh.read()


def test_apply_preserves_fresh_account_files(tmp_path: Path) -> None:
    dossier = _aged_account_file(tmp_path, "tailspin-health", days=5)

    res = sweep_stale_pii(PROFILE, dossier_ttl_days=60, apply=True, content_root=tmp_path, now=NOW)

    assert res.archived_dossiers == []
    assert dossier.exists()


# --------------------------------------------------------------------------- campaign-aware
# What counts as FINISHED — draft, paused, blank, unknown, no manifest — is pinned in
# tests/unit/test_retention_campaign_gate.py. These are the sweep's side of that gate.


def test_apply_is_refused_while_a_campaign_is_unfinished(tmp_path: Path) -> None:
    _aged_account_file(tmp_path, "northwind-robotics")
    _aged_export(tmp_path)
    _campaign(tmp_path, "autumn-outbound", "active")
    _campaign(tmp_path, "spring-outbound", "closed")
    before = _tree(tmp_path)

    with pytest.raises(UnfinishedCampaignRefusal) as exc:
        sweep_stale_pii(PROFILE, apply=True, content_root=tmp_path, now=NOW)

    assert exc.value.slugs == ("autumn-outbound",)
    assert _tree(tmp_path) == before, "a refusal writes nothing — not an archive, not a lock"


def test_an_unfinished_campaign_does_not_block_the_plan(tmp_path: Path) -> None:
    export = _aged_export(tmp_path)
    _campaign(tmp_path, "autumn-outbound", "draft")

    res = sweep_stale_pii(PROFILE, content_root=tmp_path, now=NOW)

    assert res.archived == [export]
    assert [c.label for c in res.campaigns.unfinished] == ["autumn-outbound (status: draft)"]


def test_apply_proceeds_once_every_campaign_is_closed(tmp_path: Path) -> None:
    export = _aged_export(tmp_path)
    _campaign(tmp_path, "spring-outbound", "closed")

    res = sweep_stale_pii(PROFILE, apply=True, content_root=tmp_path, now=NOW)

    assert len(res.archived) == 1
    assert not export.exists()


def test_a_manifest_that_cannot_be_read_counts_as_unfinished(tmp_path: Path) -> None:
    """The manifest loader skips a malformed file, which for a purge gate would fail OPEN: a
    typo in the one running campaign's manifest would make it invisible and permit the purge.
    A finished sibling beside it must not rescue it either."""
    export = _aged_export(tmp_path)
    _campaign(tmp_path, "spring-outbound", "closed")
    folder = tmp_path / PROFILE / "plans" / "campaigns"
    (folder / "autumn-outbound.campaign.toml").write_text('slug = "autumn-outbound\n', "utf-8")

    with pytest.raises(UnfinishedCampaignRefusal) as exc:
        sweep_stale_pii(PROFILE, apply=True, content_root=tmp_path, now=NOW)

    assert "autumn-outbound.campaign.toml" in str(exc.value)
    assert export.exists()


def test_a_roster_export_named_by_any_campaign_is_never_archived(tmp_path: Path) -> None:
    """Finished as well as running: a closed campaign's roster is what its status page is built
    from, and `--force` is a statement about staging files, not about a campaign's record."""
    roster = _aged_export(tmp_path, "prospects-20260801-hubspot.csv")
    other = _aged_export(tmp_path, "prospects-20260715-hubspot.csv")
    _campaign(
        tmp_path, "spring-outbound", "closed", 'roster_globs = ["prospects-20260801-*.csv"]\n'
    )

    res = sweep_stale_pii(PROFILE, apply=True, force=True, content_root=tmp_path, now=NOW)

    assert roster.exists()
    assert not other.exists()
    assert [(p, "spring-outbound" in why) for p, why in res.guarded] == [(roster, True)]


# --------------------------------------------------------------------------- the ledger


def _old(status: str, company: str) -> dict:
    return {"id": company.lower().replace(" ", "-"), "company": company, "status": status,
            "added_at": "2025-01-01T00:00:00Z"}  # fmt: skip


def test_the_ledger_is_not_examined_unless_asked(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, [_old("new", "Old Co")])
    before = ledger.read_bytes()

    res = sweep_stale_pii(PROFILE, apply=True, content_root=tmp_path, now=NOW)

    assert res.purged_accounts == []
    assert ledger.read_bytes() == before
    assert not (ledger.parent / ".snapshots").exists()


def test_an_exclusion_row_survives_apply_include_ledger(tmp_path: Path) -> None:
    """A retired or replied row is the ONLY home of an account-level exclusion. Deleting it does
    not forget the account — it re-admits it, on the next run, as a brand-new prospect."""
    ledger = _ledger(
        tmp_path,
        [
            _old("do-not-contact", "Contoso Freight"),
            _old("disqualified", "Fabrikam Staffing"),
            _old("closed-lost", "Tailspin Health"),
            _old("replied", "Northwind Robotics"),
            _old("customer", "Wingtip Logistics"),
            _old("new", "Old Co"),
        ],
    )

    res = sweep_stale_pii(PROFILE, apply=True, include_ledger=True, content_root=tmp_path, now=NOW)

    assert res.purged_accounts == ["Old Co"]
    kept = {it["company"]: it["status"] for it in json.loads(ledger.read_text("utf-8"))["items"]}
    assert kept == {
        "Contoso Freight": "do-not-contact",
        "Fabrikam Staffing": "disqualified",
        "Tailspin Health": "closed-lost",
        "Northwind Robotics": "replied",
        "Wingtip Logistics": "customer",
    }
    assert len(list((ledger.parent / ".snapshots").glob("latest-*.json"))) == 1


def test_a_rows_age_comes_only_from_the_date_the_code_maintains(tmp_path: Path) -> None:
    """`signal_observed` dates the NEWS, and the skill admits a 210-day-old signal on the day it
    is found; the file's mtime dates the last write to ANY row. Neither is this row's age."""
    ledger = _ledger(
        tmp_path,
        [
            {"id": "a", "company": "Fresh Row Old Signal", "status": "new",
             "added_at": "2026-09-18T12:00:00Z", "signal_observed": "2026-02-01"},
            {"id": "b", "company": "No Maintained Date", "status": "new",
             "signal_observed": "2026-02-01", "last_touched": "2026-02-01"},
            {"id": "c", "company": "Unparseable Date", "status": "new", "added_at": "last spring"},
        ],
    )  # fmt: skip
    _age(ledger, 400)
    before = ledger.read_bytes()

    res = sweep_stale_pii(PROFILE, apply=True, include_ledger=True, content_root=tmp_path, now=NOW)

    assert res.purged_accounts == []
    assert res.undatable == ["No Maintained Date", "Unparseable Date"]
    assert ledger.read_bytes() == before


# --------------------------------------------------------------------------- account files


def test_an_engaged_accounts_files_are_not_archived(tmp_path: Path) -> None:
    engaged = _aged_account_file(tmp_path, "northwind-robotics")
    idle = _aged_account_file(tmp_path, "old-co")
    _ledger(
        tmp_path,
        [
            {"id": "northwind-robotics", "company": "Northwind Robotics", "status": "meeting"},
            {"id": "old-co", "company": "Old Co", "status": "new"},
        ],
    )

    res = sweep_stale_pii(PROFILE, apply=True, content_root=tmp_path, now=NOW)

    assert engaged.exists()
    assert not idle.exists()
    assert [p for p, why in res.guarded if "meeting" in why] == [engaged.parent]


def test_a_symlinked_account_folder_is_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    victim = outside / "notes.md"
    victim.write_text("not this tenant's file\n", encoding="utf-8")
    _age(victim, 400)
    accounts = tmp_path / PROFILE / "accounts"
    accounts.mkdir(parents=True)
    (accounts / "contoso-freight").symlink_to(outside, target_is_directory=True)

    res = sweep_stale_pii(PROFILE, apply=True, content_root=tmp_path, now=NOW)

    assert victim.exists()
    assert res.archived_dossiers == []
    assert (accounts / "contoso-freight").is_symlink(), "the link itself is left alone too"
    assert any("symlink" in why for _, why in res.skipped)
    assert not (tmp_path / PROFILE / "prospects" / ".archive").exists()


def test_a_symlinked_file_is_not_followed(tmp_path: Path) -> None:
    victim = tmp_path / "elsewhere.csv"
    victim.write_text("email\navery.quill@northwind.example\n", encoding="utf-8")
    _age(victim, 400)
    prospects = tmp_path / PROFILE / "prospects"
    prospects.mkdir(parents=True)
    link = prospects / "prospects-20260801-hubspot.csv"
    link.symlink_to(victim)
    _age(link, 400)  # the LINK must be stale too, or "fresh" skips it and this proves nothing

    res = sweep_stale_pii(PROFILE, apply=True, content_root=tmp_path, now=NOW)

    assert victim.exists() and link.is_symlink()
    assert res.archived == []
    assert not (prospects / ".archive").exists(), "the target's bytes were not copied either"


def test_an_archive_folder_linked_outside_the_tenant_receives_nothing(tmp_path: Path) -> None:
    """The write side of the same rule: the gzip copy is cleartext PII, so where it LANDS is
    confined too — and a file that could not be archived safely is never deleted."""
    export = _aged_export(tmp_path)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (tmp_path / PROFILE / "prospects" / ".archive").symlink_to(outside, target_is_directory=True)

    res = sweep_stale_pii(PROFILE, apply=True, content_root=tmp_path, now=NOW)

    assert export.exists()
    assert list(outside.iterdir()) == []
    assert [p for p, _ in res.errors] == [export]


def test_an_unreadable_ledger_moves_no_account_file(tmp_path: Path) -> None:
    """With no readable ledger, "which accounts are in a live relationship" has no answer — and
    the old answer to a question with no answer was "none of them, archive everything"."""
    dossier = _aged_account_file(tmp_path, "northwind-robotics")
    ledger = tmp_path / PROFILE / "prospects" / "latest.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("{not json", encoding="utf-8")

    res = sweep_stale_pii(PROFILE, apply=True, content_root=tmp_path, now=NOW)

    assert dossier.exists()
    assert [p for p, _ in res.errors] == [ledger]


def test_apply_does_not_run_beside_another_writer(tmp_path: Path) -> None:
    export = _aged_export(tmp_path)

    with profile_lock(tmp_path, PROFILE), pytest.raises(LockBusy):
        sweep_stale_pii(PROFILE, apply=True, content_root=tmp_path, now=NOW)

    assert export.exists()


# --------------------------------------------------------------------------- the CLI


def test_cli_without_flags_prints_a_plan_and_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    export = _aged_export(tmp_path)
    dossier = _aged_account_file(tmp_path, "old-co")
    before = _tree(tmp_path)

    assert main(["--profile", PROFILE], now=NOW) == 0

    out = capsys.readouterr().out
    assert "DRY RUN" in out and "--apply" in out
    assert str(export) in out and str(dossier) in out
    assert "2 file(s)" in out
    assert _tree(tmp_path) == before


def test_cli_dry_run_flag_is_an_alias_of_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    _aged_export(tmp_path)
    before = _tree(tmp_path)

    assert main(["--profile", PROFILE, "--dossier-ttl-days", "60", "--dry-run"], now=NOW) == 0
    assert _tree(tmp_path) == before


def test_cli_apply_with_an_unfinished_campaign_exits_2_and_names_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    _aged_export(tmp_path)
    _campaign(tmp_path, "autumn-outbound", "paused")
    before = _tree(tmp_path)

    assert main(["--profile", PROFILE, "--apply", "--force", "--include-ledger"], now=NOW) == 2

    err = capsys.readouterr().err
    assert "autumn-outbound (status: paused)" in err
    assert "nothing is purged until" in err.lower()
    assert _tree(tmp_path) == before


@pytest.mark.parametrize("flag", ["--ignore-campaigns", "--override", "--yes", "--force-apply"])
def test_cli_has_no_flag_that_overrides_an_unfinished_campaign(flag: str) -> None:
    """§R13: the ban lives in code. An override flag is the ban with a door in it."""
    with pytest.raises(SystemExit):
        main(["--profile", PROFILE, "--apply", flag])


def test_cli_apply_says_archived_not_erased(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    _aged_export(tmp_path)

    assert main(["--profile", PROFILE, "--apply"], now=NOW) == 0

    out = capsys.readouterr().out
    archive = tmp_path / PROFILE / "prospects" / ".archive"
    assert f"archived to {archive}" in out
    assert "(gzip, still cleartext) — not erased" in out


def test_cli_exits_non_zero_when_a_file_could_not_be_archived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    export = _aged_export(tmp_path)
    # The archive path is occupied by a FILE, so the archive directory cannot be created.
    (tmp_path / PROFILE / "prospects" / ".archive").write_text("in the way\n", encoding="utf-8")

    assert main(["--profile", PROFILE, "--apply"], now=NOW) == 1

    assert export.exists(), "a file that could not be archived is never deleted"
    assert str(export) in capsys.readouterr().err
