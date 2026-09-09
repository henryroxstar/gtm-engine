"""Tripwire corpus for ``gtm_core.content_quality`` — the CLI surface, before the Phase 3A split.

Read ``tests/tripwire/README.md`` first. Unlike ``deck_lint``/``video_lint``/``brief_lint``/
``shots_lint``, this module has no ``Finding``-shaped (tier, rule, severity) return — its four
entry points (``pre_check``, ``post_check``, ``register_check``, ``script_check``) each return a
free-form dict of named boolean ``checks`` plus ``blocking``/``warnings`` prose, built from a
provisioned profile + content-item fixture tree rather than one text blob. A `deck_lint`-style
semantic inventory over every ``checks[...]`` key is real coverage this corpus does not attempt —
it is future hardening for whoever performs the split, not a gap masked here.

What this DOES pin: the CLI surface (``--help`` and the six ``cq-*`` cases already captured by
``support.py`` — pre-check clean/blocked, script overcount, register-recited, post-check text and
low-res reel) exits with the same code and prints the same JSON/text a pure-motion split must
reproduce exactly.
"""

from __future__ import annotations

import pytest

from tests.tripwire import support


def test_help_golden() -> None:
    support.check_help("content_quality")


@pytest.mark.parametrize("case", support.CASES["content_quality"], ids=lambda c: c.name)
def test_cli_golden(case: support.CliCase, tmp_path) -> None:
    support.check_case(case, tmp_path)
