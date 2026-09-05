"""Exact pre/post-split capture of the Phase-2 CLI matrices (PRD 2026-09-01 §6.2 V2).

Not a test. The committed goldens tolerate what differs between machines (fonts, encoder
builds); this tool tolerates NOTHING, because its two captures are made on the same machine
minutes apart, on either side of one pure-motion PR. Every invocation in ``golden_matrix`` is
run against freshly synthesized fixtures and recorded as: exit code, full normalized stdout and
stderr, and a fingerprint of every produced artifact — SHA-256 for text/JSON/PNG/JPEG, ffprobe
structure for encoded media (never a byte-hash: ffmpeg output is not bit-reproducible).

    uv run python tests/goldens/capture_exact.py capture OUT_DIR
    uv run python tests/goldens/capture_exact.py --compare DIR_A DIR_B

``--compare`` exits 1 on any difference in exit codes, stdout, or artifacts. stderr-only
differences are listed separately and do not fail the compare: a traceback's file paths and
line numbers legitimately move when a module becomes a package, and that is the one stream
where "different bytes" is not "different behaviour".
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import golden_matrix as gm  # noqa: E402

RECORDS = "invocations"


def capture(out_dir: Path) -> int:
    root = out_dir / "fixtures"
    out_dir.mkdir(parents=True, exist_ok=True)
    gm.build_fixtures(root)
    manifest: dict[str, dict] = {}
    written = 0
    for inv in gm.all_invocations():
        outcome = gm.run_invocation(root, inv)
        rec = out_dir / RECORDS / inv.name
        rec.mkdir(parents=True, exist_ok=True)
        (rec / "stdout.txt").write_text(outcome.stdout, encoding="utf-8")
        (rec / "stderr.txt").write_text(outcome.stderr, encoding="utf-8")
        (rec / "exit.txt").write_text(f"{outcome.exit_code}\n", encoding="utf-8")
        (rec / "artifacts.json").write_text(
            json.dumps(outcome.artifacts, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        written += 4
        manifest[inv.name] = {
            "module": inv.module,
            "argv": list(inv.argv),
            "expected_exit": inv.exit_code,
            "exit": outcome.exit_code,
            "stdout_sha256": gm._sha(outcome.stdout.encode("utf-8")),
            "stderr_sha256": gm._sha(outcome.stderr.encode("utf-8")),
            "artifacts": outcome.artifacts,
        }
        flag = "" if outcome.exit_code == inv.exit_code else "  <-- UNEXPECTED EXIT"
        print(f"{inv.name:44} exit={outcome.exit_code} artifacts={len(outcome.artifacts)}{flag}")
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out_dir / "environment.json").write_text(
        json.dumps(gm.environment(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    written += 2
    fixture_files = sum(1 for p in root.rglob("*") if p.is_file())
    print(
        f"wrote {written} record files under {out_dir / RECORDS} (+ manifest.json, "
        f"environment.json); {len(manifest)} invocations; {fixture_files} fixture + artifact "
        f"files under {root}"
    )
    return 0


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _diff(a: str, b: str, label: str) -> str:
    lines = difflib.unified_diff(
        a.splitlines(), b.splitlines(), fromfile=f"A/{label}", tofile=f"B/{label}", lineterm="", n=1
    )
    return "\n".join(f"      {line}" for line in lines)


def compare(dir_a: Path, dir_b: Path) -> int:
    a = json.loads((dir_a / "manifest.json").read_text(encoding="utf-8"))
    b = json.loads((dir_b / "manifest.json").read_text(encoding="utf-8"))
    hard = 0
    soft = 0
    for name in sorted(set(a) | set(b)):
        if name not in a or name not in b:
            hard += 1
            print(f"[missing] {name}: only in {'B' if name not in a else 'A'}")
            continue
        ra, rb = a[name], b[name]
        if ra["exit"] != rb["exit"]:
            hard += 1
            print(f"[exit] {name}: A={ra['exit']} B={rb['exit']}")
        if ra["stdout_sha256"] != rb["stdout_sha256"]:
            hard += 1
            print(f"[stdout] {name}:")
            print(
                _diff(
                    _read(dir_a / RECORDS / name / "stdout.txt"),
                    _read(dir_b / RECORDS / name / "stdout.txt"),
                    "stdout",
                )
            )
        for rel in sorted(set(ra["artifacts"]) | set(rb["artifacts"])):
            fa, fb = ra["artifacts"].get(rel), rb["artifacts"].get(rel)
            if fa != fb:
                hard += 1
                print(f"[artifact] {name}: {rel}")
                print(
                    _diff(
                        json.dumps(fa, indent=1, sort_keys=True),
                        json.dumps(fb, indent=1, sort_keys=True),
                        rel,
                    )
                )
        if ra["stderr_sha256"] != rb["stderr_sha256"]:
            soft += 1
            print(f"[stderr-only] {name}:")
            print(
                _diff(
                    _read(dir_a / RECORDS / name / "stderr.txt"),
                    _read(dir_b / RECORDS / name / "stderr.txt"),
                    "stderr",
                )
            )
    ea, eb = _read(dir_a / "environment.json"), _read(dir_b / "environment.json")
    if ea != eb:
        print("[environment] the two captures were not made with the same toolchain:")
        print(_diff(ea, eb, "environment.json"))
    print(
        f"compared {len(set(a) | set(b))} invocations: {hard} hard difference(s), "
        f"{soft} stderr-only difference(s)"
    )
    return 1 if hard else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="capture_exact.py", description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--compare",
        nargs=2,
        type=Path,
        metavar=("DIR_A", "DIR_B"),
        help="diff two captures instead of making one; exit 1 on any hard difference",
    )
    parser.add_argument(
        "capture", nargs="*", metavar="capture OUT_DIR", help="run both matrices into OUT_DIR"
    )
    args = parser.parse_args(argv)
    if args.compare is not None:
        if args.capture:
            parser.error("--compare takes no capture target")
        return compare(*args.compare)
    if len(args.capture) != 2 or args.capture[0] != "capture":
        parser.error("expected `capture OUT_DIR` or `--compare DIR_A DIR_B`")
    missing = gm.missing_prerequisite()
    if missing:
        parser.error(f"cannot capture: {missing}")
    return capture(Path(args.capture[1]))


if __name__ == "__main__":
    raise SystemExit(main())
