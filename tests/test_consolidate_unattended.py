from __future__ import annotations

import csv
from pathlib import Path

from gtm_core.prospects_consolidate.consolidate import consolidate


def test_consolidate_unattended_abort(tmp_path: Path):
    """Verify unattended mode aborts instead of raising ValueError on shrink."""
    profile_dir = tmp_path / "test" / "prospects"
    seq_dir = profile_dir / "sequences"
    seq_dir.mkdir(parents=True)
    master = seq_dir / "master-list.csv"

    # Existing master has 4 rows, but 2 share the same email
    with master.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["email"])
        w.writeheader()
        w.writerows(
            [
                {"email": "1@x.com"},
                {"email": "2@x.com"},
                {"email": "3@x.com"},
                {"email": "3@x.com"},  # Duplicate!
            ]
        )

    res = consolidate(
        "test", content_root=tmp_path, unattended=True, allow_shrink=False, rebuild_master=False
    )
    assert res == {"status": "aborted", "reason": "shrink_prevented"}
