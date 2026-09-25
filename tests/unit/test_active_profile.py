from pathlib import Path

import pytest

from gtm_core import active_profile
from gtm_core.paths import PathConfig


def test_set_then_show_round_trips(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    (tmp_path / "profiles" / "acme-robotics").mkdir(parents=True)
    (tmp_path / "profiles" / "acme-robotics" / "PROFILE.md").write_text("name = acme")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    monkeypatch.delenv("GTM_RUNTIME", raising=False)
    monkeypatch.delenv("GTM_PROFILE", raising=False)
    monkeypatch.delenv("ACTIVE_PROFILE", raising=False)
    active_profile.set_active("acme-robotics")
    assert active_profile.show() == "acme-robotics"
    assert PathConfig.from_env().default_profile == "acme-robotics"


def test_env_var_still_wins(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    (tmp_path / "profiles" / "acme-robotics").mkdir(parents=True)
    (tmp_path / "profiles" / "acme-robotics" / "PROFILE.md").write_text("name = acme")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    monkeypatch.delenv("GTM_RUNTIME", raising=False)
    active_profile.set_active("acme-robotics")
    monkeypatch.setenv("GTM_PROFILE", "other-co")
    assert PathConfig.from_env().default_profile == "other-co"


def test_refuses_an_unsafe_slug(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    with pytest.raises(ValueError):
        active_profile.set_active("../etc")


def test_headless_ignores_marker(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    (tmp_path / "profiles" / "acme-robotics").mkdir(parents=True)
    (tmp_path / "profiles" / "acme-robotics" / "PROFILE.md").write_text("name = acme")
    monkeypatch.delenv("GTM_PROFILE", raising=False)
    monkeypatch.delenv("ACTIVE_PROFILE", raising=False)
    active_profile.set_active("acme-robotics")
    monkeypatch.setenv("GTM_RUNTIME", "headless")
    assert PathConfig.from_env().default_profile == "template"


def test_missing_profile_warns_and_falls_back(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    monkeypatch.delenv("GTM_RUNTIME", raising=False)
    monkeypatch.delenv("GTM_PROFILE", raising=False)
    monkeypatch.delenv("ACTIVE_PROFILE", raising=False)
    active_profile.set_active("ghost-co")
    assert PathConfig.from_env().default_profile == "template"
    captured = capsys.readouterr()
    assert "no longer exists" in captured.err
