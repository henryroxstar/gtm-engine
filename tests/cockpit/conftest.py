"""Shared setup for cockpit handler tests.

Puts this directory on sys.path so test modules can ``from fakes import …``
(same convention as tests/contracts/minijsonschema), and keeps the
``HERMES_PUBLISH_*`` env clean for every test — ``PublishSettings.from_env``
is read at ``Cockpit`` construction, so a leaked env var would silently arm
the real publisher's kill switch inside a unit test.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

_HERMES_ENV = (
    "HERMES_PUBLISH_ENABLED",
    "HERMES_PUBLISH_URL",
    "HERMES_PUBLISH_SECRET",
    "HERMES_PUBLISH_MAX_PER_HOUR",
    "HERMES_PUBLISH_MAX_CHARS",
)


@pytest.fixture(autouse=True)
def _clean_hermes_env(monkeypatch):
    for var in _HERMES_ENV:
        monkeypatch.delenv(var, raising=False)
