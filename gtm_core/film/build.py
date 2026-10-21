"""``build`` — the whole chain from film.json to a checked final cut, redoing only what changed.

Stages, in order (``--from`` starts part-way, ``--to`` stops early):

    voice     prep every new take (engine preset), refuse a take older than its words
    plan      solve the voice-led timeline
    graphics  render each Mission Control scene at its planned length (cached by scene + length)
    clips     cut footage / cutaways / split screens / conformed shots (cached by source + cut)
    picture   shot list -> overlays -> caption burn -> stitch
    audio     voice track -> music bed (+ fade-out found automatically) -> duck -> SFX -> mux
    finish    finish spec -> grade/loudness/disclosure (`video_finish run`) -> `video_lint`
    review    per-line review sheet, push-in softness, lexicon check

Every media step goes through ``gtm_core.video_finish`` / ``gtm_core.video_lint``; this module
orchestrates and owns no pixels. Known traps are handled here once instead of per session: the
previous finish record is set aside before ``run`` (it skips when its plan id is unchanged), the
caption sidecar is carried from the stitch onto the master, and the disclosure is burned per shot
because a pre-burned master skips ``run``'s caption pass.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess  # nosec B404 — python -m gtm_core.* arg lists only, never shell=True
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from . import captions_plan, doc, timeline, voice
from .spec import Film, FilmError

STAGES = ("voice", "plan", "graphics", "clips", "picture", "audio", "finish", "review")


def _py(*args: str) -> str:
    r = subprocess.run(  # nosec B603 — sys.executable, fixed module, arg list
        [sys.executable, "-m", *args], capture_output=True, text=True
    )
    if r.returncode not in (0, 1) or (r.returncode == 1 and args[0] != "gtm_core.video_lint"):
        raise FilmError(f"{' '.join(args[:2])} failed:\n{(r.stderr or r.stdout)[-1500:]}")
    return r.stdout


def _vf(*args: str) -> str:
    return _py("gtm_core.video_finish", *args, "--json")


def _key(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


class Build:
    def __init__(self, film: Film, kit: dict[str, Any], repo_root: Path) -> None:
        self.film, self.kit, self.repo = film, kit, repo_root
        self.out = film.out
        self.lex = voice.lexicon(kit)
        self.disclosure = (kit.get("disclosure") or {}).get("line")
        self.cache_p = self.out / "_cache.json"
        self.cache = json.loads(self.cache_p.read_text()) if self.cache_p.exists() else {}
        self.r = film.ratio_tag
        self.report: dict[str, Any] = {}

    # -- helpers -----------------------------------------------------------------------------
    def _fresh(self, name: str, key: str, target: Path) -> bool:
        return self.cache.get(name) == key and target.exists()

    def _mark(self, name: str, key: str) -> None:
        self.cache[name] = key
        self.cache_p.write_text(json.dumps(self.cache, indent=1))

    def _plan(self) -> dict[str, Any]:
        return json.loads((self.out / "plan.json").read_text())

    def _profile(self) -> list[str]:
        prod = self.film.data.get("product")
        return ["--profile", self.film.data["profile"], *(["--product", prod] if prod else [])]

    # -- stages ------------------------------------------------------------------------------
    def voice(self) -> None:
        problems = voice.check_film(self.film, self.lex)
        if problems:
            raise FilmError("the script carries respellings:\n  - " + "\n  - ".join(problems))
        self.report["voice"] = voice.prep(self.film, self.lex)["prepped"]

    def plan(self) -> None:
        p = timeline.plan(self.film, voice.line_lengths(self.film))
        if p["total_s"] < p["voice_total_s"] - 0.05:
            raise FilmError(
                f"the picture ({p['total_s']} s) ends before the voice ({p['voice_total_s']} s); "
                f"footage too short for its lines (s over): {p['unresolved_s']}. Give a "
                "neighbouring graphic more room, add a cutaway, or pin a length with dur_s"
            )
        (self.out / "plan.json").write_text(json.dumps(p, indent=1))
        self.report["plan"] = {
            "total_s": p["total_s"],
            "unused": p["unused"],
            "off_shot_lines": p["off_shot_lines"],
        }

    def graphics(self) -> None:
        g = self.film.data.get("graphics", {})
        dur = {p["id"]: p["dur"] for p in self._plan()["shots"]}
        jobs = []
        for shot in self.film.shots:
            if shot["kind"] != "graphic" or shot.get("file"):
                continue
            sid = shot["id"]
            if shot.get("graphic_doc"):
                script = json.loads(self.film.resolve(shot["graphic_doc"]).read_text())
                for sc in script["scenes"]:
                    sc["dur"] = round(dur[sid], 2)
                script["name"] = sid
            else:
                script = {
                    "name": sid,
                    "title": f"{g.get('title_prefix', self.film.data['slug'])} {sid}",
                    "product": g.get("product", ""),
                    "fps": g.get("fps", 25),
                    "width": 1920,
                    "height": 1080,
                    "brief": g.get("brief", {}),
                    "scenes": [{**shot["graphic"], "dur": round(dur[sid], 2)}],
                }
            target = self.out / "mc" / sid / f"{sid}.mp4"
            key = _key(script)
            if not self._fresh(f"mc:{sid}", key, target):
                jobs.append((sid, script, key, shot.get("poster_s", g.get("poster_s", 1.5))))

        def render(job: tuple[str, dict, str, float]) -> str:
            sid, script, key, poster = job
            src = self.out / "mc" / f"{sid}.mc.json"
            src.parent.mkdir(parents=True, exist_ok=True)
            src.write_text(json.dumps(script, indent=2) + "\n")
            _py(
                "gtm_core.screen_ui.mc_video.render",
                str(src),
                "--out",
                str(self.out / "mc" / sid),
                *self._profile(),
                "--poster",
                str(poster),
            )
            return sid

        with ThreadPoolExecutor(max_workers=3) as ex:
            done = list(ex.map(render, jobs))
        for sid, _script, key, _poster in jobs:
            self._mark(f"mc:{sid}", key)
        self.report["graphics"] = done

    def clips(self) -> None:
        from ..video_finish.compose import split_screen

        work = self.out / "_work" / "clips"
        work.mkdir(parents=True, exist_ok=True)
        done = []
        for p in self._plan()["shots"]:
            shot = self.film.shot(p["id"])
            if p["kind"] == "G":
                continue
            sid, cut = p["id"], self.out / "cut" / f"{p['id']}.mp4"
            src = self.film.resolve(shot["source"]) if shot.get("source") else None
            key = _key(
                [
                    shot.get("source"),
                    shot.get("split"),
                    p["offset"],
                    p["dur"],
                    src.stat().st_mtime if src else None,
                    shot.get("conform"),
                ]
            )
            if self._fresh(f"clip:{sid}", key, cut):
                continue
            if p["kind"] == "S":
                sp = dict(shot["split"])
                left, right = self.film.resolve(sp.pop("left")), self.film.resolve(sp.pop("right"))
                cut.parent.mkdir(parents=True, exist_ok=True)
                split_screen(left, right, out_path=cut, duration_s=p["dur"], **sp)
            else:
                cuts = work / f"{sid}.cuts.json"
                end = round(p["offset"] + p["dur"], 3)
                cuts.write_text(
                    json.dumps({"cuts": [{"shot_id": sid, "start_s": p["offset"], "end_s": end}]})
                )
                _vf(
                    "split",
                    "--in",
                    str(src),
                    "--cuts",
                    str(cuts),
                    "--out-dir",
                    str(cut.parent),
                    "--workdir",
                    str(work),
                )
            if shot.get("conform"):
                seg = work / f"{sid}.seg.json"
                seg.write_text(json.dumps({"segments": [{"path": str(cut)}]}))
                _vf(
                    "stitch",
                    "--segments",
                    str(seg),
                    "--ratio",
                    self.film.ratio,
                    "--out",
                    str(self.out / "conf" / f"{sid}.mp4"),
                    "--workdir",
                    str(work / f"conf-{sid}"),
                    "--normalize",
                    "--video-bitrate",
                    "16M",
                )
            self._mark(f"clip:{sid}", key)
            done.append(sid)
        self.report["clips"] = done

    def picture(self) -> None:
        from ..video_finish.ffmpeg import _probe_fps

        plan = self._plan()
        segs = captions_plan.caption_segments(self.film, plan, self.disclosure)
        shots_p = self.out / "shots.json"
        pre = doc.shot_list(self.film, plan, segs, repo_root=self.repo, overlaid=False)
        shots_p.write_text(json.dumps(pre, indent=2))
        by_fps: dict[int, list[str]] = {}
        for s in pre["shots"]:
            if s.get("production", {}).get("overlay"):
                fps = round(_probe_fps(self.out / s["file"]))
                by_fps.setdefault(fps, []).append(s["id"])
        for fps, ids in by_fps.items():
            _vf(
                "overlays",
                "--shots",
                str(shots_p),
                "--ratio",
                self.film.ratio,
                *self._profile(),
                "--out-dir",
                str(self.out / "overlaid"),
                "--workdir",
                str(self.out / "_work/ov"),
                "--shot-ids",
                ",".join(ids),
                "--fps",
                str(fps),
            )
        final = doc.shot_list(self.film, plan, segs, repo_root=self.repo)
        shots_p.write_text(json.dumps(final, indent=2))
        burned = json.loads(
            _vf(
                "burn-captions",
                "--shots",
                str(shots_p),
                "--ratio",
                self.film.ratio,
                *self._profile(),
                "--out-dir",
                str(self.out / "captioned"),
                "--workdir",
                str(self.out / "_work/burn"),
            )
        )
        ok = {b["shot_id"] for b in burned["burned"]}
        seg_list = [
            {
                "path": str(self.out / "captioned" / f"{s['id']}-captioned.mp4")
                if s["id"] in ok
                else str(self.out / s["file"])
            }
            for s in final["shots"]
        ]
        seg_p = self.out / "_work" / "segments.json"
        seg_p.write_text(json.dumps({"segments": seg_list}, indent=1))
        bitrate = self.film.data.get("stitch_bitrate", "12M")
        _vf(
            "stitch",
            "--segments",
            str(seg_p),
            "--ratio",
            self.film.ratio,
            "--out",
            str(self.out / f"joined-{self.r}.mp4"),
            "--workdir",
            str(self.out / "_work/st"),
            "--normalize",
            "--video-bitrate",
            bitrate,
            "--transitions-from-shots",
            str(shots_p),
        )
        self.report["picture"] = {"burned": len(ok), "shots": len(final["shots"])}

    def audio(self) -> None:
        from ..video_finish.audio import build_music_bed, duck_music_bed
        from ..video_finish.ffmpeg import _probe_duration

        work = self.out / "_work" / "mix"
        work.mkdir(parents=True, exist_ok=True)
        picture = self.out / f"joined-{self.r}.mp4"
        vo = self.out / "voice-track.m4a"
        _vf("narration-track", "--shots", str(self.out / "shots.json"), "--out", str(vo))
        mix = vo
        music = self.film.data.get("music")
        if music:
            pic_s = _probe_duration(picture, stream_selector="v:0")
            bed = build_music_bed(
                self.film.resolve(music["path"]),
                out_path=work / "bed.m4a",
                duration_s=pic_s,
                workdir=work / "bed",
                body_end_s=music.get("body_end_s", "auto"),
                loop_from_s=music.get("loop_from_s"),
                fade_out_s=music.get("fade_out_s", 3.0),
            )
            mix = duck_music_bed(
                vo,
                bed,
                out_path=work / "ducked.m4a",
                workdir=work / "duck",
                duck_ratio=music.get("duck_ratio", 4.0),
                music_gain_db=music.get("gain_db", -18.0),
            )
        cues = doc.cue_sheet(self.film, self._plan())
        final_audio = self.out / f"mix-{self.r}.m4a"
        if cues["cues"]:
            cue_p = self.out / "cues.json"
            cue_p.write_text(json.dumps(cues, indent=1))
            sfx = json.loads(
                _vf(
                    "mix-sfx",
                    "--in",
                    str(mix),
                    "--cues",
                    str(cue_p),
                    "--out",
                    str(final_audio),
                    "--workdir",
                    str(work / "sfx"),
                )
            )
            self.report["sfx"] = len(cues["cues"]), sfx.get("verified", sfx.get("ok"))
        else:
            shutil.copy(mix, final_audio)
        _vf(
            "mux",
            "--video",
            str(picture),
            "--audio",
            str(final_audio),
            "--out",
            str(self.out / f"master-{self.r}.mp4"),
            "--workdir",
            str(work / "mux"),
        )

    def finish(self) -> None:
        plan = self._plan()
        spec_p = self.out / f"finish-spec-{self.r}.json"
        spec_p.write_text(json.dumps(doc.finish_spec(self.film, plan, self.disclosure), indent=2))
        shutil.copy(
            self.out / f"joined-{self.r}.captions.json", self.out / f"master-{self.r}.captions.json"
        )
        record = self.out / f"finish-{self.r}.json"
        if record.exists():  # run skips when its plan id is unchanged; identity is not in it
            record.replace(self.out / "_work" / record.name)
        _vf(
            "run",
            *self._profile(),
            "--slug",
            self.film.data["slug"],
            "--ratio",
            self.film.ratio,
            "--spec",
            str(spec_p),
            "--source",
            str(self.out / f"master-{self.r}.mp4"),
            "--out-dir",
            str(self.out),
        )
        final = self.out / f"{self.film.data['slug']}-{self.r}-final.mp4"
        lint = _py(
            "gtm_core.video_lint",
            str(final),
            "--ratio",
            self.film.ratio,
            "--manifest",
            str(record),
            "--json",
        )
        (self.out / f"lint-{self.r}.json").write_text(lint)
        self.report["finish"] = str(final)

    def review(self) -> None:
        from ..cover_frame import line_sheet

        plan = self._plan()
        final = self.out / f"{self.film.data['slug']}-{self.r}-final.mp4"
        rows = []
        for shot in self.film.shots:
            p = next(x for x in plan["shots"] if x["id"] == shot["id"])
            if p["line_onset"] is None:
                continue
            on, ln = p["line_onset"], p["line_len"]
            speakers = {sp for sp, _ in shot.get("turns") or []}
            times = (
                [on + ln * f for f in (0.15, 0.5, 0.85)]
                if speakers - {"narrator"}
                else [on + 0.2, on + ln * 0.6]
            )
            rows.append(
                {
                    "label": f"{shot['id']} {int(on // 60)}:{on % 60:04.1f}",
                    "text": " / ".join(t for _, t in shot["turns"]),
                    "times": times,
                }
            )
        sheet = self.out / f"review-lines-{self.r}.jpg"
        line_sheet(final, sheet, rows=rows)
        soft = _py(
            "gtm_core.video_lint.sharpness",
            "--shots",
            str(self.out / "shots.json"),
            "--base-dir",
            str(self.out),
            "--json",
        )
        self.report["review"] = {
            "sheet": str(sheet),
            "softness": json.loads(soft) if soft.strip() else None,
            "lexicon": voice.check_film(self.film, self.lex) or "clean",
        }


def run(
    film: Film, kit: dict[str, Any], *, repo_root: Path, start: str = "voice", stop: str = "review"
) -> dict[str, Any]:
    b = Build(film, kit, repo_root)
    b.out.mkdir(parents=True, exist_ok=True)
    (b.out / "_work").mkdir(exist_ok=True)
    for stage in STAGES[STAGES.index(start) : STAGES.index(stop) + 1]:
        getattr(b, stage)()
    return b.report
