"""The canonical Soul must agree across every kit that carries a copy of it.

The defect this prevents (recorded 2026-08-18, resolved 2026-08-20): ``identity/henry/IDENTITY.toml``
declared one Soul while a profile's generated ``BRAND.toml`` copy declared a different live one.
Both were ``ready`` on the provider, so nothing failed loudly — two profiles would simply render two
different faces for the same person, and the *stated source of truth* was the one that was behind.

The trap that makes a comment insufficient: the superseded Soul carries the **later** training date.
Anyone reasoning from recency will "correct" the canonical id back. Only a test stops that.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
IDENTITY = REPO / "identity/henry/IDENTITY.toml"

pytestmark = pytest.mark.skipif(
    not IDENTITY.is_file(),
    # identity/ and profiles/ are both excluded from the OSS carve by design.
    reason="identity/ not present (private operator identity)",
)


def _load(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _targets() -> list[Path]:
    """The kits IDENTITY.toml itself declares as carrying a copy of its handles."""
    declared = _load(IDENTITY).get("propagate", {}).get("handle_targets", [])
    return [REPO / rel for rel in declared]


def test_every_declared_handle_target_exists() -> None:
    """A target listed but absent means propagation silently covers nothing."""
    missing = [str(p.relative_to(REPO)) for p in _targets() if not p.is_file()]
    assert not missing, (
        f"IDENTITY.toml [propagate].handle_targets names kit(s) that do not exist: {missing}. "
        "Either create the kit or drop it from the list — a target that isn't there is a "
        "propagation rule that quietly applies to nothing."
    )


def test_soul_id_agrees_across_every_company_kit() -> None:
    """Company kits must carry the canonical Soul, or carry none at all.

    Product kits are deliberately NOT checked: a product kit may set ``soul_id = ""`` as a real
    override to empty (render generically), which is a different statement from "inherit".
    """
    canonical = _load(IDENTITY)["handles"]["soul_id"]
    assert canonical, (
        "IDENTITY.toml [handles].soul_id is empty — there is no canonical Soul to hold"
    )

    disagreeing = {}
    for kit in _targets():
        if not kit.is_file():
            continue  # covered by the test above
        found = _load(kit).get("identity", {}).get("soul_id", "")
        if found and found != canonical:
            disagreeing[str(kit.relative_to(REPO))] = found

    assert not disagreeing, (
        f"kit(s) declare a Soul other than the canonical {canonical}: {disagreeing}. "
        "Fix by writing the canonical id through the brandkit CLI — never by editing the TOML:\n"
        "  uv run python -m gtm_core.brandkit --profile <p> --set identity.soul_id "
        f"--value {canonical} --note '<why, dated>'\n"
        "Do NOT resolve this the other way by reading training dates: the superseded Soul is the "
        "one with the LATER retrain date. Canonical is an operator decision, not a timestamp."
    )
