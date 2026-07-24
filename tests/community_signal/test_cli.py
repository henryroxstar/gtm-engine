"""CLI tests for gtm_core.community_signal.score / .render — the --profile misroute
guard and the --filters strict tenant-drop wiring (SDK-INDEPENDENT).

Drives both CLIs with --repo-root tmp_path so all state (and the guard's content-root
resolution) lands under a throwaway dir — mirrors tests/agent/test_ledger_cli.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core.community_signal.render import main as render_main
from gtm_core.community_signal.score import main as score_main

PROFILE = "demo"
OTHER_PROFILE = "acme"


def _match(filter_str: str) -> dict:
    return {
        "id": filter_str,
        "filter": filter_str,
        "item": {"backend": "reddit"},
        "analysis": {"accept": True},
    }


def _write_pull(path: Path, filters: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([_match(f) for f in filters]), encoding="utf-8")


# --- score: --profile misroute guard ------------------------------------------


def test_score_refuses_out_under_wrong_profile(tmp_path, capsys):
    pull = tmp_path / "content" / PROFILE / "community-signals" / "raw" / "pull.json"
    _write_pull(pull, ["okta identity"])
    bad_out = tmp_path / "content" / OTHER_PROFILE / "community-signals" / "metrics.json"

    with pytest.raises(SystemExit) as exc:
        score_main(
            [
                "--repo-root",
                str(tmp_path),
                "--profile",
                PROFILE,
                "--out",
                str(bad_out),
                str(pull),
            ]
        )
    assert exc.value.code == 2 or "refusing" in str(exc.value)
    assert not bad_out.exists()


def test_score_refuses_pull_under_wrong_profile(tmp_path):
    pull = tmp_path / "content" / OTHER_PROFILE / "community-signals" / "raw" / "pull.json"
    _write_pull(pull, ["okta identity"])
    out = tmp_path / "content" / PROFILE / "community-signals" / "metrics.json"

    with pytest.raises(SystemExit):
        score_main(
            ["--repo-root", str(tmp_path), "--profile", PROFILE, "--out", str(out), str(pull)]
        )
    assert not out.exists()


def test_score_succeeds_when_paths_match_profile(tmp_path, capsys):
    pull = tmp_path / "content" / PROFILE / "community-signals" / "raw" / "pull.json"
    _write_pull(pull, ["okta identity"])
    out = tmp_path / "content" / PROFILE / "community-signals" / "metrics.json"

    rc = score_main(
        ["--repo-root", str(tmp_path), "--profile", PROFILE, "--out", str(out), str(pull)]
    )
    assert rc == 0
    assert out.exists()


def test_score_without_profile_flag_is_unguarded(tmp_path):
    # Back-compat: omitting --profile skips the guard entirely, wherever --out lands.
    pull = tmp_path / "content" / OTHER_PROFILE / "community-signals" / "raw" / "pull.json"
    _write_pull(pull, ["okta identity"])
    out = tmp_path / "content" / PROFILE / "community-signals" / "metrics.json"

    rc = score_main(["--repo-root", str(tmp_path), "--out", str(out), str(pull)])
    assert rc == 0
    assert out.exists()


# --- score: --filters strict partition, end to end ----------------------------


def test_score_cli_strict_filters_drops_off_tenant(tmp_path):
    pull = tmp_path / "content" / PROFILE / "community-signals" / "raw" / "pull.json"
    _write_pull(pull, ["okta identity", "intruder filter"])
    out = tmp_path / "content" / PROFILE / "community-signals" / "metrics.json"
    filters_path = tmp_path / "syften-filters.json"
    filters_path.write_text(
        json.dumps(
            {
                "strict": True,
                "filters": {"okta identity": {"entity": "Okta", "category": "identity"}},
            }
        ),
        encoding="utf-8",
    )

    rc = score_main(
        [
            "--repo-root",
            str(tmp_path),
            "--profile",
            PROFILE,
            "--filters",
            str(filters_path),
            "--out",
            str(out),
            str(pull),
        ]
    )
    assert rc == 0
    metrics = json.loads(out.read_text())
    assert metrics["totals"]["raw"] == 1
    assert metrics["totals"]["dropped_off_tenant"] == 1


# --- render: --profile misroute guard ------------------------------------------


def _write_model(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"meta": {"title": "Test brief"}}), encoding="utf-8")


def test_render_refuses_out_under_wrong_profile(tmp_path):
    model = tmp_path / "content" / PROFILE / "community-signals" / "model.json"
    _write_model(model)
    bad_out = tmp_path / "content" / OTHER_PROFILE / "community-signals" / "report.html"

    with pytest.raises(SystemExit):
        render_main(
            [
                "--repo-root",
                str(tmp_path),
                "--profile",
                PROFILE,
                str(model),
                "--out",
                str(bad_out),
            ]
        )
    assert not bad_out.exists()


def test_render_succeeds_when_paths_match_profile(tmp_path):
    model = tmp_path / "content" / PROFILE / "community-signals" / "model.json"
    _write_model(model)
    out = tmp_path / "content" / PROFILE / "community-signals" / "report.html"

    rc = render_main(
        ["--repo-root", str(tmp_path), "--profile", PROFILE, str(model), "--out", str(out)]
    )
    assert rc == 0
    assert out.exists()
