"""Per-product run state: two products never share a resume point (PRD R-B2).

The default product keeps the legacy ``run_state.json`` — same name, same key set — so a profile
with one product reads and writes what it always did. A second product gets its own file. Before
this, one file per profile meant a Stream run resumed an unfinished Gateway run, and the only exit,
``reset``, deleted the other product's progress.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from gtm_core import run_scope, run_state
from gtm_core.prospect_paths import prospects_dir


@pytest.fixture
def env(one_product_profiles, tmp_path, monkeypatch):
    content = tmp_path / "content"
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(one_product_profiles))
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content))
    return content


_NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


class _Frozen(datetime):
    """``run_state`` computes a run's age against ``datetime.now``; the boundary needs it fixed."""

    @classmethod
    def now(cls, tz=None):
        return _NOW


def _set_start(path, when: datetime) -> None:
    data = json.loads(path.read_text())
    data["started_at"] = when.isoformat()
    path.write_text(json.dumps(data))


def test_default_product_uses_the_legacy_file_with_the_legacy_keys(env):
    run_state.get_or_create_run_state("realshape", content_root=env, product="alpha")
    legacy = prospects_dir("realshape", env) / "run_state.json"
    assert legacy.is_file()
    assert sorted(json.loads(legacy.read_text())) == [
        "completed_at",
        "mode",
        "profile",
        "run_id",
        "stages",
        "started_at",
    ]
    assert not list(prospects_dir("realshape", env).glob("run_state.*.json"))


def test_second_product_has_its_own_file(env):
    run_state.get_or_create_run_state("realshape", content_root=env, product="beta")
    assert (prospects_dir("realshape", env) / "run_state.beta.json").is_file()
    assert not (prospects_dir("realshape", env) / "run_state.json").exists()


def test_an_unfinished_default_run_never_resumes_for_the_second_product(env):
    alpha, _ = run_state.get_or_create_run_state("realshape", content_root=env, product="alpha")
    beta, resumed = run_state.get_or_create_run_state("realshape", content_root=env, product="beta")
    assert resumed is False
    assert beta.run_id != alpha.run_id


@pytest.mark.parametrize(
    ("age", "resumed"),
    [
        (timedelta(hours=47, minutes=59), True),
        (timedelta(hours=48), True),
        (timedelta(hours=48, seconds=1), False),
    ],
)
def test_the_48_hour_boundary_holds_per_product(env, monkeypatch, age, resumed):
    first, _ = run_state.get_or_create_run_state("realshape", content_root=env, product="beta")
    _set_start(prospects_dir("realshape", env) / "run_state.beta.json", _NOW - age)
    monkeypatch.setattr(run_state, "datetime", _Frozen)
    second, did_resume = run_state.get_or_create_run_state(
        "realshape", content_root=env, product="beta"
    )
    assert did_resume is resumed
    assert (second.run_id == first.run_id) is resumed


def test_reset_for_one_product_leaves_the_other_byte_identical(env, capsys):
    run_state.get_or_create_run_state("realshape", content_root=env, product="alpha")
    run_state.get_or_create_run_state("realshape", content_root=env, product="beta")
    legacy = prospects_dir("realshape", env) / "run_state.json"
    before = legacy.read_bytes()
    assert run_state.main(["--profile", "realshape", "--product", "beta", "reset"]) == 0
    assert not (prospects_dir("realshape", env) / "run_state.beta.json").exists()
    assert legacy.read_bytes() == before


def test_every_cli_action_takes_product(env, capsys):
    for action in ("status", "resume-from", "reset"):
        assert run_state.main(["--profile", "realshape", "--product", "beta", action]) == 0
    assert (
        run_state.main(
            ["--profile", "realshape", "--product", "beta", "start-stage", "--stage", "init"]
        )
        == 0
    )
    assert (prospects_dir("realshape", env) / "run_state.beta.json").is_file()


def test_omitting_the_product_on_a_multi_product_profile_refuses(env, capsys):
    assert run_state.main(["--profile", "realshape", "status"]) == 2
    assert "more than one product" in capsys.readouterr().err
    with pytest.raises(run_scope.ProductRequired):
        run_state.get_or_create_run_state("realshape", content_root=env)


def test_a_single_product_profile_needs_no_product(env):
    run_state.get_or_create_run_state("oneprod", content_root=env)
    assert (prospects_dir("oneprod", env) / "run_state.json").is_file()
    assert run_state.main(["--profile", "oneprod", "status"]) == 0


def test_a_stage_bracket_for_the_second_product_saves_to_its_own_file_only(env, capsys):
    """``start-stage`` is the path that SAVES the state. Saving it to the default file would let a
    second product's run advance (and later be resumed as) the default product's (audit F6)."""
    run_state.get_or_create_run_state("realshape", content_root=env, product="alpha")
    legacy = prospects_dir("realshape", env) / "run_state.json"
    before = legacy.read_bytes()
    argv = ["--profile", "realshape", "--product", "beta", "start-stage", "--stage", "enrichment"]
    assert run_state.main(argv) == 0
    beta = prospects_dir("realshape", env) / "run_state.beta.json"
    assert "enrichment" in beta.read_text()
    assert legacy.read_bytes() == before
