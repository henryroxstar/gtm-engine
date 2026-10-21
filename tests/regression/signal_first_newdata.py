"""The files signal-first adds to a tenant, built by this checkout's own code over the corpus.

Used by the compatibility checks: old code must read a tree that has these files and behave as if
they were absent (CM1), and deleting them must put the tree back exactly (CM8). Built, not
hand-written, so the registry, member-set state, observation shards, unresolved queue and capture
are the real formats. Everything is fictional and lives only in a scratch directory.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import shutil
import tempfile
from pathlib import Path

from signal_first_capture import PROFILE, _seed

SOURCE_URL = "https://members.example.test/list"
_REGISTRY = """
[[source]]
id = "corpus-directory"
title = "Corpus members list"
url = "{url}"
kind = "member_directory"
premise = ""
claim_gap = "Membership shows the firm takes part in this fictional network."
precision = "9/10"
member_role = "buyer"
timing = "listing_date"
cadence_days = 30
extractor = "links"
extractor_args = {{}}
max_members = 50
expires_on = "2026-12-01"
owner = "ops"
"""
_PAGE_ONE = """# Members

- [Cascade Logistics](https://cascade-logistics.example/)
- [Eastvale Logistics](https://eastvale-logistics.example/)
- [Fictional Newcomer Works](https://newcomer-works.example/)
"""
_PAGE_TWO = _PAGE_ONE + "- [Brightpath Networks](https://brightpath-networks.example/)\n"


def _files(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@contextlib.contextmanager
def _switch_open():
    """The source-list switch is closed by default; building the files means opening it, briefly."""
    held = os.environ.get("GTM_SIGNAL_SOURCES_ENABLED")
    os.environ["GTM_SIGNAL_SOURCES_ENABLED"] = "1"
    try:
        yield
    finally:
        if held is None:
            os.environ.pop("GTM_SIGNAL_SOURCES_ENABLED", None)
        else:
            os.environ["GTM_SIGNAL_SOURCES_ENABLED"] = held


def build_new_data(dest: Path) -> list[str]:
    """Write the new-data tree into ``dest`` (``content/…``, ``profiles/…``); returns its files."""
    with _switch_open():
        return _build_new_data(Path(dest))


def _build_new_data(dest: Path) -> list[str]:
    from gtm_core.signal_obs import due, extract
    from gtm_core.signal_sources import store_capture

    dest = Path(dest)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        _seed(work)
        before = _files(work)
        content, profiles = work / "content", work / "profiles"
        (profiles / PROFILE / "knowledge" / "signal-sources.toml").write_text(
            _REGISTRY.format(url=SOURCE_URL), encoding="utf-8"
        )
        (content / PROFILE / "settings.json").write_text(
            json.dumps({"observer_id": "corpus"}), encoding="utf-8"
        )
        for day, page in (("2026-09-01", _PAGE_ONE), ("2026-09-20", _PAGE_TWO)):
            store_capture(
                SOURCE_URL,
                page,
                sources_dir=content / PROFILE / "sources",
                fetched_at=f"{day}T08:00:00+00:00",
            )
            d = datetime.date.fromisoformat(day)
            report = extract.run_extract(
                PROFILE,
                "corpus-directory",
                profiles_root=profiles,
                content_root=content,
                today=d,
                now=datetime.datetime(d.year, d.month, d.day, 9, tzinfo=datetime.UTC),
                run_id=f"r{day[-2:]}",
            )
            assert report.status in ("baseline", "diff"), report
        due.write_due_manifest(
            PROFILE,
            run_id="corpus1",
            content_root=content,
            profiles_root=profiles,
            today=datetime.date(2026, 10, 30),
        )
        enrol = {
            "schema": 1,
            "email": "a.person@cascade-logistics.example",
            "sequence_id": "SEQ-1",
            "enrolled_at": "2026-10-01T09:00:00+00:00",
        }
        (content / PROFILE / "prospects" / "enrolments.jsonl").write_text(
            json.dumps(enrol) + "\n", encoding="utf-8"
        )
        after = _files(work)
        added: list[str] = []
        for rel, data in sorted(after.items()):
            if rel in before and before[rel] == data:
                continue
            out = dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            if rel in before:  # an existing .jsonl: keep only the lines that were appended
                assert rel.endswith(".jsonl") and data.startswith(before[rel]), rel
                out.write_bytes(data[len(before[rel]) :])
            else:
                out.write_bytes(data)
            added.append(rel)
    return added


def remove_new_data(work: Path, added: list[str], new_lines: dict[str, bytes]) -> None:
    """The rollback procedure: delete the new files and cut the lines appended to old ones."""
    work = Path(work)
    for rel in added:
        target = work / rel
        if rel in new_lines:
            target.write_bytes(target.read_bytes().removesuffix(new_lines[rel]))
        else:
            target.unlink()
    for empty in sorted({p.parent for p in (work / r for r in added)}, reverse=True):
        if empty.is_dir() and not any(empty.iterdir()):
            shutil.rmtree(empty)
