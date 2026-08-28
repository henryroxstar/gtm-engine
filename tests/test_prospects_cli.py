"""Tests for the umbrella CLI — one front door over the pipeline's deterministic verbs.

The audit counted 33 CLI verbs across 24 modules, with the module a verb lives in being
an implementation detail a reader has to memorise. This dispatcher exists to remove that
burden; these tests pin that it forwards faithfully and hides nothing.
"""

from __future__ import annotations

import importlib

import pytest

from gtm_core import prospects as cli


def test_every_verb_resolves_to_an_importable_module_with_an_entry_point():
    """A verb that does not dispatch is worse than no verb: it looks available."""
    missing = []
    for verb, module_name in cli.VERBS.items():
        module = importlib.import_module(module_name)
        if cli._entry_point(module) is None:
            missing.append(f"{verb} -> {module_name}")
    assert not missing, f"verbs with no CLI entry point: {missing}"


def test_dispatch_forwards_the_remaining_argv_verbatim(monkeypatch):
    seen = {}

    def _fake(argv):
        seen["argv"] = argv
        return 0

    module = importlib.import_module(cli.VERBS["consolidate"])
    monkeypatch.setattr(module, "_cli", _fake, raising=False)
    monkeypatch.setattr(module, "main", _fake, raising=False)

    assert cli.main(["consolidate", "--profile", "acme", "--allow-shrink"]) == 0
    assert seen["argv"] == ["--profile", "acme", "--allow-shrink"], (
        "the dispatcher rewrote the verb's own arguments"
    )


def test_the_exit_code_is_the_modules_own(monkeypatch):
    module = importlib.import_module(cli.VERBS["list-fit"])
    monkeypatch.setattr(module, "main", lambda argv: 1)
    assert cli.main(["list-fit", "--csv", "x"]) == 1


def test_a_module_returning_none_is_success():
    """argparse-style mains often return None on success; None is not a failure."""

    class _Fake:
        @staticmethod
        def main(argv):
            return None

    assert cli._entry_point(_Fake) is not None
    assert int(_Fake.main([]) or 0) == 0


def test_an_unknown_verb_exits_two_and_lists_the_real_ones(capsys):
    assert cli.main(["definitely-not-a-verb"]) == 2
    err = capsys.readouterr().err
    assert "unknown verb" in err
    assert "consolidate" in err, "a wrong guess must be told what the right ones are"


@pytest.mark.parametrize("flag", [[], ["--help"], ["-h"], ["help"]])
def test_help_lists_every_verb(flag, capsys):
    assert cli.main(flag) == 0
    out = capsys.readouterr().out
    for verb in cli.VERBS:
        assert verb in out, f"{verb} is dispatchable but undocumented"


def test_help_does_not_import_the_pipeline(monkeypatch):
    """`--help` is a question about spelling; it must not drag in every module."""
    calls = []
    real = importlib.import_module

    def _counting(name, *a, **kw):
        calls.append(name)
        return real(name, *a, **kw)

    monkeypatch.setattr(importlib, "import_module", _counting)
    cli.main(["--help"])
    assert not [c for c in calls if c in set(cli.VERBS.values())]


def test_the_umbrella_command_classifies_as_allowed():
    """Skill text will quote this form; the permission layer has to accept it.

    Pinned explicitly rather than assumed from the older `gtm_core.<module>` form —
    a new command shape reaching the skills and being denied at run time is exactly
    the class of surprise this repo's tool-corpus lint exists to prevent.
    """
    from agent.permissions import classify_tool

    verdict = classify_tool(
        "Bash", {"command": "uv run python -m gtm_core.prospects consolidate --profile acme"}
    )
    assert verdict == "allow", verdict


def test_the_old_entry_points_still_work():
    """This adds a door; it does not move any rooms."""
    from gtm_core import list_fit, prospects_consolidate

    assert callable(prospects_consolidate._cli)
    assert callable(list_fit.main)
