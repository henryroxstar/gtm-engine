"""Merge invariants in ``gtm_core.prospects_state`` that were documented but unasserted.

Found by scoped mutation testing over the full covering set (11 test files, 404
tests): each mutation named below SURVIVED, meaning the suite executed the line and
did not notice the behaviour change. These are the silent failure modes — nothing
raises, a wrong value is simply written.

1. ``_identity_key`` is DOMAIN-FIRST (``keys[0] -> keys[1]`` survived). It is the
   grouping key for in-file dedup (``prospects_import``), the ``upsert_latest``
   merge, ``set_status``, the status receipt, and the consolidate account key — it
   decides whether two rows are the same account, and nothing pinned its order.

2. ``added_at`` is stamped ONCE and never overwritten (dropping ``not`` survived).
   ``retention_sweep`` ages every row on ``added_at`` and its own docstring says
   "``upsert_latest`` stamps ``added_at`` once", so re-stamping on each merge resets
   every row's age and the sweep can never age anything out. Note the asymmetry the
   run exposed: the sibling ``account_id`` never-overwrite guard one line ABOVE is
   killed by the suite; this one was not.

3. A no-op ``mutate_account`` must not write or snapshot (``changed = False -> True``
   survived). A spurious write also burns a slot in a bounded snapshot ring,
   rotating real history out.

4. ``SNAPSHOT_KEEP`` is that ring's depth (``30 -> 31`` survived).

5. An exact company-name collision merges two DIFFERENT domains and silently drops
   one — see the xfail at the bottom, which is a defect characterisation, not a
   passing invariant.
"""

from __future__ import annotations

import json

from gtm_core import prospects_state as ps


def _write_latest(root, profile, items):
    p = ps.latest_path(profile, content_root=root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"kind": "prospects", "profile": profile, "items": items}), encoding="utf-8"
    )
    return p


def _read_latest(root, profile):
    return json.loads(ps.latest_path(profile, content_root=root).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# 1. _identity_key precedence — the grouping key
# --------------------------------------------------------------------------- #


def test_identity_key_prefers_domain_over_id_and_company():
    """Domain-first is what the merge's "two companies never collide" claim rests on."""
    key = ps._identity_key(
        {"domain": "acme-robotics.example", "id": "row-1", "company": "Acme Robotics"}
    )
    assert key == "d:acme-robotics.example"


def test_identity_key_falls_back_to_id_then_company():
    assert ps._identity_key({"id": "row-1", "company": "Acme Robotics"}) == "i:row-1"
    assert ps._identity_key({"company": "Acme Robotics"}) == "c:acme robotics"


def test_identity_key_is_empty_when_nothing_identifies_the_row():
    """An empty key means "append, never merge" — the caller must not drop the row."""
    assert ps._identity_key({"notes": "no identifiers here"}) == ""


def test_identity_keys_are_ordered_most_authoritative_first():
    keys = ps._identity_keys(
        {
            "domain": "acme.example",
            "id": "row-1",
            "company": "Acme Robotics",
            ps.ACCOUNT_ID_FIELD: "a-0123456789",
        }
    )
    assert keys == ["d:acme.example", "i:row-1", "c:acme robotics", "a:a-0123456789"]


# --------------------------------------------------------------------------- #
# 2. added_at is stamped once — the retention sweep's age basis
# --------------------------------------------------------------------------- #


def test_added_at_is_not_overwritten_on_a_later_merge(tmp_path):
    original = "2025-01-15T00:00:00+00:00"
    _write_latest(
        tmp_path, "p", [{"company": "Acme", "domain": "acme.example", "added_at": original}]
    )

    ps.upsert_latest(
        "p",
        [{"company": "Acme", "domain": "acme.example", "notes": "refreshed"}],
        "run-2",
        generated_at="2026-09-22T00:00:00+00:00",
        content_root=tmp_path,
    )

    items = _read_latest(tmp_path, "p")["items"]
    assert len(items) == 1
    assert items[0]["added_at"] == original, (
        "added_at was re-stamped — every row's age resets and retention ages nothing out"
    )


def test_added_at_is_stamped_when_absent(tmp_path):
    """The other half of the guard: a row with no added_at must gain one."""
    _write_latest(tmp_path, "p", [])
    ps.upsert_latest(
        "p",
        [{"company": "Acme", "domain": "acme.example"}],
        "run-1",
        generated_at="2026-09-22T00:00:00+00:00",
        content_root=tmp_path,
    )
    assert _read_latest(tmp_path, "p")["items"][0]["added_at"] == "2026-09-22T00:00:00+00:00"


def test_account_id_is_not_reassigned_on_a_later_merge(tmp_path):
    """The sibling invariant, kept beside added_at so the pair cannot drift apart."""
    _write_latest(
        tmp_path,
        "p",
        [{"company": "Acme", "domain": "acme.example", ps.ACCOUNT_ID_FIELD: "a-deadbeef01"}],
    )
    ps.upsert_latest(
        "p",
        [{"company": "Acme", "domain": "acme.example", "notes": "refreshed"}],
        "run-2",
        content_root=tmp_path,
    )
    assert _read_latest(tmp_path, "p")["items"][0][ps.ACCOUNT_ID_FIELD] == "a-deadbeef01"


# --------------------------------------------------------------------------- #
# 3. a no-op mutate_account writes nothing
# --------------------------------------------------------------------------- #


def test_noop_mutate_account_does_not_write_or_snapshot(tmp_path):
    _write_latest(tmp_path, "p", [{"company": "Acme", "domain": "acme.example", "status": "new"}])
    path = ps.latest_path("p", content_root=tmp_path)
    before = path.read_bytes()

    result = ps.mutate_account("p", "acme.example", {"status": "new"}, content_root=tmp_path)

    assert result["changed"] is False, "a no-op mutate reported a change"
    assert result["snapshot"] is None, "a no-op mutate burned a snapshot slot"
    assert path.read_bytes() == before, "a no-op mutate rewrote latest.json"


def test_real_mutate_account_does_write_and_snapshot(tmp_path):
    """Control: the same call path must still write when something actually changes."""
    _write_latest(tmp_path, "p", [{"company": "Acme", "domain": "acme.example", "status": "new"}])
    result = ps.mutate_account(
        "p", "acme.example", {"status": "disqualified"}, content_root=tmp_path
    )
    assert result["changed"] is True
    assert result["snapshot"] is not None
    assert _read_latest(tmp_path, "p")["items"][0]["status"] == "disqualified"


# --------------------------------------------------------------------------- #
# 4. the snapshot ring is bounded at SNAPSHOT_KEEP
# --------------------------------------------------------------------------- #


#: The retention depth is asserted as a LITERAL on purpose. Writing
#: ``ps.SNAPSHOT_KEEP`` on both sides moves the expectation with the constant, so
#: the test passes for every value and checks nothing — the first draft of this
#: test did exactly that and a ``30 -> 31`` mutation survived it. Changing the
#: policy should require editing this number deliberately.
EXPECTED_SNAPSHOT_KEEP = 30


def test_snapshot_keep_is_the_documented_retention_depth():
    assert ps.SNAPSHOT_KEEP == EXPECTED_SNAPSHOT_KEEP, (
        "the snapshot retention depth changed — update EXPECTED_SNAPSHOT_KEEP "
        "deliberately if that was intended"
    )


def test_prune_snapshots_keeps_exactly_the_newest_ring(tmp_path):
    snap_dir = tmp_path / ps.SNAPSHOT_DIRNAME
    snap_dir.mkdir(parents=True)
    made = []
    for i in range(EXPECTED_SNAPSHOT_KEEP + 5):
        f = snap_dir / f"latest-2026010100000{i:03d}.json"
        f.write_text("{}", encoding="utf-8")
        made.append(f)

    ps._prune_snapshots(snap_dir)

    left = sorted(snap_dir.glob("latest-*.json"))
    assert len(left) == EXPECTED_SNAPSHOT_KEEP, (
        f"snapshot ring kept {len(left)}, not {EXPECTED_SNAPSHOT_KEEP}"
    )
    assert left == made[-EXPECTED_SNAPSHOT_KEEP:], "pruning kept the wrong end of the ring"


def test_prune_snapshots_tolerates_a_file_removed_underneath_it(tmp_path, monkeypatch):
    """3-5 sessions share one working tree here, so a snapshot can vanish mid-prune.

    The race is between the glob and the unlink, so the test has to reproduce it
    there: deleting a file *before* calling ``_prune_snapshots`` only shortens the
    glob and never reaches ``missing_ok``. A phantom entry in the glob result is
    what actually exercises it.
    """
    snap_dir = tmp_path / ps.SNAPSHOT_DIRNAME
    snap_dir.mkdir(parents=True)
    for i in range(EXPECTED_SNAPSHOT_KEEP + 3):
        (snap_dir / f"latest-2026010100000{i:03d}.json").write_text("{}", encoding="utf-8")

    real_glob = ps.Path.glob
    # Sorts FIRST so it lands inside the prune window (``snaps[:-keep]``) and the
    # unlink actually reaches it; a phantom sorting last is simply kept, and the
    # missing_ok path is never exercised.
    phantom = snap_dir / "latest-0000000000000000.json"  # globbed, then gone

    def glob_with_phantom(self, pattern):
        results = list(real_glob(self, pattern))
        if self == snap_dir and pattern == "latest-*.json":
            return iter([phantom, *results])
        return iter(results)

    monkeypatch.setattr(ps.Path, "glob", glob_with_phantom)

    ps._prune_snapshots(snap_dir)  # must not raise FileNotFoundError on the phantom

    monkeypatch.undo()
    assert len(sorted(snap_dir.glob("latest-*.json"))) == EXPECTED_SNAPSHOT_KEEP


# --------------------------------------------------------------------------- #
# 5. DEFECT CHARACTERISATION — an exact name collision drops a domain
# --------------------------------------------------------------------------- #
#
# Strict xfail, the same idiom tests/contracts/test_overlay_reach.py uses for a
# known gap: it records the defect without reddening CI, and the moment someone
# fixes it the XPASS fails the run and forces the marker's removal.
#
# Two rows with the same exact company name and DIFFERENT domains both derive the
# primary key ``c:<company>``, collide in AccountMatcher's index, and merge — one
# domain is silently discarded. prospects_merge.AccountMatcher's own legal-name
# FALLBACK refuses exactly this ("a candidate whose domain differs from the item's
# is a different company"); the primary exact-name key carries no such guard, so
# the two paths on one merge disagree. tests/test_prospects_import.py:77 already
# asserts the invariant for the IMPORT path ("different domains keep them apart").
#
# The shrink tripwire cannot see it: nothing gets smaller, and the summary reports
# an ordinary ``updated=1``. Worst shape — merging onto an EXISTING qualified row
# moves the operator's status/notes/owner onto the other company's domain.
#
# Fixing it is a semantic decision (whether regional entities of one brand should
# merge) with consequences for stored data, so it is characterised here rather than
# changed unilaterally.


def test_same_name_different_domains_must_not_merge(tmp_path):
    _write_latest(tmp_path, "p", [])
    ps.upsert_latest(
        "p",
        [
            {"company": "Summit Partners", "domain": "summit-one.example"},
            {"company": "Summit Partners", "domain": "summit-two.example"},
        ],
        "run-1",
        content_root=tmp_path,
    )
    stored = _read_latest(tmp_path, "p")["items"]
    domains = {r.get("domain") for r in stored}
    assert domains == {"summit-one.example", "summit-two.example"}, (
        f"a domain was silently dropped: kept {sorted(domains)}"
    )


def test_operator_status_does_not_migrate_to_another_domain(tmp_path):
    _write_latest(
        tmp_path,
        "p",
        [
            {
                "company": "Summit Partners",
                "domain": "summit-one.example",
                "status": "qualified",
                "notes": "CFO intro booked",
            }
        ],
    )
    ps.upsert_latest(
        "p",
        [{"company": "Summit Partners", "domain": "summit-two.example"}],
        "run-2",
        content_root=tmp_path,
    )
    stored = _read_latest(tmp_path, "p")["items"]
    qualified = [r for r in stored if r.get("status") == "qualified"]
    assert len(qualified) == 1, "the operator's qualification was duplicated or lost"
    assert qualified[0]["domain"] == "summit-one.example", (
        "the qualification now points at a domain the operator never qualified"
    )


def test_parent_and_subsidiary_sharing_a_name_stay_apart(tmp_path):
    """The shape that motivated the fix: one brand, two legal entities, two domains."""
    _write_latest(tmp_path, "p", [])
    ps.upsert_latest(
        "p",
        [
            {"company": "Northwind Bank", "domain": "northwind.example"},
            {"company": "Northwind Bank", "domain": "northwind.com.sg"},
        ],
        "run-1",
        content_root=tmp_path,
    )
    stored = _read_latest(tmp_path, "p")["items"]
    assert {r.get("domain") for r in stored} == {"northwind.example", "northwind.com.sg"}


# --------------------------------------------------------------------------- #
# The MC-01 guard must stay NARROW.
#
# It fires only when a match rests on the company NAME alone AND both sides carry a
# domain. Widening it would stop legitimate merges and strand the operator's status
# on a duplicate row — the very failure _identity_keys was built to fix, so these
# cases matter as much as the ones above.
# --------------------------------------------------------------------------- #


def test_a_domainless_row_still_merges_when_it_later_gains_a_domain(tmp_path):
    """The guard needs BOTH sides to carry a domain, so this must still merge."""
    _write_latest(tmp_path, "p", [{"company": "Acme", "status": "qualified"}])
    ps.upsert_latest(
        "p",
        [{"company": "Acme", "domain": "acme.example"}],
        "run-2",
        content_root=tmp_path,
    )
    stored = _read_latest(tmp_path, "p")["items"]
    assert len(stored) == 1, "a domainless row was orphaned instead of resolving"
    assert stored[0]["domain"] == "acme.example"
    assert stored[0]["status"] == "qualified", "the operator's status did not follow"


def test_a_reworded_name_on_the_same_domain_still_merges(tmp_path):
    """Matches on ``d:`` — an identifier — and never reaches the name guard."""
    _write_latest(
        tmp_path,
        "p",
        [{"company": "Acme Robotics, Inc.", "domain": "acme.example", "status": "qualified"}],
    )
    ps.upsert_latest(
        "p",
        [{"company": "Acme Robotics", "domain": "acme.example"}],
        "run-2",
        content_root=tmp_path,
    )
    assert len(_read_latest(tmp_path, "p")["items"]) == 1


def test_a_company_that_changed_domain_still_merges_on_its_id(tmp_path):
    """``i:`` is an identifier, not a name — the guard is scoped to ``c:`` only."""
    _write_latest(
        tmp_path,
        "p",
        [{"id": "row-7", "company": "Acme", "domain": "old.example", "status": "qualified"}],
    )
    ps.upsert_latest(
        "p",
        [{"id": "row-7", "company": "Acme", "domain": "new.example"}],
        "run-2",
        content_root=tmp_path,
    )
    stored = _read_latest(tmp_path, "p")["items"]
    assert len(stored) == 1, "an id match was blocked by a guard meant for names"
    assert stored[0]["status"] == "qualified"


def test_a_legal_form_variant_still_merges_when_one_side_is_domainless(tmp_path):
    """The legal-name fallback path, whose own domain guard also needs both domains."""
    _write_latest(tmp_path, "p", [{"company": "Contoso Freight", "status": "qualified"}])
    ps.upsert_latest(
        "p",
        [{"company": "Contoso Freight, Inc.", "domain": "contoso.example"}],
        "run-2",
        content_root=tmp_path,
    )
    assert len(_read_latest(tmp_path, "p")["items"]) == 1
