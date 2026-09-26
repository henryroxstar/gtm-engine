"""Tests for captured signal sources (W6 R6.1, R6.4).

Requirements:
- R6.1:
  - url_norm(url: str) -> str: case-fold host, strip fragment, strip utm_* query params,
    normalize trailing slash; http and https stay distinct.
  - Content-addressed capture storage: stores text by content hash (<sha256(text)>.txt),
    records entry in sources.jsonl index. A second capture of a changed page adds a row,
    and the latest capture wins.
  - The module imports NO HTTP client.
- R6.4:
  - prune subcommand: without --apply, changes nothing and lists files to remove;
    with --apply, removes unreferenced/orphaned source files.
"""

from __future__ import annotations

import ast
import datetime
import hashlib
import json
from pathlib import Path

import pytest

from gtm_core.signal_sources import (
    get_latest_capture,
    prune,
    store_capture,
    url_norm,
)


def test_url_norm_case_fold_host() -> None:
    assert url_norm("HTTPS://Example.COM/Path") == "https://example.com/Path"
    assert url_norm("http://SUB.DOMAIN.EXAMPLE/foo/bar") == "http://sub.domain.example/foo/bar"


def test_url_norm_strip_fragment() -> None:
    assert url_norm("https://example.com/path#heading") == "https://example.com/path"
    assert url_norm("https://example.com/path?a=1#section-2") == "https://example.com/path?a=1"


def test_url_norm_strip_utm_params() -> None:
    raw = "https://example.com/path?utm_source=twitter&utm_medium=cpc&id=123&utm_campaign=fall"
    assert url_norm(raw) == "https://example.com/path?id=123"
    assert url_norm("https://example.com/path?UTM_CONTENT=test") == "https://example.com/path"


def test_url_norm_trailing_slash() -> None:
    assert url_norm("https://example.com/path/") == "https://example.com/path"
    assert url_norm("https://example.com/") == "https://example.com"
    assert url_norm("https://example.com/path/subpath/") == "https://example.com/path/subpath"


def test_url_norm_http_and_https_stay_distinct() -> None:
    http_norm = url_norm("http://example.com/path")
    https_norm = url_norm("https://example.com/path")
    assert http_norm == "http://example.com/path"
    assert https_norm == "https://example.com/path"
    assert http_norm != https_norm


def test_content_addressed_capture_storage(tmp_path: Path) -> None:
    text = "Halden Systems raised a $40M Series B led by Fernway Ventures."
    expected_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    url = "https://example.com/news/series-b/"

    h = store_capture(
        url,
        text,
        sources_dir=tmp_path,
        tool="firecrawl",
        fetched_at="2026-09-01T12:00:00Z",
    )
    assert h == expected_hash

    # Text stored content-addressed by hash
    text_file = tmp_path / f"{expected_hash}.txt"
    assert text_file.exists()
    assert text_file.read_text(encoding="utf-8") == text

    # Index entry in sources.jsonl
    index_file = tmp_path / "sources.jsonl"
    assert index_file.exists()
    rows = [
        json.loads(line) for line in index_file.read_text(encoding="utf-8").splitlines() if line
    ]
    assert len(rows) == 1
    assert rows[0]["url_norm"] == "https://example.com/news/series-b"
    assert rows[0]["sha256"] == expected_hash
    assert rows[0]["tool"] == "firecrawl"
    assert rows[0]["fetched_at"] == "2026-09-01T12:00:00Z"


def test_second_capture_of_changed_page_adds_row_and_latest_wins(tmp_path: Path) -> None:
    url = "https://example.com/news/1"
    text1 = "Halden Systems announced early pilot."
    text2 = "Halden Systems raised a $40M Series B to expand its agent orchestration platform."

    h1 = store_capture(url, text1, sources_dir=tmp_path, fetched_at="2026-09-01T10:00:00Z")
    h2 = store_capture(url, text2, sources_dir=tmp_path, fetched_at="2026-09-02T10:00:00Z")
    assert h1 != h2

    # Two rows in sources.jsonl
    index_file = tmp_path / "sources.jsonl"
    rows = [
        json.loads(line) for line in index_file.read_text(encoding="utf-8").splitlines() if line
    ]
    assert len(rows) == 2
    assert rows[0]["sha256"] == h1
    assert rows[1]["sha256"] == h2

    # Latest capture query returns the latest one
    latest = get_latest_capture(url, sources_dir=tmp_path)
    assert latest is not None
    assert latest.sha256 == h2
    assert latest.text == text2
    assert latest.fetched_at == "2026-09-02T10:00:00Z"


def test_signal_sources_imports_no_http_client() -> None:
    target = Path(__file__).resolve().parents[2] / "gtm_core" / "signal_sources.py"
    tree = ast.parse(target.read_text(encoding="utf-8"))

    forbidden = {
        "requests",
        "httpx",
        "aiohttp",
        "urllib3",
        "urllib.request",
        "http.client",
    }

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                imported.add(n.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            imported.add(mod)
            for n in node.names:
                imported.add(f"{mod}.{n.name}")

    hits = {f for f in forbidden if any(i == f or i.startswith(f"{f}.") for i in imported)}
    assert hits == set(), f"gtm_core.signal_sources imports HTTP client: {hits}"


def test_prune_without_apply_lists_unreferenced_files_and_changes_nothing(tmp_path: Path) -> None:
    text = "Referenced page text."
    h = store_capture("https://example.com/p1", text, sources_dir=tmp_path)

    # Create an orphaned file with no entry in index
    orphaned_hash = hashlib.sha256(b"Orphaned text").hexdigest()
    orphaned_file = tmp_path / f"{orphaned_hash}.txt"
    orphaned_file.write_text("Orphaned text", encoding="utf-8")

    # Without --apply, changes nothing and returns files to remove
    to_remove = prune(sources_dir=tmp_path, apply=False)
    assert to_remove == [orphaned_file]
    assert orphaned_file.exists()
    assert (tmp_path / f"{h}.txt").exists()


def test_prune_with_apply_removes_unreferenced_files(tmp_path: Path) -> None:
    text = "Referenced page text."
    h = store_capture("https://example.com/p1", text, sources_dir=tmp_path)

    orphaned_hash = hashlib.sha256(b"Orphaned text").hexdigest()
    orphaned_file = tmp_path / f"{orphaned_hash}.txt"
    orphaned_file.write_text("Orphaned text", encoding="utf-8")

    # With --apply, removes the orphaned file
    removed = prune(sources_dir=tmp_path, apply=True)
    assert removed == [orphaned_file]
    assert not orphaned_file.exists()
    assert (tmp_path / f"{h}.txt").exists()


def test_prune_older_than_with_apply(tmp_path: Path) -> None:
    old_text = "Old content from long ago."
    recent_text = "Recent content."

    h_old = store_capture(
        "https://example.com/old",
        old_text,
        sources_dir=tmp_path,
        fetched_at="2026-01-01T00:00:00Z",
    )
    h_recent = store_capture(
        "https://example.com/recent",
        recent_text,
        sources_dir=tmp_path,
        fetched_at="2026-09-20T00:00:00Z",
    )

    as_of = datetime.date(2026, 9, 25)
    # 30 days older than 2026-09-25 is 2026-08-26. Old text (Jan 2026) is older.
    removed = prune(sources_dir=tmp_path, older_than_days=30, as_of=as_of, apply=True)
    assert [p.stem for p in removed] == [h_old]
    assert not (tmp_path / f"{h_old}.txt").exists()
    assert (tmp_path / f"{h_recent}.txt").exists()


def test_signal_sources_cli_capture(tmp_path: Path) -> None:
    from gtm_core.signal_sources import main

    url = "https://example.com/cli-test"
    text = "CLI captured page content with plenty of details."
    ret = main(["capture", "--url", url, "--text", text, "--sources-dir", str(tmp_path)])
    assert ret == 0
    cap = get_latest_capture(url, sources_dir=tmp_path)
    assert cap is not None
    assert cap.text == text


def test_firecrawl_capture_hook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util
    import io
    import sys

    hook_file = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "firecrawl_capture.py"
    if not hook_file.is_file():
        pytest.skip("firecrawl_capture.py only exists in private tree")
    spec = importlib.util.spec_from_file_location("firecrawl_capture", str(hook_file))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    sources_dir = tmp_path / "demo" / "sources"
    event = {
        "tool_name": "mcp__firecrawl__scrape",
        "tool_input": {"url": "https://example.com/scraped-page"},
        "tool_result": {"markdown": "This is markdown returned by the firecrawl scrape tool."},
    }
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    from gtm_core.active_profile import set_active

    set_active("demo", content_root=tmp_path)

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
    ret = mod.main()
    assert ret == 0

    cap = get_latest_capture("https://example.com/scraped-page", sources_dir=sources_dir)
    assert cap is not None
    assert "This is markdown returned" in cap.text
