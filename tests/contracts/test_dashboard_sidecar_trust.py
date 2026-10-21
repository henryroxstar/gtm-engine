"""Round-2 red team (2026-10-02): a page's `.inputs.json` is UNTRUSTED, so it may not decide that the
page is exempt from the check.

Round 1 filed a page under "retired candidate, not counted" when the sidecar's own ``scope`` /
``slugs`` named a campaign nobody owns. Both are text in a file an operator, a bad merge or a second
writer can change, so one edit made any page — the rollup included — read as finished, and the check
exited 0 with a stale page on disk. A manifest that failed to PARSE did the same thing without any
edit to a sidecar at all: ``_load_manifests`` skips it silently, so every page naming it looked
orphaned.

What decides a page's identity is its FILE NAME. What decides "retired" is the manifests, read as
facts: every campaign the page names has no manifest, and no manifest is merely unreadable. The
sidecar is only ever checked AGAINST those, and a sidecar that disagrees is stale and named.
"""

from __future__ import annotations

import json

import pytest

from gtm_core import email_campaign_dashboard as gd
from gtm_core.email_campaign_dashboard import check, freshness, pages
from gtm_core.email_campaign_dashboard.config import FIGURES_MAX_AGE_DAYS as LIMIT
from tests.contracts.test_dashboard_check_every_page import (
    _DELETE,
    ROLLUP,
    SLUG,
    _days_ago,
    _edit_a_shared_input,
    _edit_sidecar,
    _run,
    _set_status,
    _three_pages,
    _tree,
)

NAMED = f"campaign-{SLUG}.html"
OPEN = "campaign-open.html"
SECOND = "second-20260905"
COMBO = f"campaign-campaigns-{SLUG}+{SECOND}.html"
FIGURES_HINT = (
    "the sending figures are old — refresh them with the email-sequence skill, "
    "then run --refresh-all again"
)


def _second_campaign(base, slug=SECOND):
    (base / "plans" / "campaigns" / f"{slug}.campaign.toml").write_text(
        f'slug = "{slug}"\ntitle = "Second"\nstatus = "active"\n'
        'roster_globs = ["mine-20260904-hubspot.csv"]\n[targets]\nprospects = 1\n',
        encoding="utf-8",
    )


def _four_pages(tmp_path):
    """The live shape plus a two-campaign page; every page current."""
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    _second_campaign(base)
    gd.render_dashboard("acme", tmp_path, stubs=False, scope="open")
    gd.render_dashboard("acme", tmp_path, stubs=False, scope="campaign", campaign=SLUG)
    gd.render_dashboard(
        "acme", tmp_path, stubs=False, scope="campaign", campaign=f"{SLUG},{SECOND}"
    )
    gd.render_dashboard("acme", tmp_path, stubs=False, scope="all")
    assert check.check_all_pages("acme", tmp_path).ok
    return base


def _edit_fields(base, page, fields):
    """`_edit_sidecar` with the fields as a dict: one of them is called `page`."""
    path = base / page.replace(".html", ".inputs.json")
    rec = json.loads(path.read_text(encoding="utf-8"))
    for key, value in fields.items():
        if value is _DELETE:
            rec.pop(key, None)
        else:
            rec[key] = value
    path.write_text(json.dumps(rec), encoding="utf-8")


def _manifest(base, slug=SLUG):
    return base / "plans" / "campaigns" / f"{slug}.campaign.toml"


# --- C1: the rollup can never be retired -------------------------------------------------------

_ROLLUP_CLAIMS = [
    ("campaign", ["ghost"]),
    ("campaign", [SLUG]),
    ("open", []),
    ("open", [SLUG]),
    ("all", ["ghost"]),
    ("all", [SLUG]),
]


@pytest.mark.parametrize("scope,slugs", _ROLLUP_CLAIMS)
def test_the_rollup_is_never_retired_whatever_its_sidecar_claims(tmp_path, capsys, scope, slugs):
    """C1. A damaged rollup sidecar (`scope=campaign, slugs=[ghost]`) read "retired candidate, not
    counted", so with the scoped pages edited away the check counted ZERO pages and exited 0.
    Catches: deleting the rollup rule from `pages._agrees` (it then falls to the retire branch)."""
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    _edit_a_shared_input(base)  # the rollup is genuinely stale
    _edit_sidecar(base, ROLLUP, scope=scope, slugs=slugs)

    rc, _out, err = _run(tmp_path, capsys, "--check-fresh")
    assert rc == 1
    assert f"{ROLLUP}: STALE" in err
    assert f"{ROLLUP}: retired" not in err
    assert "disagrees with its own file name" in err or "records no usable scope" in err
    assert ", 0 retired candidate(s)." in err


def test_a_profile_whose_only_sidecar_is_a_damaged_rollup_is_not_green(tmp_path, capsys):
    """The empty-profile variant: nothing else counted, so a retired rollup was an all-clear."""
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    for page in (OPEN, NAMED):
        (base / page).unlink()
        (base / page.replace(".html", ".inputs.json")).unlink()
    _edit_sidecar(base, ROLLUP, scope="campaign", slugs=["ghost"])

    rc, _out, err = _run(tmp_path, capsys, "--check-fresh")
    assert rc == 1
    assert "1 pages checked: 0 fresh, 1 stale, 0 retired candidate(s)." in err


def test_refresh_all_repairs_a_damaged_rollup_sidecar(tmp_path, capsys):
    """The rollup is always re-rendered, which rewrites its sidecar: the damage is cleared by the
    documented remedy, so the check is not a dead end."""
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    _edit_sidecar(base, ROLLUP, scope="campaign", slugs=["ghost"])
    assert _run(tmp_path, capsys, "--check-fresh")[0] == 1
    assert _run(tmp_path, capsys, "--refresh-all")[0] == 0
    assert _run(tmp_path, capsys, "--check-fresh")[0] == 0


# --- C1: a named page's identity is its file name ----------------------------------------------

_NAMED_CLAIMS = {
    "open": ({"scope": "open"}, "disagrees with its own file name"),
    "ghost": ({"slugs": ["ghost"]}, "disagrees with its own file name"),
    "mixed": ({"slugs": [SLUG, "ghost"]}, "disagrees with its own file name"),
    "other-real": ({"slugs": [SECOND]}, "disagrees with its own file name"),
    "both": ({"slugs": [SLUG, SECOND]}, "disagrees with its own file name"),
    "all": ({"scope": "all", "slugs": []}, "records no usable scope"),
    "junk-entry": ({"slugs": [SLUG, 7]}, "records no usable scope"),
}


@pytest.mark.parametrize("claim", list(_NAMED_CLAIMS), ids=list(_NAMED_CLAIMS))
def test_a_named_pages_scope_must_agree_with_its_file_name(tmp_path, capsys, claim):
    """C1/I1(b). `campaign-<slug>.html` is about `<slug>` because of its NAME. A sidecar that says
    otherwise is stale and named — never retired, and never rendered AS the scope it claims: the
    refresh used to write `campaign-open.html` (or create `campaign-<other>.html`) and exit 0 while
    the page the check named stayed red.
    Catches: dropping the file-name agreement in `pages.scope_of`."""
    fields, phrase = _NAMED_CLAIMS[claim]
    base = _four_pages(tmp_path)
    _edit_a_shared_input(base)
    _edit_sidecar(base, NAMED, **fields)
    files = sorted(p.name for p in base.glob("campaign-*"))

    rc, _out, err = _run(tmp_path, capsys, "--check-fresh")
    assert rc == 1
    assert f"{NAMED}: STALE" in err and phrase in err
    assert f"{NAMED}: retired" not in err and ", 0 retired candidate(s)." in err

    rc, out, err = _run(tmp_path, capsys, "--refresh-all")
    assert rc == 1, "the page the check names is still red, so the refresh is not done"
    assert f"FAILED to re-render {NAMED}" in err
    assert sorted(p.name for p in base.glob("campaign-*")) == files, "no page was created"
    assert out.count(OPEN) == 1, "campaign-open.html is re-rendered for ITSELF, once"


def test_a_page_named_for_the_open_scope_cannot_claim_a_campaign(tmp_path, capsys):
    base = _four_pages(tmp_path)
    _edit_a_shared_input(base)
    _edit_sidecar(base, OPEN, scope="campaign", slugs=["ghost"])
    rc, _out, err = _run(tmp_path, capsys, "--check-fresh")
    assert rc == 1 and f"{OPEN}: STALE" in err and "disagrees with its own file name" in err
    assert f"{OPEN}: retired" not in err


def test_an_agreeing_sidecar_is_still_live(tmp_path, capsys):
    """The positive control for the two tests above: the agreement rule is not "convict
    everything"."""
    _four_pages(tmp_path)
    rc, out, _err = _run(tmp_path, capsys, "--check-fresh")
    assert rc == 0 and "4 pages checked: 4 fresh, 0 stale, 0 retired candidate(s)." in out


def test_two_campaign_slugs_in_either_order_agree_with_the_sorted_file_name(tmp_path):
    """`--campaign b,a` records `[b, a]` but names the file with the sorted stem, so agreement is
    judged by the same naming code, not by comparing lists."""
    base = _four_pages(tmp_path)
    _edit_sidecar(base, COMBO, slugs=[SECOND, SLUG])
    reports = {r.page.name: r for r in check.check_all_pages("acme", tmp_path).reports}
    assert reports[COMBO].ok, reports[COMBO].explain()


# --- C1: retire only when ALL the page's own campaigns are gone --------------------------------


def test_a_page_whose_campaigns_are_only_partly_gone_is_stale_not_retired(tmp_path, capsys):
    """Mixed slugs never retire (red team mutant N01: retiring when ANY slug was missing survived).
    Half the page's campaigns are still alive, so it is neither finished nor re-renderable at its
    own scope: stale, named, and the refresh fails on it rather than silently skipping.
    Catches: retiring on `any(missing)` instead of `all(missing)` in `pages.scope_of`."""
    base = _four_pages(tmp_path)
    _manifest(base, SECOND).unlink()

    rc, _out, err = _run(tmp_path, capsys, "--check-fresh")
    assert rc == 1
    assert f"{COMBO}: STALE" in err
    assert "no longer resolve" in err and SECOND in err and "while others still do" in err
    assert f"{COMBO}: retired" not in err

    rc, _out, err = _run(tmp_path, capsys, "--refresh-all")
    assert rc == 1 and f"FAILED to re-render {COMBO}" in err


def test_a_page_whose_campaigns_are_all_gone_is_retired(tmp_path, capsys):
    """The other half, so the rule above is not "never retire"."""
    base = _four_pages(tmp_path)
    _manifest(base, SECOND).unlink()
    _manifest(base, SLUG).unlink()

    rc, out, err = _run(tmp_path, capsys, "--refresh-all")
    assert rc == 0, err
    for page in (NAMED, COMBO, OPEN):
        assert (
            f"{page}: retired candidate — its campaign no longer resolves; not re-rendered" in out
        )
    rc, out, err = _run(tmp_path, capsys, "--check-fresh")
    assert rc == 0, err
    assert "1 pages checked: 1 fresh, 0 stale, 3 retired candidate(s)." in out


# --- C2: a manifest that does not LOAD is not a manifest that is GONE --------------------------


def _break(base, how):
    man = _manifest(base)
    if how == "typo":
        man.write_text('slug = "mine-20260904\ntitle = "x"\n', encoding="utf-8")
    elif how == "directory":
        man.unlink()
        man.mkdir()
    elif how == "no-slug":
        man.write_text('title = "no slug here"\nstatus = "active"\n', encoding="utf-8")


MANIFEST_SENTENCE = "its campaign manifest {slug} cannot be read — fix it, then run --refresh-all"


@pytest.mark.parametrize("how", ["typo", "directory", "no-slug"])
def test_a_manifest_that_does_not_load_never_retires_a_page(tmp_path, capsys, how):
    """C2, the natural trigger: an operator is mid-edit on a manifest while `consolidate`'s tail
    re-renders the rollup. `_load_manifests` skips the broken file silently, every page that named
    it looked orphaned, and the unscoped check exited 0 over two stale pages while `--campaign X`
    exited 1 on the same page. All three forms now agree.
    Catches: dropping the `broken` test in `pages.scope_of`."""
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    _edit_a_shared_input(base)
    _break(base, how)
    gd.render_dashboard("acme", tmp_path, stubs=False, scope="all")  # the tail's own call
    sentence = MANIFEST_SENTENCE.format(slug=SLUG)

    rc, _out, err = _run(tmp_path, capsys, "--check-fresh")
    assert rc == 1
    for page in (OPEN, NAMED):
        assert f"{page}: STALE — {sentence}." in err
        assert f"{page}: retired" not in err
    assert ", 0 retired candidate(s)." in err

    rc, _out, err = _run(tmp_path, capsys, "--check-fresh", "--scope", "open")
    assert rc == 1 and sentence in err
    rc, _out, err = _run(tmp_path, capsys, "--check-fresh", "--campaign", SLUG)
    assert rc == 1 and sentence in err
    rc, _out, err = _run(
        tmp_path, capsys, "--check-fresh", "--scope", "campaign", "--campaign", SLUG
    )
    assert rc == 1 and sentence in err

    rc, out, err = _run(tmp_path, capsys, "--refresh-all")
    assert rc == 1
    assert f"FAILED to re-render {NAMED}: {sentence}" in err
    assert f"FAILED to re-render {OPEN}: {sentence}" in err
    assert "retired candidate" not in out


def test_a_campaign_with_an_unreadable_manifest_and_no_page_is_named_not_called_missing(
    tmp_path, capsys
):
    """`--campaign X` for a campaign whose manifest does not load, with no page ever rendered for
    it: the answer is the manifest, not "no inventory" and not "unknown campaign".
    Catches: removing the `if unknown:` return in `check.check_one`."""
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    _second_campaign(base)
    _manifest(base, SECOND).write_text('slug = "second-20260905\n', encoding="utf-8")
    rc, _out, err = _run(tmp_path, capsys, "--check-fresh", "--campaign", SECOND)
    assert rc == 1
    assert f"STALE — {MANIFEST_SENTENCE.format(slug=SECOND)}." in err


def test_refreshing_a_profile_that_does_not_exist_raises_before_rendering_anything(tmp_path):
    """The API form (the consolidate tail calls it): a typo is an abort, not a "failed page".
    Catches: removing the profile validation in `freshness.refresh_pages`."""
    _three_pages(tmp_path, fetched=_days_ago(1))
    before = _tree(tmp_path)
    with pytest.raises(FileNotFoundError):
        freshness.refresh_pages("acmee", tmp_path)
    assert _tree(tmp_path) == before


def test_repairing_the_manifest_and_refreshing_reaches_green(tmp_path, capsys):
    """The sentence's own advice works: "fix it, then run --refresh-all"."""
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    original = _manifest(base).read_text(encoding="utf-8")
    _break(base, "typo")
    assert _run(tmp_path, capsys, "--check-fresh")[0] == 1
    _manifest(base).write_text(original, encoding="utf-8")
    assert _run(tmp_path, capsys, "--refresh-all")[0] == 0
    assert _run(tmp_path, capsys, "--check-fresh")[0] == 0


def test_a_removed_manifest_still_retires_unlike_a_broken_one(tmp_path, capsys):
    """The contrast that stops the fix from being "never retire": a manifest that is GONE is a
    finished campaign."""
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    _manifest(base).unlink()
    rc, out, err = _run(tmp_path, capsys, "--refresh-all")
    assert rc == 0, err
    assert f"{NAMED}: retired candidate" in out


def test_the_manifest_reader_agrees_with_the_one_the_page_uses(tmp_path):
    """`pages.read_manifests` re-reads the manifests so it can see the ones that fail to load; it
    must not drift from `campaigns_dashboard._load_manifests` on the ones that do."""
    from gtm_core.campaigns_dashboard import _load_manifests

    base = _four_pages(tmp_path)
    mans = pages.read_manifests("acme", tmp_path)
    assert {m["slug"] for m in mans.loaded} == {
        m["slug"] for m in _load_manifests("acme", tmp_path)
    }
    assert mans.broken == ()
    _break(base, "typo")
    assert pages.read_manifests("acme", tmp_path).broken == (SLUG,)


# --- C3: ONE classifier, whether the check asks about one page or all of them ------------------

_DAMAGE = {
    "scope-mismatch": {"scope": "campaign", "slugs": ["ghost"]},
    "page-field": {"page": "other.html"},
    "page-field-deleted": {"page": _DELETE},
    "scope-deleted": {"scope": _DELETE},
    "no-inputs": {"inputs": _DELETE},
    "no-globs": {"globs": _DELETE},
    "no-page-sha": {"page_sha256": _DELETE},
}


@pytest.mark.parametrize("damage", list(_DAMAGE), ids=list(_DAMAGE))
def test_a_single_page_check_classifies_a_damaged_sidecar_like_the_walk(tmp_path, capsys, damage):
    """C3. `--check-fresh --scope open` went straight to the digests and said "fresh" on a sidecar
    the all-pages walk convicts. Both now go through `pages.classify`, so the two forms agree on
    the verdict AND on the sentence.
    Catches: routing `render.check_fresh` around `check.check_one`'s classifier."""
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    _edit_fields(base, OPEN, _DAMAGE[damage])

    rc, _out, err = _run(tmp_path, capsys, "--check-fresh", "--scope", "open")
    assert rc == 1, "the single-page form said fresh on a sidecar the walk convicts"
    walked = {r.page.name: r for r in check.check_all_pages("acme", tmp_path).reports}[OPEN]
    assert walked.ok is False
    assert walked.explain() in err


def test_a_damaged_rollup_sidecar_convicts_scope_all_too(tmp_path, capsys):
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    _edit_sidecar(base, ROLLUP, scope="campaign", slugs=["ghost"])
    rc, _out, err = _run(tmp_path, capsys, "--check-fresh", "--scope", "all")
    assert rc == 1 and "disagrees with its own file name" in err


def test_a_clean_single_page_check_still_exits_zero(tmp_path, capsys):
    _three_pages(tmp_path, fetched=_days_ago(1))
    for args in (["--scope", "open"], ["--scope", "all"], ["--campaign", SLUG]):
        assert _run(tmp_path, capsys, "--check-fresh", *args)[0] == 0


# --- I1: --refresh-all exits 0 only when the check would be green ------------------------------


@pytest.mark.parametrize("fetched", [_days_ago(LIMIT + 13), "yesterday"], ids=["old", "undated"])
def test_refresh_all_cannot_fix_old_figures_and_says_so(tmp_path, capsys, fetched):
    """I1(a). A re-render cannot refresh the sending figures, so after it the check is still red —
    and the routine used to exit 0 over that, against its own contract. The verdict is the check's,
    stated once, with the one remedy.
    Catches: returning 0 without evaluating the post-refresh check in `cli._run`."""
    _three_pages(tmp_path, fetched=fetched)
    rc, out, err = _run(tmp_path, capsys, "--refresh-all")
    assert rc == 1
    assert "wrote " in out, "the pages were still re-rendered"
    assert FIGURES_HINT in err
    assert err.count(FIGURES_HINT) == 1, "the verdict is stated once"
    assert _run(tmp_path, capsys, "--check-fresh")[0] == 1


def test_refresh_all_on_current_figures_exits_zero_without_the_hint(tmp_path, capsys):
    _three_pages(tmp_path, fetched=_days_ago(1))
    rc, _out, err = _run(tmp_path, capsys, "--refresh-all")
    assert rc == 0 and FIGURES_HINT not in err


def test_a_refresh_that_only_needed_a_rerender_does_not_blame_the_figures(tmp_path, capsys):
    """The pre-figure-tracking inventory is fixed by the re-render itself: no figures hint."""
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    _edit_a_shared_input(base)
    rc, _out, err = _run(tmp_path, capsys, "--refresh-all")
    assert rc == 0 and FIGURES_HINT not in err


# --- M4: flags that conflict, and a campaign that does not exist -------------------------------


def test_check_fresh_with_refresh_all_is_a_usage_error_and_writes_nothing(tmp_path, capsys):
    """M4. `--refresh-all` won silently, so a command that said "check" rewrote three pages and the
    stubs. argparse refuses the pair.
    Catches: removing the mutual-exclusion check in `cli._cli`."""
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    before = _tree(base)
    with pytest.raises(SystemExit) as exc:
        _run(tmp_path, capsys, "--check-fresh", "--refresh-all")
    assert exc.value.code == 2
    assert "--check-fresh" in capsys.readouterr().err
    assert _tree(base) == before


@pytest.mark.parametrize(
    "args",
    [
        ["--scope", "campaign", "--campaign", "ghost"],
        ["--campaign", "ghost"],
    ],
)
def test_check_fresh_of_an_unknown_campaign_fails_loudly(tmp_path, capsys, args):
    _three_pages(tmp_path, fetched=_days_ago(1))
    with pytest.raises(SystemExit) as exc:
        _run(tmp_path, capsys, "--check-fresh", *args)
    assert "ghost" in str(exc.value) and "no campaign manifest" in str(exc.value)


@pytest.mark.parametrize("scope", ["open", "all"])
def test_check_fresh_refuses_a_campaign_it_would_ignore(tmp_path, capsys, scope):
    """`--scope open --campaign ghost` used to check the open page and drop the campaign on the
    floor, exit 0."""
    _three_pages(tmp_path, fetched=_days_ago(1))
    with pytest.raises(SystemExit) as exc:
        _run(tmp_path, capsys, "--check-fresh", "--scope", scope, "--campaign", "ghost")
    assert "--campaign" in str(exc.value) and f"--scope {scope}" in str(exc.value)


# --- the remedy sentences are pinned whole (red team mutants B20, N26, N27) --------------------


def test_the_gone_rollup_message_carries_its_remedy(tmp_path, capsys):
    """The prefix `page file is gone` was pinned and the remedy after it was not: a message that
    stops saying what to RUN is a conviction with no way out."""
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    (base / ROLLUP).unlink()
    _rc, _out, err = _run(tmp_path, capsys, "--check-fresh")
    assert f"{ROLLUP}: STALE — page file is gone — run --refresh-all to re-render it." in err


def test_the_gone_named_page_message_and_its_refresh_failure_carry_their_remedies(tmp_path, capsys):
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    (base / OPEN).unlink()
    _rc, _out, err = _run(tmp_path, capsys, "--check-fresh")
    assert (
        f"{OPEN}: STALE — page file is gone — delete campaign-open.inputs.json, or re-render it "
        "with its explicit --scope."
    ) in err
    rc, _out, err = _run(tmp_path, capsys, "--refresh-all")
    cmd = "python -m gtm_core.email_campaign_dashboard --profile acme"
    assert rc == 1
    assert (
        f"FAILED to re-render {OPEN}: the page file is gone — delete campaign-open.inputs.json, "
        f"or re-render it with its own explicit scope ({cmd} --scope open, or {cmd} --scope "
        "campaign --campaign <slug>)"
    ) in err


def test_the_no_sidecar_message_carries_the_whole_remedy(tmp_path, capsys):
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    (base / "campaign-legacy-20240101.html").write_text("<html>old</html>", encoding="utf-8")
    _rc, _out, err = _run(tmp_path, capsys, "--refresh-all")
    cmd = "python -m gtm_core.email_campaign_dashboard --profile acme"
    assert (
        "FAILED to re-render campaign-legacy-20240101.html: no campaign-legacy-20240101.inputs.json,"
        f" so its scope cannot be recovered — re-render it with its own explicit scope ({cmd} "
        f"--scope open, or {cmd} --scope campaign --campaign <slug>), or delete it"
    ) in err


def test_a_profile_typo_is_reported_by_name_not_as_an_empty_profile(tmp_path, capsys):
    """C12. The check validates the profile before reading anything; without it a typo'd profile
    was an empty one, whose answer is "1 stale", not "that profile does not exist"."""
    _three_pages(tmp_path, fetched=_days_ago(1))
    from gtm_core.email_campaign_dashboard.cli import _cli

    rc = _cli(["--profile", "acmee", "--content-root", str(tmp_path), "--check-fresh"])
    assert rc == 1
    assert "ABORTED: Profile 'acmee' does not exist" in capsys.readouterr().err
    rc = _cli(["--profile", "acmee", "--content-root", str(tmp_path), "--refresh-all"])
    assert rc == 1
    assert "ABORTED: Profile 'acmee' does not exist" in capsys.readouterr().err


def test_a_retired_status_edit_alone_does_not_retire_a_named_page(tmp_path, capsys):
    """`done` is a STATUS, and a named page names its campaign explicitly: it still resolves."""
    base = _three_pages(tmp_path, fetched=_days_ago(1))
    _set_status(base, SLUG, "done")
    rc, out, err = _run(tmp_path, capsys, "--refresh-all")
    assert rc == 0, err
    assert f"{NAMED}: retired" not in out and f"wrote {base / NAMED}" in out
