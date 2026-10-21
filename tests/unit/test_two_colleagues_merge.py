"""CM5: two colleagues' content trees, one that ran the default product and one that ran a second,
merged with git.

Nobody edits the same file: the second product's run state is ``run_state.<slug>.json``, its export
sits under ``prospects/by-product/<slug>/``, and it adds companies to ``latest.json`` with identity
fields only. So the merge is clean and the shared ledger gains no fit field from the second product.
Both colleagues writing ``latest.json`` in the same window is the case a per-product ledger (the
plan's deferred layer) exists for, so it is not asserted here.

Real ``git`` in a temp repo. Fictional data only.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gtm_core import prospects_import as pi
from gtm_core import prospects_state as ps
from gtm_core import run_state

ALPHA = {"company": "Fictional Alpha Co", "domain": "alpha-fictional.example", "tier": "A"}
DELTA = {
    "company": "Fictional Delta Co",
    "domain": "delta-fictional.example",
    "email": "bo@delta-fictional.example",
}


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "user.name=cm5", "-c", "user.email=cm5@example.invalid", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def _ok(repo: Path, *args: str) -> str:
    done = _git(repo, *args)
    assert done.returncode == 0, f"git {' '.join(args)}: {done.stdout}{done.stderr}"
    return done.stdout


@pytest.fixture
def repo(one_product_profiles, tmp_path, monkeypatch):
    root = tmp_path / "repo"
    content = root / "content"
    content.mkdir(parents=True)
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(one_product_profiles))
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content))
    _ok(root, "init", "-q", "-b", "main")
    ps.upsert_latest("realshape", [ALPHA], "seed", product="alpha")
    run_state.get_or_create_run_state("realshape", content_root=content, product="alpha")
    _ok(root, "add", "-A")
    _ok(root, "commit", "-q", "-m", "base")
    return root


def test_two_colleagues_trees_merge_cleanly_and_beta_adds_no_fit(repo):
    # colleague A: the default product's run moves ``run_state.json`` forward
    _ok(repo, "checkout", "-q", "-b", "alpha-colleague")
    assert (
        run_state.main(
            ["--profile", "realshape", "--product", "alpha", "start-stage", "--stage", "enrichment"]
        )
        == 0
    )
    _ok(repo, "add", "-A")
    _ok(repo, "commit", "-q", "-m", "alpha run")

    # colleague B: a second product's run, from the same base
    _ok(repo, "checkout", "-q", "main")
    _ok(repo, "checkout", "-q", "-b", "beta-colleague")
    assert (
        run_state.main(
            ["--profile", "realshape", "--product", "beta", "start-stage", "--stage", "enrichment"]
        )
        == 0
    )
    pi.finalize("realshape", [{**DELTA, "tier": "A"}], "run-b", product="beta", identity_only=True)
    _ok(repo, "add", "-A")
    _ok(repo, "commit", "-q", "-m", "beta run")

    merged = _git(repo, "merge", "--no-edit", "alpha-colleague")
    assert merged.returncode == 0, merged.stdout + merged.stderr

    prospects = repo / "content" / "realshape" / "prospects"
    assert (prospects / "run_state.json").is_file()
    assert (prospects / "run_state.beta.json").is_file()
    alpha_state = json.loads((prospects / "run_state.json").read_text())
    beta_state = json.loads((prospects / "run_state.beta.json").read_text())
    assert (
        alpha_state["stages"] != beta_state["stages"]
        or alpha_state["run_id"] != beta_state["run_id"]
    )
    assert list((prospects / "by-product" / "beta").glob("prospects-run-b-hubspot.csv"))

    rows = {r["company"]: r for r in ps.load_latest("realshape")["items"]}
    assert rows["Fictional Alpha Co"]["tier"] == "A"  # the default product's fit is intact
    assert not {"tier", "score", "verdict", "lane", "status"} & set(rows["Fictional Delta Co"])
