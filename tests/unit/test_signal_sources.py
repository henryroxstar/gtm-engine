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
    page_text,
    prune,
    sources_dir_for,
    store_capture,
    url_norm,
    validate_source_evidence,
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


def _load_hook_module():
    import importlib.util

    hook_file = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "firecrawl_capture.py"
    if not hook_file.is_file():
        pytest.skip("firecrawl_capture.py only exists in private tree")
    spec = importlib.util.spec_from_file_location("firecrawl_capture", str(hook_file))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_firecrawl_capture_hook_refuses_with_no_profile_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-09-27: with no profile bound, the hook silently wrote to a tenant-less
    `content/sources/` that the enrollment gate — reading `content/<profile>/sources/` —
    never saw. It now writes nothing and exits 2 so Claude sees why."""
    import io
    import sys

    mod = _load_hook_module()
    event = {
        "tool_name": "mcp__firecrawl__scrape",
        "tool_input": {"url": "https://example.com/scraped-page"},
        "tool_result": {"markdown": "Page text."},
    }
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))  # no .active-profile written
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))

    ret = mod.main()

    assert ret == 2
    assert not (tmp_path / "sources").exists()
    assert not any(tmp_path.rglob("index.jsonl"))


def test_firecrawl_capture_hook_unwraps_a_string_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The response shape observed in ~76 of 122 real captures: `tool_result` arrives as a
    JSON *string*. The hook must store the page text, not that string."""
    import io
    import sys

    mod = _load_hook_module()
    envelope = json.dumps({"markdown": "First paragraph.\n\nSecond paragraph.", "metadata": {}})
    event = {
        "tool_name": "mcp__firecrawl__scrape",
        "tool_input": {"url": "https://example.com/scraped-page"},
        "tool_result": envelope,
    }
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    from gtm_core.active_profile import set_active

    set_active("demo", content_root=tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))

    ret = mod.main()

    assert ret == 0
    cap = get_latest_capture("https://example.com/scraped-page", profile="demo")
    assert cap is not None
    assert cap.text == "First paragraph.\n\nSecond paragraph."


# --- page_text: unwrapping the raw MCP response (2026-09-27 defect 1) ---------------


def test_page_text_unwraps_a_json_string_envelope() -> None:
    """The shape observed in ~76 of 122 real captures: the hook stored `tool_result` as a
    JSON *string* verbatim, so a paragraph break was the two characters `\\n`, not a real
    newline, and a verbatim quote spanning it could never match."""
    envelope = json.dumps({"markdown": "First paragraph.\n\nSecond paragraph.", "metadata": {}})
    assert page_text(envelope) == "First paragraph.\n\nSecond paragraph."


def test_page_text_unwraps_mcp_content_blocks() -> None:
    raw = {"content": [{"type": "text", "text": "Page body here."}]}
    assert page_text(raw) == "Page body here."


def test_page_text_prefers_data_markdown() -> None:
    raw = {"data": {"markdown": "# Title\n\nBody."}}
    assert page_text(raw) == "# Title\n\nBody."


def test_page_text_blank_on_http_error() -> None:
    raw = json.dumps({"markdown": "Not Found", "metadata": {"statusCode": 404}})
    assert page_text(raw) == ""


def test_page_text_never_reads_the_ai_summary() -> None:
    """`summary`/`json`/`query` are Firecrawl's own generated output about the page, not
    the page — a clause quoting one is a paraphrase of the source, never verbatim."""
    raw = json.dumps({"summary": "The company raised money.", "metadata": {"statusCode": 200}})
    assert page_text(raw) == ""


def test_page_text_plain_string_passes_through() -> None:
    assert page_text("Plain page text, not JSON.") == "Plain page text, not JSON."


# --- validate_source_evidence: verbatim check survives real capture shapes ----------


PARA_1 = "Halden Systems closed a Series B round on 3 March."
PARA_2 = "The round will fund an identity programme across its two regional banks."
QUOTE = 'Halden Systems opened by saying, "we plan to double headcount next year."'


def _envelope_capture(tmp_path: Path, markdown: str) -> Path:
    sources_dir = tmp_path / "sources"
    store_capture(
        "https://halden.example/news/series-b",
        json.dumps({"markdown": markdown, "metadata": {"statusCode": 200}}),
        sources_dir=sources_dir,
    )
    return sources_dir


def test_evidence_spanning_a_json_escaped_paragraph_break_now_passes(tmp_path: Path) -> None:
    sources_dir = _envelope_capture(tmp_path, f"{PARA_1}\n\n{PARA_2}")
    evidence = f"{PARA_1[-20:]} {PARA_2[:20]}"
    findings = validate_source_evidence(
        evidence,
        "https://halden.example/news/series-b",
        lane="personalised",
        sources_dir=sources_dir,
    )
    assert findings == []


def test_evidence_containing_a_double_quote_now_passes(tmp_path: Path) -> None:
    sources_dir = _envelope_capture(tmp_path, f"# Halden\n\n{QUOTE}")
    findings = validate_source_evidence(
        QUOTE, "https://halden.example/news/series-b", lane="personalised", sources_dir=sources_dir
    )
    assert findings == []


def test_evidence_across_a_markdown_link_now_passes(tmp_path: Path) -> None:
    md = "Halden Systems, per [its filing](https://sec.example/x), raised a Series B."
    sources_dir = _envelope_capture(tmp_path, md)
    evidence = "Halden Systems, per its filing, raised a Series B."
    findings = validate_source_evidence(
        evidence,
        "https://halden.example/news/series-b",
        lane="personalised",
        sources_dir=sources_dir,
    )
    assert findings == []


def test_a_paraphrase_still_fails_after_unwrapping() -> None:
    """Loosening the comparison for markup must never admit a clause that is not actually
    a verbatim reduction of the source — the whole point of the check."""

    def _run(tmp_path: Path) -> list:
        sources_dir = _envelope_capture(tmp_path, f"{PARA_1}\n\n{PARA_2}")
        paraphrase = "Halden Systems announced new funding to grow across two banks."
        return validate_source_evidence(
            paraphrase,
            "https://halden.example/news/series-b",
            lane="personalised",
            sources_dir=sources_dir,
        )

    import tempfile

    with tempfile.TemporaryDirectory() as td:
        findings = _run(Path(td))
    assert [f.rule for f in findings] == ["signal-evidence-not-in-source"]


def test_changed_closing_punctuation_still_fails(tmp_path: Path) -> None:
    """A real near-miss on the live pool: the source closes the quote with `,\"` and the
    drafted evidence closed it with `.\"` instead — a one-character drift that must still
    block, not a case the markdown-syntax fix should paper over."""
    sources_dir = _envelope_capture(
        tmp_path, 'The company said, "we expect broad adoption by next year," in the release.'
    )
    evidence = '"we expect broad adoption by next year."'
    findings = validate_source_evidence(
        evidence,
        "https://halden.example/news/series-b",
        lane="personalised",
        sources_dir=sources_dir,
    )
    assert [f.rule for f in findings] == ["signal-evidence-not-in-source"]


# --- sources_dir_for / tenant-folder divergence (2026-09-27 defect 2) ---------------


def test_sources_dir_for_includes_the_profile_segment(tmp_path: Path) -> None:
    assert sources_dir_for("acme", tmp_path) == tmp_path / "acme" / "sources"


def test_gate_style_lookup_now_finds_what_the_hook_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Writer = the hook, which resolves the active profile and writes under
    `<content>/<profile>/sources` (here: an explicit ``profile=`` kwarg, standing in for the
    hook's own profile resolution). Reader = ``sources_dir_for(profile, content_root)``, the
    helper `account_integrity.audit_rows` and `preflight_report` now call instead of the
    tenant-less `content_root / "sources"` that once found nothing. The end-to-end version
    of this — through the real gate call site — is `tests/test_account_integrity.py`.
    """
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    store_capture(
        "https://halden.example/news/series-b",
        "Halden Systems closed a Series B round.",
        profile="acme",
    )

    findings = validate_source_evidence(
        "Halden Systems closed a Series B round.",
        "https://halden.example/news/series-b",
        lane="personalised",
        sources_dir=sources_dir_for("acme", tmp_path),
    )
    assert findings == []


def test_malformed_active_profile_marker_is_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bare `except Exception` this replaced turned a malformed marker into the SAME
    silent tenant-less fallback as having no profile at all — hiding a real error."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    (tmp_path / ".active-profile").write_text("../escape", encoding="utf-8")

    with pytest.raises(ValueError):
        get_latest_capture("https://halden.example/news/series-b")
