from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from ..ledgers import Ledgers
from ..paths import _safe_segment, resolve_profiles_root
from .sources import _repo_root


def _month_budget_remaining(ledgers: Ledgers, budget: float | None) -> tuple[bool, float]:
    """Return (under_or_at_budget, spent_usd). ``budget`` ``None`` means no cap."""
    from datetime import UTC, datetime

    month = datetime.now(UTC).strftime("%Y-%m")
    spent = ledgers.month_cost_total(month)
    if budget is None:
        return True, spent
    return spent < budget or abs(spent - budget) < 1e-9, spent


def _run_linter(asset_path: Path, ban_file: Path | None = None) -> tuple[bool, list[str]]:
    """Run ``tests/linter/content_linter.py`` on an asset. Return (pass, messages)."""
    linter = _repo_root() / "tests" / "linter" / "content_linter.py"
    cmd = [sys.executable, str(linter), str(asset_path)]
    if ban_file is not None:
        cmd.extend(["--ban-file", str(ban_file)])
    try:
        result = subprocess.run(  # nosec B603 — fixed path + item path only
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, [f"linter could not run: {exc}"]
    ok = result.returncode == 0
    messages = [result.stdout.strip(), result.stderr.strip()]
    return ok, [m for m in messages if m]


def _safe_to_share(text: str) -> tuple[bool, list[str]]:
    linter = _repo_root() / "tests" / "linter" / "content_linter.py"
    try:
        result = subprocess.run(  # nosec B603 — fixed path + text only
            [sys.executable, str(linter), "--safe-to-share", text],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, [f"safe-to-share check could not run: {exc}"]
    ok = result.returncode == 0
    messages = [result.stdout.strip(), result.stderr.strip()]
    return ok, [m for m in messages if m]


def _asset_text(asset: dict) -> str:
    """Assemble a searchable text blob from an asset dict (mirrors content_linter._asset_text)."""
    parts: list[str] = []
    for key in ("hook", "body", "caption"):
        value = asset.get(key)
        if isinstance(value, str):
            parts.append(value)
    for key in ("tweets", "slides", "key_points"):
        value = asset.get(key)
        if isinstance(value, list):
            parts.extend(str(v) for v in value if isinstance(v, str))
    return "\n".join(parts)


def _voice_bans_violated(text: str, bans: tuple[str, ...]) -> list[str]:
    violations: list[str] = []
    for ban in bans:
        pattern = re.compile(rf"\b{re.escape(ban)}\b", re.IGNORECASE)
        if pattern.search(text):
            violations.append(f"voice ban violated: {ban!r}")
    return violations


def _load_bans(path: Path | None) -> tuple[str, ...]:
    if path is None or not path.is_file():
        return ()
    out: list[str] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                out.append(s)
    except OSError:
        pass
    return tuple(out)


def _resolve_ban_file(facts: dict) -> Path | None:
    bans = facts.get("voice-bans")
    if not bans:
        return None
    profile_dir = (
        resolve_profiles_root() / _safe_segment(facts.get("profile", ""), "profile") / "knowledge"
    )
    path = profile_dir / "voice-bans.txt"
    return path if path.is_file() else None
