"""A3 — POST /v1/auth/exchange: the five normative rules, rule-per-test + the
adversarial suite (forged, wrong aud/iss, alg confusion, expired/nbf, missing/
unknown kid, rotation, cross-issuer collision, oversized token).

Zero network: RSA/EC keys are generated in-test (cryptography), the JWKS is
served through the injectable transport, and the identity DB is an in-memory
fake with ON CONFLICT semantics. Convention: no pytest-asyncio (TestClient
drives the async routes); synthetic issuers only (https://idp-a.example).
"""

from __future__ import annotations

import base64
import json
import os
import time
import uuid
from contextlib import asynccontextmanager, contextmanager

import pytest

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-key-32-bytes-long-xx")

import jwt  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec, rsa  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import oidc  # noqa: E402
from backend.routers import auth as auth_router  # noqa: E402

ISSUER_A = "https://idp-a.example"
ISSUER_B = "https://idp-b.example"
AUD = "gtm-backend"


# ── key material (module-scoped: keygen is the slow part) ─────────────────────


@pytest.fixture(scope="module")
def rsa_keys():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return {"key": key, "other": other}


@pytest.fixture(scope="module")
def ec_key():
    return ec.generate_private_key(ec.SECP256R1())


def _pem(private_key) -> bytes:
    return private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _jwk(public_key, kid: str, alg: str) -> dict:
    algo = jwt.algorithms.RSAAlgorithm if alg.startswith("RS") else jwt.algorithms.ECAlgorithm
    d = json.loads(algo.to_jwk(public_key))
    d.update(kid=kid, use="sig", alg=alg)
    return d


def _token(
    private_key,
    *,
    alg: str = "RS256",
    kid: str | None = "kid-a1",
    iss: str = ISSUER_A,
    aud: str = AUD,
    sub: str = "alice",
    exp_delta: int = 3600,
    nbf_delta: int | None = None,
    drop_exp: bool = False,
    extra: dict | None = None,
) -> str:
    now = int(time.time())
    claims: dict = {"iss": iss, "aud": aud, "sub": sub, "iat": now, **(extra or {})}
    if not drop_exp:
        claims["exp"] = now + exp_delta
    if nbf_delta is not None:
        claims["nbf"] = now + nbf_delta
    headers = {"kid": kid} if kid is not None else {}
    return jwt.encode(claims, _pem(private_key), algorithm=alg, headers=headers)


# ── fakes: JWKS transport + identity DB ───────────────────────────────────────


class JwksServer:
    """Injectable transport serving per-URL JWKS docs, counting fetches."""

    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}
        self.fetches = 0
        self.fail = False

    async def __call__(self, url: str, *, timeout: float):
        self.fetches += 1
        if self.fail:
            raise ConnectionError("jwks down")
        return 200, self.docs.get(url, {"keys": []})


class IdentityDb:
    """users + external_identities + workspace_for_user with ON CONFLICT semantics."""

    def __init__(self) -> None:
        self.users: dict[str, dict] = {}  # email -> {id, display_name, password_hash}
        self.identities: dict[tuple[str, str], str] = {}  # (issuer, subject) -> user_id
        self.workspaces: dict[str, str] = {}  # user_id -> workspace_id

    async def fetchval(self, sql: str, *args):
        if "FROM external_identities" in sql:
            return self.identities.get((args[0], args[1]))
        if "SELECT id FROM users WHERE email" in sql:
            row = self.users.get(args[0])
            return row["id"] if row else None
        if "workspace_for_user" in sql:
            return self.workspaces.setdefault(str(args[0]), str(uuid.uuid4()))
        raise AssertionError(f"unexpected fetchval: {sql}")

    async def execute(self, sql: str, *args):
        if "INSERT INTO users" in sql:
            email, display, _hash = args[0], args[1], None
            if email not in self.users:  # ON CONFLICT (email) DO NOTHING
                self.users[email] = {
                    "id": str(uuid.uuid4()),
                    "display_name": display,
                    "password_hash": None,
                }
            return
        if "INSERT INTO external_identities" in sql:
            key = (args[0], args[1])
            self.identities.setdefault(key, str(args[2]))  # ON CONFLICT DO NOTHING
            return
        raise AssertionError(f"unexpected execute: {sql}")


class _FakePool:
    def __init__(self, conn) -> None:
        self._conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self._conn


# ── app harness ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _fresh_oidc(monkeypatch):
    oidc.reset_state()
    yield
    oidc.reset_state()


def _issuers_env(entries: list[dict]) -> str:
    return json.dumps(entries)


def _default_config(with_b: bool = False) -> str:
    entries = [
        {
            "issuer": ISSUER_A,
            "jwks_url": f"{ISSUER_A}/jwks.json",
            "audience": AUD,
            "algs": ["RS256"],
        }
    ]
    if with_b:
        entries.append(
            {
                "issuer": ISSUER_B,
                "jwks_url": f"{ISSUER_B}/jwks.json",
                "audience": AUD,
                "algs": ["ES256"],
            }
        )
    return _issuers_env(entries)


@contextmanager
def exchange_client(monkeypatch, *, env: str, server: JwksServer, db: IdentityDb | None = None):
    monkeypatch.setenv("TRUSTED_ISSUERS", env)
    oidc.reset_state()
    monkeypatch.setattr(oidc, "_httpx_transport", server)
    app = FastAPI()
    app.include_router(auth_router.router, prefix="/v1")
    app.state.pool = _FakePool(db if db is not None else IdentityDb())
    with TestClient(app) as client:
        yield client


def _post(client: TestClient, token: str):
    return client.post("/v1/auth/exchange", json={"token": token})


def _workspace_of(resp) -> str:
    body = resp.json()
    payload = jwt.decode(
        body["access_token"], os.environ["BACKEND_JWT_SECRET"], algorithms=["HS256"]
    )
    return payload["workspace_id"]


# ── boot validation (config is validated at boot, loudly) ─────────────────────


def test_boot_validation_rejects_bad_config():
    ok = {"issuer": ISSUER_A, "jwks_url": f"{ISSUER_A}/j", "audience": AUD, "algs": ["RS256"]}
    for bad in (
        {**ok, "jwks_url": "http://idp-a.example/jwks.json"},  # non-https JWKS
        {**ok, "algs": ["HS256"]},  # symmetric alg in config
        {**ok, "algs": ["none"]},  # 'none' in config
        {**ok, "algs": []},  # empty algs
        {**ok, "issuer": ""},  # empty issuer
        {**ok, "audience": ""},  # empty audience
    ):
        with pytest.raises(ValueError):
            oidc.parse_trusted_issuers(json.dumps([bad]))
    with pytest.raises(ValueError):
        oidc.parse_trusted_issuers(json.dumps([ok, ok]))  # duplicate issuer
    with pytest.raises(ValueError):
        oidc.parse_trusted_issuers("{not json")
    assert oidc.parse_trusted_issuers(None) == {}
    assert oidc.parse_trusted_issuers("") == {}
    # Positive control: a valid entry parses.
    assert ISSUER_A in oidc.parse_trusted_issuers(json.dumps([ok]))


def test_unconfigured_federation_503(monkeypatch):
    server = JwksServer()
    with exchange_client(monkeypatch, env="", server=server) as client:
        resp = _post(client, "irrelevant")
    assert resp.status_code == 503


# ── happy paths (the positive controls every denial below is measured against) ─


def test_exchange_happy_path_rs256_bootstraps_once(monkeypatch, rsa_keys):
    server = JwksServer()
    server.docs[f"{ISSUER_A}/jwks.json"] = {
        "keys": [_jwk(rsa_keys["key"].public_key(), "kid-a1", "RS256")]
    }
    db = IdentityDb()
    with exchange_client(monkeypatch, env=_default_config(), server=server, db=db) as client:
        first = _post(client, _token(rsa_keys["key"]))
        second = _post(client, _token(rsa_keys["key"]))

    assert first.status_code == 200 and second.status_code == 200
    assert set(first.json()) >= {"access_token", "refresh_token", "token_type"}
    # Same (issuer, subject) → same identity, same workspace — no duplicate user.
    assert _workspace_of(first) == _workspace_of(second)
    assert len(db.users) == 1 and len(db.identities) == 1
    # Password-less, synthetic non-deliverable email — never the IdP's email claim.
    (email,) = db.users
    assert email.startswith("fed-") and email.endswith("@federated.invalid")
    assert db.users[email]["password_hash"] is None


def test_second_issuer_es256_zero_code_change(monkeypatch, rsa_keys, ec_key):
    """A second configured issuer (different alg family) works with no code change."""
    server = JwksServer()
    server.docs[f"{ISSUER_A}/jwks.json"] = {
        "keys": [_jwk(rsa_keys["key"].public_key(), "kid-a1", "RS256")]
    }
    server.docs[f"{ISSUER_B}/jwks.json"] = {"keys": [_jwk(ec_key.public_key(), "kid-b1", "ES256")]}
    with exchange_client(monkeypatch, env=_default_config(with_b=True), server=server) as client:
        resp = _post(client, _token(ec_key, alg="ES256", kid="kid-b1", iss=ISSUER_B))
    assert resp.status_code == 200


# ── rule 1: explicit asymmetric algs; none/HS* rejected before key lookup ─────


def test_rule1_alg_none_and_hs_confusion_rejected(monkeypatch, rsa_keys):
    server = JwksServer()
    public_pem = (
        rsa_keys["key"]
        .public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    server.docs[f"{ISSUER_A}/jwks.json"] = {
        "keys": [_jwk(rsa_keys["key"].public_key(), "kid-a1", "RS256")]
    }
    with exchange_client(monkeypatch, env=_default_config(), server=server) as client:
        # alg=none (hand-built: PyJWT refuses to mint these).
        now = int(time.time())
        seg = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
        none_token = (
            f"{seg({'alg': 'none', 'kid': 'kid-a1'})}."
            f"{seg({'iss': ISSUER_A, 'aud': AUD, 'sub': 'alice', 'exp': now + 600})}."
        )
        assert _post(client, none_token).status_code == 401

        # Classic confusion: HS256 signed with the PUBLIC key bytes as the HMAC
        # secret (hand-built — PyJWT itself refuses to mint one, an attacker
        # wouldn't). Must die on the alg check, BEFORE any key lookup.
        import hashlib
        import hmac

        fetches_before = server.fetches
        signing_input = (
            f"{seg({'alg': 'HS256', 'kid': 'kid-a1', 'typ': 'JWT'})}."
            f"{seg({'iss': ISSUER_A, 'aud': AUD, 'sub': 'alice', 'exp': now + 600})}"
        ).encode()
        sig = base64.urlsafe_b64encode(
            hmac.new(public_pem, signing_input, hashlib.sha256).digest()
        ).rstrip(b"=")
        confused = f"{signing_input.decode()}.{sig.decode()}"
        assert _post(client, confused).status_code == 401
        assert server.fetches == fetches_before, "alg check must precede key lookup"


# ── rule 2: kid-only resolution against the boot-pinned JWKS URL ──────────────


def test_rule2_missing_and_unknown_kid_rejected(monkeypatch, rsa_keys):
    server = JwksServer()
    server.docs[f"{ISSUER_A}/jwks.json"] = {
        "keys": [_jwk(rsa_keys["key"].public_key(), "kid-a1", "RS256")]
    }
    with exchange_client(monkeypatch, env=_default_config(), server=server) as client:
        assert _post(client, _token(rsa_keys["key"], kid=None)).status_code == 401
        assert _post(client, _token(rsa_keys["key"], kid="kid-nope")).status_code == 401
        # Positive control on the same config/keys.
        assert _post(client, _token(rsa_keys["key"])).status_code == 200


# ── rule 3: exact iss/aud; exp/nbf with ≤60s skew ─────────────────────────────


def test_rule3_iss_aud_exact_and_60s_skew(monkeypatch, rsa_keys):
    server = JwksServer()
    server.docs[f"{ISSUER_A}/jwks.json"] = {
        "keys": [_jwk(rsa_keys["key"].public_key(), "kid-a1", "RS256")]
    }
    with exchange_client(monkeypatch, env=_default_config(), server=server) as client:
        k = rsa_keys["key"]
        assert _post(client, _token(k, aud="other-audience")).status_code == 401
        assert _post(client, _token(k, iss="https://idp-evil.example")).status_code == 401
        assert _post(client, _token(k, exp_delta=-120)).status_code == 401  # beyond skew
        assert _post(client, _token(k, nbf_delta=120)).status_code == 401  # nbf in future
        assert _post(client, _token(k, drop_exp=True)).status_code == 401  # exp required
        # Within the 60s leeway: expired 30s ago still passes (skew tolerance).
        assert _post(client, _token(k, exp_delta=-30)).status_code == 200


def test_forged_signature_rejected(monkeypatch, rsa_keys):
    """Right kid, right claims, WRONG private key — pure signature forgery."""
    server = JwksServer()
    server.docs[f"{ISSUER_A}/jwks.json"] = {
        "keys": [_jwk(rsa_keys["key"].public_key(), "kid-a1", "RS256")]
    }
    with exchange_client(monkeypatch, env=_default_config(), server=server) as client:
        assert _post(client, _token(rsa_keys["other"], kid="kid-a1")).status_code == 401
        assert _post(client, _token(rsa_keys["key"], kid="kid-a1")).status_code == 200


# ── rule 4: identity key is (issuer, subject) — never subject alone ───────────


def test_rule4_cross_issuer_subject_collision_two_workspaces(monkeypatch, rsa_keys, ec_key):
    server = JwksServer()
    server.docs[f"{ISSUER_A}/jwks.json"] = {
        "keys": [_jwk(rsa_keys["key"].public_key(), "kid-a1", "RS256")]
    }
    server.docs[f"{ISSUER_B}/jwks.json"] = {"keys": [_jwk(ec_key.public_key(), "kid-b1", "ES256")]}
    db = IdentityDb()
    with exchange_client(
        monkeypatch, env=_default_config(with_b=True), server=server, db=db
    ) as client:
        at_a = _post(client, _token(rsa_keys["key"], sub="alice"))
        at_b = _post(client, _token(ec_key, alg="ES256", kid="kid-b1", iss=ISSUER_B, sub="alice"))

    assert at_a.status_code == 200 and at_b.status_code == 200
    assert _workspace_of(at_a) != _workspace_of(at_b), "same subject, two issuers ⇒ two workspaces"
    assert len(db.users) == 2 and len(db.identities) == 2


# ── rule 5: TTL cache + rate-limited refetch; rotation recovers ───────────────


def test_rule5_rotation_recovers_without_restart(monkeypatch, rsa_keys):
    server = JwksServer()
    url = f"{ISSUER_A}/jwks.json"
    server.docs[url] = {"keys": [_jwk(rsa_keys["key"].public_key(), "kid-a1", "RS256")]}
    with exchange_client(monkeypatch, env=_default_config(), server=server) as client:
        assert _post(client, _token(rsa_keys["key"])).status_code == 200

        # Rotate: new key, new kid, old kid gone.
        server.docs[url] = {"keys": [_jwk(rsa_keys["other"].public_key(), "kid-a2", "RS256")]}
        rotated = _token(rsa_keys["other"], kid="kid-a2")

        # Within the refetch rate limit the stale cache rejects (no hammering)…
        monkeypatch.setattr(oidc, "JWKS_REFETCH_MIN_INTERVAL_S", 10_000.0)
        fetches_before = server.fetches
        assert _post(client, rotated).status_code == 401
        assert server.fetches == fetches_before, "unknown kid inside the window must not refetch"

        # …and once the limiter allows a refetch, recovery needs NO restart.
        monkeypatch.setattr(oidc, "JWKS_REFETCH_MIN_INTERVAL_S", 0.0)
        assert _post(client, rotated).status_code == 200
        assert server.fetches > fetches_before


def test_jwks_outage_keeps_cached_keys_and_rejects_unknown(monkeypatch, rsa_keys):
    server = JwksServer()
    server.docs[f"{ISSUER_A}/jwks.json"] = {
        "keys": [_jwk(rsa_keys["key"].public_key(), "kid-a1", "RS256")]
    }
    with exchange_client(monkeypatch, env=_default_config(), server=server) as client:
        assert _post(client, _token(rsa_keys["key"])).status_code == 200
        server.fail = True
        monkeypatch.setattr(oidc, "JWKS_REFETCH_MIN_INTERVAL_S", 0.0)
        # Cached key still verifies through the outage (stale-but-present)…
        monkeypatch.setattr(oidc, "JWKS_TTL_S", 0.0)
        assert _post(client, _token(rsa_keys["key"])).status_code == 200
        # …but an unknown kid can't be resolved while the fetch fails.
        assert _post(client, _token(rsa_keys["key"], kid="kid-a9")).status_code == 401


def test_jwks_empty_cache_is_still_rate_limited(monkeypatch):
    """B4: an outage leaves the cache empty. The old `or not state.keys` clause let
    EVERY request bypass the refetch limiter and hammer the IdP. Now the first
    attempt is allowed (cold start) and a second within the interval is not, even
    though keys never populated."""
    import asyncio

    monkeypatch.setattr(oidc, "JWKS_REFETCH_MIN_INTERVAL_S", 10_000.0)
    cfg = oidc.parse_trusted_issuers(
        json.dumps(
            [
                {
                    "issuer": ISSUER_A,
                    "jwks_url": f"{ISSUER_A}/jwks.json",
                    "audience": AUD,
                    "algs": ["RS256"],
                }
            ]
        )
    )[ISSUER_A]
    server = JwksServer()
    server.fail = True  # IdP down → keys never populate

    async def body():
        assert await oidc.get_signing_key(cfg, "kid-1", transport=server) is None
        assert server.fetches == 1, "cold-start attempt is allowed"
        # second call within the interval: rate-limited despite the empty cache.
        assert await oidc.get_signing_key(cfg, "kid-2", transport=server) is None
        assert server.fetches == 1, "empty cache must not bypass the refetch limiter (B4)"

    asyncio.run(body())


# ── remaining adversarial cases ───────────────────────────────────────────────


def test_oversized_and_malformed_tokens(monkeypatch, rsa_keys):
    server = JwksServer()
    server.docs[f"{ISSUER_A}/jwks.json"] = {
        "keys": [_jwk(rsa_keys["key"].public_key(), "kid-a1", "RS256")]
    }
    with exchange_client(monkeypatch, env=_default_config(), server=server) as client:
        big = "a" * (oidc.MAX_TOKEN_BYTES + 1)
        assert _post(client, big).status_code == 401
        assert server.fetches == 0, "oversized token must be rejected before any work"
        assert _post(client, "not.a.jwt").status_code == 401
        assert _post(client, "garbage").status_code == 401


def test_error_detail_is_uniform(monkeypatch, rsa_keys):
    """No rule oracle: every 401 carries the same generic detail."""
    server = JwksServer()
    server.docs[f"{ISSUER_A}/jwks.json"] = {
        "keys": [_jwk(rsa_keys["key"].public_key(), "kid-a1", "RS256")]
    }
    with exchange_client(monkeypatch, env=_default_config(), server=server) as client:
        details = {
            _post(client, t).json()["detail"]
            for t in (
                _token(rsa_keys["key"], aud="wrong"),
                _token(rsa_keys["key"], kid="kid-nope"),
                _token(rsa_keys["other"], kid="kid-a1"),
                "garbage",
            )
        }
    assert details == {"Invalid federated token"}


def test_exchange_rate_limited_like_login(monkeypatch, rsa_keys):
    """Parity with login: the exchange route carries the same 10/minute limit.
    slowapi wired exactly as backend/main.py does, BEFORE the app serves."""
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded

    from backend.ratelimit import limiter

    monkeypatch.setenv("TRUSTED_ISSUERS", _default_config())
    oidc.reset_state()
    monkeypatch.setattr(oidc, "_httpx_transport", JwksServer())
    app = FastAPI()
    app.include_router(auth_router.router, prefix="/v1")
    app.state.pool = _FakePool(IdentityDb())
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    limiter.reset()
    limiter.enabled = True
    try:
        with TestClient(app) as client:
            codes = [_post(client, "garbage").status_code for _ in range(11)]
    finally:
        limiter.enabled = False
        limiter.reset()
    assert codes[:10] == [401] * 10
    assert codes[10] == 429


# ── native-auth guard for federated users ─────────────────────────────────────


def test_login_null_password_hash_fails_closed(monkeypatch):
    """A federated (password-less) user must not be loggable-in natively — and the
    failure is indistinguishable from a wrong password (no account-type oracle)."""
    from unittest.mock import AsyncMock

    conn = AsyncMock()
    conn.fetchrow.return_value = {"id": str(uuid.uuid4()), "password_hash": None}
    app = FastAPI()
    app.include_router(auth_router.router, prefix="/v1")
    app.state.pool = _FakePool(conn)
    with TestClient(app) as client:
        resp = client.post(
            "/v1/auth/login",
            json={"email": "someone@acme.example", "password": "hunter2-hunter2"},
        )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"
