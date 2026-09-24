"""Guards for the derived third-party roster — the gate for the leak class with no shape.

A grouping or matching key is a claim about identity, and this one is derived from live
tenant data rather than written down, so it has to be proven against that data rather
than against a handful of invented strings. These tests pin four properties:

  1. the keys the roster produces actually catch the names that DID reach the public repo;
  2. word-bounded matching does not fire on ordinary prose (a noisy gate gets skipped, and
     that is how a naive roster of 1,200 account names was dismissed as unusable before);
  3. the committed digests agree with the live roster, so CI enforces the same rule the
     commit hook does rather than a stale subset;
  4. the roster's own noise filters keep working — an all-English phrase and a short token
     must not become keys.

The samples here are synthetic. A test proving "we keep other people's names out of the
repo" must not itself carry them; where a real leaked name is needed to prove a property
it is constructed at run time from the live roster, never written into this file.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"tests/lint/{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


roster = _load("third_party_roster")
pii_check = _load("pii_check")

HAS_TENANT_DATA = bool(roster._local_account_trees(ROOT))
requires_tenant_data = pytest.mark.skipif(
    not HAS_TENANT_DATA, reason="derives from tenant data, which the OSS carve does not ship"
)


# --- the keys are real, and they cover the leaks that shipped -------------------------- #


@requires_tenant_data
def test_the_roster_is_derived_and_non_empty():
    keys = roster.derive_keys(ROOT)
    assert len(keys) > 100, "the roster collapsed — check the tenant-data globs"


@requires_tenant_data
def test_every_unreachable_account_falls_into_a_documented_residual():
    """~30% of accounts yield no key, and that is a KNOWN limit, not a bug — but every
    miss must be explainable by one of the filters, or a filter has broken.

    Measured 2026-09-05 on 1,244 accounts: 308 all-English names ("harmonic", "steady
    oak"), 70 tokens below the length floor ("anz", "ema"), 9 released by the allowlist.
    An unexplained miss means `derive_keys` stopped producing a key it used to produce,
    which no other test here would notice. A bare count would go stale the day an account
    is added; this asserts the property instead.
    """
    words = roster._dictionary()
    allowed = roster.load_third_party_allowed()
    rx = roster.matcher(roster.derive_keys(ROOT))
    unexplained = []
    for acc in roster._local_account_trees(ROOT):
        for folder in (f for f in acc.iterdir() if f.is_dir()):
            if rx.search(folder.name.replace("-", " ")):
                continue
            toks = [t for t in re.split(r"[^A-Za-z0-9]+", folder.name.lower()) if t]
            stripped = [t for t in toks if t not in roster._CORP_SUFFIXES]
            if (
                not stripped  # nothing but a corporate form
                # all-English (inflections included): the documented residual. This must
                # be the SAME predicate `derive_keys` drops a head token on, or a token it
                # deliberately dropped reads here as an unexplained miss.
                or all(roster.is_ordinary_word(t, words) for t in stripped)
                or (len(stripped) == 1 and len(stripped[0]) < roster.MIN_TOKEN_LEN)
                or any(t in allowed for t in stripped)
                or stripped != toks  # a suffix was stripped, so the key is shorter
            ):
                continue
            unexplained.append(folder.name)
    assert not unexplained, f"unexplained roster misses: {unexplained[:10]}"


# --- it does not cry wolf ------------------------------------------------------------- #


@requires_tenant_data
@pytest.mark.parametrize(
    "prose",
    [
        # Word-bounded matching is the property under test: each of these contains a real
        # account's key as a SUBSTRING, which is what made a naive roster look unusable.
        "the ledger is immutable once written",
        "certain rows are dropped before the send",
        "the researcher published a benchmark",
        "improvisation is not a strategy",
        "scoring weights are tunable and documented",
        "an academic medical centre on a .edu domain",
    ],
)
def test_ordinary_prose_does_not_trip_the_roster(prose):
    rx = roster.matcher(roster.derive_keys(ROOT))
    assert not rx.search(prose), f"false positive on: {prose}"


@requires_tenant_data
def test_the_whole_source_surface_is_clean():
    """The gate's own regression test: the shippable surface carries no third-party name.

    This is what makes the roster a ratchet rather than a report — a new leak fails here
    even if nobody runs the release identity read.
    """
    assert pii_check.main([]) == 0


# --- the noise filters -------------------------------------------------------------- #


def test_an_all_english_phrase_is_not_a_key(tmp_path):
    if not roster.DICT_FILE.exists():
        pytest.skip(f"{roster.DICT_FILE} is missing (install wamerican)")
    (tmp_path / "content/acme/accounts/the-first-state-bank").mkdir(parents=True)
    assert roster.derive_keys(tmp_path) == set()


@pytest.mark.parametrize("slug", ["decisions", "funding", "circles", "interactions"])
def test_an_inflected_english_word_is_not_a_key(tmp_path, slug):
    """A plural or gerund is as ordinary as its base form, and the wordlist does not say so.

    `/usr/share/dict/words` is a 1934 list of BASE forms. A bare membership test therefore
    called these four identities, and the gate fired 409 times on ordinary prose across the
    shipped surface — a dict key in the signal taxonomy, "returns one of three decisions",
    Venn-diagram guidance. A gate that cries wolf is one people learn to skip, so the fix
    belongs in key GENERATION (see `is_ordinary_word`), never in triaging the findings.
    """
    if not roster.DICT_FILE.exists():
        pytest.skip(f"{roster.DICT_FILE} is missing (install wamerican)")
    (tmp_path / f"content/acme/accounts/{slug}").mkdir(parents=True)
    assert roster.derive_keys(tmp_path) == set(), f"{slug!r} became a key"


def test_a_coined_word_the_stemmer_cannot_reach_is_still_a_key(tmp_path):
    """The stemmer is not a general English oracle, and must not be mistaken for one.

    `cyber` has no base form in a 1934 wordlist, so it survives generation and is released
    by `[third-party-allowed]` with a written reason instead. Pinned so that a later, more
    aggressive stemmer cannot quietly take over the allowlist's job: the allowlist requires
    a human and a reason, and that is the property being protected.
    """
    if not roster.DICT_FILE.exists():
        pytest.skip(f"{roster.DICT_FILE} is missing (install wamerican)")
    (tmp_path / "content/acme/accounts/cyber").mkdir(parents=True)
    assert roster.is_ordinary_word("cyber", roster._dictionary()) is False
    assert "cyber" in roster.load_third_party_allowed(), (
        "released by the allowlist, not the stemmer"
    )


def test_the_stemmer_never_invents_a_base_form_for_a_real_name(tmp_path):
    """Over-generating stems is safe only because candidates are CHECKED, never emitted.

    `zylophanes` must not be released just because stripping `s` produced something; it is
    released only if that something is in the dictionary. This is the direction the filter
    cannot afford to get wrong.
    """
    if not roster.DICT_FILE.exists():
        pytest.skip(f"{roster.DICT_FILE} is missing (install wamerican)")
    (tmp_path / "content/acme/accounts/zylophanes").mkdir(parents=True)
    assert "zylophanes" in roster.derive_keys(tmp_path)


def test_a_short_token_is_not_a_key(tmp_path):
    (tmp_path / "content/acme/accounts/sgg").mkdir(parents=True)
    assert roster.derive_keys(tmp_path) == set()


def test_a_multi_word_name_yields_a_phrase_key_that_spans_separators(tmp_path):
    (tmp_path / "content/acme/accounts/zylophane-robotics").mkdir(parents=True)
    rx = roster.matcher(roster.derive_keys(tmp_path))
    for form in ("zylophane-robotics", "Zylophane Robotics", "zylophane_robotics"):
        assert rx.search(form), form


def test_a_corporate_suffix_is_not_part_of_the_identity(tmp_path):
    (tmp_path / "content/acme/accounts/zylophane-pte-ltd").mkdir(parents=True)
    assert "zylophane" in roster.derive_keys(tmp_path)


def test_the_allowlist_releases_a_key(tmp_path, monkeypatch):
    (tmp_path / "content/acme/accounts/zylophane").mkdir(parents=True)
    assert "zylophane" in roster.derive_keys(tmp_path)
    monkeypatch.setattr(roster, "load_third_party_allowed", lambda: {"zylophane"})
    assert "zylophane" not in roster.derive_keys(tmp_path)


# --- the digest path CI relies on ------------------------------------------------------ #


@requires_tenant_data
def test_the_committed_digest_covers_the_live_roster():
    """CI has no tenant data and enforces against the digests. A live key MISSING from the
    digest means CI is enforcing a weaker roster than pre-commit — the mirror-drift footgun
    this repo has hit repeatedly. Regenerate with --write-digest.

    Containment, not equality. The digest is a union that only grows (`write_digest`), so
    it legitimately holds keys the current derivation no longer produces: a consolidated
    folder's old spelling, a tenant on external storage, a partial checkout. Those are the
    harmless direction — the gate refusing a name nobody uses any more. Equality would
    force the opposite, turning every such shrink into permission to name the company.
    """
    live = {roster.digest(k) for k in roster.derive_keys(ROOT)}
    missing = live - roster.load_digests()
    assert not missing, (
        f"third_party_digest.txt is missing {len(missing)} live key(s) — run "
        "`uv run python tests/lint/third_party_roster.py --write-digest`"
    )


@requires_tenant_data
def test_digest_mode_and_live_mode_find_the_same_thing():
    """Both modes must agree on the same bytes, including a bare name inside a comment —
    an earlier draft's digest mode tested only the greedy multi-word span and silently
    missed every single-word key."""
    keys = sorted(roster.derive_keys(ROOT))
    sample = next(k for k in keys if " " not in k and len(k) > 6)
    body = f"# a comment naming {sample} in passing\n"
    path = Path("tests/probe.py")
    domains, addresses, url_domains = pii_check.load_allowlist()

    live = pii_check.scan_file(
        path, body, domains, addresses, url_domains, roster.matcher(set(keys)), set()
    )
    dig = pii_check.scan_file(
        path, body, domains, addresses, url_domains, None, roster.load_digests()
    )
    assert live and [f[1] for f in live] == [f[1] for f in dig]


# --- scope --------------------------------------------------------------------------- #


@requires_tenant_data
def test_a_python_identifier_is_never_a_finding():
    """Only string literals and comments are read, so an attribute or variable that
    happens to spell a company name is code, not data."""
    keys = roster.derive_keys(ROOT)
    sample = next(k for k in sorted(keys) if " " not in k and k.isalpha() and len(k) > 6)
    domains, addresses, url_domains = pii_check.load_allowlist()
    body = f"{sample} = 1\nresult = {sample} + 1\n"
    assert not pii_check.scan_file(
        Path("gtm_core/probe.py"),
        body,
        domains,
        addresses,
        url_domains,
        roster.matcher(keys),
        set(),
    )


@requires_tenant_data
def test_docs_are_out_of_scope():
    """Shipped docs cite vendors and standards bodies by name as content; that surface
    stays with the human identity read rather than a gate that would fire on every
    citation. Pinned so the scope decision is deliberate rather than incidental."""
    assert not pii_check.in_third_party_scope(Path("docs") / "a-shipped-guide.md")
    assert pii_check.in_third_party_scope(Path("gtm_core/x.py"))
    assert pii_check.in_third_party_scope(Path("plugin/skills/prospect/SKILL.md"))


def test_a_ci_shaped_checkout_uses_the_digests_not_a_partial_roster(tmp_path, monkeypatch):
    """`content/` is gitignored and `profiles/` is tracked, so a CI checkout can derive a
    handful of case-study names — non-empty, and a silently weaker gate than the committed
    digests. Mode selection must key on the account tree's presence, not on a truthy set."""
    (tmp_path / "profiles/acme/knowledge").mkdir(parents=True)
    (tmp_path / "profiles/acme/knowledge/outreach-case-studies.txt").write_text("zylophane\n")
    assert roster.derive_keys(tmp_path), "precondition: profiles/ alone yields keys"
    assert not roster.has_tenant_data(tmp_path)

    # pii_check imports its own instance of the module; patch THAT one.
    monkeypatch.setattr(pii_check.third_party_roster, "has_tenant_data", lambda root=None: False)
    matcher, digests = pii_check.load_roster()
    assert matcher is None and digests, "must fall back to the committed digests"


@requires_tenant_data
def test_the_self_referential_files_carry_no_real_name():
    """`pii_check` skips its own machinery (SELF_REFERENTIAL) so the rule can be documented
    and its tests can hold rule-triggering inputs. That exemption is a hole: the first
    draft of the roster's docstring used three real customers as examples and reached a
    finished carve. Nothing else scans these files, so this does."""
    rx = roster.matcher(roster.derive_keys(ROOT))
    findings = []
    for rel in (
        "tests/lint/third_party_roster.py",
        "tests/lint/pii_check.py",
        "tests/lint/test_pii_check.py",
        "tests/lint/test_third_party_roster.py",
        "tests/test_fictionalize.py",
        "gtm_core/fictionalize.py",
    ):
        path = ROOT / rel
        if not path.exists():
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            findings += [f"{rel}:{lineno}: {m}" for m in rx.findall(line)]
    assert not findings, f"real third-party name in an unscanned file: {findings}"


# --- external storage is not a source surface ----------------------------------------- #
#
# One tenant keeps its account and content data in a cloud-synced folder, reached through a
# symlink at `content/<tenant>`. Those bytes are STORAGE, not source. A lint that globs
# through the symlink makes a commit hook depend on a file provider being mounted and on
# the OS permission that provider sits behind — neither of which is a property of the diff
# being committed. On 2026-09-24 that is exactly what happened: `iterdir` raised
# PermissionError from the cloud mount and the whole pre-commit run died, which reads as
# "the lint is broken" and, worse, meant the gate reported nothing at all while three real
# findings sat in the tree.


def _tenant(root, name, *, external=False, tmp_path=None):
    """A tenant account tree; `external=True` makes content/<name> a symlink elsewhere."""
    if external:
        outside = tmp_path / "elsewhere" / name / "accounts"
        outside.mkdir(parents=True)
        (outside / "northgate-systems").mkdir()
        (root / "content").mkdir(parents=True, exist_ok=True)
        (root / "content" / name).symlink_to(outside.parent, target_is_directory=True)
        return outside
    accounts = root / "content" / name / "accounts"
    accounts.mkdir(parents=True)
    (accounts / "harborline-freight").mkdir()
    return accounts


def test_a_symlinked_tenant_tree_is_never_traversed(tmp_path):
    root = tmp_path / "repo"
    local = _tenant(root, "acme")
    _tenant(root, "external", external=True, tmp_path=tmp_path)
    assert roster._local_account_trees(root) == [local]


def test_a_symlinked_tenants_names_are_not_derived_keys(tmp_path):
    """The stated residual: an external tenant contributes nothing to the roster."""
    root = tmp_path / "repo"
    _tenant(root, "acme")
    _tenant(root, "external", external=True, tmp_path=tmp_path)
    keys = roster.derive_keys(root)
    assert "harborline" in keys, "the local tenant must still derive"
    assert not any("northgate" in k for k in keys), "reached through the symlink"


def test_the_roster_does_not_read_an_unreadable_external_tree(tmp_path):
    """The regression proper. The external tree is unreadable exactly as a cloud mount is;
    deriving must not raise, because it must never have gone there."""
    root = tmp_path / "repo"
    _tenant(root, "acme")
    outside = _tenant(root, "external", external=True, tmp_path=tmp_path)
    outside.chmod(0o000)
    try:
        assert "harborline" in roster.derive_keys(root)
        assert roster.has_tenant_data(root)
    finally:
        outside.chmod(0o755)


def test_the_digest_is_a_union_that_only_grows(tmp_path, monkeypatch):
    """`--write-digest` must never delete a key just because the live derivation shrank.

    Three things shrink it without shrinking the truth: a folder consolidation, a tenant on
    external storage, a partial checkout. A plain rewrite hands all three back as permission
    to name those companies again.
    """
    kept = roster.digest("retired account name")
    digest_file = tmp_path / "third_party_digest.txt"
    digest_file.write_text(f"# header\n\n{kept}\n", encoding="utf-8")
    monkeypatch.setattr(roster, "DIGEST_FILE", digest_file)
    root = tmp_path / "repo"
    _tenant(root, "acme")  # derives `harborline`, and nothing else
    roster.write_digest(root)
    after = roster.load_digests()
    assert kept in after, "a key present before regeneration was deleted"
    assert roster.digest("harborline") in after, "the live key was not added"


def test_a_digest_only_name_still_fires_when_the_live_matcher_is_active(monkeypatch):
    """The half of the union that the local path used to throw away.

    `load_roster` returned `set()` for digests whenever any local tree existed, so a name
    the digest still banned — a renamed folder, an external tenant — passed pre-commit and
    the export gate, both of which derive live. Both routes must run.
    """
    live_only, digest_only = "alphaworks", "betaworks holdings"
    findings = pii_check._third_party_findings(
        Path("x.md"),
        f"the {live_only} deck and the {digest_only} deck\n",
        "x.md",
        roster.matcher({live_only}),
        {roster.digest(digest_only)},
    )
    found = " ".join(f[2] for f in findings)
    assert live_only in found, "the live matcher stopped working"
    assert digest_only in found, "the digest route was not consulted beside the matcher"
