"""The prospect skill's deterministic spine, driven end to end the way SKILL.md cites it.

Unit tests prove each CLI on its own fixtures. None of them feeds one step's OUTPUT into the next
step's INPUT, which is where every defect below lives: the scorer keeps a row the skill says it
drops, `--standard` turns that row into `verdict: send`, and consolidate lists it as ready.

Each test builds a throwaway tenant from the de-branded `profiles/_template` under tmp_path and
confines every command with GTM_CONTENT_ROOT / GTM_PROFILES_ROOT. All data is fictional.
Each test below the divider was a strict xfail until its defect (tracked as PSK-0xx in the
2026-09-21 prospect skill E2E audit) was fixed.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PROFILE = "qa-sandbox"


class Tenant:
    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "content").mkdir()
        shutil.copytree(REPO / "profiles" / "_template", root / "profiles" / PROFILE)
        prof = root / "profiles" / PROFILE / "PROFILE.md"
        lines = prof.read_text(encoding="utf-8").splitlines()
        prof.write_text(
            "\n".join(
                "target_markets:  [Singapore]" if ln.startswith("target_markets:") else ln
                for ln in lines
            ),
            encoding="utf-8",
        )
        self.env = dict(
            os.environ,
            GTM_CONTENT_ROOT=str(root / "content"),
            GTM_PROFILES_ROOT=str(root / "profiles"),
        )
        self.prospects = root / "content" / PROFILE / "prospects"
        self._n = 0

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", *args],
            cwd=REPO,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=300,
        )

    def finalize(
        self, items: list[dict], source_run: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        self._n += 1
        path = self.root / f"items-{self._n}.json"
        path.write_text(json.dumps(items), encoding="utf-8")
        return self.run(
            "gtm_core.prospects_import", "finalize", "--profile", PROFILE, "--items", str(path),
            "--source-run", source_run or f"qa-run-{self._n}", "--standard",
        )  # fmt: skip

    def score(self, items: list[dict], dropped: bool = False) -> list[dict]:
        """The scorer's published rows — or, with `dropped`, the rows it put below threshold."""
        src, out, below = (self.root / n for n in ("cands.json", "scored.json", "dropped.json"))
        src.write_text(json.dumps(items), encoding="utf-8")
        p = self.run("gtm_core.score_prospects", "--items", str(src), "--out", str(out),
                     "--dropped-out", str(below))  # fmt: skip
        assert p.returncode == 0, p.stderr
        return json.loads((below if dropped else out).read_text(encoding="utf-8"))

    def consolidate(self) -> subprocess.CompletedProcess[str]:
        return self.run("gtm_core.prospects_consolidate", "consolidate", "--profile", PROFILE)

    def ledger(self) -> list[dict]:
        return json.loads((self.prospects / "latest.json").read_text(encoding="utf-8"))["items"]

    def set_status(self, company: str, status: str) -> None:
        path = self.prospects / "latest.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        for item in state["items"]:
            if item["company"] == company:
                item["status"] = status
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def ready(self) -> list[str]:
        path = self.prospects / "sequences" / "ready-to-load.csv"
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as fh:
            return [row["email"] for row in csv.DictReader(fh)]


@pytest.fixture
def tenant(tmp_path: Path) -> Tenant:
    return Tenant(tmp_path)


#: A row carrying a SCORE must name the rubric that produced it, or `finalize` refuses before
#: writing anything (`prospects_import.require_rubric_provenance`). Supplied on every scored
#: fixture below rather than switched off, so they keep the shape a real scored row has.
RUBRIC = {"rubric_source": "knowledge/fixture.md#rubric", "rubric_version": "2026-01-01"}


def _account(company: str, domain: str, first: str, last: str, **kw) -> dict:
    base = {
        "company": company, "segment": "startup", "market": "Singapore", "country": "Singapore",
        "score": 8, "tier": "A", "domain": domain, "contact_name": f"{first} {last}", "contact_title": "Founder",
        "contact_email": f"{first}.{last}@{domain}".lower(), "email_status": "verified", "verdict": "send",
        **RUBRIC,
    }  # fmt: skip
    base.update(kw)
    return base


RESEARCHED = {
    "why_now": "Contoso Freight opened its agent platform to partner organisations.",
    "signal_source_url": "https://contosofreight.example/news/partner-agents",
    "signal_observed": "2026-09-10",
    "signal_evidence": "Contoso Freight opened its agent platform to partner organisations.",
    "signal_subject": "Contoso Freight", "signal_agent_kind": "ai", "category_relation": "prospect",
}  # fmt: skip

MINIMAL_REDISCOVERY = {
    # SKILL.md Step 10's "minimal item": core fields only — no domain, no signal record. It still
    # carries a score, so it still has to name its rubric; "minimal" is about the research fields.
    "company": "Contoso Freight", "segment": "startup", "market": "Singapore", "score": 9, "tier": "A",
    "contact_name": "Rowan Pike", "contact_title": "Founder", **RUBRIC,
}  # fmt: skip


# --------------------------------------------------------------------------- the spine holds


def test_a_send_row_flows_from_scorer_to_ready_to_load(tenant: Tenant) -> None:
    scored = tenant.score(
        [_account("Northwind Robotics", "northwind.example", "Avery", "Quill", fit_score=8)]
    )
    assert tenant.finalize(scored).returncode == 0
    assert tenant.consolidate().returncode == 0
    assert tenant.ready() == ["avery.quill@northwind.example"]


def test_a_later_run_never_erases_an_earlier_runs_accounts(tenant: Tenant) -> None:
    tenant.finalize([_account("Northwind Robotics", "northwind.example", "Avery", "Quill")])
    tenant.finalize([_account("Tailspin Health", "tailspinhealth.example", "Jules", "Marsh")])
    assert {i["company"] for i in tenant.ledger()} == {"Northwind Robotics", "Tailspin Health"}


def test_an_operator_do_not_contact_survives_rediscovery_and_is_excluded(tenant: Tenant) -> None:
    """Positive control for the leak tests below: with identity intact the exclusion works."""
    tenant.finalize([_account("Contoso Freight", "contosofreight.example", "Rowan", "Pike")])
    tenant.set_status("Contoso Freight", "do-not-contact")
    tenant.finalize(
        [_account("Contoso Freight", "contosofreight.example", "Dana", "Holt", status="new")]
    )
    tenant.consolidate()
    assert [i["status"] for i in tenant.ledger()] == ["do-not-contact"]
    assert tenant.ready() == []


# --------------------------------------------------------------------------- regressions (were known defects)


def _below_threshold_rows() -> list[dict]:
    rows = [_account("Fabrikam Staffing", "fabrikamstaffing.example", "Sam", "Low", fit_score=2,
                     why_now="Fabrikam Staffing hires customer service agents for a new call centre.")]  # fmt: skip
    for row in rows:
        del row["score"], row["tier"], row["verdict"]
    return rows


def test_a_row_the_scorer_dropped_never_reaches_ready_to_load(tenant: Tenant) -> None:
    """PSK-008: the scorer's published output is what Step 10 finalizes; a dropped row is not in it."""
    published = tenant.score(_below_threshold_rows())
    assert published == []
    tenant.finalize(published)
    tenant.consolidate()
    assert tenant.ready() == []


def test_a_dropped_row_handed_to_finalize_anyway_is_never_listed_as_ready(tenant: Tenant) -> None:
    """PSK-011: the `--dropped-out` file is one concatenation away from the items file, and
    `--standard` used to default a `tier: drop` row to `verdict: send`."""
    dropped = tenant.score(_below_threshold_rows(), dropped=True)
    assert [row["tier"] for row in dropped] == ["drop"]
    assert tenant.finalize(dropped).returncode == 0
    tenant.consolidate()
    assert tenant.ready() == []
    assert [i.get("verdict") for i in tenant.ledger()] in ([], ["drop"])


def test_a_minimal_rediscovery_never_blanks_a_populated_field(tenant: Tenant) -> None:
    """PSK-013: nine researched fields (the signal record, domain, email) used to be blanked."""
    tenant.finalize(
        [_account("Contoso Freight", "contosofreight.example", "Rowan", "Pike", **RESEARCHED)]
    )
    before = tenant.ledger()[0]
    tenant.finalize([dict(MINIMAL_REDISCOVERY)])
    after = tenant.ledger()[0]
    blanked = sorted(
        k
        for k, v in before.items()
        if v not in ("", None, [], 0, False) and after.get(k) in ("", None, [])
    )
    assert blanked == []


def test_the_researchers_verdict_is_never_machine_overwritten(tenant: Tenant) -> None:
    """PSK-013: `send` used to become `re-angle` with a fabricated reason."""
    tenant.finalize(
        [_account("Contoso Freight", "contosofreight.example", "Rowan", "Pike", **RESEARCHED)]
    )
    tenant.finalize([dict(MINIMAL_REDISCOVERY)])
    after = tenant.ledger()[0]
    assert (after["verdict"], after.get("verdict_reason", "")) == ("send", "")


def test_a_do_not_contact_account_never_reaches_ready_to_load(tenant: Tenant) -> None:
    """PSK-014, as it happened: a minimal re-discovery blanked the ledger's domain (PSK-013)."""
    tenant.finalize([_account("Contoso Freight", "contosofreight.example", "Rowan", "Pike")])
    tenant.set_status("Contoso Freight", "do-not-contact")
    tenant.finalize([dict(MINIMAL_REDISCOVERY)])
    tenant.consolidate()
    assert tenant.ledger()[0]["status"] == "do-not-contact"
    assert tenant.ready() == []


def test_a_do_not_contact_ledger_row_with_no_domain_still_excludes_its_contacts(
    tenant: Tenant,
) -> None:
    """PSK-014 on its own: ledgers damaged BEFORE the PSK-013 fix still carry domain-less retired
    rows. The exclusion must match on any identity key, not on the domain alone."""
    tenant.finalize([_account("Contoso Freight", "contosofreight.example", "Rowan", "Pike")])
    path = tenant.prospects / "latest.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    for item in state["items"]:
        item.update(status="do-not-contact", domain="", account_id="")
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tenant.consolidate()
    assert tenant.ready() == []


def test_source_run_cannot_write_outside_the_tenant_folder(tenant: Tenant) -> None:
    """PSK-022: refused outright — a run id is a path segment, and the export is prospect PII."""
    done = tenant.finalize(
        [_account("Northwind Robotics", "northwind.example", "Avery", "Quill")],
        source_run="r1/../../../ESCAPED",
    )
    assert done.returncode != 0
    assert list(tenant.root.rglob("*ESCAPED*")) == []
    assert not (tenant.prospects / "latest.json").exists()


def test_consolidate_never_purges_a_do_not_contact_account_from_the_ledger(tenant: Tenant) -> None:
    """PSK-015: the ledger is the only home of an account-level exclusion; purging the row
    re-admits it. Consolidate is a build step — it archives and purges nothing, ever."""
    tenant.finalize([_account("Contoso Freight", "contosofreight.example", "Rowan", "Pike",
                              **{**RESEARCHED, "signal_observed": "2026-05-01"})])  # fmt: skip
    tenant.set_status("Contoso Freight", "do-not-contact")
    assert tenant.consolidate().returncode == 0
    assert tenant.consolidate().returncode == 0
    assert [i["status"] for i in tenant.ledger()] == ["do-not-contact"]
    assert tenant.ready() == []
    assert list(tenant.root.rglob(".archive")) == []
