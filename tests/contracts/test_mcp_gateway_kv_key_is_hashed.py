"""The edge Worker never stores a caller's plaintext API key as a KV key name.

``deploy/mcp-gateway/src/index.ts`` caches each key's entitlement in ``ENTITLEMENTS_KV``. KV key names are
readable by anyone with list access to the namespace, and the backend itself only keeps SHA-256 hashes of
keys — so a cache keyed on ``apikey:<raw key>`` was the one place plaintext keys persisted. The cache key is
now ``apikey:<sha256 hex of the raw key>``.

This test runs the real Worker source under Node (which strips the TypeScript types) against a fake KV and a
stubbed ``fetch``, and records every key name the Worker reads or writes. It proves behaviour, not text: a
miss, a hit and a revoked key all touch only the hashed name, and the hash is recomputed here independently.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INDEX_TS = REPO / "deploy" / "mcp-gateway" / "src" / "index.ts"

if not INDEX_TS.exists():
    # deploy/ is excluded from the OSS carve by design.
    pytest.skip("deploy/ not present (private deployment surface)", allow_module_level=True)

NODE = shutil.which("node")

_KEY = "dummy-key-fictional-0001"  # gitleaks:allow
_REVOKED = "dummy-key-fictional-0002"  # gitleaks:allow

_HARNESS = r"""
import { pathToFileURL } from "node:url";

const [indexPath, liveKey, revokedKey] = process.argv.slice(2);
const worker = (await import(pathToFileURL(indexPath).href)).default;

const store = new Map();
const reads = [];
const writes = [];
const kv = {
  async get(key, type) {
    reads.push(key);
    const v = store.get(key);
    return v === undefined ? null : type === "json" ? JSON.parse(v) : v;
  },
  async put(key, value, opts) {
    writes.push({ key, ttl: opts?.expirationTtl });
    store.set(key, value);
  },
};

let verifyCalls = 0;
globalThis.fetch = async (input, init) => {
  const url = typeof input === "string" ? input : input.url;
  if (url.endsWith("/v1/api-keys/verify")) {
    verifyCalls += 1;
    const auth = init.headers.Authorization;
    if (auth === `Bearer ${revokedKey}`) return new Response("{}", { status: 401 });
    return Response.json({ workspace_id: "ws-fictional", tier: "pro", active: true });
  }
  return new Response("origin-ok", { status: 200 });
};

const env = {
  ENTITLEMENTS_KV: kv,
  ORIGIN_MCP_URL: "https://origin.example.test",
  BACKEND_API_URL: "https://backend.example.test",
};

async function call(key) {
  const pending = [];
  const ctx = { waitUntil: (p) => pending.push(p), passThroughOnException() {} };
  // Bodiless GET: Node, unlike Workers, refuses to re-wrap a streamed body without `duplex`.
  const req = new Request("https://gateway.example.test/mcp", {
    headers: { Authorization: `Bearer ${key}` },
  });
  const res = await worker.fetch(req, env, ctx);
  await Promise.all(pending);
  return res.status;
}

const miss = await call(liveKey);
const verifyAfterMiss = verifyCalls;
const hit = await call(liveKey);
const verifyAfterHit = verifyCalls;
const revoked = await call(revokedKey);

console.log(JSON.stringify({ miss, hit, revoked, verifyAfterMiss, verifyAfterHit, reads, writes }));
"""


def _hashed(key: str) -> str:
    return "apikey:" + hashlib.sha256(key.encode()).hexdigest()


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict:
    if NODE is None:
        pytest.skip("node not on PATH")
    harness = tmp_path_factory.mktemp("gateway") / "harness.mjs"
    harness.write_text(_HARNESS)
    proc = subprocess.run(
        [NODE, str(harness), str(INDEX_TS), _KEY, _REVOKED],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_cache_miss_writes_the_hashed_key_name_with_the_ttl(run):
    assert run["miss"] == 200
    assert run["writes"] == [{"key": _hashed(_KEY), "ttl": 60}]


def test_cache_hit_reads_the_hashed_key_name_and_skips_verify(run):
    assert run["hit"] == 200
    assert run["verifyAfterHit"] == run["verifyAfterMiss"] == 1


def test_a_revoked_key_still_fails_closed_and_is_never_cached(run):
    assert run["revoked"] == 401
    assert _hashed(_REVOKED) in run["reads"]
    assert all(w["key"] != _hashed(_REVOKED) for w in run["writes"])


def test_no_kv_key_name_contains_a_raw_key(run):
    assert run["reads"] == [_hashed(_KEY), _hashed(_KEY), _hashed(_REVOKED)]
    names = run["reads"] + [w["key"] for w in run["writes"]]
    for name in names:
        assert _KEY not in name and _REVOKED not in name
        assert name in {_hashed(_KEY), _hashed(_REVOKED)}
