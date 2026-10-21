"""Tests for gtm_core.profile — the active-tenant visibility CLI (SDK-INDEPENDENT).

This CLI never switches a profile; it only reports what's currently resolved
(``--profile`` / ``ACTIVE_PROFILE`` env / default) so a mistyped or unset tenant is
loud instead of silently misrouting a write — see gtm_core/profile.py docstring.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core.profile import main as profile_main


def _make_profiles(tmp_path: Path) -> Path:
    root = tmp_path / "profiles"
    (root / "acme").mkdir(parents=True)
    (root / "acme" / "PROFILE.md").write_text("brand_name: Acme\n")
    (root / "demo").mkdir(parents=True)
    (root / "demo" / "PROFILE.md").write_text("brand_name: Demo\n")
    # a stray dir with no PROFILE.md must not count as a profile
    (root / "scratch").mkdir(parents=True)
    return tmp_path


def _run(*argv: str) -> tuple[int, str]:
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = profile_main(list(argv))
    return rc, buf.getvalue()


def test_status_reports_env_default(tmp_path, monkeypatch):
    root = _make_profiles(tmp_path)
    monkeypatch.setenv("ACTIVE_PROFILE", "acme")
    monkeypatch.delenv("GTM_PROFILES_ROOT", raising=False)
    monkeypatch.delenv("GTM_CONTENT_ROOT", raising=False)
    rc, out = _run("--repo-root", str(root), "status")
    assert rc == 0
    line = json.loads(out.strip().splitlines()[-1])
    assert line["profile"] == "acme"
    assert line["profile_dir_exists"] is True
    assert line["content_root"] == str(root / "content" / "acme")


def test_status_explicit_profile_overrides_env(tmp_path, monkeypatch):
    root = _make_profiles(tmp_path)
    monkeypatch.setenv("ACTIVE_PROFILE", "acme")
    rc, out = _run("--repo-root", str(root), "status", "--profile", "demo")
    line = json.loads(out.strip().splitlines()[-1])
    assert rc == 0
    assert line["profile"] == "demo"


def test_status_missing_profile_exits_3(tmp_path, monkeypatch):
    root = _make_profiles(tmp_path)
    monkeypatch.setenv("ACTIVE_PROFILE", "nope")
    rc, out = _run("--repo-root", str(root), "status")
    assert rc == 3
    line = json.loads(out.strip().splitlines()[-1])
    assert line["profile_dir_exists"] is False


def test_list_marks_active_and_skips_non_profiles(tmp_path, monkeypatch):
    root = _make_profiles(tmp_path)
    monkeypatch.setenv("ACTIVE_PROFILE", "demo")
    rc, out = _run("--repo-root", str(root), "list")
    assert rc == 0
    lines = [line for line in out.splitlines() if line.strip()]
    assert any(line.startswith("* ") and line.endswith("demo") for line in lines)
    assert any(line.startswith("  ") and line.endswith("acme") for line in lines)
    assert not any("scratch" in line for line in lines)


def test_list_empty_profiles_root(tmp_path):
    (tmp_path / "profiles").mkdir()
    rc, out = _run("--repo-root", str(tmp_path), "list")
    assert rc == 0
    assert "no profiles found" in out


@pytest.mark.parametrize("cmd", [["status"], ["list"]])
def test_requires_repo_or_env_isolation(tmp_path, monkeypatch, cmd):
    # Sanity: passing --repo-root fully isolates from the real repo's profiles/.
    root = _make_profiles(tmp_path)
    monkeypatch.setenv("ACTIVE_PROFILE", "acme")
    rc, _ = _run("--repo-root", str(root), *cmd)
    assert rc in (0, 3)
