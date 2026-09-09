"""Pytest layer for `tests/lint/manifest_prose_check.py` and `gtm_core.carve_manifest`.

Together these two are the distribution half of Gate C: `stub_carve` withholds a private
skill's body, `carve_manifest` withholds its manifest docstring, and the prose check holds
the one field that survives both — `description` — to an interface rather than a lab
notebook.

The property worth testing hardest is not the regexes. It is that the docstring strip leaves
a carved manifest IMPORTABLE with every structural field unchanged, because the whole design
rests on that: the carve runs its own pytest, codegen-sync and pack load against the stripped
tree, and a manifest that no longer parses would take the export down with it.
"""

from __future__ import annotations

import ast
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


mpc = _load("manifest_prose_check")


# ── the rule itself ────────────────────────────────────────────────────────────────


def test_the_live_tree_is_clean():
    """The shipped state passes. This is the ratchet: it went green by moving prose into the
    withheld bodies, and it must not go red by moving prose back."""
    assert mpc.scan(REPO) == []


@pytest.mark.parametrize(
    "prose",
    [
        "Renders a thing. Measured 2026-08-30 at 0.865 credits/second over the batch.",
        "Renders a thing, verified 2026-08-19 against nine sampled frames.",
        "Renders a thing. 12 of 12 jobs agreed the floor is real.",
        "Renders a thing; the balance delta showed it cost more than quoted.",
        # NOTE: a RATE ("~23 credits per render") is deliberately NOT here. It moved to §R17
        # (`provider_rate_check.py`) when that rule was written, so exactly one rule reports it.
        # `test_provider_rate_check.py::test_a_rate_is_reported_by_r17_and_not_also_by_r15`
        # pins the partition from both sides.
    ],
)
def test_forensics_phrasings_are_caught(prose):
    assert any(p.search(prose) for _, p, _ in mpc._RULES), prose


@pytest.mark.parametrize(
    "prose",
    [
        # Specs applied forward — the thing this rule must never delete.
        "Captions sit in the centre 60% of frame, ~48-62px at 1080 width, 28-36 chars/line.",
        "Floors `<break time=…>` at 0.5s — below that the render fails outright.",
        "Emits 7-10 cards, or at most 4 for X.",
        "Refuses a shipped VO on anything but a `professional` voice clone.",
    ],
)
def test_specs_are_not_caught(prose):
    """A constraint the skill enforces is interface, not a measurement, however numeric."""
    assert not any(p.search(prose) for _, p, _ in mpc._RULES), prose


def test_private_resolution_matches_gating():
    """`manifest_prose_check` re-derives `oss = "private"` from gating.toml by AST rather than
    importing gtm_core (it runs in pre-commit, before any sync). Pin the copy against the real
    resolver so the two cannot drift."""
    from gtm_core.gating import stub_list

    assert mpc.private_skill_names(REPO) == set(stub_list())


def test_allowlist_entries_are_well_formed():
    """Same contract as every other lint allowlist in this directory: an entry names a real
    skill and a real rule, so a stale exemption cannot silently widen the gate."""
    known_rules = {r for r, _, _ in mpc._RULES} | {"budget"}
    private = mpc.private_skill_names(REPO)
    for entry in mpc.load_allowlist():
        skill, _, rule = entry.partition(":")
        assert skill in private, f"{entry}: {skill!r} is not a private skill"
        assert rule in known_rules, f"{entry}: unknown rule {rule!r}"


# ── §R16: a generation-bearing skill must resolve private ──────────────────────────


def test_the_live_tree_has_no_tier_mismatch():
    assert mpc.scan_tier_mismatch(REPO) == []


@pytest.mark.skipif(
    not (REPO / "plugin" / "skills" / "video-avatar" / "body_template.md").exists(),
    reason="video-avatar body_template.md not present (paid-tier stub) — nothing for "
    "scan_tier_mismatch to find a generation verb in",
)
def test_it_catches_the_defect_that_started_this(tmp_path: Path):
    """POSITIVE CONTROL (§R12). Reconstruct the real 2026-08-21 state — `video-avatar` declared
    PIPELINE with no gating override — and assert the check fires. Without this, a green run
    proves nothing: every gate downstream of `capability_tier` trusts it, which is why this
    defect survived seventeen days and two releases."""
    shutil.copytree(
        REPO / "gtm_core",
        tmp_path / "gtm_core",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copytree(
        REPO / "plugin", tmp_path / "plugin", ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    m = tmp_path / "gtm_core/skills/video_avatar.py"
    m.write_text(
        m.read_text(encoding="utf-8").replace(
            "capability_tier=Tier.PRODUCTION", "capability_tier=Tier.PIPELINE"
        ),
        encoding="utf-8",
    )
    g = tmp_path / "gtm_core/gating.toml"
    src = g.read_text(encoding="utf-8")
    g.write_text(
        src[: src.index("[skills.video-avatar]")] + src[src.index("[skills.creator-brief]") :],
        encoding="utf-8",
    )

    findings = mpc.scan_tier_mismatch(tmp_path)
    assert findings, "the pre-fix video-avatar state must be caught"
    assert "video_avatar.py" in findings[0]
    assert "create_video_from_avatar" in findings[0], "name the verb that gives it away"
    assert "Tier.PRODUCTION" in findings[0], "the message must say how to fix it"


def test_an_explicit_public_override_is_honoured(tmp_path: Path):
    """`airq-scan` is technically PRODUCTION, sold free and shipped PUBLIC by founder decision —
    the reference case for the two axes being separate. A written-down decision must not trip
    this rule, or the rule would force the tier to lie in the other direction."""
    assert "airq-scan" not in " ".join(mpc.scan_tier_mismatch(REPO))
    import tomllib

    policy = tomllib.loads((REPO / "gtm_core" / "gating.toml").read_text(encoding="utf-8"))
    entry = policy["skills"]["airq-scan"]
    assert entry.get("oss") == "public" and entry.get("reason", "").strip(), (
        "the exemption must be an explicit override carrying a reason, not a silent pass"
    )


# ── the docstring strip ────────────────────────────────────────────────────────────


@pytest.fixture
def carved(tmp_path: Path) -> Path:
    dest = tmp_path / "carve"
    dest.mkdir()
    shutil.copytree(
        REPO / "gtm_core",
        dest / "gtm_core",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    return dest


def test_strip_removes_the_docstring_and_keeps_every_structural_field(carved: Path):
    """The load-bearing property: after stripping, each private manifest still parses and its
    `GTMSkill(...)` call is byte-identical — only the docstring changed."""
    from gtm_core.carve_manifest import strip_manifest_docstrings
    from gtm_core.gating import stub_list

    def skill_call(path: Path) -> str:
        src = path.read_text(encoding="utf-8")
        for node in ast.parse(src).body:
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "SKILL" for t in node.targets
            ):
                return ast.get_source_segment(src, node.value) or ""
        raise AssertionError(f"no SKILL assignment in {path}")

    private = sorted(stub_list())
    before = {
        n: skill_call(carved / "gtm_core" / "skills" / f"{n.replace('-', '_')}.py") for n in private
    }

    assert sorted(strip_manifest_docstrings(carved)) == private

    for name in private:
        path = carved / "gtm_core" / "skills" / f"{name.replace('-', '_')}.py"
        src = path.read_text(encoding="utf-8")
        ast.parse(src)  # still valid Python
        assert skill_call(path) == before[name], f"{name}: SKILL call changed"
        assert "not included in this distribution" in ast.get_docstring(ast.parse(src))


def test_strip_leaves_the_carved_registry_importable(carved: Path):
    """Import the STRIPPED tree in a subprocess and confirm the registry is unchanged — the
    carve runs its own pytest and codegen-sync against exactly this state."""
    from gtm_core.carve_manifest import strip_manifest_docstrings

    assert strip_manifest_docstrings(carved)

    probe = (
        "import gtm_core;"
        f" assert gtm_core.__file__.startswith({str(carved)!r}), gtm_core.__file__;"
        " from gtm_core.skills.registry import all_skills;"
        " print(len(all_skills()))"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], cwd=carved, capture_output=True, text=True, check=True
    )

    from gtm_core.skills.registry import all_skills

    assert int(out.stdout.strip()) == len(all_skills())


def test_strip_is_idempotent(carved: Path):
    """The export may be re-run against a fresh carve; a second pass over an already-stripped
    tree must not corrupt it (the stub is itself a valid module docstring)."""
    from gtm_core.carve_manifest import strip_manifest_docstrings

    strip_manifest_docstrings(carved)
    first = (carved / "gtm_core" / "skills" / "video_render.py").read_text(encoding="utf-8")
    strip_manifest_docstrings(carved)
    assert (carved / "gtm_core" / "skills" / "video_render.py").read_text(encoding="utf-8") == first


def test_strip_fails_closed_on_a_missing_manifest(carved: Path):
    """A private skill with no carved manifest means the carve is not the shape this was
    written against — stop, do not skip."""
    from gtm_core.carve_manifest import strip_manifest_docstrings
    from gtm_core.gating import GatingPolicyError

    (carved / "gtm_core" / "skills" / "video_render.py").unlink()
    with pytest.raises(GatingPolicyError) as e:
        strip_manifest_docstrings(carved)
    assert e.value.rule == "missing_manifest"


def test_strip_actually_removes_the_forensics(carved: Path):
    """End-to-end: the specific strings this work was started over must not survive into a
    carved manifest."""
    from gtm_core.carve_manifest import strip_manifest_docstrings

    strip_manifest_docstrings(carved)
    blob = "\n".join(
        p.read_text(encoding="utf-8")
        for p in (carved / "gtm_core" / "skills").glob("*.py")
        if p.stem.replace("_", "-") in __import__("gtm_core.gating", fromlist=["x"]).stub_list()
    )
    for marker in ("0.865", "credits/second", "12 of 12", "shipped twice", "Phase 17"):
        assert marker not in blob, f"{marker!r} survived into a carved private manifest"
