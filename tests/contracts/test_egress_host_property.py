"""The three §R6 raw-HTTP exceptions pin their hosts by SET MEMBERSHIP on the parsed
hostname. That is the correct shape, and these tests exist so it stays that way.

The refactor this guards against is `host.endswith(suffix)`, which reads as equivalent and
is not: it admits `evil-acme.example` for `acme.example`. Two classic bypasses were also unpinned —
a userinfo prefix (`https://allowed.host@evil.example/`, where the real host is `evil.example`
but a naive string search finds the allowed name) and a trailing dot (`allowed.host.`, the
absolute-DNS form, which resolves to the same server but is a different string).

Each module is exercised through its own `_check_url`, not a reimplementation — a test that
re-derived the rule would agree with itself and prove nothing (§R18).
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

MODULES = [
    ("gtm_core.dataset_fetch", "ALLOWED_HOSTS"),
    ("gtm_core.reap_upload", "ALLOWED_UPLOAD_HOSTS"),
    ("gtm_core.media_fetch", "ALLOWED_HOSTS"),
]


def _mod(name):
    return importlib.import_module(name)


@pytest.mark.parametrize(("name", "const"), MODULES)
def test_the_allowlist_is_non_empty_and_is_a_set(name, const):
    """Anti-vacuity: an empty allowlist would make every refusal test below pass for the
    wrong reason, and a list/str would change the membership semantics."""
    mod = _mod(name)
    hosts = getattr(mod, const)
    assert isinstance(hosts, frozenset), f"{name}.{const} is no longer a frozenset"
    assert hosts, f"{name}.{const} is empty — the host checks below assert nothing"


@pytest.mark.parametrize(("name", "const"), MODULES)
def test_an_allowed_host_is_accepted(name, const):
    """Positive control (§R12): a checker that refuses everything is not a checker."""
    mod = _mod(name)
    host = sorted(getattr(mod, const))[0]
    assert mod._check_url(f"https://{host}/some/path") == f"https://{host}/some/path"


@pytest.mark.parametrize(("name", "const"), MODULES)
def test_a_userinfo_prefix_cannot_smuggle_an_allowed_name(name, const):
    """`https://<allowed>@evil.example/` really goes to evil.example."""
    mod = _mod(name)
    host = sorted(getattr(mod, const))[0]
    with pytest.raises(mod.EgressRefused):
        mod._check_url(f"https://{host}@evil.example.test/x")


@pytest.mark.parametrize(("name", "const"), MODULES)
def test_a_lookalike_suffix_is_refused(name, const):
    """The endswith-refactor guard: `evil-<allowed>` and `<allowed>.evil.test`."""
    mod = _mod(name)
    host = sorted(getattr(mod, const))[0]
    for bad in (f"evil-{host}", f"{host}.evil.example.test", f"not{host}"):
        with pytest.raises(mod.EgressRefused):
            mod._check_url(f"https://{bad}/x")


@pytest.mark.parametrize(("name", "const"), MODULES)
def test_a_trailing_dot_host_is_refused(name, const):
    """`allowed.host.` is the absolute-DNS form: same server, different string. Exact
    membership refuses it, which is the fail-closed direction — documented here so the
    behaviour is a decision rather than an accident."""
    mod = _mod(name)
    host = sorted(getattr(mod, const))[0]
    with pytest.raises(mod.EgressRefused):
        mod._check_url(f"https://{host}./x")


@pytest.mark.parametrize(("name", "const"), MODULES)
def test_plain_http_is_refused_even_on_an_allowed_host(name, const):
    mod = _mod(name)
    host = sorted(getattr(mod, const))[0]
    with pytest.raises(mod.EgressRefused):
        mod._check_url(f"http://{host}/x")
