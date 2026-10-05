"""Deterministic capture of the surfaces the signal-first work must not move (test plan T0.2).

Written to run against **BASE code** (a ``git archive`` of the pinned commit, put first on
``sys.path``) and against HEAD. It calls only entry points that exist at BASE, through the same
public functions and CLIs a run uses, over the committed synthetic corpus
(``tests/fixtures/signal_first/corpus``). Every output is normalised: the work directory, the
random ``pool_row_id``s and every clock are replaced or pinned, so a diff is a behaviour change
and never noise.

``capture(work, flags=...)`` returns ``{surface: text-or-json}``. ``flags`` is ``"closed"`` (the
two signal-first variables unset) or ``"open"`` (both set, with no observations on disk); at BASE
neither variable is read, so both must reproduce the same bytes.
"""

from __future__ import annotations

import contextlib
import datetime
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
CORPUS = HERE.parent / "fixtures" / "signal_first" / "corpus"
PROFILE = "sigfirst"
TODAY = datetime.date(2026, 10, 1)
STAMP = "2026-10-01"
FLAGS = ("GTM_SIGNAL_SOURCES_ENABLED", "GTM_SIGNAL_VIEW_ROUTING")

_ROW_ID = re.compile(r"\br-[0-9a-f]{10}\b")


class _Norm:
    """Replaces the work path and the random row ids with stable tokens, in order of appearance."""

    def __init__(self, work: Path):
        self._paths = sorted({str(work), str(work.resolve())}, key=len, reverse=True)
        self._ids: dict[str, str] = {}

    def __call__(self, text: str) -> str:
        for p in self._paths:
            text = text.replace(p, "<WORK>")

        def tok(m: re.Match) -> str:
            return self._ids.setdefault(m.group(0), f"<row-id-{len(self._ids) + 1:03d}>")

        return _ROW_ID.sub(tok, text)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", newline="") if path.is_file() else "<absent>"


def _call(main, argv: list[str], *, err: list | None = None) -> tuple[int, str]:
    """Run a ``main(argv)`` in-process; return (exit code, stdout). stderr goes to ``err`` if given."""
    buf = io.StringIO()
    ebuf = io.StringIO()
    rc = 0
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(ebuf):
        try:
            ret = main(argv)
            rc = ret if isinstance(ret, int) else 0
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    if err is not None:
        err.append(ebuf.getvalue())
    return rc, buf.getvalue()


def _seed(work: Path) -> tuple[Path, Path]:
    """Copy the committed corpus into ``work``; returns (content_root, profiles_root)."""
    # The repo's .gitignore ignores any directory named content/, so the corpus keeps its state as
    # state/ and it becomes content/ here.
    shutil.copytree(CORPUS / "state", work / "content")
    shutil.copytree(CORPUS / "profiles", work / "profiles")
    return work / "content", work / "profiles"


def _prospects(content: Path) -> Path:
    return content / PROFILE / "prospects"


def _env(work: Path, flags: str) -> dict[str, str]:
    env = {
        "GTM_CONTENT_ROOT": str(work / "content"),
        "GTM_PROFILES_ROOT": str(work / "profiles"),
        "GTM_JUDGE_REPAIR_REQUIRES_CALIBRATION": "0",
        "GTM_SCORECARD_STRICT_PROVENANCE": "false",
    }
    if flags == "open":
        env.update(dict.fromkeys(FLAGS, "1"))
    return env


def _data_side(work: Path, norm: _Norm, out: dict) -> None:
    content = work / "content"
    pros = _prospects(content)
    seq = pros / "sequences"

    from gtm_core.prospects_consolidate import cli as cons_cli

    with patch("uuid.uuid4", side_effect=_uuid_stream()):
        rc, stdout = _call(cons_cli._cli, ["consolidate", "--profile", PROFILE, "--unattended"])
    out["consolidate.rc"] = rc
    for name in ("master-list.csv", "needs-verification.csv"):
        out[f"consolidate.pool/{name}"] = norm(_read(seq / ".pool" / name))
    for name in ("ready-to-load.csv", "hand-send.csv"):
        out[f"consolidate.{name}"] = norm(_read(seq / name))
    out["consolidate.latest.json"] = norm(_read(pros / "latest.json"))

    from gtm_core.lanes import cli as lanes_cli

    for mode, extra in (("attended", []), ("unattended", ["--unattended"])):
        # Route from a pristine copy each time: the command rewrites ready-to-load.csv.
        snap = work / f"_lanes_{mode}"
        shutil.copytree(content / PROFILE, snap / PROFILE)
        with patch.dict(os.environ, {"GTM_CONTENT_ROOT": str(snap)}):
            rc, stdout = _call(
                lanes_cli.main,
                ["route", "--profile", PROFILE, "--csv", str(snap / PROFILE / "prospects/sequences/ready-to-load.csv"),
                 "--as-of", STAMP, "--stamp", STAMP, *extra],
            )  # fmt: skip
        norm_snap = _Norm(snap)
        out[f"lanes.{mode}.rc"] = rc
        out[f"lanes.{mode}.stdout"] = norm_snap(norm(stdout))
        base = snap / PROFILE / "prospects" / "sequences"
        files = sorted(
            p
            for p in (snap / PROFILE).rglob("*")
            if p.is_file() and p.suffix != ".lock" and (STAMP in p.name or "lanes-state" in p.name)
        )
        for p in files:
            rel = p.relative_to(snap / PROFILE)
            out[f"lanes.{mode}.{rel}"] = norm_snap(norm(_read(p)))
        out[f"lanes.{mode}.ready-to-load.csv"] = norm_snap(norm(_read(base / "ready-to-load.csv")))

    from gtm_core import account_integrity

    lists = work / "lists"
    shutil.copytree(CORPUS / "lists", lists)
    for gen in ("gen1", "gen2", "gen3", "gen3-generic", "gen3-personalised", "gen3-mixed"):
        for lane in ("generic", "personalised", "repair", "signal"):
            err: list = []
            argv = ["--csv", str(lists / f"{gen}.csv"), "--profile", PROFILE]
            rc, stdout = _call(
                account_integrity.main, [*argv, "--lane", lane, "--as-of", STAMP], err=err
            )
            out[f"integrity.{gen}.{lane}"] = {
                "rc": rc,
                "stdout": norm(stdout),
                "stderr": norm(err[0]),
            }


def _uuid_stream():
    import uuid

    def gen():
        i = 0
        while True:
            i += 1
            yield uuid.UUID(int=i)

    g = gen()
    return lambda: next(g)


# ---------------------------------------------------------------------------------------------
# Clocks. No surface has a freeze switch, so each clock is pinned by patching its own module.
# ---------------------------------------------------------------------------------------------

_NOW = datetime.datetime(2026, 10, 1, 12, 0, 0, tzinfo=datetime.UTC)


class _FrozenDateTime(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return _NOW if tz is None else _NOW.astimezone(tz)


class _FrozenDate(datetime.date):
    @classmethod
    def today(cls):
        return TODAY


class _TimeProxy:
    """``time`` with a pinned ``time()``; everything else is the real module."""

    @staticmethod
    def time():
        return _NOW.timestamp()

    def __getattr__(self, name):
        return getattr(time, name)


@contextlib.contextmanager
def _frozen_clocks():
    targets = [
        ("gtm_core.email_campaign_dashboard.model.datetime", _FrozenDateTime),
        ("gtm_core.email_campaign_dashboard.frontier.datetime", _FrozenDateTime),
        ("gtm_core.email_campaign_dashboard.views_ops.datetime", _FrozenDateTime),
        ("gtm_core.prospects_dashboard.datetime", _FrozenDateTime),
        ("gtm_core.campaigns_dashboard.datetime", _FrozenDateTime),
        ("gtm_core.email_campaign_dashboard.forecast.date", _FrozenDate),
        ("gtm_core.email_campaign_dashboard.loadfiles.time", _TimeProxy()),
    ]
    with contextlib.ExitStack() as stack:
        for target, repl in targets:
            try:
                stack.enter_context(patch(target, repl))
            except (AttributeError, ModuleNotFoundError):
                pass  # the module does not use that clock at this commit
        stack.enter_context(patch("gtm_core.prospect_readiness._today", lambda: TODAY))
        yield


def _pin_mtimes(root: Path) -> None:
    epoch = _NOW.timestamp()
    for p in root.rglob("*"):
        os.utime(p, (epoch, epoch))


def _json(obj) -> str:
    import dataclasses

    def default(o):
        if dataclasses.is_dataclass(o) and not isinstance(o, type):
            return dataclasses.asdict(o)
        return str(o)

    return json.dumps(obj, indent=1, sort_keys=True, default=default, ensure_ascii=False)


def _lint_rows(work: Path) -> list[dict]:
    import csv

    with (work / "lists" / "lint.csv").open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _tree_root() -> Path:
    import gtm_core

    return Path(gtm_core.__file__).resolve().parents[1]


def _other_data(work: Path, norm: _Norm, out: dict) -> None:
    content = work / "content"

    from gtm_core import cells

    out["supply_profile"] = json.loads(_json(cells.supply_profile(PROFILE, content)))

    sys.path.insert(0, str(_tree_root() / "scripts"))
    try:
        import admin_sync
    finally:
        sys.path.pop(0)
    for name in ("accounts", "contacts", "suppressions", "people", "outcomes"):
        rows = getattr(admin_sync, f"collect_{name}")(content, PROFILE)
        out[f"pg.{name}"] = json.loads(norm(_json(rows)))
    items = sorted(
        getattr(admin_sync, "collect_content_items")(content, PROFILE),
        key=lambda r: json.dumps(r, sort_keys=True, default=str),
    )
    out["pg.content_items"] = json.loads(norm(_json(items)))

    from gtm_core import score_prospects

    scored = work / "scored.json"
    err: list = []
    rc, _ = _call(
        score_prospects.main,
        ["--items", str(work / "lists" / "score-items.json"), "--out", str(scored)],
        err=err,
    )
    out["score_prospects"] = {"rc": rc, "out": _read(scored), "stderr": err[0]}

    from gtm_core import prospects_import
    from gtm_core.prospects_export import RunExport
    from gtm_core.prospects_item import normalise_items

    raw = []
    for r in _lint_rows(work):
        raw.append(
            {
                "company": r["company"], "domain": r["company_domain"],
                "contact_name": f"{r['first']} {r['last']}", "contact_email": r["email"],
                "contact_title": r["title"], "market": r["country"], "segment": r["segment"],
                "score": 7, "tier": "B", "why_now": r["why_now"], "heat": 1,
                "rubric_source": "seed", "rubric_version": "seed-v1",
            }
    )  # fmt: skip
    export = RunExport(
        normalise_items(raw),
        prospects_import._HUBSPOT_COLUMNS,
        lambda a: prospects_import._hubspot_row(a, run_date=STAMP, rubric_version="seed-v1"),
    )
    export.plan([{"company": r["company"], "domain": r["domain"], "status": "active"} for r in raw])
    path = work / "hubspot.csv"
    export.write(path)
    out["hubspot_export"] = _read(path)

    from gtm_core.email_campaign_dashboard import model

    _pin_mtimes(content)
    with _frozen_clocks():
        m = model.build_model(PROFILE, content)
    m.pop("_content_root", None)
    out["dashboard_model"] = json.loads(norm(_json(m)))


def _message_side(work: Path, norm: _Norm, out: dict) -> None:
    lists = work / "lists"
    tree = _tree_root()
    env = {
        **os.environ,
        "PYTHONHASHSEED": "0",
        "PYTHONPATH": str(tree),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for name in ("relay", "seat", "unknown"):
        proc = subprocess.run(
            [sys.executable, str(tree / "tests/linter/outreach_linter.py"), "render",
             str(lists / f"spec-{name}.md"), "--csv", str(lists / "lint.csv"), "--profile", PROFILE],
            capture_output=True, text=True, env=env, cwd=tree, timeout=300,
        )  # fmt: skip
        out[f"render_lint.{name}"] = {
            "rc": proc.returncode, "stdout": norm(proc.stdout), "stderr": norm(proc.stderr),
        }  # fmt: skip

    from gtm_core.messaging import cli as msg_cli

    csv_path = str(lists / "lint.csv")
    err: list = []
    rc, stdout = _call(msg_cli.main, ["resolve", "--profile", PROFILE, "--csv", csv_path], err=err)
    out["resolve.text"] = {"rc": rc, "stdout": norm(stdout), "stderr": norm(err[0])}
    rc, stdout = _call(msg_cli.main, ["resolve", "--profile", PROFILE, "--csv", csv_path, "--json"])
    out["resolve.json"] = {"rc": rc, "stdout": norm(stdout)}

    from gtm_core import groundedness, signal_record

    real = signal_record.check_record

    def pinned(row, **kw):
        return real(row, as_of=TODAY, **kw)

    reports = []
    with patch.object(groundedness, "check_record", pinned):
        for i, r in enumerate(_lint_rows(work)):
            row = {
                **r,
                "profile": PROFILE,
                "lane": "personalised",
                "verdict": r.get("verdict", "send") or "send",
            }
            body = (
                f"Hi {r['first']}, {r['why_now']}. We cut triage time by 40% for teams like yours."
            )
            rep = groundedness.groundedness_report(f"row-{i:03d}", row, body, "")
            reports.append(rep)
    out["groundedness"] = json.loads(norm(_json(reports)))

    _send_cards(work, norm, out)


def _send_cards(work: Path, norm: _Norm, out: dict) -> None:
    from gtm_core import send_cards

    rows = _lint_rows(work)

    def members(chunk):
        return [
            {
                "name": f"{r['first']} {r['last']}", "email": r["email"], "company": r["company"],
                "industry": r["industry"], "country": r["country"], "level": "Head",
                "seat": "platform", "opener": r["signal_clause"] or "", "source_url": r["signal_source_url"],
                "capture_date": "2026-09-20", "signal_kind": "event", "ticked": True,
            }
            for r in chunk
        ]  # fmt: skip

    def cell(cell_id, personalised, seq, chunk):
        return {
            "cell_id": cell_id, "cohort": "enterprise", "seat": "platform",
            "message_variant": "v1", "angle": "Relay angle", "segment": "enterprise",
            "premise_ids": ["relay-premise"], "proof_ids": ["relay-proof"],
            "gate_receipt": {"integrity": "pass", "suppression": "pass", "compliance": "pass", "freshness": "pass"},
            "is_personalised": personalised,
            "example_member": {"name": "Ex Ample", "email": "ex@ample.example", "company": "Example Co", "subject": "s", "body": "<p>b</p>"},
            "members": members(chunk), "panel_verdicts": [],
            "sequence_id": seq, "step_id": f"step-{seq}", "title": "Head of Platform",
            "spec": f"{seq}.md",
        }  # fmt: skip

    cells = [
        cell("c:pers", True, "SEQ-card-pers", rows[:4]),
        cell("c:gen", False, "SEQ-card-gen", rows[4:8]),
    ]
    export = send_cards.create_card_export(
        cells,
        decisions={"c:pers": "send this cell", "c:gen": "send this cell"},
        revealed_before={"c:pers": True, "c:gen": True},
        run_id="run-fixed",
        wave="wave-fixed",
    )
    path = work / "card-export.json"
    path.write_text(json.dumps(export, sort_keys=True), encoding="utf-8")
    send_cards.send_cards_apply(
        path, profile=PROFILE, content_root=work / "content", run_id="run-fixed"
    )
    seq = work / "content" / PROFILE / "prospects" / "sequences"
    labels: set[str] = set()
    for f in sorted((seq / ".pending").glob("*.json")):
        out[f"send_cards.draft.{f.name}"] = norm(_read(f))
        for row in json.loads(f.read_text(encoding="utf-8")).get("prospect_list", []):
            labels.update(row)
    out["send_cards.merge_labels"] = sorted(labels)
    for name in ("SEQ-card-pers-enrolled.csv", "SEQ-card-gen-enrolled.csv", "cells.toml"):
        out[f"send_cards.{name}"] = norm(_read(seq / name))
    out["send_cards.page"] = norm(send_cards.generate_cards_page(cells, stamp=STAMP, profile=""))


def apply_extra_tree(work: Path, extra: Path) -> list[str]:
    """Lay ``extra`` (``content/…`` and ``profiles/…``) over the seeded corpus; returns what it added.

    A file that is new is copied. A ``.jsonl`` the corpus already has gets the extra's lines
    appended, which is what a real capture does to the index. Any other clash is an error: the
    new-data tree may add to the corpus, never replace a byte of it.
    """
    added: list[str] = []
    for src in sorted(p for p in Path(extra).rglob("*") if p.is_file()):
        rel = src.relative_to(extra)
        dst = work / rel
        if dst.exists():
            if dst.suffix != ".jsonl":
                raise FileExistsError(f"the new-data tree would overwrite {rel}")
            with dst.open("ab") as fh:
                fh.write(src.read_bytes())
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        added.append(str(rel))
    return added


def capture(
    work: Path, flags: str = "closed", extra_tree: Path | None = None, seeded: bool = False
) -> dict:
    """Run every surface over the corpus in ``work``. ``seeded`` means ``work`` already holds it."""
    work = Path(work)
    if not seeded:
        _seed(work)
    if extra_tree is not None:
        apply_extra_tree(work, extra_tree)
    norm = _Norm(work)
    out: dict = {}
    with patch.dict(os.environ, _env(work, flags)):
        for f in FLAGS:
            if flags == "closed":
                os.environ.pop(f, None)
        _data_side(work, norm, out)
        _other_data(work, norm, out)
        _message_side(work, norm, out)
    return out


if __name__ == "__main__":
    import argparse
    import tempfile

    ap = argparse.ArgumentParser()
    ap.add_argument("--flags", default="closed", choices=["closed", "open"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--extra-tree", default=None, help="files to lay over the corpus (compat CM1)")
    a = ap.parse_args()
    import gtm_core

    with tempfile.TemporaryDirectory() as tmp:
        result = capture(Path(tmp), a.flags, Path(a.extra_tree) if a.extra_tree else None)
    Path(a.out).write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"captured {len(result)} surfaces using {Path(gtm_core.__file__).resolve().parents[1]}",
        file=sys.stderr,
    )
    sys.stdout.flush()


#: Two keys the status-page work (commits 273b36f6, af06b60c) added to the dashboard model on
#: 2026-09-30, for its own reasons and not signal-first's. They are additive and read nothing
#: signal-first writes, so BASE's golden cannot have them. This is the one named exclusion; any
#: other key that moves still fails.
STATUS_PAGE_KEYS = ("figures", "sources", "_now")

#: Added 2026-10-01 by the same status-page freshness work: `status.snapshot.file_meta` records the
#: sequencer snapshot file's own metadata, and `_now` is the model's pinned clock. Neither reads
#: anything signal-first writes, so BASE's golden cannot carry them. Named, never a wildcard.
STATUS_PAGE_NESTED = (("status", "snapshot", "file_meta"),)


#: Differences from BASE that other PRDs introduced on purpose (2026-10-02). Each is named here,
#: removed from HEAD's output before the BASE comparison, and a no-op on BASE's own output, so the
#: test that runs BASE's code (cm1) still compares BASE with its own golden. Anything else that
#: moves still fails.
#:
#: * two pool columns, appended after every existing one by the signal-quality scoring PRD. They
#:   are dropped only while EVERY value in them is blank: a populated one is a real difference.
INTENDED_NEW_COLUMNS = ("signal_fit", "signal_virality")

#: * one ``note:`` line from the regulator/competitor classifier PRD, printed when a lane-policy
#:   still carries the deprecated ``[hold] regulated_domains`` list.
_DEPRECATION_NOTE = re.compile(
    r"^note: lane-policy\.toml: \[hold\] regulated_domains is deprecated.*\n", re.M
)

#: * the review page's "Export decisions" button and script (send-safety hardening PRD). The
#:   header block is put back to BASE's three lines and the script function is cut out.
_NEW_PAGE_HEADER = re.compile(
    r'  <div style="display:flex; justify-content:space-between; align-items:center; width:100%;">\n'
    r"    <div>\n"
    r'      <h1 style="display:inline-block; margin-right:12px;">Outreach Campaign Review</h1>\n'
    r"      (<span style=\"color:var\(--muted\)\">Profile: [^\n]*</span>)\n"
    r"    </div>\n"
    r"    <button onclick=\"downloadDecisions\(\)\"[^\n]*</button>\n"
    r"  </div>\n"
    r"  <p style=\"width: 100%; margin: 8px 0 0 0; [^\n]*</p>\n"
)
_OLD_PAGE_HEADER = (
    "  <h1>Outreach Campaign Review</h1>\n  {span}\n"
    '  <p style="width: 100%; margin: 0; font-size: 13px; color: var(--muted);">Review the email '
    "template and recipient list below, then select your decision at the bottom to approve or "
    "skip.</p>\n"
)
_EXPORT_SCRIPT = re.compile(r"^function downloadDecisions\(\) \{\n.*?^\}\n", re.M | re.S)


def _without_new_columns(text: str) -> str:
    import csv

    rows = list(csv.reader(io.StringIO(text, newline="")))
    if not rows or not any(c in rows[0] for c in INTENDED_NEW_COLUMNS):
        return text
    drop = {i for i, c in enumerate(rows[0]) if c in INTENDED_NEW_COLUMNS}
    if any(row[i] for row in rows[1:] for i in drop if i < len(row)):
        return text
    out = io.StringIO(newline="")
    csv.writer(out).writerows([[v for i, v in enumerate(row) if i not in drop] for row in rows])
    return out.getvalue()


def _base_equivalent(key: str, value):
    """HEAD's ``value`` with the intended differences above taken out. BASE's own output is unchanged."""
    if not isinstance(value, str):
        return value
    if key.endswith(".csv"):
        return _without_new_columns(value)
    if key.endswith(".stdout"):
        return _DEPRECATION_NOTE.sub("", value)
    if key == "send_cards.page":
        value = _NEW_PAGE_HEADER.sub(lambda m: _OLD_PAGE_HEADER.format(span=m.group(1)), value)
        return _EXPORT_SCRIPT.sub("", value)
    return value


def comparable(got: dict) -> dict:
    """``got`` without the status-page keys and the named intended differences, for comparison
    against BASE's golden."""
    got = {k: _base_equivalent(k, v) for k, v in got.items()}
    model = got.get("dashboard_model")
    if isinstance(model, dict):
        kept = {k: v for k, v in model.items() if k not in STATUS_PAGE_KEYS}
        for path in STATUS_PAGE_NESTED:
            kept = _without(kept, path)
        got = {**got, "dashboard_model": kept}
    return got


def _without(tree: dict, path: tuple) -> dict:
    """``tree`` with the one nested key at ``path`` removed; every other key untouched."""
    head, rest = path[0], path[1:]
    if head not in tree:
        return tree
    if not rest:
        return {k: v for k, v in tree.items() if k != head}
    child = tree[head]
    return {**tree, head: _without(child, rest) if isinstance(child, dict) else child}
