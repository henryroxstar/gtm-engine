"""F1-2 (the core promise) and F1-9 against the REAL SDK and CLI.

F1-2: tell the cockpit a unique fact, "restart the process" (a genuinely new OS process, new state objects),
ask for it -> recalled. Negative control: delete the transcript (what losing the Docker volume does) -> NOT
recalled, the operator gets the exact failed-resume notice, and the conversation still answers.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PROJECTS = Path.home() / ".claude" / "projects"


def _turn(root: Path, cwd: Path, prompt: str) -> dict:
    r = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("_resume_worker.py")),
            "--root",
            str(root),
            "--cwd",
            str(cwd),
            "--prompt",
            prompt,
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=240,
        env={**os.environ, "PYTHONPATH": str(REPO)},
    )
    line = next((x for x in r.stdout.splitlines() if x.startswith("RESULT ")), None)
    assert line, f"worker produced no result:\nSTDOUT:{r.stdout[-800:]}\nSTDERR:{r.stderr[-800:]}"
    return json.loads(line[len("RESULT ") :])


def _delete_transcripts(session_ids: set[str]) -> int:
    """Delete only transcripts whose file name IS one of these (throwaway) session ids."""
    n = 0
    for sid in session_ids:
        for f in PROJECTS.rglob(f"{sid}.jsonl"):
            f.unlink()
            n += 1
    return n


def test_f1_2_resume_restores_context_across_processes_and_the_negative_control(tmp_path):
    from agent.cockpit_state import NOTICE_FAILED_RESUME

    word = "zephyr" + uuid.uuid4().hex[:8]
    cwd = tmp_path / "proj"
    cwd.mkdir()
    root = tmp_path / "content"
    root.mkdir()
    seen: set[str] = set()
    try:
        a = _turn(root, cwd, f"Remember this code word exactly: {word}. Reply with only: OK")
        seen.add(a["session_id"])
        assert a["resumed_from"] is None  # a brand new conversation

        # ---- restart: a NEW process, a NEW CockpitState reading the file the first one wrote ----
        b = _turn(
            root, cwd, "What was the code word I asked you to remember? Reply with only the word."
        )
        seen.add(b["session_id"])
        assert b["resumed_from"] == a["session_id"], (
            "the second process must have asked to resume the first's id"
        )
        assert word in b["reply"], f"context NOT restored: {b['reply']!r}"
        assert b["notices"] == []

        # ---- negative control: lose the transcript volume ----
        assert _delete_transcripts(seen) >= 1
        c = _turn(
            root, cwd, "What was the code word I asked you to remember? Reply with only the word."
        )
        seen.add(c["session_id"])
        assert word not in c["reply"], (
            "recalled WITHOUT a transcript — the positive result proves nothing"
        )
        assert NOTICE_FAILED_RESUME in c["notices"]
        assert c["resumed_from"] is None  # it fell back to a fresh conversation, and said so
    finally:
        _delete_transcripts(seen)


def test_f1_9_cli_retention_needs_the_setting_in_an_enabled_source(tmp_path):
    """The cockpit loads ONLY the ``project`` settings source. The CLI's retention sweep runs only when an
    ENABLED source provides ``cleanupPeriodDays`` — so without it in the project settings the transcript
    volume would grow forever. Observed in a THROWAWAY config dir (the sweep is global to a config dir, so
    this must never point at the real ~/.claude)."""
    import asyncio
    import time

    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

    async def observe(with_setting: bool) -> tuple[bool, bool]:
        base = tmp_path / ("with" if with_setting else "without")
        cfg_dir, proj = base / "cfg", base / "proj"
        (proj / ".claude").mkdir(parents=True)
        if (
            with_setting
        ):  # the SAME file the container mounts: the repo's .claude/settings.json value
            repo = json.loads((REPO / ".claude" / "settings.json").read_text())
            (proj / ".claude" / "settings.json").write_text(
                json.dumps({"cleanupPeriodDays": repo["cleanupPeriodDays"]})
            )
        pdir = cfg_dir / "projects" / "-throwaway"
        pdir.mkdir(parents=True)
        old, fresh = (
            pdir / "00000000-0000-4000-8000-0000000000aa.jsonl",
            pdir / "00000000-0000-4000-8000-0000000000bb.jsonl",
        )
        for f, age_days in ((old, 90), (fresh, 0)):  # 90 days old: older than any sane retention
            f.write_text('{"type":"summary","summary":"x"}\n')
            t = time.time() - age_days * 86400
            os.utime(f, (t, t))
        client = ClaudeSDKClient(
            options=ClaudeAgentOptions(
                cwd=str(proj), setting_sources=["project"], env={"CLAUDE_CONFIG_DIR": str(cfg_dir)}
            )
        )
        await client.connect()
        await asyncio.sleep(10)
        await client.disconnect()
        return old.exists(), fresh.exists()

    old_kept_with, fresh_with = asyncio.run(observe(True))
    old_kept_without, _ = asyncio.run(observe(False))
    assert fresh_with, "a fresh transcript must never be swept"
    assert old_kept_with is False, "with the setting, an old transcript is cleaned"
    assert old_kept_without is True, "without it the sweep is skipped — the trap this test pins"
