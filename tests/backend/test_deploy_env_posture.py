"""`ENV` must resolve to a posture fail-closed, and `staging` must not become `development`.

The bug: three guards compared ENV to the exact string "production", and
deploy/docker-compose.stg.yml sets ENV=staging. No Python compared to "staging", so on the
only deployed stack the CORS guard, the RLS-bypass assertion and the migration-DSN
requirement were all relaxed. The fix inverts the polarity — strict unless explicitly
lenient — so an unrecognised value hardens instead of opening.

The opposite mistake would be worse: widening `is_development` alongside it would turn fake
runs on in staging, serving fabricated runs to real tenants while looking healthy. That is
why the two predicates are not complements, and why it is pinned here.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-for-unit-tests-only-32x")

from gtm_core.deploy_env import is_development, is_hardened  # noqa: E402

# (value, hardened, development)
POSTURE = [
    ("production", True, False),
    ("staging", True, False),
    ("development", False, True),
    ("test", False, False),
    ("", True, False),
    ("Staging", True, False),
    ("  staging  ", True, False),
    ("PRODUCTION", True, False),
    # Case-sensitive on purpose: a near-miss must fail towards STRICT, never be coerced
    # into granting laptop capability. Pinned by the two boot-guard suites as well.
    ("Development", True, False),
    ("DEVELOPMENT", True, False),
    ("Test", True, False),
    ("prod", True, False),
    ("stg", True, False),
    ("dev", True, False),
    ("nonsense", True, False),
]


@pytest.mark.parametrize(("value", "hardened", "development"), POSTURE)
def test_env_resolves_to_the_expected_posture(value, hardened, development):
    assert is_hardened(value) is hardened, f"{value!r} hardened"
    assert is_development(value) is development, f"{value!r} development"


def test_an_unknown_env_hardens_rather_than_opens():
    """The property that makes this fail-closed. Stated on its own because it is the whole
    point: the next environment name someone invents is strict by default."""
    for invented in ("preprod", "qa", "canary", "sandbox", "staging2"):
        assert is_hardened(invented) is True
        assert is_development(invented) is False


# ── the three guards that were failing open ───────────────────────────────────


def test_cors_wildcard_is_refused_on_staging():
    from backend.main import check_cors_origins

    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        check_cors_origins("*", "staging")
    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        check_cors_origins(None, "staging")


def test_cors_explicit_origins_still_boot_on_staging():
    """Positive control (§R12) — a guard that only ever raises discriminates nothing."""
    from backend.main import check_cors_origins

    assert check_cors_origins("https://app.example.com", "staging") == ["https://app.example.com"]


def test_cors_wildcard_is_still_allowed_on_a_laptop():
    from backend.main import check_cors_origins

    assert check_cors_origins("*", "development") == ["*"]


def test_migration_dsn_requirement_now_covers_staging(monkeypatch):
    """`is_prod` gates POSTGRES_MIGRATION_URL and POSTGRES_GTMAPI_PASSWORD. It reads the
    process ENV, so assert through the resolver it now calls."""
    monkeypatch.setenv("ENV", "staging")
    assert is_hardened() is True
    monkeypatch.setenv("ENV", "development")
    assert is_hardened() is False


# ── the widening-in-the-wrong-direction guard ─────────────────────────────────


def test_fake_runs_refuse_to_boot_on_staging():
    """If `is_development` had been widened to match `is_hardened`'s inverse, staging would
    serve scripted runs to real tenants. This is the test that would have caught it."""
    from backend.main import check_fake_runs

    with pytest.raises(RuntimeError, match="GTM_FAKE_RUNS"):
        check_fake_runs("1", "staging")
    with pytest.raises(RuntimeError, match="GTM_FAKE_RUNS"):
        check_fake_runs("1", "production")
    with pytest.raises(RuntimeError, match="GTM_FAKE_RUNS"):
        check_fake_runs("1", "qa")


def test_fake_runs_still_work_on_a_laptop():
    from backend.main import check_fake_runs

    assert check_fake_runs("1", "development") is True
    assert check_fake_runs(None, "development") is False


def test_dev_secrets_are_refused_on_staging():
    """The committed local-dev secrets are public (deploy/.env.dev.example), so they must
    never serve staging either."""
    from backend.main import check_no_dev_secrets

    # Positive control: on a laptop the same value is fine.
    check_no_dev_secrets(
        "development", jwt_secret="local-dev-secret", billing_sync_secret=None, vault_kek=None
    )

    with pytest.raises(RuntimeError):
        check_no_dev_secrets(
            "staging",
            jwt_secret="local-dev-secret",
            billing_sync_secret=None,
            vault_kek=None,
        )
