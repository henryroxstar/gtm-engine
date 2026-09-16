"""One canonical reading of the ``ENV`` deploy-environment variable.

Why this module exists. Three security guards compared ``ENV`` to the exact string
``"production"`` to decide whether to be strict:

* ``backend/main.py`` — whether a wildcard/unset ``CORS_ORIGINS`` may boot alongside
  ``allow_credentials=True``;
* ``gtm_core/db.py`` — whether a superuser/``BYPASSRLS`` runtime role is a hard failure or
  a ``warnings.warn`` (i.e. whether RLS being inert stops the process);
* ``backend/main.py`` again — whether ``POSTGRES_MIGRATION_URL`` and
  ``POSTGRES_GTMAPI_PASSWORD`` are required, or the two-role split may silently collapse.

``deploy/docker-compose.stg.yml`` sets ``ENV: staging``. No Python anywhere compared to
``"staging"``, so on the ONLY deployed stack all three guards were relaxed — the exact
posture they exist to prevent. The bug is not the string; it is that an unrecognised value
resolved to *lenient*. A guard whose default is "off" fails open on every future typo and
every new environment name.

So the polarity is inverted here: **strict unless explicitly told otherwise.** Only the two
names that genuinely need a relaxed posture — ``development`` (a laptop) and ``test``
(synthesised under pytest) — are lenient. Everything else, including an empty string, a typo
and a name nobody has invented yet, is hardened.

The two predicates are deliberately NOT complements. ``is_development()`` is exact-match and
gates things that must exist *only* on a laptop — fake runs, dev secrets, uvicorn reload.
Widening that alongside :func:`is_hardened` would have turned fake runs on in staging, which
is the opposite mistake and a worse one.
"""

from __future__ import annotations

import os

#: The deploy environments that get a RELAXED posture. Everything else is hardened,
#: including an unrecognised value — see the module docstring.
LENIENT_ENVS: frozenset[str] = frozenset({"development", "test"})

#: The single laptop-only environment. Exact match, never widened.
DEVELOPMENT = "development"

#: Recognised names, for error messages and tests. Not a validation set: an unknown value is
#: hardened rather than rejected, because refusing to boot on a typo in ENV would be a worse
#: failure than running strict.
KNOWN_ENVS: frozenset[str] = frozenset({"production", "staging", "development", "test"})


def deploy_env(value: str | None = None) -> str:
    """The deploy environment, whitespace-trimmed. Defaults to ``production`` when unset.

    Deliberately **case-SENSITIVE**: ``"Development"`` is not ``"development"``. The
    lenient postures grant capability (fake runs, dev secrets, a wildcard CORS), so a
    near-miss must fail towards strict rather than be helpfully coerced. Casefolding here
    would silently turn ``ENV=Development`` on a real host into a laptop posture —
    ``tests/backend/test_hardening_residuals.py`` and ``test_fake_runs.py`` already pin
    that, and they caught exactly that mistake when this module was introduced.
    """
    raw = os.getenv("ENV", "production") if value is None else value
    return (raw or "").strip()


def is_hardened(value: str | None = None) -> bool:
    """True when this environment must run production-grade guards.

    Fail-closed: true for ``production`` and ``staging``, and **also** for any value this
    module does not recognise. Only :data:`LENIENT_ENVS` opt out.
    """
    return deploy_env(value) not in LENIENT_ENVS


def is_development(value: str | None = None) -> bool:
    """True only for the exact ``development`` environment.

    Gates laptop-only capability (fake runs, dev secrets, reload). Never widened to include
    staging — see the module docstring.
    """
    return deploy_env(value) == DEVELOPMENT
