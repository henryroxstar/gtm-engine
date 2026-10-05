"""One paid page is filed once and billed once, however many hooks see the same tool call.

Verification audit 2026-10-02, Critical 5(c): in a headless capture run both the project's editor
hook and the run's own hook match the scrape tool, so one scrape wrote two index rows and two cost
rows and the monthly cap tripped at about half the real spend. The check sits inside the ledger's
own lock, because the two hooks can run at the same moment.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

from gtm_core import capture_cost, signal_sources

REPO = Path(__file__).resolve().parents[2]
URL = "https://registry.example.test/members"
TEXT = "Members of the fictional register: Northwind Traders, Contoso Freight."


def _index(dir_path: Path) -> list[dict]:
    p = dir_path / "index.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.is_file() else []


def _costs(root: Path, profile="p") -> list[dict]:
    p = root / profile / "costs.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.is_file() else []


def _iso(minutes: float) -> str:
    base = datetime.datetime(2026, 10, 2, 8, 0, tzinfo=datetime.UTC)
    return (base + datetime.timedelta(minutes=minutes)).isoformat()


# --- the capture index ----------------------------------------------------------------------


def test_the_same_page_filed_twice_in_a_moment_is_one_index_row(tmp_path):
    a = signal_sources.store_capture(URL, TEXT, sources_dir=tmp_path, fetched_at=_iso(0))
    b = signal_sources.store_capture(URL, TEXT, sources_dir=tmp_path, fetched_at=_iso(0.5))
    assert a == b
    assert len(_index(tmp_path)) == 1
    assert len((tmp_path / "sources.jsonl").read_text().splitlines()) == 1


def test_a_changed_page_is_a_new_row(tmp_path):
    signal_sources.store_capture(URL, TEXT, sources_dir=tmp_path, fetched_at=_iso(0))
    signal_sources.store_capture(
        URL, TEXT + " And one more.", sources_dir=tmp_path, fetched_at=_iso(0.5)
    )
    assert len(_index(tmp_path)) == 2


def test_the_same_page_fetched_again_later_is_a_new_row_because_cadence_reads_the_date(tmp_path):
    signal_sources.store_capture(URL, TEXT, sources_dir=tmp_path, fetched_at=_iso(0))
    signal_sources.store_capture(URL, TEXT, sources_dir=tmp_path, fetched_at=_iso(10))
    assert len(_index(tmp_path)) == 2


def test_a_different_url_with_the_same_text_is_a_new_row(tmp_path):
    signal_sources.store_capture(URL, TEXT, sources_dir=tmp_path, fetched_at=_iso(0))
    signal_sources.store_capture(URL + "/2", TEXT, sources_dir=tmp_path, fetched_at=_iso(0))
    assert len(_index(tmp_path)) == 2


def test_a_damaged_index_line_never_stops_a_capture_being_filed(tmp_path):
    (tmp_path / "index.jsonl").write_text("{not json\n", encoding="utf-8")
    sha = signal_sources.store_capture(URL, TEXT, sources_dir=tmp_path, fetched_at=_iso(0))
    assert (tmp_path / f"{sha}.txt").read_text() == TEXT
    assert len(_index_lines(tmp_path)) == 2


def _index_lines(dir_path: Path) -> list[str]:
    return (dir_path / "index.jsonl").read_text().splitlines()


# --- the cost ledger ------------------------------------------------------------------------


def _bill(root: Path, sha="a" * 64, url=URL, credits=1, tool="mcp__firecrawl__firecrawl_scrape"):
    return capture_cost.record_capture_cost(
        profile="p", url=url, sha256=sha, credits=credits, tool=tool, content_root=root
    )


def test_one_capture_billed_twice_in_a_moment_is_one_cost_row(tmp_path):
    first = _bill(tmp_path)
    second = _bill(tmp_path, tool="mcp__plugin_gtm-engine_firecrawl__firecrawl_scrape")
    assert len(_costs(tmp_path)) == 1
    assert second["ts"] == first["ts"]


def test_another_page_is_billed_separately(tmp_path):
    _bill(tmp_path)
    _bill(tmp_path, sha="b" * 64)
    _bill(tmp_path, url=URL + "/2")
    assert len(_costs(tmp_path)) == 3


def test_a_capture_billed_after_the_window_is_billed_again(tmp_path):
    _bill(tmp_path)
    path = tmp_path / "p" / "costs.jsonl"
    row = json.loads(path.read_text().splitlines()[0])
    row["ts"] = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=30)).isoformat()
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    _bill(tmp_path)
    assert len(_costs(tmp_path)) == 2


def test_two_threads_billing_the_same_capture_at_once_write_one_row(tmp_path):
    barrier = threading.Barrier(8)

    def go():
        barrier.wait()
        _bill(tmp_path)

    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(_costs(tmp_path)) == 1


def test_a_cost_row_that_is_not_a_capture_never_hides_a_capture_row(tmp_path):
    capture_cost.record_search_cost(
        profile="p",
        query="q",
        results=3,
        credits=2,
        credits_source="response",
        tool="t",
        content_root=tmp_path,
    )
    _bill(tmp_path)
    assert [r["op"] for r in _costs(tmp_path)] == ["search", "capture"]


# --- the two real hooks on one call ---------------------------------------------------------


def test_the_run_hook_and_the_editor_hook_on_one_scrape_file_and_bill_it_once(tmp_path):
    from agent.capture_hook import make_capture_hook

    tool = "mcp__firecrawl__firecrawl_scrape"
    event = {
        "tool_name": tool,
        "tool_input": {"url": URL, "formats": ["markdown"], "onlyMainContent": False, "maxAge": 0},
        "tool_response": json.dumps({"markdown": TEXT, "creditsUsed": 1}),
    }
    asyncio.run(make_capture_hook(SimpleNamespace(content_root=tmp_path), "p")(event, "id", None))
    env = {
        **os.environ,
        "GTM_CONTENT_ROOT": str(tmp_path),
        "GTM_ACTIVE_PROFILE": "p",
        "CLAUDE_PROJECT_DIR": str(REPO),
    }
    subprocess.run(
        [sys.executable, str(REPO / ".claude/hooks/firecrawl_capture.py")],
        input=json.dumps(event),
        text=True,
        env=env,
        capture_output=True,
        check=False,
    )
    assert len(_costs(tmp_path)) == 1
    assert len(_index(tmp_path / "p" / "sources")) == 1
