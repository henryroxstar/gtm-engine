"""A retired account's contacts never reach `ready-to-load.csv` under a name variant.

Driven through the CLIs, the way the prospect skill runs them: `finalize` merges a thin,
domain-less re-discovery into the do-not-contact account it re-describes — and the run's CSV
must then either carry that account's identity or not carry the contact at all. Written from
the incoming item it carried neither, and `consolidate` listed the contact as ready to load.

Every tenant is a throwaway under tmp_path; all data is fictional (§R9).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from tests.integration.test_prospect_chain_e2e import Tenant, _account


@pytest.fixture
def tenant(tmp_path: Path) -> Tenant:
    return Tenant(tmp_path)


def _thin(first: str, last: str) -> dict:
    item = _account("Contoso Freight", "", first, last)
    del item["domain"]
    item["contact_email"] = f"{first}.{last}@contosofreight.example".lower()
    return item


def _run_csv(summary_stdout: str) -> list[dict]:
    with Path(json.loads(summary_stdout)["hubspot_csv"]).open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_a_do_not_contact_accounts_new_contact_is_not_exported_under_a_cleaned_name(
    tenant: Tenant,
) -> None:
    first = tenant.finalize(
        [_account("Contoso Freight, Inc.", "contosofreight.example", "Rowan", "Pike")]
    )
    assert first.returncode == 0, first.stderr
    tenant.set_status("Contoso Freight, Inc.", "do-not-contact")

    for first_name, last_name in (("Ines", "Vale"), ("Omar", "Reed")):
        run = tenant.finalize([_thin(first_name, last_name)])
        assert run.returncode == 0, run.stderr
        summary = json.loads(run.stdout)
        assert (summary["added"], summary["updated"]) == (0, 1)
        assert summary["excluded_retired"] == 1
        assert _run_csv(run.stdout) == []
        assert "NOT EXPORTED Contoso Freight, Inc." in run.stderr
        assert tenant.consolidate().returncode == 0

    (account,) = tenant.ledger()
    assert account["status"] == "do-not-contact"
    assert tenant.ready() == []


def test_a_live_accounts_new_contact_is_exported_under_the_ledgers_name_and_domain(
    tenant: Tenant,
) -> None:
    assert (
        tenant.finalize(
            [_account("Contoso Freight, Inc.", "contosofreight.example", "Rowan", "Pike")]
        ).returncode
        == 0
    )
    run = tenant.finalize([_thin("Ines", "Vale")])
    assert run.returncode == 0, run.stderr
    (row,) = _run_csv(run.stdout)
    assert row["Company Name"] == "Contoso Freight, Inc."
    assert row["Company Domain Name"] == "contosofreight.example"
    assert tenant.consolidate().returncode == 0
    assert "ines.vale@contosofreight.example" in tenant.ready()
