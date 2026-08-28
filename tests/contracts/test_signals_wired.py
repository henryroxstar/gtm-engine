"""Contract: the always-on signal layer stays CONNECTED to something.

``gtm_core/signals.py`` shipped 2026-07-21 in ``eb8993b`` ("feat(post-reply lifecycle):
Calendly scheduling, inbound triage, always-on signals") and sat with NO consumer outside
``tests/test_signals.py`` until W1b wired ``agent/optout_sweep.py`` on 2026-08-27. These
assertions are what keep it that way. Its own design note still described the whole thing
as "design only — no code yet" five weeks after the code landed, which is how a whole
module stays inert without anyone noticing.

Modelled on ``test_email_quality_wired.py::test_roles_judge_has_a_consumer``, against the
same failure: every function here is unit-tested in ``tests/test_signals.py``, and not one
of those tests notices that nothing ever calls them.

NAMING TRAP — there are TWO ``signals`` modules. ``gtm_core/voc/signals.py`` also exists
and IS consumed (``gtm_core/voc/delta.py``). A consumer test that greps for ``signals``
passes vacuously on the wrong module, so every assertion below pins ``gtm_core.signals``
and its specific function names.

Fixtures: no third-party data of any kind is used here (docs/RULES.md §R9).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

#: The three entry points the signal loop needs a caller for.
_SIGNAL_API = ("build_signal", "new_signals", "record_signals")

#: Trees a legitimate consumer could live in. Deliberately excludes tests/.
_CONSUMER_TREES = ("agent", "gtm_core", "cockpit")


def _iter_candidates():
    """Every non-test .py that could legitimately consume gtm_core.signals."""
    for tree in _CONSUMER_TREES:
        root = REPO / tree
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if path.name == "signals.py" or "voc" in path.parts:
                continue  # the module itself, and its same-named VoC cousin
            yield path


def _parsed(path: Path) -> ast.AST | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return None


def _consumer_files() -> list[Path]:
    """Files that IMPORT gtm_core.signals — AST, never text.

    A textual search false-positives on prose: gtm_core/optout_watch.py's docstring says
    it "mirrors ``gtm_core.signals``", which is a comparison, not a consumer. That very
    false positive XPASSed this test on first run, which is precisely why it is AST-based
    now — the same reason `repo_root_literals` is.
    """
    out: list[Path] = []
    for path in _iter_candidates():
        tree = _parsed(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(a.name == "gtm_core.signals" for a in node.names):
                    out.append(path)
                    break
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                from_pkg = mod == "gtm_core" and any(a.name == "signals" for a in node.names)
                # `from .signals import X` inside gtm_core/ (level>0, relative)
                relative = node.level > 0 and mod == "signals"
                if mod == "gtm_core.signals" or from_pkg or relative:
                    out.append(path)
                    break
    return out


def _callers_of(func: str) -> list[Path]:
    """Files containing a real CALL to ``func`` — AST, so a mention in prose never counts."""
    out: list[Path] = []
    for path in _iter_candidates():
        tree = _parsed(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            name = (
                target.id
                if isinstance(target, ast.Name)
                else target.attr
                if isinstance(target, ast.Attribute)
                else None
            )
            if name == func:
                out.append(path)
                break
    return out


# ── non-vacuity: the module and the search space must both be real ───────────


def test_the_signal_module_and_its_api_are_present():
    """Anti-vacuity: pin the API so a rename fails LOUD instead of emptying this file."""
    from gtm_core import signals

    missing = [name for name in _SIGNAL_API if not hasattr(signals, name)]
    assert not missing, f"gtm_core.signals lost {missing} — this contract now asserts nothing"


def test_the_two_signals_modules_are_still_distinct():
    """Guards the naming trap this file's docstring describes.

    If voc/signals.py is ever merged into gtm_core/signals.py, the exclusion in
    `_consumer_files` starts hiding real consumers and this contract silently inverts.
    """
    assert (REPO / "gtm_core" / "signals.py").exists()
    assert (REPO / "gtm_core" / "voc" / "signals.py").exists(), (
        "gtm_core/voc/signals.py is gone — re-check the 'voc' exclusions in this file, "
        "they may now be hiding a legitimate consumer of gtm_core.signals"
    )


def test_the_voc_signals_module_is_the_one_with_a_consumer():
    """The negative control that makes the trap concrete: voc.signals IS wired."""
    delta = REPO / "gtm_core" / "voc" / "delta.py"
    assert delta.exists() and "signals" in delta.read_text(encoding="utf-8"), (
        "the VoC signals consumer moved; this control no longer demonstrates the collision"
    )


# ── T4: the contract ─────────────────────────────────────────────────────────


def test_record_signals_has_a_consumer_outside_its_own_tests():
    """A signal recorder nothing calls records nothing.

    `record_signals` appends to history.jsonl so the next pass dedups — the whole
    always-on loop. With no caller, the dedup source stays empty and every "eyes on the
    market" claim in the design docs is aspirational.
    """
    callers = _callers_of("record_signals")
    assert callers, (
        "nothing under agent/, gtm_core/ or cockpit/ calls gtm_core.signals.record_signals. "
        "The module is shipped and inert — the same defect as a registry role with no "
        "consumer. See PRD §3.6 (W1b: the sensor pass)."
    )


def test_gtm_core_signals_is_imported_by_something_that_is_not_a_test():
    """Pins the import specifically, so a stray mention in a docstring cannot satisfy it."""
    consumers = _consumer_files()
    assert consumers, (
        "gtm_core.signals is imported only by its own test. Note this asserts the "
        "gtm_core.signals module specifically — gtm_core/voc/signals.py is a different "
        "module that IS wired, and must not be mistaken for this one."
    )


# ── T9: the dispatch map ─────────────────────────────────────────────────────
#
# Restated per PRD §11.1(c), which withdrew the W1-era version of this test as wrong in
# both readings. `SUGGESTED_ACTIONS` maps a signal TYPE (`reply_received`, `funding`) to
# an action VERB (`draft_reply`, `escalate_to_operator`) — neither side is a skill slug,
# so "every value names a directory under plugin/skills/" could never have held.
#
# What belongs here is the assertion against the **dispatch map**: the thing that turns
# an action verb into a pack/variant or a notifier. That did not exist until W4.


def _dispatch():
    from agent import signal_dispatch

    return signal_dispatch


def test_every_suggested_action_has_a_dispatch_entry():
    """T9. An action verb with no dispatch entry is a signal that dedups and dies.

    It would be recorded in history.jsonl, deduped against on the next sweep, and never
    acted on — an inert path that looks wired from the ledger's side.
    """
    d = _dispatch()
    unmapped = {a for a in d.SUGGESTED_ACTIONS.values() if a not in d.ACTION_DISPATCH}
    assert not unmapped, (
        f"{sorted(unmapped)} are suggested_action values with no ACTION_DISPATCH entry. "
        "A signal carrying one is recorded, deduped, and then dropped."
    )


def test_every_dispatch_target_resolves_to_something_real():
    """A dispatch map pointing at a renamed pack fails at 09:15, not in CI."""
    from gtm_core.packs.loader import load_pack_graph

    d = _dispatch()
    for action, target in d.ACTION_DISPATCH.items():
        if target is None:
            continue  # an explicit no-op; `review` records and stops
        if isinstance(target, d.PackTarget):
            graph = REPO / "packs" / target.pack / "graphs" / f"{target.variant}.toml"
            assert graph.is_file(), f"{action} -> {graph.relative_to(REPO)} does not exist"
            load_pack_graph(graph)  # must also pass the fail-closed loader
        else:
            assert callable(target.notify), f"{action} -> notifier is not callable"


def test_the_dispatch_map_is_non_vacuous():
    d = _dispatch()
    assert len(d.ACTION_DISPATCH) >= 4
    assert any(isinstance(t, d.PackTarget) for t in d.ACTION_DISPATCH.values())


def test_no_dispatch_target_can_name_a_destination():
    """The unrepresentable-destination property, extended to the dispatch layer.

    A signal is untrusted data (§R5). It may inform WHAT gets drafted; it must never be
    able to say WHERE anything goes. `PackTarget` therefore carries a pack and a variant
    and nothing else — no channel, no account, no address field to populate.
    """
    d = _dispatch()
    fields = set(d.PackTarget.__dataclass_fields__)
    assert fields == {"pack", "variant"}, (
        f"PackTarget gained {fields - {'pack', 'variant'}}. A dispatch target with a "
        "destination field is a destination a signal could populate."
    )


@pytest.mark.private_tree  # activation lives in a tenant packs.toml; the carve ships none
def test_every_dispatch_pack_is_activated_for_at_least_one_profile():
    """The loader is fail-closed on activation, so an unactivated pack defers forever.

    A dispatch that always defers is indistinguishable from one that never runs — which
    is the defect class this PRD is about, arriving one layer down.
    """
    import tomllib

    d = _dispatch()
    wanted = {t.pack for t in d.ACTION_DISPATCH.values() if isinstance(t, d.PackTarget)}
    activated: set[str] = set()
    for cfg in (REPO / "profiles").glob("*/packs.toml"):
        activated |= set(tomllib.loads(cfg.read_text(encoding="utf-8")).get("active", []))
    missing = wanted - activated
    assert not missing, (
        f"{sorted(missing)} is a dispatch target but no profile activates it in packs.toml. "
        "agent/__main__ refuses an unactivated pack fail-closed, so every dispatch defers."
    )
