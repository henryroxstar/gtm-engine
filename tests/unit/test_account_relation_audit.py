"""Tests for gtm_core.account_relation_audit."""

from __future__ import annotations

from gtm_core.account_relation_audit import run_audit


def test_audit_passes_on_acme():
    code, report = run_audit("acme")
    assert code == 0
    assert report["unstable_keys"] == []
