"""`consolidate` and a retired account: the exclusion holds on ANY identity key, and the ledger
row that carries it is never swept away as a side effect.

Two defects from the 2026-09-21 prospect-skill audit (PSK-014, PSK-015):

* The send-list build reduced each pooled row to ONE account key (domain-first) while the retired
  set held every key of the ledger item. A ledger item that lost its ``domain`` on a minimal
  re-discovery stopped matching, and a do-not-contact account's contact was listed as ready.
* `consolidate` ran a destructive retention sweep as its last step. The ledger row is the only
  home of an account-level exclusion, so purging it re-admits the account on the next run.

All data is fictional and lives under ``tmp_path``.
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from gtm_core import prospects_consolidate as pc
from gtm_core.prospects_consolidate.accounts import LedgerUnreadableError

PROFILE = "qa-tenant"
EXPORT_HEADER = ["First Name", "Last Name", "Email", "Company", "Company Domain Name",
                 "Email Status"]  # fmt: skip


@pytest.fixture(autouse=True)
def _no_real_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The market gate reads PROFILE.md; point it at an empty tree so no real tenant is consulted.
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))


def _prospects(root: Path) -> Path:
    return root / PROFILE / "prospects"


def _export(
    root: Path, rows: list[list[str]], name: str = "prospects-20260827-hubspot.csv"
) -> Path:
    path = _prospects(root) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(EXPORT_HEADER)
        writer.writerows(rows)
    return path


def _ledger(root: Path, items: list[dict]) -> Path:
    path = _prospects(root) / "latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"kind": "prospects", "profile": PROFILE, "items": items}), encoding="utf-8"
    )
    return path


def _ready(root: Path) -> list[str]:
    path = pc.ready_to_load_path(PROFILE, root)
    with path.open(encoding="utf-8") as fh:
        return [row["email"] for row in csv.DictReader(fh)]


ROWAN = ["Rowan", "Pike", "rowan.pike@contosofreight.example", "Contoso Freight",
         "contosofreight.example", "verified"]  # fmt: skip


# --------------------------------------------------------------------------- PSK-014


def test_a_retired_account_that_lost_its_domain_is_still_excluded_by_company(
    tmp_path: Path,
) -> None:
    _export(tmp_path, [ROWAN])
    _ledger(tmp_path, [{"company": "Contoso  Freight", "domain": None, "status": "do-not-contact"}])

    res = pc.consolidate(PROFILE, content_root=tmp_path)

    assert res["disqualified_excluded"] == 1
    assert _ready(tmp_path) == []


def test_a_retired_account_is_excluded_by_its_slug_id_alone(tmp_path: Path) -> None:
    """The ledger spells the company differently, so only the account slug still joins them."""
    _export(tmp_path, [ROWAN])
    _ledger(
        tmp_path,
        [{"id": "contoso-freight", "company": "Contoso Freight, Inc.", "status": "closed-lost"}],
    )

    res = pc.consolidate(PROFILE, content_root=tmp_path)

    assert res["disqualified_excluded"] == 1
    assert _ready(tmp_path) == []


def test_a_retired_account_is_still_excluded_by_domain(tmp_path: Path) -> None:
    """Positive control for the path that already worked."""
    _export(tmp_path, [ROWAN])
    _ledger(
        tmp_path,
        [{"company": "CF Group", "domain": "contosofreight.example", "status": "disqualified"}],
    )

    assert pc.consolidate(PROFILE, content_root=tmp_path)["disqualified_excluded"] == 1


def test_a_company_whose_name_merely_contains_a_retired_name_is_not_excluded(
    tmp_path: Path,
) -> None:
    """Negative control: the match is exact on the normalised name, never a substring — widening
    the key set must not let one retirement take out a neighbour."""
    _export(
        tmp_path,
        [
            ["Avery", "Quill", "avery.quill@contosofreightlines.example", "Contoso Freight Lines",
             "contosofreightlines.example", "verified"],
            ["Jules", "Marsh", "jules.marsh@contoso.example", "Contoso",
             "contoso.example", "verified"],
        ],
    )  # fmt: skip
    _ledger(tmp_path, [{"id": "contoso-freight", "company": "Contoso Freight",
                        "status": "do-not-contact"}])  # fmt: skip

    res = pc.consolidate(PROFILE, content_root=tmp_path)

    assert res["disqualified_excluded"] == 0
    assert sorted(_ready(tmp_path)) == [
        "avery.quill@contosofreightlines.example",
        "jules.marsh@contoso.example",
    ]


# --------------------------------------------------------------------------- PSK-015


def test_consolidate_never_archives_or_purges_anything(tmp_path: Path) -> None:
    """Everything here is past every retention threshold the sweep knows. Consolidating twice
    must leave all of it exactly where it was: retention is the operator's explicit command."""
    export = _export(tmp_path, [ROWAN])
    dossier = tmp_path / PROFILE / "accounts" / "contoso-freight" / "dossier.md"
    dossier.parent.mkdir(parents=True)
    dossier.write_text("# Contoso Freight\n", encoding="utf-8")
    ledger = _ledger(
        tmp_path,
        [{"id": "contoso-freight", "company": "Contoso Freight",
          "domain": "contosofreight.example", "status": "do-not-contact",
          "signal_observed": "2026-02-01", "added_at": "2025-01-01T00:00:00Z"}],
    )  # fmt: skip
    ancient = time.time() - 400 * 86400
    for path in (export, dossier, ledger):
        os.utime(path, (ancient, ancient))
    before = ledger.read_bytes()

    pc.consolidate(PROFILE, content_root=tmp_path)
    pc.consolidate(PROFILE, content_root=tmp_path)

    assert not (_prospects(tmp_path) / ".archive").exists()
    assert export.exists() and dossier.exists()
    assert ledger.read_bytes() == before
    assert _ready(tmp_path) == []


# --------------------------------------------------------------------------- H1: fail closed
#
# Review finding H1 (2026-09-21): every ledger read in `accounts.py` answered an unreadable
# `latest.json` with an empty index, so a truncated ledger retired nobody — exit 0, and the
# do-not-contact contact back in `ready-to-load.csv`.


def _cli(root: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, GTM_CONTENT_ROOT=str(root), GTM_PROFILES_ROOT=str(root / "profiles"))
    return subprocess.run(
        [sys.executable, "-m", "gtm_core.prospects_consolidate", "consolidate",
         "--profile", PROFILE],
        cwd=Path(__file__).resolve().parents[2], env=env, capture_output=True, text=True,
        timeout=120,
    )  # fmt: skip


def _outputs(root: Path) -> dict[str, bytes]:
    base = _prospects(root) / "sequences"
    return {str(p.relative_to(base)): p.read_bytes() for p in sorted(base.rglob("*.csv"))}


def test_the_cli_aborts_on_a_truncated_ledger_and_writes_nothing(tmp_path: Path) -> None:
    second = ["Ines", "Vale", "ines.vale@contosofreight.example", "Contoso Freight",
              "contosofreight.example", "verified"]  # fmt: skip
    _export(tmp_path, [ROWAN])
    ledger = _ledger(
        tmp_path,
        [{"id": "contoso-freight", "company": "Contoso Freight",
          "domain": "contosofreight.example", "status": "do-not-contact"}],
    )  # fmt: skip

    healthy = _cli(tmp_path)  # positive control: readable ledger, account excluded, exit 0
    assert healthy.returncode == 0, healthy.stderr
    assert _ready(tmp_path) == []
    before = _outputs(tmp_path)

    ledger.write_text(ledger.read_text(encoding="utf-8")[:-40], encoding="utf-8")  # a cut write
    _export(tmp_path, [second], name="prospects-20260828-hubspot.csv")  # new work to fold in
    aborted = _cli(tmp_path)

    assert aborted.returncode != 0
    abort_lines = [ln for ln in aborted.stderr.splitlines() if "ABORTED" in ln]
    assert len(abort_lines) == 1, aborted.stderr
    assert abort_lines[0].startswith("ABORTED: account ledger unreadable")
    assert "latest.json" in abort_lines[0] and "do-not-contact" in abort_lines[0]
    assert "Traceback" not in aborted.stderr
    assert aborted.stdout.strip() == "", "no result JSON for a build that did not happen"
    assert _outputs(tmp_path) == before, "no output file was created or rewritten"
    assert _ready(tmp_path) == []


def test_an_absent_ledger_is_a_first_run_not_an_abort(tmp_path: Path) -> None:
    """`load_latest`'s contract: absent = an empty ledger. First runs must still work."""
    _export(tmp_path, [ROWAN])

    done = _cli(tmp_path)

    assert done.returncode == 0, done.stderr
    assert _ready(tmp_path) == [ROWAN[2]]


@pytest.mark.parametrize(
    "text",
    ['{"items": [{"company": "Contoso Freight", "status": "do-not-con', "[]",
     '{"items": {"a": 1}}', '{"items": ["not-an-object"]}', ""],
    ids=["truncated", "not-an-object", "items-not-a-list", "item-not-an-object", "empty-file"],
)  # fmt: skip
def test_every_unreadable_ledger_shape_aborts_before_any_write(tmp_path: Path, text: str) -> None:
    _export(tmp_path, [ROWAN])
    _ledger(tmp_path, []).write_text(text, encoding="utf-8")

    with pytest.raises(LedgerUnreadableError) as exc:
        pc.consolidate(PROFILE, content_root=tmp_path)

    assert str(exc.value).startswith("ABORTED: account ledger unreadable")
    assert not (_prospects(tmp_path) / "sequences").exists()


def test_a_ledger_path_that_is_not_a_file_aborts(tmp_path: Path) -> None:
    _export(tmp_path, [ROWAN])
    (_prospects(tmp_path) / "latest.json").mkdir()

    with pytest.raises(LedgerUnreadableError):
        pc.consolidate(PROFILE, content_root=tmp_path)


# --------------------------------------------------------------------------- PS15
# Operator decision 2026-09-24: tier C gets the general email; an account the scorecard could
# not place in a target market, or could not score for missing research, is kept off the send
# list AUTOMATICALLY — by the same rule the enrollment gate reads (`account_hold_reason`).


def test_an_out_of_market_account_is_kept_off_the_list(tmp_path: Path) -> None:
    _export(tmp_path, [ROWAN])
    _ledger(tmp_path, [{"company": "Contoso Freight", "domain": "contosofreight.example",
                        "tier": "unscored", "score_missing_inputs": ["in_target_market"]}])  # fmt: skip
    assert pc.consolidate(PROFILE, content_root=tmp_path)["disqualified_excluded"] == 1
    assert _ready(tmp_path) == []


def test_an_account_not_yet_researched_is_kept_off_until_it_is(tmp_path: Path) -> None:
    _export(tmp_path, [ROWAN])
    _ledger(tmp_path, [{"company": "Contoso Freight", "domain": "contosofreight.example",
                        "tier": "unscored", "score_missing_input": "research_on_file"}])  # fmt: skip
    assert pc.consolidate(PROFILE, content_root=tmp_path)["disqualified_excluded"] == 1

    # Researched and rescored: it rejoins by itself, with nobody deciding anything.
    _ledger(tmp_path, [{"company": "Contoso Freight", "domain": "contosofreight.example",
                        "tier": "C", "score": 58}])  # fmt: skip
    assert pc.consolidate(PROFILE, content_root=tmp_path)["disqualified_excluded"] == 0
    assert _ready(tmp_path) == ["rowan.pike@contosofreight.example"]


def test_a_tier_c_account_stays_on_the_list(tmp_path: Path) -> None:
    """Negative control: tier C is a fit and must not be excluded by the new rule."""
    _export(tmp_path, [ROWAN])
    _ledger(tmp_path, [{"company": "Contoso Freight", "domain": "contosofreight.example",
                        "tier": "C", "score": 51}])  # fmt: skip
    assert pc.consolidate(PROFILE, content_root=tmp_path)["disqualified_excluded"] == 0
    assert _ready(tmp_path) == ["rowan.pike@contosofreight.example"]
