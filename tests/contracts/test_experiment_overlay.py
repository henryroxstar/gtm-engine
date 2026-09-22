"""Contract: the overlay axis, and the refusals that are its entire security model.

Phases 1, 1b and 2 of the ICP & messaging experiment layer. (The design doc is withheld from
the public carve, so it is described here rather than cited by path.)

**Method note, which is the point of this file.** This feature adds no egress, no tool, no
gate and no network call. What it adds is a set of things the system must decline to do — so
a test that asserts "it raised" is worth nothing on its own: it passes identically against an
implementation that raises on everything, and against one checking the wrong condition.

Every refusal below is therefore a **PAIR**: a document or call that must be refused, and a
sibling differing only in the field under test that must succeed. That is what makes the
assertion discriminate (``docs/RULES.md`` §R18).

The single most important test here is :func:`test_an_overlay_never_addresses_the_content_root`.
Cloning a profile is the obvious way to get a temporary ICP, and it is wrong because it forks
``content/`` — and with it the suppression ledger, so a person who opted out receives a second
arc from what is functionally a second sender. That is a compliance failure, not an analytics
one, and it is the reason this feature is an overlay at all.
"""

from __future__ import annotations

import ast
import datetime as _dt
from pathlib import Path

import pytest

from gtm_core import experiments as ex
from gtm_core.assignment import (
    AssignmentError,
    assign_all,
    assign_variant,
    balance,
    collapsed_strata,
    stratum_of,
)
from gtm_core.cells import BASE_OVERLAY, cell_id
from gtm_core.paths import resolve_knowledge_file

REPO = Path(__file__).resolve().parents[2]
TODAY = _dt.date(2026, 9, 21)
FUTURE = "2026-12-31"
PAST = "2026-09-20"


def _overlay(tmp_path: Path, *, slug="w38", expires=FUTURE, files=None, manifest=True) -> Path:
    root = tmp_path / "tenant" / "experiments" / slug
    root.mkdir(parents=True)
    if manifest:
        (root / ex.MANIFEST_NAME).write_text(
            f'slug = "{slug}"\nowner = "strategy"\nquestion = "does it?"\n'
            f'created = "2026-09-21"\nexpires = "{expires}"\n',
            encoding="utf-8",
        )
    for name, body in (files or {"icp-personas.md": "# experimental ICP\n"}).items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return tmp_path


@pytest.fixture
def on(monkeypatch):
    """Kill switch open — the default posture for tests about the OTHER refusals."""
    monkeypatch.setenv(ex.ENABLED_ENV, "1")


# --- the control ----------------------------------------------------------------------


def test_a_well_formed_overlay_admits(tmp_path, on) -> None:
    """If this fails, every "must refuse" test below is meaningless."""
    root = _overlay(tmp_path)
    exp = ex.admit("tenant", "w38", root, today=TODAY)
    assert exp.slug == "w38"
    assert exp.files == ("icp-personas.md",)
    assert exp.expires == _dt.date(2026, 12, 31)


# --- refusal 1: the kill switch ---------------------------------------------------------


def test_the_kill_switch_refuses_and_does_not_fall_back(tmp_path, monkeypatch) -> None:
    """A silent fallback would run the BASE ICP under the experiment's label.

    Asserted on the absence of a result, not only on the raise: a refusal that still resolved
    the base profile and then complained is the bug this is written against.
    """
    root = _overlay(tmp_path)
    monkeypatch.delenv(ex.ENABLED_ENV, raising=False)
    with pytest.raises(ex.OverlayError, match="disabled"):
        ex.admit("tenant", "w38", root, today=TODAY)
    # control: the identical overlay, switch on
    monkeypatch.setenv(ex.ENABLED_ENV, "1")
    assert ex.admit("tenant", "w38", root, today=TODAY).slug == "w38"


@pytest.mark.parametrize(
    "value,enabled",
    [("1", True), ("true", True), ("on", True), ("0", False), ("", False), ("no", False)],
)
def test_the_kill_switch_is_closed_by_default(monkeypatch, value, enabled) -> None:
    monkeypatch.setenv(ex.ENABLED_ENV, value)
    assert ex.overlay_enabled() is enabled
    monkeypatch.delenv(ex.ENABLED_ENV, raising=False)
    assert ex.overlay_enabled() is False


# --- refusal 2: expiry ------------------------------------------------------------------


def test_an_expired_overlay_refuses_on_the_day_after(tmp_path, on) -> None:
    """Pins the BOUNDARY, not just the happy path.

    The guarded failure is a "temporary" ICP that performs well enough that nobody revisits
    it, until it is the tenant's ICP with no one who decided that.
    """
    root = _overlay(tmp_path, expires="2026-09-21")
    assert ex.admit("tenant", "w38", root, today=_dt.date(2026, 9, 21)).slug == "w38"  # on the day
    with pytest.raises(ex.OverlayError, match="expired"):
        ex.admit("tenant", "w38", root, today=_dt.date(2026, 9, 22))  # the day after


def test_a_renewed_overlay_loads_again(tmp_path, on) -> None:
    expired = _overlay(tmp_path / "a", expires=PAST)
    with pytest.raises(ex.OverlayError, match="expired"):
        ex.admit("tenant", "w38", expired, today=TODAY)
    renewed = _overlay(tmp_path / "b", expires=FUTURE)
    assert ex.admit("tenant", "w38", renewed, today=TODAY).slug == "w38"


def test_expiry_is_required(tmp_path, on) -> None:
    root = tmp_path / "tenant" / "experiments" / "w38"
    root.mkdir(parents=True)
    (root / ex.MANIFEST_NAME).write_text(
        'slug = "w38"\nowner = "s"\nquestion = "q"\n', encoding="utf-8"
    )
    (root / "icp-personas.md").write_text("x", encoding="utf-8")
    with pytest.raises(ex.OverlayError, match="expires"):
        ex.admit("tenant", "w38", tmp_path, today=TODAY)


# --- refusal 3: the manifest ------------------------------------------------------------


def test_a_missing_manifest_refuses(tmp_path, on) -> None:
    root = _overlay(tmp_path, manifest=False)
    with pytest.raises(ex.OverlayError, match=ex.MANIFEST_NAME):
        ex.admit("tenant", "w38", root, today=TODAY)


def test_a_slug_that_disagrees_with_its_directory_refuses(tmp_path, on) -> None:
    """They are one identity; a mismatch means one of them attributes results elsewhere."""
    root = tmp_path / "tenant" / "experiments" / "w38"
    root.mkdir(parents=True)
    (root / ex.MANIFEST_NAME).write_text(
        f'slug = "w39"\nowner = "s"\nquestion = "q"\nexpires = "{FUTURE}"\n', encoding="utf-8"
    )
    (root / "icp-personas.md").write_text("x", encoding="utf-8")
    with pytest.raises(ex.OverlayError, match="directory"):
        ex.admit("tenant", "w38", tmp_path, today=TODAY)


@pytest.mark.parametrize("field", ["owner", "question"])
def test_owner_and_question_are_required(tmp_path, on, field) -> None:
    root = tmp_path / "tenant" / "experiments" / "w38"
    root.mkdir(parents=True)
    fields = {"owner": "s", "question": "q"}
    fields[field] = ""
    (root / ex.MANIFEST_NAME).write_text(
        f'slug = "w38"\nowner = "{fields["owner"]}"\nquestion = "{fields["question"]}"\n'
        f'expires = "{FUTURE}"\n',
        encoding="utf-8",
    )
    (root / "icp-personas.md").write_text("x", encoding="utf-8")
    with pytest.raises(ex.OverlayError, match=field):
        ex.admit("tenant", "w38", tmp_path, today=TODAY)


# --- refusal 4: the allowlist -----------------------------------------------------------


@pytest.mark.parametrize("refused", sorted(ex.REFUSED))
def test_each_refused_file_is_refused_by_name(tmp_path, on, refused) -> None:
    """Asserted PER FILE, not in aggregate.

    A per-file assertion is what catches an allowlist that grew a hole: an aggregate test
    ("some refused file is refused") passes while eleven of twelve silently became legal.
    """
    root = _overlay(tmp_path, files={"icp-personas.md": "x", refused: "x"})
    with pytest.raises(ex.OverlayError, match=refused.replace(".", r"\.")):
        ex.admit("tenant", "w38", root, today=TODAY)


def test_an_unknown_file_is_a_load_error_not_a_silent_extra(tmp_path, on) -> None:
    """The direction matters: a silently ignored file reads as a change that is not happening."""
    root = _overlay(tmp_path, files={"icp-personas.md": "x", "notes.md": "x"})
    with pytest.raises(ex.OverlayError, match="notes"):
        ex.admit("tenant", "w38", root, today=TODAY)
    # control: the identical overlay without the stray file
    assert ex.admit("tenant", "w38", _overlay(tmp_path / "b"), today=TODAY).files


@pytest.mark.parametrize("allowed", sorted(ex.OVERLAYABLE))
def test_each_allowlisted_file_admits(tmp_path, on, allowed) -> None:
    """The other half of the pair: everything the allowlist names must actually pass."""
    root = _overlay(tmp_path, files={allowed: "x"})
    assert ex.admit("tenant", "w38", root, today=TODAY).files == (allowed,)


def test_the_industry_directory_is_allowed_but_others_are_not(tmp_path, on) -> None:
    ok = _overlay(tmp_path / "a", files={"industry/banking.md": "x"})
    assert ex.admit("tenant", "w38", ok, today=TODAY).files == ("industry/banking.md",)
    bad = _overlay(tmp_path / "b", files={"secrets/keys.md": "x"})
    with pytest.raises(ex.OverlayError, match="secrets"):
        ex.admit("tenant", "w38", bad, today=TODAY)


def test_an_overlay_that_overrides_nothing_refuses(tmp_path, on) -> None:
    """It would be the base profile wearing an experiment's label."""
    root = tmp_path / "tenant" / "experiments" / "w38"
    root.mkdir(parents=True)
    (root / ex.MANIFEST_NAME).write_text(
        f'slug = "w38"\nowner = "s"\nquestion = "q"\nexpires = "{FUTURE}"\n', encoding="utf-8"
    )
    with pytest.raises(ex.OverlayError, match="overrides nothing"):
        ex.admit("tenant", "w38", tmp_path, today=TODAY)


# --- refusal 5: one overlay per run -----------------------------------------------------


def test_only_one_overlay_per_run() -> None:
    assert ex.parse_overlay_arg("w38") == "w38"
    assert ex.parse_overlay_arg(None) is None
    for bad in ("a,b", "a b"):
        with pytest.raises(ex.OverlayError, match="ONE slug"):
            ex.parse_overlay_arg(bad)


@pytest.mark.parametrize("bad", ["../../etc", "..", "a/b", "a\\b", "$HOME"])
def test_an_overlay_slug_cannot_traverse(bad) -> None:
    with pytest.raises(ValueError):
        ex.parse_overlay_arg(bad)


# --- the load-bearing invariant ---------------------------------------------------------


def test_an_overlay_never_addresses_the_content_root() -> None:
    """``gtm_core.experiments`` must not be able to reach ``content/``.

    Forking a profile forks ``content/`` and therefore the suppression ledger, the
    already-enrolled set and ``latest.json`` — so an experiment run as a profile clone
    re-contacts people who opted out. The overlay design avoids that by living entirely under
    ``profiles/``, and this asserts the module has no way back: no content-root import, no
    ``content`` string anywhere in it.
    """
    src = (REPO / "gtm_core" / "experiments.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
    assert "resolve_content_root" not in imported, (
        "gtm_core.experiments imported a content-root resolver. An overlay that can address "
        "content/ can fork the suppression ledger, which is the exact failure this design "
        "exists to prevent."
    )
    assert "content_root" not in src and "resolve_content_root" not in src


# --- Phase 1: resolution ----------------------------------------------------------------


def test_overlay_beats_product_beats_profile(tmp_path) -> None:
    base = tmp_path / "tenant"
    (base / "knowledge").mkdir(parents=True)
    (base / "products" / "p1").mkdir(parents=True)
    (base / "experiments" / "w38").mkdir(parents=True)
    (base / "knowledge" / "icp-personas.md").write_text("profile", encoding="utf-8")

    r = lambda **kw: resolve_knowledge_file(tmp_path, "tenant", "icp-personas.md", **kw)  # noqa: E731
    assert r().read_text() == "profile"
    assert r(overlay="w38").read_text() == "profile", "an absent overlay file falls through"

    (base / "products" / "p1" / "icp-personas.md").write_text("product", encoding="utf-8")
    assert r(product="p1").read_text() == "product"

    (base / "experiments" / "w38" / "icp-personas.md").write_text("overlay", encoding="utf-8")
    assert r(product="p1", overlay="w38").read_text() == "overlay", "overlay is most specific"


def test_resolution_is_not_admission(tmp_path) -> None:
    """Resolving against an unadmitted overlay must not raise — they are different jobs.

    Admission happens once, at the start of a run, before any model call. Resolution happens
    hundreds of times afterwards. Folding the checks into the resolver would mean either
    re-validating on every read or validating none of them.
    """
    (tmp_path / "tenant" / "knowledge").mkdir(parents=True)
    (tmp_path / "tenant" / "knowledge" / "icp-personas.md").write_text("p", encoding="utf-8")
    assert (
        resolve_knowledge_file(
            tmp_path, "tenant", "icp-personas.md", overlay="never-admitted"
        ).read_text()
        == "p"
    )


# --- Phase 1b: assignment ---------------------------------------------------------------


def test_assignment_is_deterministic_and_salted() -> None:
    people = [(f"p{i}@acme.example", "enterprise", "cto") for i in range(200)]
    a = assign_all(people, ["a", "b"], "w38")
    assert a == assign_all(people, ["a", "b"], "w38"), "same salt, same arms"
    moved = sum(1 for k, v in a.items() if assign_all(people, ["a", "b"], "w39")[k] != v)
    assert 60 < moved < 140, f"a re-salt should move about half, moved {moved}/200"


def test_assignment_is_balanced_within_each_stratum() -> None:
    """Balanced INSIDE the stratum is the whole mechanism — that is what makes an arm
    difference readable as a message difference rather than an audience one."""
    people = [
        (f"p{i}@acme.example", ["enterprise", "startup"][i % 2], ["ceo", "cto", "security"][i % 3])
        for i in range(600)
    ]
    variants = ["a", "b"]
    assigned = assign_all(people, variants, "w38")
    b = balance(assigned, people)
    assert len(b) == 6
    for stratum, counts in b.items():
        n = sum(counts.values())
        skew = abs(counts["a"] - counts["b"]) / n
        assert skew < 0.25, f"{stratum} is {skew:.0%} lopsided over n={n}"
    assert collapsed_strata(b, variants) == []


def test_a_stratum_missing_an_arm_is_named() -> None:
    """A collapsed stratum contributes to one arm's total and to no comparison at all."""
    people = [("solo@acme.example", "enterprise", "ceo")]
    assigned = assign_all(people, ["a", "b"], "w38")
    assert collapsed_strata(balance(assigned, people), ["a", "b"]) == [("enterprise", "ceo")]


def test_assignment_refuses_what_it_cannot_make_readable() -> None:
    with pytest.raises(AssignmentError, match="no variants"):
        assign_variant("a@acme.example", ("enterprise", "cto"), [], "w38")
    with pytest.raises(AssignmentError, match="distinct"):
        assign_variant("a@acme.example", ("enterprise", "cto"), ["a", "a"], "w38")
    with pytest.raises(AssignmentError, match="identity"):
        assign_variant("", ("enterprise", "cto"), ["a", "b"], "w38")
    # control
    assert assign_variant("a@acme.example", ("enterprise", "cto"), ["a", "b"], "w38") in {"a", "b"}


def test_an_unresolved_seat_is_still_assigned() -> None:
    """Dropping them would make the arms not sum to the list — a denominator error nobody sees."""
    assert stratum_of(None, None) == ("unknown", "unknown")
    assert assign_all([("a@acme.example", None, None)], ["a", "b"], "w38")


# --- Phase 2: the cell dimension ---------------------------------------------------------


def test_cell_id_carries_the_overlay_and_defaults_to_base() -> None:
    assert cell_id("enterprise", "cto", "v1") == f"{BASE_OVERLAY}:enterprise:cto:v1"
    assert cell_id("enterprise", "cto", "v1", "w38") == "w38:enterprise:cto:v1"
    assert cell_id("enterprise", "cto", "v1", "") == f"{BASE_OVERLAY}:enterprise:cto:v1"


def test_two_cells_differing_only_in_overlay_are_comparable() -> None:
    """The payoff of making overlay a dimension rather than a tag."""
    from gtm_core.cells import _mark_comparability

    cells = [
        {
            "cell_id": cell_id("enterprise", "cto", "v1", "base"),
            "overlay": "base",
            "segment": "enterprise",
            "seat": "cto",
            "variant": "v1",
            "comparable_on": [],
        },
        {
            "cell_id": cell_id("enterprise", "cto", "v1", "w38"),
            "overlay": "w38",
            "segment": "enterprise",
            "seat": "cto",
            "variant": "v1",
            "comparable_on": [],
        },
        # differs in overlay AND seat — confounded, must NOT be marked
        {
            "cell_id": cell_id("enterprise", "ceo", "v1", "w38"),
            "overlay": "w38",
            "segment": "enterprise",
            "seat": "ceo",
            "variant": "v1",
            "comparable_on": [],
        },
    ]
    _mark_comparability(cells)
    assert any("overlay vs" in e for e in cells[0]["comparable_on"])
    assert not any("w38:enterprise:ceo" in e for e in cells[0]["comparable_on"]), (
        "a pair differing in two dimensions is confounded and must not be offered as a comparison"
    )


def test_a_merged_rubric_is_the_overlays_not_the_profiles(tmp_path) -> None:
    """The wiring the targeting lint depends on, proven end to end on a real tree.

    ``tests/lint/test_profile_targeting_invariants.py`` re-runs its cohort-order and
    geo-bonus checks against the MERGED rubric, but it is parametrised over overlays that
    exist on disk — and today none of the real tenants ships one, so those cases skip.

    A skip is the correct scope (they activate on the first real overlay) but it is NOT
    evidence, and "0 collected, all skipped" reads far too much like "checked". This closes
    that gap the honest way: build a profile and an overlay, and assert the resolver hands
    back the overlay's rubric rather than the tenant's. Shipping a fake overlay into
    `_template` just to make the lint fire would have been gaming it.
    """
    base = tmp_path / "tenant"
    (base / "knowledge").mkdir(parents=True)
    (base / "experiments" / "w38").mkdir(parents=True)
    (base / "knowledge" / "icp-scoring.toml").write_text(
        '[[cohort]]\nname = "live"\nweight = 30\nkeywords = ["a"]\n'
        '[geo_bonus]\n"united states" = 5\n',
        encoding="utf-8",
    )
    (base / "experiments" / "w38" / "icp-scoring.toml").write_text(
        '[[cohort]]\nname = "experimental"\nweight = 20\nkeywords = ["b"]\n'
        '[geo_bonus]\n"singapore" = 4\n',
        encoding="utf-8",
    )

    import tomllib

    def rubric(overlay=None):
        return tomllib.loads(
            resolve_knowledge_file(
                tmp_path, "tenant", "icp-scoring.toml", overlay=overlay
            ).read_text()
        )

    assert rubric()["cohort"][0]["name"] == "live"
    assert rubric("w38")["cohort"][0]["name"] == "experimental", (
        "the lint would have validated the tenant's rubric while the run used the overlay's"
    )
    assert set(rubric("w38")["geo_bonus"]) == {"singapore"}
