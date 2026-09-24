"""Contracts the 2026-09-24 merge introduced, and only the merge can break.

The three ported suites beside this file (`test_outreach_pack_linter.py`,
`test_merge_render_linter.py`, `test_merge_render_mutation_suite.py`) are the regression net
that proves the merge changed no rule. This file holds the properties that did not exist
before there was one package: one rules version, one rule inventory, one seat resolver, one
command line.
"""

from __future__ import annotations

import ast
import pathlib
import re
import subprocess
import sys

import outreach
import pytest
from outreach import ALL_RULE_IDS, RULE_CATALOGUE, RULES_VERSION, UNCATALOGUED_RULES

PKG = pathlib.Path(outreach.__file__).parent
REPO = PKG.parents[2]
CLI = PKG.parent / "outreach_linter.py"


def _emitted_rule_ids() -> set[str]:
    """Every rule id the linter can raise, derived from the AST rather than from a run.

    Static on purpose: the point is every branch, including ones no fixture reaches. Two
    constructors carry a rule id and they put it in the same position — ``Violation(level,
    email, rule, detail)`` in this package and ``Finding(level, field, rule, detail)`` in
    ``gtm_core.merge_hygiene``, which ``driver`` calls once per render. Reading a rule id off
    a bound name (``x.rule``) is deliberately NOT counted: those re-emit an existing
    violation, and counting them would inflate the set with names that are not rules.
    """
    out: set[str] = set()
    sources = [*sorted(PKG.glob("*.py")), REPO / "gtm_core" / "merge_hygiene" / "api.py"]
    for path in sources:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "id", None) not in {"Violation", "Finding"}:
                continue
            if len(node.args) > 2 and isinstance(node.args[2], ast.Constant):
                out.add(node.args[2].value)
            for kw in node.keywords:
                if kw.arg == "rule" and isinstance(kw.value, ast.Constant):
                    out.add(kw.value.value)
    return {r for r in out if isinstance(r, str)}


def test_list_rules_count_equals_what_the_linter_can_raise():
    """``--list-rules`` must name every id the gate can emit, not the subset it describes.

    This is the drift that produced a wrong number in a PRD: `RULE_CATALOGUE` held 88 entries
    while the gate raised 108, because the old backstop AST-walked one of the two linter files
    and never walked ``check_row`` at all. The expected set here is re-derived from the source
    on every run, so the inventory cannot fall behind the rules again — and the failure names
    the drift in both directions rather than just a count.
    """
    emitted = _emitted_rule_ids()
    assert set(ALL_RULE_IDS) == emitted, (
        f"--list-rules would under-report {sorted(emitted - set(ALL_RULE_IDS))} and "
        f"over-report {sorted(set(ALL_RULE_IDS) - emitted)}"
    )
    assert set(RULE_CATALOGUE) | set(UNCATALOGUED_RULES) == set(ALL_RULE_IDS)
    assert not set(RULE_CATALOGUE) & set(UNCATALOGUED_RULES), (
        "an id is both catalogued and listed as uncatalogued — the two lists disagree"
    )


def test_list_rules_prints_the_same_ids_to_stdout_and_to_a_file(tmp_path):
    """Both forms of the flag, because the file form is what a headless run uses (a shell
    redirect is denied at runtime) and the stdout form is what a human reaches for."""
    out = tmp_path / "rules.txt"
    to_file = subprocess.run(
        [sys.executable, str(CLI), "--list-rules", str(out)], capture_output=True, text=True
    )
    assert to_file.returncode == 0, to_file.stderr
    to_stdout = subprocess.run(
        [sys.executable, str(CLI), "--list-rules"], capture_output=True, text=True
    )
    assert to_stdout.returncode == 0, to_stdout.stderr
    written = [ln for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]
    printed = [ln for ln in to_stdout.stdout.splitlines() if ln.strip()]
    assert written == printed == list(ALL_RULE_IDS)


#: Numerals the retirement block spells out. Small on purpose — a numeral this map does not
#: know fails loudly below rather than reading as "no claim made", which is how a typed count
#: escapes review in the first place.
_NUMERALS = {
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "twenty-three": 23,
    "twenty-four": 24,
    "twenty-five": 25,
    "twenty-six": 26,
}


def _retirement_block() -> tuple[list[str], list[str], str]:
    """``(mapped ids, honest-loss ids, the block's text)`` from ``model.py``'s comment.

    The block is prose by design — it is the one home for where each retired rule's intent
    went, and a dict could not carry the argument. Prose is also where a count goes wrong
    without anything noticing, which is what the two tests below are for, so the parse is
    deliberately strict: an id row is ``#`` + exactly three spaces + a lowercase id, and
    every continuation line is indented past that.
    """
    src = (PKG / "model.py").read_text(encoding="utf-8")
    block = src.split("--- what retired", 1)[1].split("#: Every check this gate can make", 1)[0]
    # Split at the table's underline, so the `retired rule / intent now lives in` header is
    # not mistaken for an id.
    _, _, rows = block.partition("---   ---")
    mapped_txt, _, losses_txt = rows.partition("HONEST LOSSES")
    ident = re.compile(r"^#   ([a-z][a-z0-9-]*)\s+\S", re.M)
    return ident.findall(mapped_txt), ident.findall(losses_txt), block


def _coverage_finding_ids() -> set[str]:
    """Every ``<id>: `` prefix ``hook_coverage.audit`` appends to ``Coverage.findings``.

    Read from the source, not from a run: the point is every branch, including the ones no
    fixture in this repo reaches.
    """
    src = (REPO / "gtm_core" / "hook_coverage" / "audit.py").read_text(encoding="utf-8")
    out: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.JoinedStr):
            continue
        first = node.values[0] if node.values else None
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            m = re.match(r"^([a-z][a-z0-9-]*): ", first.value)
            if m:
                out.add(m.group(1))
    return out


def test_the_retirement_block_counts_the_ids_it_actually_lists():
    """§R14 on the block's own numbers: they are a claim about the list below them.

    The block promises "nothing may be dropped silently, so every retired id is listed
    below" — which makes an off-by-one indistinguishable from a dropped id, and is exactly
    how it read on 2026-09-24, when it said SIX honest losses in two places and listed five.
    Nothing had been dropped; the word was wrong. This test cannot tell those two apart
    either, and that is the point: both are failures, and both have to be resolved by
    counting rather than by reading.
    """
    mapped, losses, block = _retirement_block()
    ids = mapped + losses
    assert len(set(ids)) == len(ids), f"an id is listed twice: {sorted(ids)}"

    def stated(pattern: str) -> int:
        found = re.findall(pattern, block)
        assert len(found) == 1, f"expected one {pattern!r} in the block, found {found}"
        word = found[0].lower()
        assert word in _NUMERALS, f"unparsed numeral {found[0]!r} — add it to _NUMERALS"
        return _NUMERALS[word]

    assert stated(r"([A-Za-z-]+) rules retire here") == len(ids)
    assert stated(r"([A-Za-z]+) are honest losses") == len(losses)
    assert stated(r"([A-Z]+) HONEST LOSSES") == len(losses)


def test_no_retired_rule_id_survives_as_a_live_finding_id():
    """A retirement is a claim about the whole repo, not about this package.

    Two surfaces raise ids out of the same vocabulary: this linter, and the campaign
    coverage report (``gtm_core.hook_coverage.audit``). On 2026-09-24 the block above
    retired ``hook-cell-missing`` / ``hook-cell-unknown`` into ``angle-missing`` /
    ``angle-unknown`` while the coverage report went on raising the retired pair — one fact
    answering to two ids, and ``eval_calibration.rule_lifecycle_report`` buckets operator
    labels BY rule id, so the two spellings would have been counted as two rules.

    Checked in both directions, because either alone passes while the surfaces drift: no
    retired id may be raised anywhere, and the successors the block names must still be live
    ids on BOTH surfaces. Nothing parses these strings out of the coverage report — the one
    reader, ``gtm_core.finding_budget.group_by_rule``, splits on ``": "`` and takes whatever
    prefix it finds — so the ids are free to agree, which is why they must.
    """
    mapped, losses, _ = _retirement_block()
    retired = set(mapped) | set(losses)
    coverage = _coverage_finding_ids()

    assert not retired & set(ALL_RULE_IDS), (
        f"retired but still emittable by the linter: {sorted(retired & set(ALL_RULE_IDS))}"
    )
    assert not retired & coverage, (
        f"retired but still raised by the coverage report: {sorted(retired & coverage)}"
    )
    successors = {"angle-missing", "angle-unknown"}
    assert successors <= coverage, f"the coverage report stopped raising {successors - coverage}"
    assert successors <= set(ALL_RULE_IDS), (
        f"the linter stopped raising {successors - set(ALL_RULE_IDS)} while the coverage "
        f"report still does — the successor has to retire on both surfaces at once"
    )


def test_one_rules_version():
    """Both pre-merge values are gone, and the surviving one is the value that GATED.

    ``rules-version-stale`` compares a pack header against this constant; the other value
    ("2026-08-20") only ever appeared in a printed report line. Stamping a new date would have
    marked every fresh pack on disk stale in one commit, so the merge kept the gating one.
    """
    versions = set()
    for path in sorted(PKG.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Assign)
                and any(getattr(t, "id", None) == "RULES_VERSION" for t in node.targets)
                and isinstance(node.value, ast.Constant)
            ):
                versions.add(node.value.value)
    assert versions == {RULES_VERSION} == {"2026-09-04"}, (
        f"expected exactly one RULES_VERSION, found {sorted(versions)}"
    )


def test_persona_and_seat_have_exactly_one_implementation():
    """``gtm_core.hook_coverage.config`` re-exports these; four consumers reach them that way.

    A second definition would drift silently — the coverage page would report cells the gate
    never checked — so this asserts both that only one module defines them and that the
    re-export still hands back the SAME function object, not a lookalike.
    """
    from gtm_core.hook_coverage import config

    defs = {
        path.name
        for path in sorted(PKG.glob("*.py"))
        for node in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(node, ast.FunctionDef) and node.name in {"persona_of", "seat_of"}
    }
    assert defs == {"roles.py"}, f"seat/persona resolver defined in {sorted(defs)}"
    assert config.seat_of is outreach.seat_of
    assert config.persona_of is outreach.persona_of


def test_the_package_is_importable_under_exactly_one_name():
    """Two spellings of one package are two packages (2026-09-24 review, finding 12).

    Every consumer reaches this package through the ``sys.path`` prologue and ``from outreach
    import ...`` — ``gtm_core/cells.py``, ``rule_baseline.py``, ``build_eval_sheet.py``,
    ``hook_coverage/config.py``, ``tests/injection/``. One file used ``from tests.linter.outreach
    import ...`` instead. There is no ``__init__.py`` under ``tests/``, so BOTH resolve, into two
    ``sys.modules`` entries with their own ``RULES_VERSION``, ``_SEAT_RULES`` and ``HEDGE_CUES``.

    ``test_persona_and_seat_have_exactly_one_implementation`` cannot see this: it compares
    ``config.seat_of is outreach.seat_of``, which stays true while a second copy exists under
    another name. The check has to be static, because the fork is only *observable* at runtime in
    a session that happens to import both — and the required test command does not.
    """
    import re

    # Only real import statements, so a docstring or comment naming the hazard — this one, for
    # a start — is not itself a finding. Scoped to the names the merge owns: `outreach` and the
    # two modules it replaced, all three of which ARE reached bare through the prologue and so
    # CAN fork. `tests.linter.content_linter` has the same shape and is deliberately not covered
    # here — it is a pre-existing sibling with its own consumers, and widening this guard onto
    # it would fail two files this change has no business rewriting. Reported, not swept in.
    owned = "outreach|outreach_pack_linter|merge_render_linter"
    spelling = re.compile(
        rf"^\s*(?:import\s+tests\.linter\.(?:{owned})\b"
        rf"|from\s+tests\.linter\s+import\b.*\b(?:{owned})\b"
        rf"|from\s+tests\.linter\.(?:{owned})(?:\.\w+)?\s+import\b)"
    )
    hits = []
    for root in ("tests", "gtm_core", "agent", "backend", "scripts", "cockpit"):
        base = REPO / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if spelling.match(line):
                    hits.append(f"{path.relative_to(REPO)}:{i}: {line.strip()}")
    assert not hits, (
        "import the package as `outreach` after the sys.path prologue; these spellings fork it "
        "into a second module object:\n" + "\n".join(hits)
    )
    # §R18: the matcher can discriminate. The first two are the two spellings that actually
    # shipped; the third is the correct one and must NOT match, or the guard is a ban on
    # importing the package at all.
    assert spelling.match("from tests.linter.outreach import seat_of")
    assert spelling.match("import tests.linter.outreach")
    assert spelling.match("from tests.linter import outreach")
    # The spelling that actually shipped before the merge, on the module `outreach` replaced.
    assert spelling.match("from tests.linter.outreach_pack_linter import (")
    assert not spelling.match("from outreach import seat_of")
    assert not spelling.match("from .model import Violation")
    # ...and the sibling this guard deliberately leaves alone stays unflagged, so the exclusion
    # above is a property of the code and not a sentence in a comment.
    assert not spelling.match("from tests.linter.content_linter import lint")


def test_the_second_spelling_really_does_fork_a_module():
    """The control for the guard above: the hazard it bans is real, exercised rather than
    asserted in prose.

    Shown on `outreach_pack_linter` — one of the two shim modules the guard also covers, and
    which the package's own `sys.modules` aliasing deliberately does not reach (it is a module
    beside the package, not inside it). So the static scan is what protects these two, and this
    is the proof they still need protecting.
    """
    import importlib
    import sys

    direct = importlib.import_module("outreach_pack_linter")
    before = set(sys.modules)
    try:
        forked = importlib.import_module("tests.linter.outreach_pack_linter")
        assert forked is not direct, (
            "the two spellings resolved to ONE module — the hazard this guards is gone, so "
            "delete the guard rather than leaving a check that cannot fail"
        )
    finally:
        for name in sorted(set(sys.modules) - before, reverse=True):
            del sys.modules[name]


def test_the_cli_refuses_without_a_mode():
    """The merge turned two CLIs into two subcommands because ``--csv`` and ``--signoff``
    meant different things on each. A bare invocation must say so rather than pick one."""
    rc = subprocess.run([sys.executable, str(CLI)], capture_output=True, text=True)
    assert rc.returncode != 0
    assert "pack" in rc.stderr and "render" in rc.stderr


# --- finding 3: the pack mode reaches the registry rules too -------------------------------
#
# A fictional tenant written to a tmp tree and reached through GTM_PROFILES_ROOT. A literal
# profile name from `profiles/` would be a tenant token in `tests/`, which is carved and which
# `debrand_check.sh --release` scans (`cli._tenant_ban_phrases` takes the same care).

_FIX_PROFILE = "northwind"

_FIX_KNOWLEDGE = {
    "role-vocabulary.toml": (
        'default_persona = "ciso"\nsegments = ["enterprise", "unspecified"]\n\n'
        '[[persona]]\nname = "ciso"\ncues = ["ciso"]\n\n'
        '[[seat]]\nname = "security"\npersonas = ["ciso"]\nstakes = ["audit"]\n'
    ),
    "claims.toml": (
        '[[claim]]\nid = "audit-signed"\ngroup = "observability"\nstatus = "verified"\n'
        'statement = "Each audit entry is signed."\n'
        'source = "knowledge/references/ledger-notes.md:12"\n'
        'do_not_say = ["hash-chained"]\n'
    ),
    "proof.toml": (
        '[[proof]]\nid = "market-anchor"\nkind = "anchor"\nmarket = "Singapore"\n'
        'figure_kind = "none"\nstatement = "One identity per agent."\n'
        'source = "knowledge/guidance/notes.md:4"\n'
    ),
    "angles.toml": (
        '[[angle]]\nid = "sec-audit"\nseat = "security"\npremise = "multi-framework"\n'
        'claim = "audit-signed"\nproof = "market-anchor"\nopener_kind = "account-event"\n'
        'summary = "One chain of custody per agent action."\nstatus = "draft"\n'
    ),
    "premise-vocab.toml": (
        "schema = 1\n\n[premise.multi-framework]\n"
        'claim = "the reader runs agents on more than one framework"\n'
        'min_distinct = 1\nterms = ["langgraph"]\n'
    ),
}

_FIX_PACK = f"""Rules-Version: {RULES_VERSION}

```
angle: sec-audit
slot_signal: row.signal_evidence
slot_claim: audit-signed
slot_pain: security
slot_hedge: voice-rules.hedge.cues
slot_proof: market-anchor
```

### 1. Robin Amadi · CISO, Northwind Holdings
**To:** robin@cirrus.example
**Subject:** identity in production

Hi Robin,

Northwind Holdings runs agents on langgraph, and the trail is hash-chained across every handoff.

Usually that gap opens the moment one agent hands work to another.

Want the one-pager on carrying that evidence across a partner boundary?

Henry

---
"""


def _pack_run(tmp_path, *extra):
    import os

    profiles = tmp_path / "profiles"
    knowledge = profiles / _FIX_PROFILE / "knowledge"
    knowledge.mkdir(parents=True, exist_ok=True)
    for name, text in _FIX_KNOWLEDGE.items():
        (knowledge / name).write_text(text, encoding="utf-8")
    pack = tmp_path / "pack.md"
    pack.write_text(_FIX_PACK, encoding="utf-8")
    env = {**os.environ, "GTM_PROFILES_ROOT": str(profiles)}
    return subprocess.run(
        [sys.executable, str(CLI), "pack", str(pack), "--signoff", "Henry", *extra],
        capture_output=True,
        text=True,
        env=env,
    )


def test_pack_mode_takes_a_profile_and_runs_the_derivation_rules(tmp_path):
    """Finding 3. ``pack`` took no ``--profile``, ``_run_pack`` built no registry, and neither
    ``lint_pack`` nor ``lint_formatted_pack`` called ``lint_derivation`` — so the 1:1 path, which
    is the shape a human reviews and sends, reached none of the registry rules. Meanwhile
    ``gtm_core/messaging/card.py`` left ``claim_within_status`` off the judge's subset *because*
    "the deterministic `claim-status` linter rule is authoritative there", which on a pack it
    was not: it was absent.

    End-to-end through the real command line, because the flag's absence was the defect.
    """
    rc = _pack_run(tmp_path, "--profile", _FIX_PROFILE)
    assert "claim-status" in rc.stdout, rc.stdout + rc.stderr
    assert "hash-chained" in rc.stdout
    assert rc.returncode != 0, "a do_not_say phrase in a pack did not block"


def test_the_same_pack_without_a_profile_is_silent_on_the_registry(tmp_path):
    """§R18's other half, and it also pins the opt-in off-state: the two runs differ by the
    flag and nothing else, so a pass here cannot come from the pack being clean."""
    rc = _pack_run(tmp_path)
    assert "claim-status" not in rc.stdout, rc.stdout
    assert "angle-missing" not in rc.stdout


def test_both_modes_still_accept_a_profile(tmp_path):
    """The flag exists on `render` too, and neither mode's parser may lose it in a later
    flat-parser refactor — which is exactly how `pack` came to be missing it."""
    from outreach.cli import build_parser

    ap = build_parser()
    assert ap.parse_args(["pack", "x.md", "--profile", "p"]).profile == "p"
    assert ap.parse_args(["render", "s.md", "--csv", "c.csv", "--profile", "p"]).profile == "p"


def test_selftest_runs_both_positive_controls():
    rc = subprocess.run([sys.executable, str(CLI), "--selftest"], capture_output=True, text=True)
    assert rc.returncode == 0, rc.stdout + rc.stderr
    assert "selftest: good-pack errors=0" in rc.stdout, "the pack control did not run"
    assert "selftest OK" in rc.stdout, "the merge-render control did not run"


def test_selftest_loads_the_tenant_ban_file():
    """FR0 residue, closed 2026-09-24: ``--selftest`` ran the gate with an EMPTY ban list.

    Both positive controls therefore shipped phrases their own tenant had retired — ``My
    hunch:``, ``My bet on the open piece:`` — and both went green, so a test was pinning the
    retired wording as the house pattern. A control that cannot go red for the thing the gate
    exists to catch is not a control.

    Three assertions, in the order that makes the loading PROVEN rather than assumed:

    1. the ban set is non-empty and actually holds the retired hedges (a check that cannot
       discriminate is not a check — §R18);
    2. the rewritten controls, which carry none of them, stay green (the negative control);
    3. a retired phrase PLANTED into each control turns its half of the selftest red.

    Both halves are planted, because they load the set separately and one could regress alone.
    """
    from outreach import cli

    bans = cli._tenant_ban_phrases()
    assert bans, "no profile ban file was read — the selftest would pass on an empty set"
    for retired in ("my hunch", "my read", "my bet"):
        assert retired in bans, f"{retired!r} missing: the set cannot catch the FR0 residue"

    # Negative control: untouched, both controls are clean under that same set.
    assert cli._selftest_pack() == 0
    assert cli._selftest_render() == 0

    planted_pack = cli._GOOD.replace(
        "Likely you have part of this already.", "My hunch: you have part of this already."
    )
    assert planted_pack != cli._GOOD, "the plant did not apply — the control was reworded"
    original = cli._GOOD
    try:
        cli._GOOD = planted_pack
        assert cli._selftest_pack() == 1, "a retired phrase in the pack control did not go red"
    finally:
        cli._GOOD = original

    planted_spec = cli._GOOD_SPEC.replace(
        "> Likely you have part of this.", "> My read: you have part of this."
    )
    assert planted_spec != cli._GOOD_SPEC, "the plant did not apply — the control was reworded"
    original_spec = cli._GOOD_SPEC
    try:
        cli._GOOD_SPEC = planted_spec
        with pytest.raises(AssertionError, match="banned-word"):
            cli._selftest_render()
    finally:
        cli._GOOD_SPEC = original_spec


def test_the_positive_controls_are_registry_derived():
    """Every slot of a control body names where it came from (FR3 Task 3.5).

    The shape ``rules_derivation`` checks: one declared ``angle:`` and one ``slot_<id>:`` per
    slot. The rules themselves are silent here — the selftest passes no registry, deliberately,
    since a fixture registry inside the shipped CLI would be a second copy of a tenant's facts.
    What is pinned is that the control a reader copies is the DERIVED shape, not the pre-registry
    one where "where did this sentence come from" had no answer.
    """
    from outreach import cli
    from outreach.rules_derivation import SLOT_IDS

    from gtm_core.hook_coverage.declared import declaration_surface

    for name, text in (("_GOOD", cli._GOOD), ("_GOOD_SPEC", cli._GOOD_SPEC)):
        surface = declaration_surface(text)
        assert "angle:" in surface, f"{name} declares no angle"
        for slot in SLOT_IDS:
            assert f"slot_{slot}:" in surface, f"{name} names no source for slot {slot!r}"


def test_no_doc_or_skill_quotes_a_rule_count():
    """§R14: a number in prose is derived, never typed. The count this linter emits moved
    from 88 to 108 the moment the catalogue stopped being the whole inventory — any doc that
    had typed a number would now be wrong and nothing would have told it.
    """
    import re

    targets = [
        *sorted((REPO / "plugin" / "skills").glob("*/body_template.md")),
        *sorted((REPO / "docs").glob("*.md")),
        REPO / "PENDING.md",
    ]
    pattern = re.compile(r"\b(\d{2,3})\s+rules?\b", re.IGNORECASE)
    hits = []
    for path in targets:
        if not path.exists():
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "outreach_linter" not in line and "RULE_CATALOGUE" not in line:
                continue
            if pattern.search(line):
                hits.append(f"{path.relative_to(REPO)}:{i}: {line.strip()}")
    assert not hits, "cite the command, never the count (§R14):\n" + "\n".join(hits)
    # Negative control: the check can discriminate.
    assert pattern.search("the outreach_linter catalogue holds 108 rules today")


def test_likely_is_a_recognised_hedge_cue():
    """Standing FR0 item: `likely` had to be in the cue table before any voice doc could
    recommend it, or the doc would be telling an author to write something the gate refused.

    The honest note is that `hedge-missing` — the rule that REFUSED an unlisted hedge — retired
    on 2026-09-24, so the item is now closed twice over: nothing refuses an unhedged body at
    all. The entry stays because `HEDGE_CUES` is also this house's statement of what counts as
    hedging (mirrored in `voice-rules.toml` `[hedge].cues`), and the next reader should find the
    two agreeing. Negative control below, so this cannot pass on an empty tuple.
    """
    assert "likely" in outreach.HEDGE_CUES
    assert "definitely" not in outreach.HEDGE_CUES
    assert "certainly" not in outreach.HEDGE_CUES


def test_a_one_word_hedge_cue_cannot_widen_the_template_share_whitelist():
    """The one way a new cue can still do harm, and the proof this one cannot.

    `HEDGE_CUES` now has exactly one reader: `_hedge_ngram_whitelist`, which exempts a hedge's
    own 6-grams from `body-template-share`. A cue shorter than NGRAM_N contributes no gram, so
    adding `likely` cannot make the batch-duplication gate blinder. The positive control is the
    other branch — a cue that IS long enough does contribute — so this test fails if the
    whitelist stops working rather than passing vacuously.
    """
    whitelist = outreach._hedge_ngram_whitelist()
    assert not any("likely" in gram for gram in whitelist), (
        "a one-word cue put a gram into the template-share exemption"
    )
    long_cue = "tell me if you've got this covered"
    assert len(outreach._norm_tokens(long_cue)) >= outreach.NGRAM_N
    assert any(
        gram in whitelist
        for gram in outreach._ngrams(outreach._norm_tokens(long_cue), outreach.NGRAM_N)
    ), "the whitelist no longer exempts a real hedge — the control branch is dead"


# ── One module object, under either spelling ──────────────────────────────────


_FORK_PROBE = """
import importlib, sys
sys.path.insert(0, {real!r})
sys.path.insert(0, {repo!r})
sys.path.insert(0, {linter!r})
a = importlib.import_module({first!r})
b = importlib.import_module({second!r})
print("SAME" if a is b else "FORKED")
sa = importlib.import_module({first!r} + ".rules_derivation")
sb = importlib.import_module({second!r} + ".rules_derivation")
print("SUBSAME" if sa is sb else "SUBFORKED")
"""


def _probe(first: str, second: str, *, repo: pathlib.Path | None = None) -> list[str]:
    """Import both spellings in a FRESH interpreter and report whether they are one object.

    A subprocess because the question is about `sys.modules` at FIRST import: inside this
    pytest process the package is already imported under both names, so an in-process
    ``assert a is b`` would pass whatever the package does and prove nothing (§R18).

    ``repo`` overrides where BOTH spellings resolve from, so the negative control can point
    them at a patched copy. The real repo stays last on the path regardless, because the
    package's submodules import `gtm_core` — a copy that could not import it would fail for
    a reason that has nothing to do with the aliasing.
    """
    root = REPO if repo is None else repo
    script = _FORK_PROBE.format(
        real=str(REPO),
        repo=str(root),
        linter=str(root / "tests" / "linter"),
        first=first,
        second=second,
    )
    out = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=str(root)
    )
    assert out.returncode == 0, f"probe failed:\n{out.stderr}"
    return out.stdout.split()


@pytest.mark.parametrize(
    ("first", "second"),
    [("outreach", "tests.linter.outreach"), ("tests.linter.outreach", "outreach")],
)
def test_both_spellings_are_one_module_in_either_import_order(first, second):
    """`tests/` has no `__init__.py`, so both names resolve — and without the aliasing in
    `outreach/__init__.py` each builds its own object, with its own `RULES_VERSION`,
    `_SEAT_RULES` and vocabulary cache. Which one a consumer got would then depend on import
    ORDER, which under pytest means test order.
    """
    assert _probe(first, second) == ["SAME", "SUBSAME"]


def test_the_probe_reports_a_fork_when_the_aliasing_is_removed(tmp_path):
    """The negative control. Without it, the contract above cannot be told apart from one that
    passes because Python happened to return the same object anyway — and the claim being made
    is that the aliasing is what makes it true.

    A patched COPY of the package, reached by putting the copy's roots ahead of the real ones,
    so the real tree is never written to.
    """
    import shutil

    linter = tmp_path / "tests" / "linter"
    linter.parent.mkdir()
    shutil.copytree(PKG.parent, linter, ignore=shutil.ignore_patterns("__pycache__"))
    init = linter / "outreach" / "__init__.py"
    text = init.read_text(encoding="utf-8")
    patched = text.replace("sys.modules.setdefault(", "dict().setdefault(")
    assert patched != text, "the aliasing call moved — this control no longer removes it"
    init.write_text(patched, encoding="utf-8")

    assert _probe("outreach", "tests.linter.outreach", repo=tmp_path) == ["FORKED", "SUBFORKED"], (
        "removing the aliasing did not fork the package — so the aliasing is not what makes "
        "the contract above true"
    )
