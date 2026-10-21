"""``python -m gtm_core.film`` — run from the repo root.

check        <film.json>                 validate; list respellings in the script
voice-status <film.json>                 which shots need (re)voicing
tts-lines    <film.json> [--shots a,b]   the exact TTS jobs to send (lexicon applied), batched
voice-prep   <film.json> [--adopt]       prep new takes with the engine preset
build        <film.json> [--from S] [--to S]
sync-script  <film.json> --script scripts/<slug>.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import build, voice
from .spec import FilmError, load


def _kit(film) -> dict:
    from ..brandkit import load_brand_kit
    from ..paths import PathConfig

    return load_brand_kit(
        PathConfig.from_env().profiles_root, film.data["profile"], film.data.get("product")
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="gtm_core.film",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "voice-status", "tts-lines", "voice-prep", "build", "sync-script"):
        p = sub.add_parser(name)
        p.add_argument("film", type=Path)
        if name == "tts-lines":
            p.add_argument("--shots", default=None, help="comma-separated ids (default: stale)")
        if name == "voice-prep":
            p.add_argument(
                "--adopt",
                action="store_true",
                help="accept takes made before film.json existed (migration only)",
            )
        if name == "build":
            p.add_argument("--from", dest="start", choices=build.STAGES, default="voice")
            p.add_argument("--to", dest="stop", choices=build.STAGES, default="review")
        if name == "sync-script":
            p.add_argument("--script", type=Path, required=True, help="scripts/<slug>.md")
    args = ap.parse_args(argv)
    try:
        film = load(args.film)
        kit = _kit(film)
        lex = voice.lexicon(kit)
        out = _dispatch(args, film, kit, lex)
    except FilmError as exc:
        print(f"film: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def _dispatch(args, film, kit, lex):
    if args.cmd == "check":
        problems = voice.check_film(film, lex)
        return {"ok": not problems, "shots": len(film.shots), "problems": problems}
    if args.cmd == "voice-status":
        return voice.status(film, lex)
    if args.cmd == "tts-lines":
        only = [s.strip() for s in args.shots.split(",")] if args.shots else None
        return voice.request(film, lex, only=only)
    if args.cmd == "voice-prep":
        film.out.mkdir(parents=True, exist_ok=True)
        return voice.prep(film, lex, adopt=args.adopt)
    if args.cmd == "build":
        return build.run(film, kit, repo_root=Path.cwd(), start=args.start, stop=args.stop)
    return _sync(args, film)


def _sync(args, film):
    from .doc import sync_script_md

    plan_p = film.out / "plan.json"
    shots_p = film.out / "shots.json"
    if not (plan_p.exists() and shots_p.exists()):
        raise FilmError("build at least to the picture stage before syncing the script")
    plan = json.loads(plan_p.read_text())
    md = sync_script_md(args.script.read_text(), film, plan)
    args.script.write_text(md)
    shots = json.loads(shots_p.read_text())
    for s in shots["shots"]:
        s["file"] = os.path.relpath(film.out / s["file"], args.script.parent)
    target = args.script.with_suffix(".shots.json")
    target.write_text(json.dumps(shots, indent=2) + "\n")
    return {"script": str(args.script), "shots": str(target), "total_s": plan["total_s"]}
