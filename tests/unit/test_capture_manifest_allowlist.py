"""R0.3: a source-capture run may scrape only what its manifest lists, exactly as pinned."""

from __future__ import annotations

import json

import pytest

from gtm_core import capture_manifest as cm

SCRAPE = "mcp__firecrawl__firecrawl_scrape"
URLS = ["https://registry.example.test/members", "https://lists.example.test/banks"]
PINNED = {"formats": ["markdown"], "onlyMainContent": False, "maxAge": 86400000}


def _manifest(cap=3, urls=URLS):
    return cm.CaptureManifest(
        run_id="run-1", profile="acme", urls=list(urls), max_age_ms=86400000, cap=cap
    )


def _call(url, **over):
    return {"url": url, **PINNED, **over}


# --- manifest file ---------------------------------------------------------------------------


def test_write_then_load_round_trips_and_pins_the_options(tmp_path):
    path = cm.write_manifest(
        profile="acme", run_id="run-1", urls=URLS, cap=3, max_age_ms=86400000, content_root=tmp_path
    )
    assert path == tmp_path / "acme" / "sources" / "manifest-run-1.json"
    data = json.loads(path.read_text())
    assert (
        data["options"]["formats"] == ["markdown"] and data["options"]["onlyMainContent"] is False
    )
    loaded = cm.load_manifest(path)
    assert loaded.urls == URLS and loaded.cap == 3 and loaded.profile == "acme"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"urls": []},
        {"cap": 0},
        {"urls": ["ftp://x.test/a"]},
        {"urls": ["not a url"]},
        {"run_id": "../escape"},
        {"profile": "../escape"},
    ],
)
def test_write_refuses_unusable_input(tmp_path, kwargs):
    base = {
        "profile": "acme",
        "run_id": "run-1",
        "urls": URLS,
        "cap": 3,
        "max_age_ms": 1000,
        "content_root": tmp_path,
    }
    with pytest.raises(cm.ManifestError):
        cm.write_manifest(**{**base, **kwargs})


def test_load_refuses_a_manifest_whose_pinned_options_were_edited(tmp_path):
    path = cm.write_manifest(
        profile="acme", run_id="run-1", urls=URLS, cap=3, max_age_ms=86400000, content_root=tmp_path
    )
    data = json.loads(path.read_text())
    data["options"]["onlyMainContent"] = True
    path.write_text(json.dumps(data))
    with pytest.raises(cm.ManifestError):
        cm.load_manifest(path)


def test_load_refuses_garbage_and_missing_files(tmp_path):
    bad = tmp_path / "manifest-x.json"
    bad.write_text("{not json")
    with pytest.raises(cm.ManifestError):
        cm.load_manifest(bad)
    with pytest.raises(cm.ManifestError):
        cm.load_manifest(tmp_path / "absent.json")


# --- the gate --------------------------------------------------------------------------------


def test_a_listed_url_with_the_pinned_options_is_allowed():
    assert cm.CaptureGate(_manifest()).check(SCRAPE, _call(URLS[0])) is None


def test_trailing_slash_and_host_case_do_not_defeat_the_match():
    assert (
        cm.CaptureGate(_manifest()).check(SCRAPE, _call("https://Registry.example.test/members/"))
        is None
    )


def test_an_off_manifest_url_is_denied():
    assert (
        cm.CaptureGate(_manifest()).check(SCRAPE, _call("https://elsewhere.example.test/x"))
        == "url-not-in-manifest"
    )


def test_a_cursor_on_the_same_host_and_path_is_allowed():
    assert cm.CaptureGate(_manifest()).check(SCRAPE, _call(URLS[0] + "?cursor=abc123")) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.test/members?cursor=abc",  # a changed host in a cursor
        "https://registry.example.test/other?cursor=abc",  # a changed path
        "http://registry.example.test/members?cursor=abc",  # a changed scheme
        "https://registry.example.test.evil.test/members",  # a look-alike host
        "https://user@registry.example.test/members",  # userinfo is a different authority
    ],
)
def test_a_cursor_that_changes_host_path_or_scheme_is_denied(url):
    assert cm.CaptureGate(_manifest()).check(SCRAPE, _call(url)) == "url-not-in-manifest"


@pytest.mark.parametrize(
    "over",
    [
        {"onlyMainContent": True},
        {"formats": ["html"]},
        {"formats": ["markdown", "links"]},
        {"maxAge": 0},
        {"actions": [{"type": "click"}]},
        {"waitFor": 5000},
        {"jsonOptions": {"prompt": "x"}},
        {"proxy": "stealth"},
    ],
)
def test_options_that_differ_from_the_pinned_ones_are_denied(over):
    assert cm.CaptureGate(_manifest()).check(SCRAPE, _call(URLS[0], **over)) == "options-differ"


@pytest.mark.parametrize("missing", ["formats", "onlyMainContent", "maxAge"])
def test_a_missing_pinned_option_is_denied_because_its_default_differs(missing):
    call = _call(URLS[0])
    del call[missing]
    assert cm.CaptureGate(_manifest()).check(SCRAPE, call) == "options-differ"


def test_the_cap_is_enforced_across_calls():
    gate = cm.CaptureGate(_manifest(cap=2))
    assert gate.check(SCRAPE, _call(URLS[0])) is None
    assert gate.check(SCRAPE, _call(URLS[1])) is None
    assert gate.check(SCRAPE, _call(URLS[0])) == "cap-exceeded"
    assert gate.used == 2


def test_a_denied_call_does_not_consume_the_cap():
    gate = cm.CaptureGate(_manifest(cap=1))
    assert gate.check(SCRAPE, _call("https://elsewhere.example.test/x")) == "url-not-in-manifest"
    assert gate.check(SCRAPE, _call(URLS[0])) is None


@pytest.mark.parametrize(
    "leaf",
    [
        "firecrawl_search",
        "firecrawl_crawl",
        "firecrawl_map",
        "firecrawl_agent",
        "firecrawl_interact",
    ],
)
def test_every_other_firecrawl_tool_is_denied(leaf):
    assert (
        cm.CaptureGate(_manifest()).check(f"mcp__firecrawl__{leaf}", {"url": URLS[0]})
        == "tool-not-allowed"
    )


def test_the_connector_variant_of_the_scrape_tool_is_governed_too():
    assert (
        cm.CaptureGate(_manifest()).check(
            "mcp__abc-123__firecrawl_scrape", _call("https://x.test/y")
        )
        == "url-not-in-manifest"
    )


@pytest.mark.parametrize(
    "tool",
    [
        "WebFetch",
        "WebSearch",
        "Bash",
        "Write",
        "Edit",
        "NotebookEdit",
        "Glob",
        "Grep",
        "Skill",
        "Task",
        "Agent",
        "mcp__apollo__search",
        "mcp__plugin_gtm-engine_apollo__search",
        "SomeToolNobodyHasHeardOf",
        "",
    ],
)
def test_every_tool_that_is_not_the_scrape_or_the_manifest_read_is_denied(tool):
    """Verification audit 2026-10-02, Critical 4: a capture run is a closed allowlist.

    A page the brain scraped is untrusted text. If any other tool stayed open, text on that page
    could steer the brain to fetch an arbitrary URL, write into content/, or run a shell command.
    """
    gate = cm.CaptureGate(_manifest(), manifest_path="/data/acme/sources/manifest-run-1.json")
    assert gate.check(tool, {"url": "https://evil.test/x", "command": "ls"}) == "tool-not-allowed"
    assert gate.used == 0


MANIFEST_FILE = "/data/acme/sources/manifest-run-1.json"


def test_the_run_may_read_its_own_manifest_and_nothing_else():
    gate = cm.CaptureGate(_manifest(), manifest_path=MANIFEST_FILE)
    assert gate.check("Read", {"file_path": MANIFEST_FILE}) is None
    assert (
        gate.check("Read", {"file_path": "/data/acme/prospects/latest.json"}) == "tool-not-allowed"
    )
    assert (
        gate.check("Read", {"file_path": "/data/acme/sources/manifest-other.json"})
        == "tool-not-allowed"
    )
    assert (
        gate.check("Read", {"file_path": MANIFEST_FILE + "/../latest.json"}) == "tool-not-allowed"
    )
    assert gate.check("Read", {"file_path": "manifest-run-1.json"}) == "tool-not-allowed"
    assert gate.check("Read", {"file_path": 7}) == "tool-not-allowed"
    assert gate.check("Read", {}) == "tool-not-allowed"
    assert gate.check("Read", None) == "tool-not-allowed"


def test_a_gate_with_no_manifest_path_reads_nothing():
    assert (
        cm.CaptureGate(_manifest()).check("Read", {"file_path": MANIFEST_FILE})
        == "tool-not-allowed"
    )


def test_reading_the_manifest_consumes_no_page_budget():
    gate = cm.CaptureGate(_manifest(cap=1), manifest_path=MANIFEST_FILE)
    assert gate.check("Read", {"file_path": MANIFEST_FILE}) is None
    assert gate.used == 0
    assert gate.check(SCRAPE, _call(URLS[0])) is None


def test_an_exhausted_budget_denies_before_the_paid_call_and_consumes_nothing():
    state = {"ok": False}
    gate = cm.CaptureGate(_manifest(cap=1), budget_ok=lambda: state["ok"])
    assert gate.check(SCRAPE, _call(URLS[0])) == "budget-exhausted"
    assert gate.used == 0
    state["ok"] = True
    assert gate.check(SCRAPE, _call(URLS[0])) is None


def test_a_budget_check_that_raises_denies_rather_than_passes():
    def boom():
        raise RuntimeError("ledger unreadable")

    gate = cm.CaptureGate(_manifest(), budget_ok=boom)
    assert gate.check(SCRAPE, _call(URLS[0])) == "budget-exhausted"


def test_a_malformed_tool_input_is_denied_not_crashed():
    gate = cm.CaptureGate(_manifest())
    assert gate.check(SCRAPE, None) == "url-not-in-manifest"
    assert gate.check(SCRAPE, {"url": 42, **PINNED}) == "url-not-in-manifest"


# --- wired into the permission layer ---------------------------------------------------------


def test_classify_tool_denies_through_the_gate_and_is_unchanged_without_one():
    from agent import permissions

    off_manifest = _call("https://elsewhere.example.test/x")
    assert permissions.classify_tool(SCRAPE, off_manifest) == "allow"  # BASE behaviour, no gate
    gate = cm.CaptureGate(_manifest(), manifest_path=MANIFEST_FILE)
    assert permissions.classify_tool(SCRAPE, off_manifest, capture_gate=gate) == "deny"
    assert permissions.classify_tool(SCRAPE, _call(URLS[0]), capture_gate=gate) == "allow"
    # Closed allowlist (audit Critical 4): everything but the scrape and the manifest read is denied
    # under a gate, and unchanged without one.
    assert permissions.classify_tool("Read", {"file_path": "notes/x.txt"}) == "allow"
    assert (
        permissions.classify_tool("Read", {"file_path": "notes/x.txt"}, capture_gate=gate) == "deny"
    )
    assert (
        permissions.classify_tool("Read", {"file_path": MANIFEST_FILE}, capture_gate=gate)
        == "allow"
    )
    for tool, args in (
        ("WebFetch", {"url": "https://x.test/", "prompt": "p"}),
        ("WebSearch", {"query": "q"}),
        ("Bash", {"command": "python -m gtm_core.signal_sources capture"}),
        ("Write", {"file_path": "content/x", "content": "y"}),
    ):
        assert permissions.classify_tool(tool, args, capture_gate=gate) == "deny", tool


def test_the_headless_callback_denies_and_writes_the_reason_to_denials_jsonl(tmp_path):
    pytest.importorskip("claude_agent_sdk")
    import asyncio
    from types import SimpleNamespace

    from agent import denial_log, permissions

    cfg = SimpleNamespace(content_root=tmp_path)
    cb = permissions.make_headless_can_use_tool(
        denial_log.make_denial_sink(cfg, "acme", "capture-test"),
        capture_gate=cm.CaptureGate(_manifest()),
    )
    result = asyncio.run(cb(SCRAPE, _call("https://elsewhere.example.test/secret-path"), None))
    assert type(result).__name__ == "PermissionResultDeny"
    assert "url-not-in-manifest" in result.message
    rows = [json.loads(x) for x in (tmp_path / "acme" / "denials.jsonl").read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["tool"] == SCRAPE and rows[0]["decision"] == "deny"
    assert "url-not-in-manifest" in rows[0]["detail"]
    assert "elsewhere" not in json.dumps(rows[0])  # the URL itself is never written to the ledger
    ok = asyncio.run(cb(SCRAPE, _call(URLS[0]), None))
    assert type(ok).__name__ == "PermissionResultAllow"


def _opts_cfg(tmp_path):
    import dataclasses

    from agent.config import Config

    base = Config.from_env(repo_root=tmp_path)
    prof = tmp_path / "profiles" / "template"
    prof.mkdir(parents=True, exist_ok=True)
    (prof / "PROFILE.md").write_text("# Template\n", encoding="utf-8")
    return dataclasses.replace(
        base, content_root=tmp_path / "content", profiles_root=tmp_path / "profiles"
    )


def test_build_agent_options_carries_the_gate_into_the_default_callback(tmp_path, monkeypatch):
    pytest.importorskip("claude_agent_sdk")
    import asyncio

    from agent.session import build_agent_options

    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(tmp_path))
    opts = build_agent_options(
        _opts_cfg(tmp_path), "template", capture_gate=cm.CaptureGate(_manifest())
    )
    denied = asyncio.run(opts.can_use_tool(SCRAPE, _call("https://elsewhere.example.test/x"), None))
    assert type(denied).__name__ == "PermissionResultDeny"


def test_a_gate_with_a_caller_supplied_callback_refuses_rather_than_being_ignored(
    tmp_path, monkeypatch
):
    pytest.importorskip("claude_agent_sdk")
    from agent.session import build_agent_options

    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(tmp_path))

    async def own_callback(*a):  # pragma: no cover - never reached
        return None

    with pytest.raises(ValueError, match="capture_gate"):
        build_agent_options(
            _opts_cfg(tmp_path),
            "template",
            can_use_tool=own_callback,
            capture_gate=cm.CaptureGate(_manifest()),
        )


# --- a query is a paging cursor or it is a channel ------------------------------------------------


@pytest.mark.parametrize(
    "query",
    ["cursor=abc123", "page=2", "page=2&cursor=xyz", "offset=50", "after=A1b2_c3-d4="],
)
def test_a_paging_cursor_is_still_allowed(query):
    assert cm.CaptureGate(_manifest()).check(SCRAPE, _call(f"{URLS[0]}?{query}")) is None


@pytest.mark.parametrize(
    "query",
    [
        "q=alice%40example.test",  # data out in a parameter that is not paging
        "cursor=" + "a" * 101,  # too long to be a cursor
        "cursor=a b",  # not a plain value
        "cursor=a&page=1&p=2",  # more than two
        "cursor=",  # empty
        "cursor",  # malformed pair
        "cursor=a&secret=1",  # a cursor plus something else
    ],
)
def test_a_query_that_is_not_a_paging_cursor_is_denied(query):
    assert (
        cm.CaptureGate(_manifest()).check(SCRAPE, _call(f"{URLS[0]}?{query}"))
        == "url-not-in-manifest"
    )


@pytest.mark.parametrize(
    "url", ["https://[registry.example.test/members", "https://registry.example.test:port/members"]
)
def test_a_malformed_url_is_denied_not_raised(url):
    gate = cm.CaptureGate(_manifest())
    assert gate.check(SCRAPE, _call(url)) == "url-not-in-manifest" and gate.used == 0


@pytest.mark.parametrize(
    "suffix",
    [
        "?utm_source=alice%40example.test",  # url_norm strips utm_*: it must not let data out
        "?UTM_x=" + "a" * 500,
        "#" + "b" * 300,  # a fragment never reaches the source but does reach the scraper
        "?cursor=abc#frag",
        "?page=2&utm_medium=x",
    ],
)
def test_data_cannot_ride_out_in_a_utm_parameter_or_a_fragment(suffix):
    gate = cm.CaptureGate(_manifest())
    assert gate.check(SCRAPE, _call(URLS[0] + suffix)) == "url-not-in-manifest" and gate.used == 0


@pytest.mark.parametrize("url", [URLS[0], URLS[0] + "/", "HTTPS://Registry.Example.Test/members"])
def test_a_listed_page_still_passes_however_its_host_and_slash_are_written(url):
    assert cm.CaptureGate(_manifest()).check(SCRAPE, _call(url)) is None


@pytest.mark.parametrize(
    "mangle",
    [
        lambda u: u.replace("members", "mem\tbers"),  # urlsplit drops \t \r \n; the scraper may not
        lambda u: u.replace("registry", "regis\ntry"),
        lambda u: u + "\n?page=2",
        lambda u: u + "\r\n",
        lambda u: " " + u,
        lambda u: u + " ",
        lambda u: u + "///",
        lambda u: u.replace("members", "mémbers"),
        lambda u: u + "\x00",
        lambda u: u + "?page=2\t",
    ],
)
def test_a_url_with_control_characters_spaces_or_extra_slashes_is_denied(mangle):
    gate = cm.CaptureGate(_manifest())
    assert gate.check(SCRAPE, _call(mangle(URLS[0]))) == "url-not-in-manifest" and gate.used == 0


@pytest.mark.parametrize(
    "url",
    ["https://ok.example.test/a\tb", " https://ok.example.test/a", "https://ok.example.test/a\n"],
)
def test_a_manifest_refuses_a_url_with_control_characters_or_spaces(tmp_path, url):
    with pytest.raises(cm.ManifestError):
        cm.write_manifest(
            profile="acme", run_id="r1", urls=[url], cap=1, max_age_ms=0, content_root=tmp_path
        )


# --- a scoped pack stage: the runner builds the gate and the real callback enforces it -----------


def _scoped_stage_options(tmp_path, monkeypatch) -> list:
    """Run one scoped stage of a declared-scope graph; return the options the runner built.

    Real ``build_agent_options`` and a real manifest file: only the model turn is stubbed, so the
    callback the assertions call is the one the headless run would hand the SDK.
    """
    pytest.importorskip("claude_agent_sdk")
    import asyncio

    from agent import pipeline_executor as pe
    from agent.packs import make_executor_from_pack
    from gtm_core.packs.loader import PackGraph, PackNode

    monkeypatch.setenv("GTM_WORKSPACES_ROOT", str(tmp_path))
    monkeypatch.setenv("GTM_SIGNAL_SOURCES_ENABLED", "1")
    cfg = _opts_cfg(tmp_path)
    cm.write_manifest(
        profile="template",
        run_id="run-1",
        urls=URLS,
        cap=2,
        max_age_ms=86400000,
        content_root=cfg.content_root,
    )
    built: list = []
    real_build = pe.build_agent_options

    def _capturing(*a, **k):
        built.append(real_build(*a, **k))
        return built[-1]

    async def _no_turn(options, prompt):
        return
        yield  # pragma: no cover - an empty async generator

    monkeypatch.setattr(pe, "build_agent_options", _capturing)
    monkeypatch.setattr(pe, "stream_brain_messages", _no_turn)
    graph = PackGraph(
        pack="p",
        variant="v",
        nodes=(PackNode(id="capture", prompt="capture"),),
        internal=True,
        egress_scope="capture_manifest",
    )
    executor = make_executor_from_pack(
        cfg, "template", graph, run_inputs={"manifest_run_id": "run-1"}
    )
    asyncio.run(executor("capture", {"run_id": "r1"}))
    return built


def test_a_scoped_stage_denies_an_off_manifest_url_and_allows_a_listed_one(tmp_path, monkeypatch):
    import asyncio

    (opts,) = _scoped_stage_options(tmp_path, monkeypatch)
    off = asyncio.run(opts.can_use_tool(SCRAPE, _call("https://elsewhere.example.test/x"), None))
    assert type(off).__name__ == "PermissionResultDeny" and "url-not-in-manifest" in off.message
    ok = asyncio.run(opts.can_use_tool(SCRAPE, _call(URLS[0]), None))
    assert type(ok).__name__ == "PermissionResultAllow"
    other = asyncio.run(opts.can_use_tool("mcp__firecrawl__firecrawl_search", {"query": "x"}, None))
    assert type(other).__name__ == "PermissionResultDeny" and "tool-not-allowed" in other.message


def test_a_scoped_stage_makes_no_paid_call_once_the_monthly_cap_is_reached(tmp_path, monkeypatch):
    import asyncio

    (opts,) = _scoped_stage_options(tmp_path, monkeypatch)
    monkeypatch.setattr("agent.budget.vps_budget_ok", lambda *_a: False)
    over = asyncio.run(opts.can_use_tool(SCRAPE, _call(URLS[0]), None))
    assert type(over).__name__ == "PermissionResultDeny" and "budget-exhausted" in over.message
    monkeypatch.setattr("agent.budget.vps_budget_ok", lambda *_a: True)
    assert type(asyncio.run(opts.can_use_tool(SCRAPE, _call(URLS[0]), None))).__name__ == (
        "PermissionResultAllow"
    )
