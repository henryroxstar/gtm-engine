"""A visual-reference register may not lie about what it holds.

WHY THIS EXISTS
---------------
The brand document's §13 ends with a rule:

    "Do not leave a reference ID in a user-facing prompt unless the corresponding reference file
    is available to the AI assistant."

That rule is unenforceable on its own. A prompt can cite ``REF-ABSTRACT-01``, the id can resolve to
nothing, and no check notices — the generated asset is simply less consistent and nobody can say
why. ``knowledge/brand/reference-register.md`` was created to make it checkable by listing every id
as PRESENT (with a real path) or ABSENT.

But the register only moves the problem one level up unless something verifies the register itself.
Its own Rule 5 says so:

    "A row whose file does not exist must say ABSENT — a register that lies is worse than no
    register, because it restores exactly the unenforceable state this file was created to end."

This test is what makes Rule 5 true rather than aspirational. It became load-bearing on 2026-09-02,
when the brand team's image drop moved three slots from ABSENT to PRESENT: before that the register
had two rows and was easy to eyeball, and now it has five paths that skills may cite.

WHAT IS CHECKED, AND WHY NOT MORE
---------------------------------
1. A PRESENT row must link a file that EXISTS. The whole point.
2. An ABSENT row must NOT name a file. The inverse failure — a row that quietly gained a path
   without gaining a status — would let a prompt cite an id the brand team never approved.
3. Every image in ``reference-images/`` must be mentioned by the register. Catches the orphan: a
   file vendored into the repo that no row governs is a file with no usage rules attached, and the
   next pass will cite it as though it were approved.

Deliberately NOT checked: palette conformance of the referenced images. Provenance here is
"supplied by the brand team", not a measured colour check — and a dominant-chroma sample cannot
separate the approved ``#3464FD`` from the retired ``#1D58FC`` (~10° apart in hue). Asserting
on-palette-ness would encode a claim this repo cannot actually verify, which is the failure mode
this whole file exists to prevent.

The test is driven by ``references.register`` in each profile's BRAND.toml, so a second tenant that
adds a register is covered automatically and one that has none is skipped rather than failed.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILES = REPO_ROOT / "profiles"

_ROW = re.compile(
    r"^\|\s*`(REF-[A-Z0-9-]+)`\s*\|\s*(\*\*PRESENT\*\*|PRESENT|ABSENT)\s*\|\s*([^|]*)\|",
    re.MULTILINE,
)
_LINK = re.compile(r"\]\(([^)]+)\)")
_EMPTY_CELL = {"—", "-", ""}


def _registers() -> list[tuple[str, Path]]:
    """(profile, register path) for every profile whose kit declares one."""
    found = []
    for kit_path in sorted(PROFILES.glob("*/knowledge/BRAND.toml")):
        kit = tomllib.loads(kit_path.read_text(encoding="utf-8"))
        rel = (kit.get("references") or {}).get("register")
        if rel:
            found.append((kit_path.parents[1].name, REPO_ROOT / rel))
    return found


REGISTERS = _registers()


def _rows(register: Path):
    return _ROW.findall(register.read_text(encoding="utf-8"))


@pytest.mark.skipif(not REGISTERS, reason="no profile declares references.register")
@pytest.mark.parametrize("profile,register", REGISTERS, ids=[p for p, _ in REGISTERS])
def test_register_file_exists_and_has_rows(profile: str, register: Path) -> None:
    assert register.is_file(), (
        f"{profile}: references.register points at {register}, which is absent"
    )
    assert _rows(register), f"{profile}: register {register.name} declares no REF- rows"


@pytest.mark.skipif(not REGISTERS, reason="no profile declares references.register")
@pytest.mark.parametrize("profile,register", REGISTERS, ids=[p for p, _ in REGISTERS])
def test_present_rows_resolve_to_a_real_file(profile: str, register: Path) -> None:
    """Rule 5, the forward direction: PRESENT must mean the file is actually there."""
    broken = []
    for ref_id, status, cell in _rows(register):
        if "PRESENT" not in status:
            continue
        link = _LINK.search(cell)
        if not link:
            broken.append(f"{ref_id}: PRESENT but the File cell carries no link")
            continue
        target = (register.parent / link.group(1)).resolve()
        if not target.is_file():
            broken.append(f"{ref_id}: PRESENT -> {link.group(1)} does not exist")
    assert not broken, f"{profile}: register rows lie about PRESENT files:\n  " + "\n  ".join(
        broken
    )


@pytest.mark.skipif(not REGISTERS, reason="no profile declares references.register")
@pytest.mark.parametrize("profile,register", REGISTERS, ids=[p for p, _ in REGISTERS])
def test_absent_rows_name_no_file(profile: str, register: Path) -> None:
    """Rule 5, the inverse: a row may not gain a path without gaining a status."""
    offenders = [
        f"{ref_id}: ABSENT but the File cell reads {cell.strip()!r}"
        for ref_id, status, cell in _rows(register)
        if "PRESENT" not in status and cell.strip() not in _EMPTY_CELL
    ]
    assert not offenders, f"{profile}: ABSENT rows naming a file:\n  " + "\n  ".join(offenders)


@pytest.mark.skipif(not REGISTERS, reason="no profile declares references.register")
@pytest.mark.parametrize("profile,register", REGISTERS, ids=[p for p, _ in REGISTERS])
def test_no_orphaned_reference_images(profile: str, register: Path) -> None:
    """A vendored image no row governs is an image with no usage rules attached."""
    kit = tomllib.loads(
        (PROFILES / profile / "knowledge" / "BRAND.toml").read_text(encoding="utf-8")
    )
    rel = (kit.get("references") or {}).get("images")
    if not rel:
        pytest.skip(f"{profile}: kit declares no references.images directory")
    images_dir = REPO_ROOT / rel
    assert images_dir.is_dir(), f"{profile}: references.images -> {rel} is not a directory"
    text = register.read_text(encoding="utf-8")
    orphans = sorted(p.name for p in images_dir.iterdir() if p.is_file() and p.name not in text)
    assert not orphans, (
        f"{profile}: {rel} holds images the register never mentions, so nothing says how they may "
        f"be used:\n  " + "\n  ".join(orphans)
    )
