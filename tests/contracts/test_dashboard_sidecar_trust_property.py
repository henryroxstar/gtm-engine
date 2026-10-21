"""The property the routine rests on, over randomised page states: `--refresh-all` exits 0 exactly
when the check is green afterwards, and it deletes nothing and writes nothing outside the profile
folder.

Round 2 ran 119 such states by hand and found ten where the refresh said "done" over a red check
(old figures; a sidecar whose scope disagreed with its page). This is that run, seeded, so the
property is a test and the next state a person thinks of is one more entry in `_MUTATIONS`.

Real files, real renders, the real CLI entry point, in a copy of one rendered template per state.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import random
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from gtm_core.email_campaign_dashboard import check
from gtm_core.email_campaign_dashboard.cli import _cli
from tests.contracts.test_dashboard_check_every_page import SLUG, _days_ago, _three_pages

SEED = 20261002
STATES = 130


def _sidecar(base: Path, page: str) -> Path:
    return base / page.replace(".html", ".inputs.json")


def _edit(base: Path, which: str, **fields) -> None:
    path = _sidecar(base, which)
    if not path.exists():
        return
    rec = json.loads(path.read_text(encoding="utf-8"))
    for key, value in fields.items():
        if value is None:
            rec.pop(key, None)
        else:
            rec[key] = value
    path.write_text(json.dumps(rec), encoding="utf-8")


def _manifest(base: Path) -> Path:
    return base / "plans" / "campaigns" / f"{SLUG}.campaign.toml"


def _symlink_page(base: Path, page: str) -> None:
    path = base / page
    if path.exists():
        target = base.parent / f"elsewhere-{page}"
        target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        path.unlink()
        path.symlink_to(target)


def _symlink_sidecar(base: Path, page: str) -> None:
    path = _sidecar(base, page)
    if path.exists():
        target = base.parent / f"elsewhere-{path.name}"
        target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        path.unlink()
        path.symlink_to(target)


def _old_figures(base: Path) -> None:
    pool = base / "prospects" / "sequences" / ".pool" / "sequence-stats.json"
    pool.write_text(
        json.dumps({"fetched": _days_ago(20), "sequences": [{"id": "S1", "sent": 1}]}),
        encoding="utf-8",
    )


def _typo(path: Path) -> None:
    path.write_text('slug = "oops\ntitle = "x"\n', encoding="utf-8")


def _unlink(path: Path) -> None:
    if path.exists() or path.is_symlink():
        path.unlink()


_MUTATIONS = {
    "cells_edit": lambda b: (b / "prospects/sequences/cells.toml").write_text("# edited\n"),
    "figures_old": _old_figures,
    "manifest_removed": lambda b: _manifest(b).unlink(),
    "manifest_typo": lambda b: _typo(_manifest(b)),
    "manifest_completed": lambda b: _manifest(b).write_text(
        _manifest(b).read_text().replace('status = "active"', 'status = "done"')
    ),
    "named_sidecar_deleted": lambda b: _unlink(_sidecar(b, f"campaign-{SLUG}.html")),
    "named_sidecar_corrupt": lambda b: _sidecar(b, f"campaign-{SLUG}.html").write_text("{nope"),
    "named_scope_deleted": lambda b: _edit(b, f"campaign-{SLUG}.html", scope=None),
    "named_page_deleted": lambda b: _unlink(b / f"campaign-{SLUG}.html"),
    "rollup_page_deleted": lambda b: _unlink(b / "email_campaign_status.html"),
    "rollup_sidecar_deleted": lambda b: _unlink(_sidecar(b, "email_campaign_status.html")),
    "named_slugs_ghost": lambda b: _edit(b, f"campaign-{SLUG}.html", slugs=["ghost"]),
    "named_stem_mismatch": lambda b: _edit(b, f"campaign-{SLUG}.html", scope="open"),
    "rollup_scope_damaged": lambda b: _edit(
        b, "email_campaign_status.html", scope="campaign", slugs=["ghost"]
    ),
    "open_scope_damaged": lambda b: _edit(b, "campaign-open.html", scope="campaign", slugs=["x"]),
    "named_page_symlink": lambda b: _symlink_page(b, f"campaign-{SLUG}.html"),
    "rollup_page_symlink": lambda b: _symlink_page(b, "email_campaign_status.html"),
    "named_sidecar_symlink": lambda b: _symlink_sidecar(b, f"campaign-{SLUG}.html"),
    "rollup_sidecar_symlink": lambda b: _symlink_sidecar(b, "email_campaign_status.html"),
    "named_page_field_bad": lambda b: _edit(b, f"campaign-{SLUG}.html", page="other.html"),
    "named_inputs_absent": lambda b: _edit(b, f"campaign-{SLUG}.html", inputs=None, globs=None),
    "rollup_page_sha_absent": lambda b: _edit(b, "email_campaign_status.html", page_sha256=None),
    "named_page_hand_edited": lambda b: (b / f"campaign-{SLUG}.html").write_text("<html>x</html>"),
    "new_roster": lambda b: (
        (b / "prospects" / "imports").mkdir(parents=True, exist_ok=True)
        or (b / "prospects" / "imports" / "new.csv").write_text("a,b\n1,2\n")
    ),
}


def _states() -> list[tuple[list[str], dict]]:
    rng = random.Random(SEED)
    names = list(_MUTATIONS)
    combos = [([m], {"stubs": True, "open": True, "named": True}) for m in names]
    combos.append(([], {"stubs": True, "open": True, "named": True}))
    while len(combos) < STATES:
        picked = rng.sample(names, rng.choice([1, 2, 2, 3]))
        combos.append(
            (
                picked,
                {
                    "stubs": rng.random() < 0.5,
                    "open": rng.random() < 0.7,
                    "named": rng.random() < 0.7,
                },
            )
        )
    return combos


def _tree(root: Path) -> dict[str, str]:
    out = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_symlink():
            out[rel] = "-> " + os.readlink(path)
        elif path.is_file():
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _cli_rc(root: Path, *args: str) -> int:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            return _cli(["--profile", "acme", "--content-root", str(root), *args])
        except SystemExit as exc:
            return 1 if exc.code else 0


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    """One rendered profile: the three live pages, the stubs and fresh figures."""
    root = tmp_path_factory.mktemp("template")
    _three_pages(root, fetched=_days_ago(1), stubs=True)
    return root


def test_the_state_space_is_big_enough_and_covers_every_mutation():
    states = _states()
    assert len(states) >= 100
    assert {m for muts, _ in states for m in muts} == set(_MUTATIONS)


def test_refresh_exit_zero_iff_check_green_and_nothing_is_deleted_or_escapes(template, tmp_path):
    """For every state: exit(refresh) == 0 exactly when the check that follows is green, no file
    is deleted, and no file outside `content/acme/` (a symlink target included) changes.
    Catches: dropping the post-refresh verdict in `cli._run` (old-figures states), the file-name
    agreement in `pages.scope_of` (stem-mismatch states), the symlink refusal in the write path."""
    violations = []
    for n, (muts, opts) in enumerate(_states()):
        work = tmp_path / f"s{n}"
        shutil.copytree(template, work, symlinks=True)
        base = work / "acme"
        if not opts["stubs"]:
            for stub in ("campaigns.html", "gtm.html"):
                _unlink(base / stub)
        if not opts["open"]:
            for suffix in (".html", ".inputs.json"):
                _unlink(base / f"campaign-open{suffix}")
        if not opts["named"]:
            for suffix in (".html", ".inputs.json"):
                _unlink(base / f"campaign-{SLUG}{suffix}")
        for m in muts:
            _MUTATIONS[m](base)
        before = _tree(work)

        rc = _cli_rc(work, "--refresh-all")
        green = check.check_all_pages("acme", work).ok

        after = _tree(work)
        deleted = sorted(k for k in before if k not in after)
        outside = sorted(
            k
            for k in set(before) | set(after)
            if before.get(k) != after.get(k) and not k.startswith("acme/")
        )
        if (rc == 0) != green or deleted or outside:
            violations.append((muts, opts, rc, green, deleted, outside))
        shutil.rmtree(work, ignore_errors=True)
    assert not violations, violations[:5]


def test_old_figures_state_is_actually_in_the_run(template, tmp_path):
    """Instrument check: the property above passes vacuously if the old-figures mutation never
    makes the check red."""
    work = tmp_path / "old"
    shutil.copytree(template, work, symlinks=True)
    _old_figures(work / "acme")
    assert (
        check.check_all_pages("acme", work, now=datetime.now(UTC) + timedelta(days=1)).ok is False
    )
