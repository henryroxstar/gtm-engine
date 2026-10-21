from datetime import date
from pathlib import Path

from gtm_core import budget_status


def _seed(tmp_path: Path, monkeypatch, cap: float, spent: float):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    (tmp_path / "profiles" / "acme").mkdir(parents=True)
    (tmp_path / "profiles" / "acme" / "PROFILE.md").write_text(f"monthly_tool_budget_usd: {cap}\n")
    (tmp_path / "acme").mkdir()
    (tmp_path / "acme" / "costs.jsonl").write_text(
        f'{{"ts":"2026-09-03T10:00:00Z","cost_usd":{spent},"tool":"rocketreach"}}\n'
    )


def test_one_cap_source(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, cap=50.0, spent=41.2)
    s = budget_status.status("acme", today=date(2026, 9, 24))
    assert (s.cap_usd, s.spent_usd, s.resets_on) == (50.0, 41.2, date(2026, 10, 1))
    assert budget_status.render(s) == "$41.20 of your $50.00 for September. Resets 1 Oct."


def test_agent_and_guards_read_the_same_cap(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, cap=80.0, spent=0.0)
    from agent.budget import monthly_cap_usd
    from gtm_core.paths import PathConfig
    from gtm_core.prospect_guards import get_profile_budget_limits

    assert monthly_cap_usd(PathConfig.from_env(), "acme") == 80.0
    assert get_profile_budget_limits("acme")["monthly_tool_budget_usd"] == 80.0


def test_non_existent_profile_raises():
    import pytest

    with pytest.raises((FileNotFoundError, ValueError)):
        budget_status.status("non_existent_profile_12345")


def test_cli_non_existent_profile_exits_nonzero():
    import subprocess
    import sys

    res = subprocess.run(
        [sys.executable, "-m", "gtm_core.budget_status", "--profile", "non_existent_profile_12345"],
        capture_output=True,
        text=True,
    )
    assert res.returncode != 0


def test_budget_status_streams_costs_file(tmp_path, monkeypatch):
    import pytest

    _seed(tmp_path, monkeypatch, cap=100.0, spent=0.0)
    costs_path = tmp_path / "acme" / "costs.jsonl"
    with costs_path.open("w", encoding="utf-8") as f:
        for _ in range(1000):
            f.write('{"ts":"2026-09-03T10:00:00Z","cost_usd":0.05,"tool":"apollo"}\n')

    orig_read_text = Path.read_text

    def mock_read_text(self, *args, **kwargs):
        if self == costs_path:
            raise AssertionError(
                "costs.jsonl should be streamed line-by-line, not read via read_text()"
            )
        return orig_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", mock_read_text)
    s = budget_status.status("acme", today=date(2026, 9, 24))
    assert pytest.approx(s.spent_usd, 0.01) == 50.0
