"""tests/media/ is the one directory with an environment prerequisite (ffmpeg). Fail collection
loudly by default when it is missing — a silent skip here would reproduce exactly the failure
mode this tier exists to fix (a green suite that measured
nothing). GTM_TEST_ALLOW_NO_FFMPEG=1 is the only opt-out, and it is asserted to appear nowhere in
CI by tests/contracts/test_ci_media_wiring.py.
"""

from __future__ import annotations

import os
import shutil

import pytest


def pytest_collection_modifyitems(config, items):
    if not items:
        return
    if shutil.which("ffmpeg") is not None:
        return
    if os.environ.get("GTM_TEST_ALLOW_NO_FFMPEG") == "1":
        return
    pytest.exit(
        "tests/media requires ffmpeg on PATH and it is not present. This directory's tests are "
        "not allowed to silently skip (that is the exact failure mode this test tier exists to "
        "prevent). Install ffmpeg, or set GTM_TEST_ALLOW_NO_FFMPEG=1 to explicitly opt out "
        "locally (never in CI).",
        returncode=4,
    )
