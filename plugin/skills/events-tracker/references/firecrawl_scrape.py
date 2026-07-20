#!/usr/bin/env python3
"""Reliable web scraping via Firecrawl — standard-library only (no pip install needed).

Reads the API key from the FIRECRAWL_API_KEY environment variable, or from a
.env file (checked next to this script, in the skill folder, or the current dir).
NEVER hardcode the key in this file.

CLI:
  python firecrawl_scrape.py scrape <url> [--format markdown] [--wait 4000] [--full-page]
                                          [--schema schema.json] [--prompt "..."] [--out out.json]
  python firecrawl_scrape.py extract <url> [<url> ...] --prompt "..." [--schema schema.json]
  python firecrawl_scrape.py search "<query>" [--limit 10] [--scrape]
  python firecrawl_scrape.py map <url> [--search agents] [--limit 100]
  python firecrawl_scrape.py self-check        # verify the key works (costs ~1 credit)

As a module:
  from firecrawl_scrape import scrape, extract, search, map_site
  doc = scrape("https://luma.com/genai-ny", wait_for=4000, only_main_content=False)
  events = scrape(url, json_schema=SCHEMA, json_prompt="Extract every event")["json"]
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://api.firecrawl.dev/v2"


def _load_env_file(path):
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


def get_api_key():
    """Return the Firecrawl key from env or a .env file, or exit with a clear message."""
    if os.environ.get("FIRECRAWL_API_KEY"):
        return os.environ["FIRECRAWL_API_KEY"]
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (
        os.path.join(here, "..", ".env"),
        os.path.join(here, ".env"),
        os.path.join(os.getcwd(), ".env"),
    ):
        _load_env_file(p)
        if os.environ.get("FIRECRAWL_API_KEY"):
            return os.environ["FIRECRAWL_API_KEY"]
    raise SystemExit(
        "ERROR: FIRECRAWL_API_KEY is not set.\n"
        "Set it with:  export FIRECRAWL_API_KEY=fc-...\n"
        "or copy .env.example to .env and put your key there.\n"
        "Get a key at https://www.firecrawl.dev (Dashboard -> API Keys)."
    )


def _post(path, payload, timeout=180):
    key = get_api_key()
    req = urllib.request.Request(
        API + path,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        if e.code == 401:
            raise SystemExit("Firecrawl auth failed (401): check FIRECRAWL_API_KEY.")
        if e.code == 402:
            raise SystemExit("Firecrawl (402): out of credits or plan limit reached.")
        if e.code == 429:
            raise SystemExit("Firecrawl rate limit (429): wait and retry, or reduce volume.")
        raise SystemExit(f"Firecrawl error {e.code}: {body[:600]}")
    except urllib.error.URLError as e:
        raise SystemExit(f"Network error calling Firecrawl: {e}")


def _get(path, timeout=60):
    key = get_api_key()
    req = urllib.request.Request(
        API + path, method="GET", headers={"Authorization": "Bearer " + key}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def scrape(
    url,
    formats=None,
    json_schema=None,
    json_prompt=None,
    wait_for=None,
    only_main_content=True,
    timeout=120000,
    actions=None,
):
    """Scrape ONE url. Returns the data dict: markdown / json / links / metadata.

    For structured data pass json_schema (a JSON-schema dict) and/or json_prompt.
    For JavaScript-heavy pages (Luma, SPAs) pass wait_for (ms) and often
    only_main_content=False so list/grid items aren't stripped out.
    """
    fmts = list(formats) if formats else ["markdown"]
    if json_schema or json_prompt:
        jf = {"type": "json"}
        if json_prompt:
            jf["prompt"] = json_prompt
        if json_schema:
            jf["schema"] = json_schema
        fmts = [f for f in fmts if f != "json"] + [jf]
    body = {"url": url, "formats": fmts, "onlyMainContent": only_main_content, "timeout": timeout}
    if wait_for is not None:
        body["waitFor"] = wait_for
    if actions:
        body["actions"] = actions
    resp = _post("/scrape", body)
    return resp.get("data", resp)


def extract(urls, prompt, schema=None, poll_interval=2, timeout=240):
    """LLM extraction across one or more URLs. Starts a job and polls until done."""
    body = {"urls": urls, "prompt": prompt}
    if schema:
        body["schema"] = schema
    start = _post("/extract", body)
    job = start.get("id") or start.get("jobId")
    if not job:  # already synchronous
        return start.get("data", start)
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = _get(f"/extract/{job}")
        status = st.get("status")
        if status == "completed":
            return st.get("data", st)
        if status == "failed":
            raise SystemExit(f"Firecrawl extract failed: {json.dumps(st)[:400]}")
        time.sleep(poll_interval)
    raise SystemExit("Firecrawl extract timed out.")


def search(query, limit=10, scrape_results=False):
    """Web search. Set scrape_results=True to also pull markdown for each hit."""
    body = {"query": query, "limit": limit}
    if scrape_results:
        body["scrapeOptions"] = {"formats": ["markdown"]}
    return _post("/search", body).get("data", [])


def map_site(url, search=None, limit=100):
    """Return a list of URLs on a site (cheap: ~1 credit). Optional 'search' filter."""
    body = {"url": url, "limit": limit}
    if search:
        body["search"] = search
    return _post("/map", body)


def self_check():
    d = scrape("https://example.com", formats=["markdown"])
    ok = bool(d.get("markdown"))
    print(
        "OK: Firecrawl reachable and key valid."
        if ok
        else "Reached the API but no content came back."
    )
    return ok


def _read_schema(path):
    with open(path) as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description="Reliable scraping via Firecrawl (stdlib only).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scrape", help="scrape one URL")
    s.add_argument("url")
    s.add_argument("--format", default="markdown")
    s.add_argument("--wait", type=int, help="ms to wait for JS render (e.g. 4000)")
    s.add_argument(
        "--full-page", action="store_true", help="onlyMainContent=False (keep lists/grids)"
    )
    s.add_argument("--schema")
    s.add_argument("--prompt")
    s.add_argument("--out")
    e = sub.add_parser("extract", help="LLM-extract structured data across URLs")
    e.add_argument("urls", nargs="+")
    e.add_argument("--prompt", required=True)
    e.add_argument("--schema")
    e.add_argument("--out")
    se = sub.add_parser("search", help="web search (optionally scrape each hit)")
    se.add_argument("query")
    se.add_argument("--limit", type=int, default=10)
    se.add_argument("--scrape", action="store_true")
    se.add_argument("--out")
    m = sub.add_parser("map", help="list URLs on a site")
    m.add_argument("url")
    m.add_argument("--search")
    m.add_argument("--limit", type=int, default=100)
    m.add_argument("--out")
    sub.add_parser("self-check", help="verify the API key works (~1 credit)")
    a = ap.parse_args()

    if a.cmd == "self-check":
        sys.exit(0 if self_check() else 1)
    if a.cmd == "scrape":
        out = scrape(
            a.url,
            formats=[a.format],
            json_schema=_read_schema(a.schema) if a.schema else None,
            json_prompt=a.prompt,
            wait_for=a.wait,
            only_main_content=not a.full_page,
        )
    elif a.cmd == "extract":
        out = extract(a.urls, a.prompt, _read_schema(a.schema) if a.schema else None)
    elif a.cmd == "search":
        out = search(a.query, a.limit, a.scrape)
    elif a.cmd == "map":
        out = map_site(a.url, a.search, a.limit)

    txt = json.dumps(out, indent=2, ensure_ascii=False)
    if getattr(a, "out", None):
        with open(a.out, "w") as f:
            f.write(txt)
        print("wrote", a.out)
    else:
        print(txt)


if __name__ == "__main__":
    main()
