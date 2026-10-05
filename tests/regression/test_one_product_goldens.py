"""T0.3: HEAD's output equals BASE's for a default-product run, minus the named additions.

BASE is ``efa7e349`` (see ``goldens/one_product_untouched.json``). The golden was produced by
running ``one_product_capture.capture`` against a ``git archive`` of that commit, never a worktree.
``oneprod`` and ``branddirs`` must match in *either* mode (with and without the default product
named): that is the literal "a tenant with one product notices nothing". ``realshape`` must match
when the default product is named.

``PERMITTED_ADDITIONS`` lists the only new files a second product may add. It is asserted **unused**
for every case here, because none of them runs a second product.
"""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path

import pytest
from one_product_capture import capture

HERE = Path(__file__).parent
FIXTURES = HERE.parent / "fixtures" / "one_product" / "profiles"
GOLDEN = json.loads((HERE / "goldens" / "one_product_base.json").read_text(encoding="utf-8"))

#: New second-product files that BASE never had. Anything else that differs fails the test.
PERMITTED_ADDITIONS = (
    "**/by-product/**",
    "**/run_state.*.json",
    "**/.compat/**",
)
#: Named, dated deletions from committed tenant data (the Stream move). Empty for these fixtures.
PERMITTED_REMOVALS: tuple[str, ...] = ()

CASES = [
    pytest.param("realshape:alpha", "realshape", "alpha", id="realshape-default-named"),
    pytest.param("oneprod", "oneprod", None, id="oneprod-no-product"),
    pytest.param("oneprod", "oneprod", "solo", id="oneprod-default-named"),
    pytest.param("branddirs", "branddirs", None, id="branddirs-no-product"),
    pytest.param("branddirs", "branddirs", "north", id="branddirs-default-named"),
]


def _strip_permitted(files: list[str]) -> tuple[list[str], list[str]]:
    kept, used = [], []
    for f in files:
        (used if any(fnmatch.fnmatch(f, pat) for pat in PERMITTED_ADDITIONS) else kept).append(f)
    return kept, used


@pytest.mark.parametrize(("golden_key", "profile", "product"), CASES)
def test_head_equals_base_for_the_default_product(tmp_path, golden_key, profile, product):
    got = capture(FIXTURES, tmp_path, profile, product=product, scoped=product is not None)
    want = GOLDEN[golden_key]

    files, used = _strip_permitted(got.pop("content_files"))
    want_files = want["content_files"]
    assert used == [], f"a default-product run used a permitted addition: {used}"
    assert files == want_files

    for key, value in want.items():
        if key == "content_files":
            continue
        assert got[key] == value, f"{key} differs from BASE for {golden_key} (product={product})"
    assert set(got) == set(want) - {"content_files"}


def test_the_permitted_lists_stay_narrow():
    """A broad glob here would excuse any regression; each entry names a second-product file."""
    assert all(
        "by-product" in p or "run_state." in p or ".compat" in p for p in PERMITTED_ADDITIONS
    )
    assert PERMITTED_REMOVALS == ()
