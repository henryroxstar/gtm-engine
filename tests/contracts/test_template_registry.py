"""Tenant-data contract: the `_template` profile ships a registry that LOADS.

**Why this is a contract test and not a unit test.** Nothing below is a fixture. Every
assertion reads the live ``profiles/_template/knowledge/`` tree, because the claim being
pinned is about the skeleton every new tenant is cloned from, and that claim is only true
of the real files.

**The property.** :func:`gtm_core.messaging.registry.load` treats a *missing* table as a
defect rather than as an empty one — a tenant onboarded without ``angles.toml`` would
otherwise resolve zero angles and read as covered. So a template missing any of the three
files hands every freshly-onboarded tenant a registry that refuses on its first read, and
the operator meets the feature as a stack of errors about files they have never heard of.
The three tables therefore ship *with the skeleton*, present and valid, even where they are
empty.

**The template ships NO ``verified`` claim, on purpose** (asserted below). ``verified`` is
the only status a body may assert as fact, and it is legal only with a ``source`` the
reader can go and open. A template cannot cite a tenant's evidence — it has none — so a
``verified`` example could only carry an invented path, which teaches precisely the reflex
the status exists to prevent. Every example claim ships as ``design-target`` or
``conditional``, neither of which may be stated in the present indicative, so an un-edited
template cannot put a false sentence in an email. The test keeps the *other* branch armed:
should a future edit add a ``verified`` claim, its ``source`` must resolve to a real file.

**Negative controls (§R18).** Two tests here would pass on a broken tree unless the checker
is proven able to fail, so each runs its own matcher over a case built to trip it:
:func:`test_deleting_one_template_file_refuses` deletes each registry file from a tmp copy
in turn, and :func:`test_source_resolution_rejects_an_invented_path` proves the
source-resolves helper is not simply returning ``True``.

**No real name appears below** (§R9): every assertion is written against ids, statuses,
counts and paths, never against pasted prose.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
from pathlib import Path

import pytest

from gtm_core.messaging import registry

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILES_ROOT = REPO_ROOT / "profiles"
TEMPLATE = PROFILES_ROOT / "_template"
TEMPLATE_KNOWLEDGE = TEMPLATE / "knowledge"

#: Derived from the loader's own constants, never retyped. A hand-written list is how this
#: check silently stops covering a fourth table the day one is added.
REGISTRY_FILES: tuple[str, ...] = (
    registry.CLAIMS_FILE,
    registry.PROOF_FILE,
    registry.ANGLES_FILE,
)


def _load_lint(name: str):
    """Load a ``tests/lint`` helper by path — that tree is not an importable package."""
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / f"tests/lint/{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _template_copy(tmp_path: Path) -> Path:
    """A writable ``profiles/`` root holding just ``_template``.

    The binary brand assets and raw source briefs are skipped: nothing the registry reads
    lives under them, and copying them makes the test slow for no property.
    """
    root = tmp_path / "profiles"
    shutil.copytree(
        TEMPLATE,
        root / "_template",
        ignore=shutil.ignore_patterns("brand", "source", "__pycache__"),
    )
    return root


def _source_resolves(source: str) -> bool:
    """Whether a ``<path>[:<line>]`` source names a file that exists under the template.

    Paths are profile-relative (``knowledge/product.md:12``), matching the convention the
    tenant registry headers state. A bare path with no line is accepted — the line number
    is a courtesy to the reader, the file is the claim.
    """
    path = re.sub(r":\d+$", "", source.strip())
    if not path:
        return False
    return (TEMPLATE / path).is_file()


# --- the contract ---------------------------------------------------------------------- #


def test_template_registry_loads() -> None:
    """A tenant cloned from the skeleton does not start life with a refusing registry."""
    resolved = registry.load("_template", profiles_root=PROFILES_ROOT)
    assert resolved.claims, "the template teaches the claim shape by example; it ships none"
    assert resolved.proof, "the template teaches the proof shape by example; it ships none"
    assert resolved.seats, "seats come from role-vocabulary.toml and must resolve"


@pytest.mark.parametrize("filename", REGISTRY_FILES)
def test_every_registry_file_the_loader_requires_exists_in_the_template(filename: str) -> None:
    assert (TEMPLATE_KNOWLEDGE / filename).is_file(), (
        f"{filename} is required by gtm_core.messaging.registry and missing from _template"
    )


@pytest.mark.parametrize("filename", REGISTRY_FILES)
def test_deleting_one_template_file_refuses(tmp_path: Path, filename: str) -> None:
    """Negative control: prove ``test_template_registry_loads`` is not passing vacuously."""
    root = _template_copy(tmp_path)
    (root / "_template" / "knowledge" / filename).unlink()

    with pytest.raises(registry.RegistryError) as excinfo:
        registry.load("_template", profiles_root=root)
    assert filename in str(excinfo.value), (
        f"the refusal must NAME {filename}; the reader is an operator editing TOML"
    )

    # …and the same copy, untouched, loads — so the refusal is the deletion's doing.
    intact = _template_copy(tmp_path / "intact")
    registry.load("_template", profiles_root=intact)


def test_template_carries_no_real_organisation() -> None:
    """No tenant-roster name reaches the skeleton every OTHER tenant is cloned from (§R9)."""
    roster = _load_lint("third_party_roster")
    keys = roster.derive_keys(REPO_ROOT)
    if not roster.has_tenant_data(REPO_ROOT):
        pytest.skip("roster derives from tenant data, which this checkout does not carry")
    pattern = roster.matcher(keys)
    assert pattern is not None, "a roster with no keys cannot discriminate"

    # Negative control: the matcher fires on a name the roster derived. Built at run time from
    # the live roster, never written into this file (§R9 — a test proving we keep other people's
    # names out of the repo must not itself carry one).
    probe = sorted(keys)[0]
    assert pattern.search(f'statement = "A customer at {probe} asked for it."')

    for filename in REGISTRY_FILES:
        text = (TEMPLATE_KNOWLEDGE / filename).read_text(encoding="utf-8")
        hit = pattern.search(text)
        assert hit is None, f"{filename} carries a third-party roster name at {hit and hit.start()}"


def test_template_claims_are_not_verified_without_a_source() -> None:
    """Today: zero ``verified`` claims. Tomorrow: any that appears carries a real source."""
    claims = registry.load("_template", profiles_root=PROFILES_ROOT).claims
    verified = {cid: c for cid, c in claims.items() if c.status == "verified"}

    assert not verified, (
        "the template ships no `verified` claim on purpose — see this module's docstring; "
        f"found {sorted(verified)}"
    )
    for cid, claim in verified.items():  # armed for the day that changes
        assert _source_resolves(claim.source), f"{cid}: `source` {claim.source!r} resolves nowhere"


def test_source_resolution_rejects_an_invented_path() -> None:
    """Negative control for the helper the test above would rely on."""
    assert _source_resolves("knowledge/premise-vocab.toml:1") is True
    assert _source_resolves("knowledge/no-such-file.md:1") is False
    assert _source_resolves("") is False
