"""The ``python -m backend.provision`` CLI (W1): key memory, secret hygiene, and the kill switch.

The state machine is tested through the service; this file covers only what the CLI adds. The end-to-end
run against a real database is the smoke test at the bottom of ``test_provision_live.py``.
"""

from __future__ import annotations

import asyncio
import stat

import pytest

from backend import provision as cli
from backend.services import provision as svc


def _args(*extra: str):
    return cli.parse_args(
        ["--workspace", "ws1", "--name", "Fixture Agent", "--profile", "fixtureco", *extra]
    )


def _req(*extra: str, env=None):
    return cli.build_request(_args(*extra), env or {})


def test_exactly_one_of_profile_and_url_is_required():
    with pytest.raises(SystemExit):
        cli.parse_args(["--workspace", "w", "--name", "n"])
    with pytest.raises(SystemExit):
        cli.parse_args(
            ["--workspace", "w", "--name", "n", "--profile", "p", "--url", "https://x.example"]
        )


def test_there_is_no_flag_that_takes_a_key_value():
    with pytest.raises(SystemExit):
        _args("--api-key", "test_fixture_secret")


def test_a_provider_key_is_read_from_the_named_environment_variable():
    req = _req(
        "--integration",
        "apollo=FIXTURE_APOLLO_KEY",
        env={"FIXTURE_APOLLO_KEY": "test_fixture_secret"},
    )
    assert req.integrations["apollo"].get_secret_value() == "test_fixture_secret"
    assert "test_fixture_secret" not in repr(req)


@pytest.mark.parametrize("item", ["apollo", "apollo=", "=FIXTURE_VAR", "apollo=UNSET_VAR"])
def test_a_malformed_or_unset_integration_is_refused(item):
    with pytest.raises(SystemExit):
        _req("--integration", item, env={})


def test_the_same_request_resumes_its_own_key_and_a_different_one_gets_another(tmp_path):
    state = tmp_path / "state"
    a = cli.idempotency_key_for(state, "ws1", _req())
    assert cli.idempotency_key_for(state, "ws1", _req()) == a  # re-running resumes the same job
    assert cli.idempotency_key_for(state, "ws1", _req("--budget", "5")) != a
    assert (
        cli.idempotency_key_for(state, "ws2", _req()) != a
    )  # another workspace never shares a key
    # a corrected provider key value is the same request, so it keeps the same key
    with_key = cli.build_request(_args("--integration", "apollo=V"), {"V": "test_fixture_one"})
    again = cli.build_request(_args("--integration", "apollo=V"), {"V": "test_fixture_two"})
    assert cli.idempotency_key_for(state, "ws1", with_key) == cli.idempotency_key_for(
        state, "ws1", again
    )


def test_the_remembered_key_is_private_and_holds_no_secret(tmp_path):
    state = tmp_path / "state"
    key = cli.idempotency_key_for(state, "ws1", _req())
    (file,) = state.glob("*.key")
    assert (
        stat.S_IMODE(file.stat().st_mode) == 0o600 and stat.S_IMODE(state.stat().st_mode) == 0o700
    )
    assert file.read_text().strip() == key and svc.normalise_source(_req()) not in file.read_text()


def test_the_cli_honours_the_same_kill_switch_and_touches_nothing_when_shut(
    monkeypatch, capsys, tmp_path
):
    monkeypatch.delenv("GTM_AGENT_PROVISION_ENABLED", raising=False)
    code = asyncio.run(
        cli.drive(_args("--state-dir", str(tmp_path)), {"DATABASE_URL": "postgresql://never-used"})
    )
    assert code == 2
    assert "GTM_AGENT_PROVISION_ENABLED" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())  # no key was even generated


def test_the_cli_needs_a_database_url(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("GTM_AGENT_PROVISION_ENABLED", "1")
    assert asyncio.run(cli.drive(_args("--state-dir", str(tmp_path)), {})) == 2
    assert "DATABASE_URL" in capsys.readouterr().err
