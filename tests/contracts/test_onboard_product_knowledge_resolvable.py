"""Everything the onboarding renderer writes for a product must be reachable by the resolver.

``gtm_core.paths.resolve_knowledge_file()`` is the single source of truth for the
product→profile fallback, and it looks in exactly one place for an override::

    profiles/<profile>/products/<slug>/<filename>

``<filename>`` goes through ``_safe_segment``, which refuses ``/``. So a product file the
renderer writes **below** that level is structurally unreachable: it is written, stamped with
lifecycle frontmatter and walked by the knowledge index, yet every skill that asks for it gets
the profile-level ``knowledge/`` copy instead — silently, with no error anywhere.

That is not hypothetical. Until 2026-09-21 ``render()`` wrote
``products/<slug>/knowledge/icp-personas.md`` and ``.../knowledge/market-scan-config.md``, one
directory too deep, while ``agent/wizard.py`` (the other writer), every hand-placed product and
``agent/readiness.py`` all used the flat level. A live tenant's per-product ICP and market-scan
config sat unread on disk for two months as a result.

This test pins the CLASS of bug, not that one instance: any product-scoped key the renderer
grows must round-trip through the real resolver, whatever directory someone reaches for next.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

onboard_render = pytest.importorskip("agent.onboard.render")

from gtm_core.paths import resolve_knowledge_file  # noqa: E402
from tests.agent.test_onboard import _VALID_DRAFT  # noqa: E402

PROFILE = "acme-corp"


def _write(files: dict[str, str], profiles_root: Path, profile: str) -> None:
    """Materialise a rendered file dict under ``profiles_root/<profile>/``."""
    for rel, content in files.items():
        dest = profiles_root / profile / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")


def _unreachable(files: dict[str, str], profiles_root: Path, profile: str) -> list[str]:
    """The product-scoped keys in ``files`` the resolver cannot reach, as ``key -> reason``.

    Deliberately runs against the files ON DISK, with the profile-level ``knowledge/`` copies
    written too — that is the real condition, and it is what makes the check discriminate: a
    product override at the wrong depth does not error, it quietly resolves to the fallback.
    """
    bad: list[str] = []
    for key in sorted(k for k in files if k.startswith("products/")):
        parts = key.split("/")
        if len(parts) != 3:
            bad.append(
                f"{key} -> nested below products/<slug>/; resolve_knowledge_file() takes a "
                f"BARE filename and can never reach it"
            )
            continue
        _, slug, filename = parts
        want = profiles_root / profile / "products" / slug / filename
        got = resolve_knowledge_file(profiles_root, profile, filename, product=slug)
        if got != want:
            bad.append(f"{key} -> resolver returned {got} (expected {want})")
    return bad


def test_every_rendered_product_file_is_resolvable_via_the_product_override(tmp_path):
    files = onboard_render.render(_VALID_DRAFT)
    _write(files, tmp_path, PROFILE)

    product_keys = [k for k in files if k.startswith("products/")]
    assert product_keys, "renderer emitted no product files — fixture drifted, test is vacuous"

    assert _unreachable(files, tmp_path, PROFILE) == []


#: The exact layout that shipped the bug, as a literal. Deliberately NOT derived from render()
#: output — a negative control that reshapes whatever the renderer currently emits proves nothing
#: once the renderer is the thing under suspicion (it double-nests, and the two keys collapse into
#: one). This fixture is what the tree looked like on 2026-09-20, independent of today's code.
_REGRESSED_LAYOUT = {
    "knowledge/icp-personas.md": "profile-level fallback\n",
    "knowledge/market-scan-config.md": "profile-level fallback\n",
    "products/acme-deploy/PRODUCT.md": "product doc\n",
    "products/acme-deploy/knowledge/icp-personas.md": "product override, one level too deep\n",
    "products/acme-deploy/knowledge/market-scan-config.md": "product override, too deep\n",
}


def test_the_check_fails_on_the_layout_it_exists_to_catch(tmp_path):
    """Negative control (§R18): a check that cannot discriminate is not a check.

    The historical ``knowledge/`` segment must be rejected by the same helper — with the
    profile-level fallback present on disk, exactly as it was when the bug shipped. Note the
    failure mode being pinned: nothing raises, the resolver just answers with the fallback.
    """
    _write(_REGRESSED_LAYOUT, tmp_path, PROFILE)

    bad = _unreachable(_REGRESSED_LAYOUT, tmp_path, PROFILE)
    assert len(bad) == 2, f"expected both overrides flagged, got: {bad}"
    assert all("nested below products/<slug>/" in line for line in bad), bad

    # ...and the flat sibling in the same fixture stays reachable, so the helper is not simply
    # rejecting everything under products/.
    assert not any("PRODUCT.md" in line for line in bad), bad
