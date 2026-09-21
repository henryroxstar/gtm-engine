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

HAS_TENANT_DATA = bool(list(ROOT.glob("content/*/accounts")))
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

    Measured 2026-09-05 on 1,244 accounts: 308 all-English names ("harmonic", "new york
    life"), 70 tokens below the length floor ("anz", "ema"), 9 released by the allowlist.
    An unexplained miss means `derive_keys` stopped producing a key it used to produce,
    which no other test here would notice. A bare count would go stale the day an account
    is added; this asserts the property instead.
    """
    words = roster._dictionary()
    allowed = roster.load_third_party_allowed()
    rx = roster.matcher(roster.derive_keys(ROOT))
    unexplained = []
    for acc in ROOT.glob("content/*/accounts"):
        for folder in (f for f in acc.iterdir() if f.is_dir()):
            if rx.search(folder.name.replace("-", " ")):
                continue
            toks = [t for t in re.split(r"[^A-Za-z0-9]+", folder.name.lower()) if t]
            stripped = [t for t in toks if t not in roster._CORP_SUFFIXES]
            if (
                not stripped  # nothing but a corporate form
                or all(t in words for t in stripped)  # all-English: documented residual
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
def test_the_committed_digest_matches_the_live_roster():
    """CI has no tenant data and enforces against the digests. If they drift, CI is
    enforcing a stale roster while pre-commit enforces the real one — the mirror-drift
    footgun this repo has hit repeatedly. Regenerate with --write-digest."""
    live = {roster.digest(k) for k in roster.derive_keys(ROOT)}
    assert live == roster.load_digests(), (
        "third_party_digest.txt is stale — run "
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
