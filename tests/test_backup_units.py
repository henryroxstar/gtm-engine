"""F9 (always-on hardening) — nightly backups and the monthly restore check.

Test plan: the always-on hardening test plan (2026-10-19) §3A "Phase 0".

`restic` and `docker` are replaced by small shims on PATH. The restic shim models ONE documented
restic behaviour that was verified against the real binary (restic 0.19.1) on 2026-10-19: with
`backup --stdin-from-command`, a command that exits non-zero aborts the backup and NO snapshot is
saved. Everything else the shims record is what our script asked them to do — that is the claim
under test. The decrypt check uses the real `backend.vault` AES-GCM envelope code, no mock.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SYSTEMD = ROOT / "systemd"
if not SYSTEMD.exists():
    pytest.skip("systemd/ not present (private deploy surface)", allow_module_level=True)

NIGHTLY = SYSTEMD / "backup-nightly.sh"
RESTORE = SYSTEMD / "restore-check.sh"

RESTIC_SHIM = r"""#!/usr/bin/env python3
import json, os, subprocess, sys
log = os.environ["SHIM_LOG"]
argv = sys.argv[1:]
def rec(kind, **kw):
    with open(log, "a") as f:
        f.write(json.dumps({"kind": kind, **kw}) + "\n")
if argv[0] == "backup":
    tags = [argv[i + 1] for i, a in enumerate(argv) if a == "--tag"]
    if "--stdin-from-command" in argv:
        cmd = argv[argv.index("--") + 1:]
        r = subprocess.run(cmd, stdout=subprocess.PIPE)
        if r.returncode != 0:          # restic: failed command => Fatal, no snapshot
            rec("backup_aborted", tags=tags, argv=argv)
            sys.exit(1)
        rec("snapshot", tags=tags, argv=argv, bytes=len(r.stdout))
    else:
        rec("snapshot", tags=tags, argv=argv)
elif argv[0] == "forget":
    rec("forget", argv=argv)
else:
    rec(argv[0], argv=argv)
"""

DOCKER_SHIM = r"""#!/usr/bin/env python3
import os, sys
a = sys.argv[1:]
if a[:2] == ["volume", "inspect"]:
    print(os.environ["SHIM_VOLUME_DIR"])
elif a[0] == "ps":
    print("cid-postgres")
elif a[0] == "exec":
    if os.environ.get("SHIM_PGDUMP_FAIL"):
        sys.stdout.write("-- partial dump\n"); sys.stdout.flush()
        sys.exit(3)                    # killed pg_dump
    print("-- full dump")
else:
    sys.exit(0)
"""


def _exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def shims(tmp_path: Path) -> dict:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "restic", RESTIC_SHIM)
    _exe(bin_dir / "docker", DOCKER_SHIM)
    vol = tmp_path / "vol"
    vol.mkdir()
    (vol / "f.txt").write_text("x")
    log = tmp_path / "log.jsonl"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "SHIM_LOG": str(log),
        "SHIM_VOLUME_DIR": str(vol),
        "GTM_INFRA_DIR": str(ROOT),
    }
    env.pop("SHIM_PGDUMP_FAIL", None)
    return {"env": env, "log": log, "tmp": tmp_path}


def _events(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line]


def _run(script: Path, *args: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(script), *args], env=env, capture_output=True, text=True)


# ── F9-1 pipeline safety ─────────────────────────────────────────────────────


def test_f9_1_script_is_strict_and_never_redirects_a_dump_to_disk() -> None:
    text = NIGHTLY.read_text()
    assert re.search(r"^set -euo pipefail$", text, re.M)
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "--stdin-from-command" in code
    # no plaintext dump file: no output redirect of the dump, no .sql path other than restic's stdin name
    assert not re.search(r">\s*\S*\.sql", code)
    assert "mktemp" not in code


def test_f9_1_killed_pg_dump_fails_and_no_snapshot(shims: dict) -> None:
    env = {**shims["env"], "SHIM_PGDUMP_FAIL": "1"}
    r = _run(NIGHTLY, "db", env=env)
    assert r.returncode != 0
    ev = _events(shims["log"])
    assert any(e["kind"] == "backup_aborted" and "gtm-db" in e["tags"] for e in ev)
    assert not any(e["kind"] == "snapshot" for e in ev)
    # a failed dump must not run that tag's forget/prune either (never prune on a bad night)
    assert not any(e["kind"] == "forget" for e in ev)
    leftovers = list(shims["tmp"].rglob("*.sql"))
    assert leftovers == []


def test_f9_1_good_dump_makes_one_snapshot_tagged_gtm_db(shims: dict) -> None:
    r = _run(NIGHTLY, "db", env=shims["env"])
    assert r.returncode == 0, r.stderr
    snaps = [e for e in _events(shims["log"]) if e["kind"] == "snapshot"]
    assert len(snaps) == 1 and snaps[0]["tags"] == ["gtm-db"]


# ── F9-2 tags and retention ──────────────────────────────────────────────────


def test_f9_2_each_tag_has_its_own_scoped_forget(shims: dict) -> None:
    r = _run(NIGHTLY, env=shims["env"])
    assert r.returncode == 0, r.stderr
    ev = _events(shims["log"])
    snap_tags = sorted(t for e in ev if e["kind"] == "snapshot" for t in e["tags"])
    assert snap_tags == ["gtm-cockpit", "gtm-db", "gtm-workspaces"]
    forgets = {}
    for e in ev:
        if e["kind"] == "forget":
            a = e["argv"]
            forgets[a[a.index("--tag") + 1]] = " ".join(a)
    assert set(forgets) == {"gtm-cockpit", "gtm-db", "gtm-workspaces"}
    assert "--keep-daily 7 --keep-weekly 4 --keep-monthly 6" in forgets["gtm-db"]
    for t in ("gtm-workspaces", "gtm-cockpit"):
        assert "--keep-daily 7 --keep-weekly 4" in forgets[t]
        assert "--keep-monthly" not in forgets[t]
    assert all("--prune" in v for v in forgets.values())


def test_f9_2_one_tag_failing_does_not_stop_the_others_but_fails_the_unit(shims: dict) -> None:
    env = {**shims["env"], "SHIM_PGDUMP_FAIL": "1"}
    r = _run(NIGHTLY, env=env)
    assert r.returncode != 0
    snap_tags = sorted(
        t for e in _events(shims["log"]) if e["kind"] == "snapshot" for t in e["tags"]
    )
    assert snap_tags == ["gtm-cockpit", "gtm-workspaces"]


def test_f9_2_content_policy_is_the_existing_one() -> None:
    unit = (SYSTEMD / "gtm-backup.service").read_text()
    assert "--tag gtm-content" in unit
    assert re.search(
        r"restic forget \\\s*\n\s*--keep-weekly 8 \\\s*\n\s*--keep-monthly 3 \\\s*\n\s*--prune \\\s*\n\s*--tag gtm-content",
        unit,
    )


def test_f9_2_nightly_unit_and_timer_exist_and_are_wired() -> None:
    svc = (SYSTEMD / "gtm-backup-nightly.service").read_text()
    assert "Environment=HOME=/root" in svc
    assert "doppler run --" in svc and "backup-nightly.sh" in svc
    assert re.search(r"ExecStopPost=-doppler run -- .*notify\.sh .*\$SERVICE_RESULT", svc)
    tm = (SYSTEMD / "gtm-backup-nightly.timer").read_text()
    assert re.search(r"OnCalendar=\*-\*-\* \d\d:\d\d:\d\d", tm)  # nightly
    rc = (SYSTEMD / "gtm-restore-check.timer").read_text()
    assert re.search(r"OnCalendar=.*\*-\*-(0?1|\d+)", rc)  # monthly day-of-month


def test_f9_4_backed_up_set_covers_workspaces_and_cockpit_volumes(shims: dict) -> None:
    _run(NIGHTLY, env=shims["env"])
    text = NIGHTLY.read_text()
    assert "workspaces" in text and "cockpit_projects" in text
    paths = [
        a
        for e in _events(shims["log"])
        if e["kind"] == "snapshot"
        for a in e["argv"]
        if a == shims["env"]["SHIM_VOLUME_DIR"]
    ]
    assert len(paths) == 2  # both volumes resolved through `docker volume inspect`


# ── F9-3 restore check ───────────────────────────────────────────────────────


def test_f9_3_restore_check_refuses_without_a_separately_held_key(shims: dict) -> None:
    env = {k: v for k, v in shims["env"].items() if k != "RESTORE_CHECK_VAULT_KEK"}
    r = _run(RESTORE, env=env)
    assert r.returncode == 2
    assert "RESTORE_CHECK_VAULT_KEK" in r.stderr


def _row(kek: bytes) -> dict:
    sys.path.insert(0, str(ROOT))
    from backend import vault

    enc = vault.encrypt('{"token":"fictional-not-a-secret"}', kek)
    return {k: (v.hex() if isinstance(v, bytes) else v) for k, v in enc.items()}


def _selfcheck(row: dict, kek_hex: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "VAULT_KEK": kek_hex, "PYTHONPATH": str(ROOT)}
    return subprocess.run(
        [sys.executable, "-m", "backend.vault_selfcheck"],
        input=json.dumps(row),
        env=env,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


def test_f9_3_decrypts_with_right_key_and_fails_with_wrong_key() -> None:
    good, bad = os.urandom(32), os.urandom(32)
    row = _row(good)
    ok = _selfcheck(row, good.hex())
    assert ok.returncode == 0, ok.stderr
    assert "fictional-not-a-secret" not in ok.stdout + ok.stderr  # never echo plaintext
    neg = _selfcheck(row, bad.hex())  # negative control
    assert neg.returncode != 0
    assert "fictional-not-a-secret" not in neg.stdout + neg.stderr


def test_f9_3_selfcheck_rejects_missing_or_malformed_key_and_empty_input() -> None:
    row = _row(os.urandom(32))
    assert _selfcheck(row, "").returncode != 0
    assert _selfcheck(row, "zz").returncode != 0
    assert _selfcheck({}, os.urandom(32).hex()).returncode != 0
