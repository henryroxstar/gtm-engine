"""P5 tests — the model-deprecation CI gate (tests/lint/model_deprecation_check.sh).

Exercises the gate end-to-end against crafted fixtures via its env overrides
(MODEL_REGISTRY / MODEL_DEPRECATION_MAP / MODEL_SCAN_ROOT):
  - the real registry + real map pass (the shipped state is clean)
  - a registry pinned to a denylisted id (deepseek-chat) fails
  - a hardcoded model-id literal in a scanned .py fails
  - a tool/connector name that merely contains a provider word does NOT false-positive
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tests" / "lint" / "model_deprecation_check.sh"
REAL_REGISTRY = ROOT / "gtm_core" / "models.toml"
REAL_MAP = ROOT / "tests" / "lint" / "model_deprecation_map.txt"

if shutil.which("bash") is None:
    pytest.skip("bash not available", allow_module_level=True)


def _run(env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
    env = {**os.environ, **env_overrides}
    return subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, env=env, cwd=str(ROOT)
    )


def test_real_repo_passes():
    r = _run({})
    assert r.returncode == 0, r.stdout + r.stderr


def test_denylisted_registry_id_fails(tmp_path):
    reg = tmp_path / "models.toml"
    reg.write_text(REAL_REGISTRY.read_text().replace("deepseek-v4-flash", "deepseek-chat"))
    empty_scan = tmp_path / "scan"  # no python dirs → part (a) clean, isolate part (b)
    empty_scan.mkdir()
    r = _run({"MODEL_REGISTRY": str(reg), "MODEL_SCAN_ROOT": str(empty_scan)})
    assert r.returncode == 1
    assert "deprecation denylist" in r.stdout


def test_hardcoded_literal_fails(tmp_path):
    scan = tmp_path / "scan"
    (scan / "agent").mkdir(parents=True)
    (scan / "agent" / "probe.py").write_text('BAD = "claude-sonnet-4-6"\n')
    r = _run({"MODEL_SCAN_ROOT": str(scan)})
    assert r.returncode == 1
    assert "hardcoded model id" in r.stdout


def test_tool_name_does_not_false_positive(tmp_path):
    scan = tmp_path / "scan"
    (scan / "agent").mkdir(parents=True)
    # These contain provider words but are NOT model ids — must not trip the gate.
    (scan / "agent" / "names.py").write_text(
        'FastMCP("deepseek-worker")\nTOOL = "gemini-image-worker"\n# pinned claude-sonnet-4-6\n'
    )
    r = _run({"MODEL_SCAN_ROOT": str(scan)})
    assert r.returncode == 0, r.stdout + r.stderr
