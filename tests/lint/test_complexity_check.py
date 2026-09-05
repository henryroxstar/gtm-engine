"""Guards for the complexity ratchet — the §R10 gate.

A gate nobody tests is a gate that quietly stops working. These pin the properties that make
the ratchet worth having: it fails on growth and on a stale ceiling, its failure text carries
the fix, a ceiling cannot be bumped without a dated reason, the retired-path guard catches the
silent-green patch shape, and — the standing invariant — the tree is clean under it right now.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "complexity_check", ROOT / "tests/lint/complexity_check.py"
)
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)

CAP = cc.CAP


def _py(tmp_path: Path, lines: int, name: str = "mod.py") -> Path:
    f = tmp_path / name
    f.write_text("x = 1\n" * lines, encoding="utf-8")
    return f


def _key(f: Path) -> str:
    return cc._rel(f)


# --- the standing invariant ----------------------------------------------------------- #


def test_the_tree_is_clean_under_the_ratchet():
    """Every governed file is within its ceiling, no entry is stale, no test patches a
    retired path. This is the line that fails when a file grows."""
    assert cc.main([]) == 0


def test_every_allowlist_entry_is_over_the_cap_and_exists():
    entries, malformed = cc.load_allowlist()
    assert not malformed
    for rel, (ceiling, _note) in entries.items():
        f = ROOT / rel
        assert f.is_file(), rel
        assert ceiling > CAP, f"{rel}: ceiling {ceiling} is not above the cap"
        assert cc.count_lines(f) <= ceiling, rel


# --- scope: what the ratchet governs -------------------------------------------------- #


@pytest.mark.parametrize(
    "rel",
    [
        "gtm_core/video_finish.py",
        "agent/mcp/apollo/server.py",
        "backend/routers/runs.py",
        "scripts/content_qa_check.py",
        "plugin/skills/account-plan/scripts/md_to_docx_spec.py",
        "tests/linter/outreach_pack_linter.py",  # a production linter that lives under tests/
        "tests/lint/pii_check.py",
    ],
)
def test_production_surface_is_governed(rel):
    assert cc.in_scope(ROOT / rel)


@pytest.mark.parametrize(
    "rel",
    [
        "tests/test_hook_coverage.py",  # test files get a growth-only ratchet later, not now
        "tests/linter/test_outreach_pack_linter.py",
        "tests/conftest.py",
        "profiles/acme/commercial/.build_sales_deck.py",  # one-off tenant generators, ruff-excluded
        "plugin/skills/account-plan/references/snippet.py",  # reference prose, not shipped code
        "docs/example.py",
        "gtm_core/notes.md",
    ],
)
def test_tests_fixtures_and_profiles_are_not(rel):
    assert not cc.in_scope(ROOT / rel)


# --- the predicate -------------------------------------------------------------------- #


def test_unlisted_file_over_the_cap_fails_with_the_fix(tmp_path):
    f = _py(tmp_path, CAP + 1)
    findings, _ = cc.check_files([f], {})
    assert len(findings) == 1
    where, what, fix = findings[0]
    assert f"{CAP + 1} lines > {CAP}" in what
    assert "# YYYY-MM-DD" in fix and cc.ALLOWLIST_REL in fix


def test_unlisted_file_at_the_cap_passes(tmp_path):
    findings, _ = cc.check_files([_py(tmp_path, CAP)], {})
    assert findings == []


def test_listed_file_at_its_ceiling_passes_and_one_over_fails(tmp_path):
    f = _py(tmp_path, 700)
    assert cc.check_files([f], {_key(f): (700, None)})[0] == []
    f.write_text(f.read_text() + "y = 2\n")
    findings, _ = cc.check_files([f], {_key(f): (700, None)})
    assert len(findings) == 1
    assert "701 lines > ceiling 700" in findings[0][1]
    assert f"{_key(f)} 701  # YYYY-MM-DD" in findings[0][2]


def test_listed_file_that_shrank_below_the_cap_is_a_stale_entry(tmp_path):
    """A generous leftover ceiling is an error, exactly like a stale PII allowlist entry."""
    f = _py(tmp_path, CAP)
    findings, _ = cc.check_files([f], {_key(f): (700, None)})
    assert len(findings) == 1
    assert "still listed" in findings[0][1] and "delete its line" in findings[0][2]


def test_listed_file_that_shrank_but_stays_over_the_cap_gets_a_paste_ready_note(tmp_path):
    f = _py(tmp_path, 650)
    findings, notes = cc.check_files([f], {_key(f): (700, None)})
    assert findings == []
    assert notes == [f"{_key(f)} 650"]


def test_count_lines_matches_wc_l(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("a\nb\nc")  # no trailing newline: wc -l says 2
    assert cc.count_lines(f) == 2


def test_freeze_round_trips_only_files_over_the_cap(tmp_path):
    big, small = _py(tmp_path, CAP + 5, "big.py"), _py(tmp_path, CAP, "small.py")
    entries, malformed = cc.parse_allowlist(cc.freeze([big, small]))
    assert not malformed
    assert entries == {_key(big): (CAP + 5, None)}


# --- the escape valve: growth is a recorded decision ---------------------------------- #


def test_raising_a_ceiling_without_a_dated_reason_fails():
    base = {"gtm_core/x.py": (600, None)}
    findings = cc.check_raises({"gtm_core/x.py": (620, None)}, base)
    assert len(findings) == 1 and "raised 600 -> 620" in findings[0][1]
    findings = cc.check_raises({"gtm_core/x.py": (620, "cleanup")}, base)  # undated note
    assert len(findings) == 1


def test_raising_a_ceiling_with_a_dated_reason_passes():
    base = {"gtm_core/x.py": (600, None)}
    assert (
        cc.check_raises({"gtm_core/x.py": (620, "2026-09-02 hotfix for the X parser")}, base) == []
    )


def test_lowering_or_keeping_a_ceiling_needs_no_reason():
    base = {"gtm_core/x.py": (600, None)}
    assert cc.check_raises({"gtm_core/x.py": (600, None)}, base) == []
    assert cc.check_raises({"gtm_core/x.py": (550, None)}, base) == []


def test_a_note_written_for_an_earlier_raise_does_not_cover_a_later_one():
    """720 -> 900 under the note that recorded the 720 raise leaves the 900 raise unrecorded."""
    base = {"gtm_core/x.py": (720, "2026-09-01 first raise: parser rewrite")}
    same = {"gtm_core/x.py": (900, "2026-09-01 first raise: parser rewrite")}
    findings = cc.check_raises(same, base)
    assert len(findings) == 1 and "earlier raise" in findings[0][1]
    assert cc.check_raises({"gtm_core/x.py": (900, "2026-09-02 second raise: why")}, base) == []
    assert (
        cc.check_raises({"gtm_core/x.py": (720, "2026-09-01 first raise: parser rewrite")}, base)
        == []
    )
    assert (
        cc.check_raises({"gtm_core/x.py": (700, "2026-09-01 first raise: parser rewrite")}, base)
        == []
    )


def test_new_entry_needs_a_dated_reason_the_list_only_shrinks():
    assert len(cc.check_raises({"gtm_core/new.py": (510, None)}, {})) == 1
    assert cc.check_raises({"gtm_core/new.py": (510, "2026-09-02 urgent: why")}, {}) == []


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_base_allowlist_is_read_from_the_committed_ref(tmp_path):
    """The pre-commit plumbing: `--base HEAD` reads the list as committed. When there is no
    committed list yet (the freeze commit itself) it returns None and the guard is skipped."""
    repo = tmp_path / "repo"
    (repo / "tests/lint").mkdir(parents=True)
    git = ["git", "-c", "user.email=ci@example.com", "-c", "user.name=ci"]
    subprocess.run([*git, "init", "-q"], cwd=repo, check=True)
    assert cc.load_base_allowlist("HEAD", repo_root=repo) is None  # nothing committed yet
    (repo / cc.ALLOWLIST_REL).write_text("gtm_core/x.py 600\n")
    subprocess.run([*git, "add", "."], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "freeze"], cwd=repo, check=True)
    assert cc.load_base_allowlist("HEAD", repo_root=repo) == {"gtm_core/x.py": (600, None)}
    assert cc.load_base_allowlist("no-such-ref", repo_root=repo) is None


def test_a_bare_hash_is_an_empty_note_not_a_malformed_line():
    entries, malformed = cc.parse_allowlist("gtm_core/x.py 600 #\n")
    assert malformed == [] and entries == {"gtm_core/x.py": (600, "")}


def test_listed_entries_are_validated_whatever_files_were_passed(tmp_path, monkeypatch):
    """The pre-commit shape: only the allowlist (or an unrelated file) is staged, yet a ceiling
    edited below its file's size and an entry for a deleted file must still fail."""
    (tmp_path / "gtm_core").mkdir()
    (tmp_path / "tests/lint").mkdir(parents=True)
    big, other = (
        _py(tmp_path / "gtm_core", 700, "big.py"),
        _py(tmp_path / "gtm_core", 10, "other.py"),
    )
    allow = tmp_path / "tests/lint/complexity_allowlist.txt"
    retired = tmp_path / "tests/lint/retired_patch_paths.txt"
    retired.write_text("")
    monkeypatch.setattr(cc, "ROOT", tmp_path)
    monkeypatch.setattr(cc, "ALLOWLIST", allow)
    monkeypatch.setattr(cc, "RETIRED", retired)
    allow.write_text("gtm_core/big.py 700\n")
    assert cc.main([str(other)]) == 0
    allow.write_text("gtm_core/big.py 600\n")  # lowered below the file's real size
    assert cc.main([str(other)]) == 1
    allow.write_text("gtm_core/big.py 700\ngtm_core/gone.py 650\n")  # entry for a deleted file
    assert cc.main([str(other)]) == 1
    assert big.exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_an_unresolvable_base_ref_fails_instead_of_skipping_the_raise_guard(tmp_path, monkeypatch):
    """A typo in --base must not silently disable the guard. Only "the ref resolves but has no
    allowlist yet" (the freeze commit) may skip. A "-"-prefixed ref is a revision, never a
    git option — it must neither resolve nor make git write anywhere."""
    repo = tmp_path / "repo"
    (repo / "tests/lint").mkdir(parents=True)
    (repo / "gtm_core").mkdir()
    git = ["git", "-c", "user.email=ci@example.com", "-c", "user.name=ci"]
    subprocess.run([*git, "init", "-q"], cwd=repo, check=True)
    allow, retired = repo / cc.ALLOWLIST_REL, repo / cc.RETIRED_REL
    retired.write_text("")
    monkeypatch.setattr(cc, "ROOT", repo)
    monkeypatch.setattr(cc, "ALLOWLIST", allow)
    monkeypatch.setattr(cc, "RETIRED", retired)
    (repo / "README.md").write_text("x\n")
    subprocess.run([*git, "add", "."], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "pre-freeze"], cwd=repo, check=True)
    allow.write_text("")  # the freeze: HEAD resolves, but no allowlist is committed there yet
    assert cc.main(["--base", "HEAD"]) == 0
    assert cc.main(["--base", "no-such-ref"]) == 1
    before = {p.name for p in tmp_path.iterdir()}
    # argparse itself refuses `--base --output=x` (exit 2); the value only reaches git as
    # `--base=--output=x`, where --end-of-options makes it an unresolvable revision.
    assert cc.main([f"--base=--output={tmp_path / 'injected'}"]) == 1
    assert {p.name for p in tmp_path.iterdir()} == before  # git wrote nothing
    assert cc.ref_resolves("HEAD", repo) is True and cc.ref_resolves("no-such-ref", repo) is False


def test_malformed_entries_are_reported_not_ignored():
    entries, malformed = cc.parse_allowlist("gtm_core/x.py 600\ngtm_core/y.py\n# c\n")
    assert entries == {"gtm_core/x.py": (600, None)}
    assert malformed == ["line 2: 'gtm_core/y.py'"]


# --- the retired-path guard: the silent-green patch shape ----------------------------- #


def _tests_dir(tmp_path: Path, body: str) -> Path:
    d = tmp_path / "tests"
    d.mkdir(parents=True)
    (d / "test_x.py").write_text(body, encoding="utf-8")
    return d


@pytest.mark.parametrize(
    "line",
    [
        'with patch("pkg.legacy._moved_fn") as m:\n',
        "monkeypatch.setattr('pkg.legacy._moved_fn', fake)\n",
        '@mock.patch("pkg.legacy._moved_fn", new=fake)\n',
    ],
)
def test_a_patch_naming_a_retired_path_is_caught(tmp_path, line):
    retired = ["pkg.legacy._moved_fn"]
    hits = cc.find_retired_patch_refs(retired, _tests_dir(tmp_path, line))
    assert hits and hits[0][1] == 1 and hits[0][2] == retired[0]


def test_imports_and_prose_naming_a_retired_path_are_fine(tmp_path):
    """The re-export keeps `from x import y` working — only a patch STRING goes stale."""
    body = (
        "from backend.routers.runs import _execute_run\n"
        '"""see pkg.legacy._moved_fn for the lifecycle"""\n'
        'target = "pkg.legacy._moved_fn"  # a plain string, no mock\n'
    )
    assert cc.find_retired_patch_refs(["pkg.legacy._moved_fn"], _tests_dir(tmp_path, body)) == []


def test_prose_that_says_patch_is_not_a_patch_call(tmp_path):
    """Only a mock CALL with a string target counts — not a comment or a sentence."""
    body = (
        '# we patch "pkg.legacy._moved_fn" in the fixture below\n'
        'NOTE = "the old patch target was pkg.legacy._moved_fn"\n'
    )
    assert cc.find_retired_patch_refs(["pkg.legacy._moved_fn"], _tests_dir(tmp_path, body)) == []
    body = 'patch.dict("pkg.legacy._moved_fn", {})\n'
    hits = cc.find_retired_patch_refs(["pkg.legacy._moved_fn"], _tests_dir(tmp_path / "d", body))
    assert len(hits) == 1


def test_an_excluded_name_in_the_absolute_path_does_not_disable_the_scan(tmp_path):
    """The guard's own failure class: a checkout under .../profiles/... must still be scanned."""
    root = tmp_path / "profiles" / "repo"
    body = 'with patch("pkg.legacy._moved_fn"):\n    pass\n'
    assert len(cc.find_retired_patch_refs(["pkg.legacy._moved_fn"], _tests_dir(root, body))) == 1


def test_a_longer_dotted_path_does_not_match_a_retired_prefix(tmp_path):
    """Quoted-string match, so retiring `pkg.mod.f` never flags `pkg.mod.f_other`."""
    body = 'patch("pkg.mod.f_other")\n'
    assert cc.find_retired_patch_refs(["pkg.mod.f"], _tests_dir(tmp_path, body)) == []


@pytest.mark.parametrize(
    "body",
    [
        # the shape the backend suite actually uses: a module alias + an attribute string
        "from pkg import legacy as legacy_mod\n"
        'with patch.object(legacy_mod, "_moved_fn", fake):\n    pass\n',
        "from pkg import legacy as legacy_mod\n"
        'monkeypatch.setattr(legacy_mod, "_moved_fn", fake)\n',
        'import pkg.legacy\nmock.patch.object(pkg.legacy, "_moved_fn", new=fake)\n',
        'import pkg.legacy as rr\nwith patch.object(rr, "_moved_fn"):\n    pass\n',
        'patch.multiple("pkg.legacy", _moved_fn=fake, other=1)\n',
        "from pkg import legacy\npatch.multiple(legacy, _moved_fn=fake)\n",
    ],
)
def test_an_object_patch_resolving_to_a_retired_path_is_caught(tmp_path, body):
    """`patch.object(module, "name")` carries no quoted dotted path, so the line scan is
    blind to it — yet after a split it goes stale in exactly the same silent way."""
    hits = cc.find_retired_patch_refs(["pkg.legacy._moved_fn"], _tests_dir(tmp_path, body))
    assert [h[2] for h in hits] == ["pkg.legacy._moved_fn"]


@pytest.mark.parametrize(
    "body",
    [
        # a different attribute of the same module
        'from pkg import legacy as legacy_mod\npatch.object(legacy_mod, "_other", fake)\n',
        # the same attribute name on an unrelated module
        'from pkg import other as legacy_mod\npatch.object(legacy_mod, "_moved_fn", fake)\n',
        # setattr on a plain object (not an import alias)
        'obj = Thing()\nmonkeypatch.setattr(obj, "_moved_fn", fake)\n',
        # attribute access that is not a mock call
        "from pkg import legacy as legacy_mod\nlegacy_mod._execute_run(1, 2)\n",
    ],
)
def test_object_patches_on_other_targets_are_fine(tmp_path, body):
    assert cc.find_retired_patch_refs(["pkg.legacy._moved_fn"], _tests_dir(tmp_path, body)) == []


def test_an_unparseable_test_file_does_not_crash_the_object_scan(tmp_path):
    body = "def broken(:\n    pass\n"
    assert cc.find_retired_patch_refs(["pkg.mod.f"], _tests_dir(tmp_path, body)) == []


def test_the_retired_list_parses_and_names_only_dotted_paths():
    """Phase 0 froze an empty list; every split PR appends. Each entry must be a dotted path
    (a bare name would match nothing and silently guard nothing)."""
    assert cc.RETIRED.exists()
    entries = cc.load_retired()
    assert entries, "Phase 1a retired the runs.py lifecycle names — the list is not empty"
    for e in entries:
        assert "." in e and " " not in e, e
