"""`account_folder.resolve` returns the folder an account ALREADY has before it mints a new one.

On 2026-09-23 one run split six accounts: the dossier skill wrote to ``slug(company)``
while the outreach run wrote next to the account's older pack, or to the ledger ``id``
minted before the company field was cleaned. Each shape below is one of the ways a
name reached a second folder, and each look-alike shape is a way two companies could be
wrongly joined into one. Fictional fixtures only (§R9).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core import account_folder as af

PROFILE = "acme"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _tree(tmp_path, folders, rows=()):
    accounts = tmp_path / PROFILE / "accounts"
    for f in folders:
        (accounts / f).mkdir(parents=True)
    prospects = tmp_path / PROFILE / "prospects"
    prospects.mkdir(parents=True)
    (prospects / "latest.json").write_text(
        json.dumps({"kind": "prospects", "items": list(rows)}), encoding="utf-8"
    )
    return tmp_path


def _resolve(root, company, domain=""):
    return af.resolve(company, PROFILE, domain, content_root=root)


def test_exact_slug_folder_wins(tmp_path):
    root = _tree(tmp_path, ["tarnwick-labs"])
    assert _resolve(root, "Tarnwick Labs") == ("tarnwick-labs", "exact")


def test_stale_import_id_resolves_to_the_folder_the_cleaned_name_owns(tmp_path):
    # The ledger row was imported as "Tarnwick & Co." (id tarnwick--co) and later cleaned to
    # "Tarnwick"; the folder is under the cleaned name. The old name must not mint a second one.
    root = _tree(tmp_path, ["tarnwick"], [{"id": "tarnwick--co", "company": "Tarnwick"}])
    assert _resolve(root, "Tarnwick & Co.") == ("tarnwick", "ledger")


def test_domain_finds_the_account_a_short_name_cannot(tmp_path):
    root = _tree(
        tmp_path,
        ["marrowgate-international-inc"],
        [
            {
                "id": "marrowgate-international-inc",
                "company": "Marrowgate International, Inc.",
                "domain": "marrowgate.example",
            }
        ],
    )
    assert _resolve(root, "Marrowgate", "marrowgate.example") == (
        "marrowgate-international-inc",
        "ledger",
    )


def test_a_domain_a_subsidiary_shares_narrows_and_the_name_picks(tmp_path):
    rows = [
        {"id": "velloria-health", "company": "Velloria Health", "domain": "velloria.example"},
        {"id": "velloria-care-plan", "company": "Velloria Care Plan", "domain": "velloria.example"},
    ]
    root = _tree(tmp_path, ["velloria-health", "velloria-care-plan"], rows)
    assert _resolve(root, "Velloria Health, Inc.", "velloria.example") == (
        "velloria-health",
        "suffix",
    )
    # With no name rung to choose between them, the shared domain alone is a question.
    with pytest.raises(af.AmbiguousFolder) as e:
        _resolve(root, "Velloria", "velloria.example")
    assert e.value.candidates == ["velloria-care-plan", "velloria-health"]


def test_punctuation_only_difference_is_the_same_folder(tmp_path):
    root = _tree(tmp_path, ["fernway-example"])
    assert _resolve(root, "Fernway.example") == ("fernway-example", "separator")


def test_trailing_legal_suffix_is_the_same_folder(tmp_path):
    root = _tree(tmp_path, ["brindlecove-limited"])
    assert _resolve(root, "Brindlecove") == ("brindlecove-limited", "suffix")
    root2 = _tree(tmp_path / "b", ["brindlecove"])
    assert _resolve(root2, "Brindlecove Holdings, Inc.") == ("brindlecove", "suffix")


def test_a_non_legal_token_keeps_two_banks_apart(tmp_path):
    # "bancorp" and "bankshares" name different companies; neither is a legal form.
    root = _tree(tmp_path, ["first-harrow-bancorp"])
    assert _resolve(root, "First Harrow Bankshares, Inc.") == (
        "first-harrow-bankshares-inc",
        "new",
    )


def test_an_extended_name_the_ledger_does_not_know_is_a_question_not_a_merge(tmp_path):
    root = _tree(tmp_path, ["quillon-leap-ai"])
    with pytest.raises(af.AmbiguousFolder) as e:
        _resolve(root, "Quillon")
    assert e.value.rung == "prefix"
    assert e.value.candidates == ["quillon-leap-ai"]


def test_an_extended_name_is_left_alone_when_the_ledger_knows_the_account(tmp_path):
    rows = [
        {"id": "quillon", "company": "Quillon", "domain": "quillon.example"},
        {"id": "quillon-leap-ai", "company": "Quillon Leap AI", "domain": "quillonleap.example"},
    ]
    root = _tree(tmp_path, ["quillon-leap-ai"], rows)
    assert _resolve(root, "Quillon", "quillon.example") == ("quillon", "new")


def test_two_candidates_at_one_rung_is_ambiguous(tmp_path):
    root = _tree(tmp_path, ["orvane-co", "orvane-group"])
    with pytest.raises(af.AmbiguousFolder) as e:
        _resolve(root, "Orvane")
    assert e.value.rung == "suffix"
    assert e.value.candidates == ["orvane-co", "orvane-group"]


def test_split_run_names_all_converge_on_one_folder(tmp_path):
    # The 2026-09-23 shape: dossier under the ledger company, pack under a short CSV name,
    # a later draft under the import id. Every name the run held must reach the one folder.
    rows = [{"id": "kestrel-shipping-ltd", "company": "Kestrel Shipping Limited"}]
    root = _tree(tmp_path, ["kestrel-shipping-limited"], rows)
    for name in ("Kestrel Shipping Limited", "Kestrel Shipping", "Kestrel Shipping Ltd"):
        assert _resolve(root, name)[0] == "kestrel-shipping-limited", name


def test_unsafe_folder_names_are_never_returned(tmp_path):
    root = _tree(tmp_path, ["$kestrel"])
    assert _resolve(root, "Kestrel") == ("kestrel", "new")


def test_cli_prints_the_folder_and_exits_zero(tmp_path, monkeypatch, capsys):
    root = _tree(tmp_path, ["brindlecove-limited"])
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(root))
    assert af.main(["Brindlecove", "--profile", PROFILE]) == 0
    out = capsys.readouterr()
    assert out.out.strip() == "brindlecove-limited"
    assert "suffix" in out.err


def test_cli_ambiguity_exits_3_and_prints_no_folder(tmp_path, monkeypatch, capsys):
    root = _tree(tmp_path, ["quillon-leap-ai"])
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(root))
    assert af.main(["Quillon", "--profile", PROFILE]) == af.EXIT_AMBIGUOUS
    out = capsys.readouterr()
    assert out.out == ""
    assert "quillon-leap-ai" in out.err


def test_cli_empty_name_exits_2(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(_tree(tmp_path, [])))
    assert af.main(["   ", "--profile", PROFILE]) == 2


# --- the properties that only real data can falsify ----------------------------------- #
#
# CLAUDE.md: "A new deterministic CLI needs a test against real-data properties before any
# skill cites it… A grouping key is a claim about identity: prove it on real data, and make
# the tool fail loudly when the key is unstable." Ten skills cite this resolver. The
# fixtures above prove each SHAPE in isolation; only the live tree can show that the rungs
# do not fight each other across ~1500 folders. Names are read from the tree at run time and
# never written into this file (§R9) — assertions are on counts and on identity, never on a
# company.

_LOCAL_ACCOUNTS = [
    p for p in sorted(Path(REPO_ROOT).glob("content/*/accounts")) if not p.parent.is_symlink()
]
requires_live_accounts = pytest.mark.skipif(
    not _LOCAL_ACCOUNTS, reason="needs a local tenant account tree; the OSS carve ships none"
)


@requires_live_accounts
def test_every_existing_folder_resolves_to_itself():
    """The fixed point. A folder's own name must resolve back to that folder — if it does
    not, the resolver would mint a second folder for an account that already has one, which
    is the exact failure it was written to stop.

    `AmbiguousFolder` is an acceptable answer (the operator is asked); a DIFFERENT folder is
    not, and neither is a crash.
    """
    misresolved, ambiguous, total = [], 0, 0
    for accounts in _LOCAL_ACCOUNTS:
        profile = accounts.parent.name
        root = accounts.parent.parent
        for folder in sorted(p for p in accounts.iterdir() if p.is_dir()):
            if folder.name.startswith("."):
                continue
            total += 1
            try:
                got, _rung = af.resolve(folder.name.replace("-", " "), profile, content_root=root)
            except af.AmbiguousFolder:
                ambiguous += 1
                continue
            if got != folder.name:
                misresolved.append((folder.name, got))
    assert total > 100, f"only {total} folders — the glob is not reaching the live tree"
    # Report the COUNT and the shape, never the names (§R9).
    # The COUNT and the shape only — a failure message that printed the folder would put a
    # real company name into CI output and into whatever pastes it (§R9).
    shapes = sorted({"case" if a.casefold() == b.casefold() else "other" for a, b in misresolved})
    assert not misresolved, (
        f"{len(misresolved)} of {total} folders do not resolve to themselves (shapes: {shapes})"
    )
    assert ambiguous / total < 0.02, (
        f"{ambiguous}/{total} folder names are ambiguous against their own tree — "
        "above the 2% an operator can be asked to adjudicate"
    )


@requires_live_accounts
def test_no_two_distinct_folders_collapse_onto_one():
    """The other direction: the rungs must not JOIN two accounts that really are separate.

    A fixed-point test alone cannot see this — a resolver that mapped every name to itself
    by identity would pass it. This asserts the map is injective over the live tree.
    """
    for accounts in _LOCAL_ACCOUNTS:
        profile = accounts.parent.name
        root = accounts.parent.parent
        landed: dict[str, int] = {}
        for folder in sorted(p for p in accounts.iterdir() if p.is_dir()):
            if folder.name.startswith("."):
                continue
            try:
                got, _ = af.resolve(folder.name.replace("-", " "), profile, content_root=root)
            except af.AmbiguousFolder:
                continue
            landed[got] = landed.get(got, 0) + 1
        collided = sum(1 for n in landed.values() if n > 1)
        assert collided == 0, (
            f"{collided} folder(s) in {profile} are the resolution target of more than one "
            "existing account — two real companies would share one folder"
        )
