"""Gate a finished video asset on the defects a rubric score cannot see.

The one asset this system has ever shipped scored 14/14 on the retention rubric's Part A while
being 720x1280 at 24fps/1.42 Mbps, having a caption clipped at both frame edges, and running
15.041667s into a predictor that hard-fails past ~15s — 4/4 observed scoring failures. No existing
gate had an opinion about the *artifact*; Part A is an opinion about the treatment.

Modelled on gtm_core/deck_lint.py, with its two known gaps closed for a binary artifact:

  - deck_lint's suppression comment (``<!-- lint-ok D4: reason -->``) lives INSIDE the linted
    text. An .mp4 has no comment syntax, so a suppression here lives in the sidecar JSON the
    producer (gtm_core.video_finish) already writes, as ``lint_suppressions``.
  - deck_lint never enforces a suppression REASON and never counts/reports suppressions. Both are
    fixed here: an empty/placeholder reason is a malformed input (exit 2, not a quiet pass), and
    "suppressed: N" is always printed, even at zero — success criterion §7 #4 ("% clearing V1-V4
    with zero ERRORs AND NO SUPPRESSION") is uncomputable otherwise.

Six tiers ship, each with an OBSERVED defect behind it (not a guess dressed as an ERROR gate —
see the ``evidence`` on each ``Tier`` and the promotion invariant enforced at import, below):

    V1  delivery floor    — resolution/fps/bitrate below the professional floor        ERROR
    V2  aspect exact      — frame dims must match the requested ratio, not just be     ERROR
                             close (catches the soul_2 4:5->3:4 coercion)
    V3  caption geometry  — no caption glyph within the reserved safe-area margin,     ERROR/WARN
                             and none inside the ratio's face band
    V4  predictor input   — duration <= 14.9s, ONLY when purpose="predictor"           ERROR
    V10 dead air          — measured silence, not stream presence                      ERROR
    V11 caption contrast  — burned type clears WCAG AA against its own backdrop        ERROR

V10 and V11 (2026-08-28) close the gap three-questions-p1 walked through: it passed every tier
above while 39% of its runtime was digital silence. ``Probe.has_audio`` had been captured since
V1 shipped and read by no rule — and was itself wrong, because ``probe()`` passed
``-select_streams v:0`` and never saw the audio stream at all. Both are fixed here.

``probe()`` is the only ffprobe shell-out. ``evaluate()`` is pure — it takes a ``Probe`` (real or
fabricated) plus optional context (a caption manifest, edge-luma stats, the intended purpose) and
returns findings with no I/O. That split is what makes tier-B tests honest: boundary cases can be
asserted against a fabricated ``Probe`` with no ffmpeg on the machine at all, and it is what makes
the calibration test possible — a committed, de-identified ``ffprobe`` JSON measurement can stand
in for a real (gitignored, likeness-bearing) asset.

V1 is really two sub-checks under one tier name, on purpose: resolution/fps is fixture-provable at
200ms; bitrate is not (a short CBR clip's measured bitrate is dominated by its I-frame), so the
bitrate sub-check is honestly provable only via the pure layer plus a real-asset calibration.

V3 has two inputs and only one of them is pixels. Primary (ERROR): a caption manifest's per-screen
bounding box vs. the reserved safe-area margin, computed from the PROBED frame dimensions — pure
arithmetic, exact, no pixel-guessing. Fallback (WARN): edge-strip luma stats for any asset with no
manifest (i.e. every asset produced before this program) — a WARN, and it says why it degraded.

V4 is scoped by ``purpose``, not by asset: "the asset is <=14.9s" is the obvious reading of the
PRD and it is wrong — a long-form asset is legitimately 90s. V4 fires only when the caller is
about to hand the file to the predictor (``purpose="predictor"``).

Suppression syntax: ``lint_suppressions: [{"tier": "V3", "asset": "<filename>", "reason": "..."}]``
in the producer's manifest. A tier name is matched by EQUALITY against ``Tier.id`` (never
substring), which by itself makes a "V10 read as V1" collision structurally impossible — the
anchored ``_TIER_RE`` below still exists as a second, independent line of defence on top of that
equality check, for whatever future code reads a suppression's tier field back out of the report.

Promotion is mechanical, not editorial: a Tier cannot be constructed with ``severity=ERROR`` and
fewer than two ``evidence`` entries (checked at import). A WARN candidate tier earns ERROR only
after it has caught a real defect on two separate assets, named here.

CLI:
    uv run python -m gtm_core.video_lint asset.mp4 --ratio 9x16
    uv run python -m gtm_core.video_lint asset.mp4 --ratio 9x16 --purpose predictor --json
    uv run python -m gtm_core.video_lint asset.mp4 --ratio 9x16 --manifest finish-9x16.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess  # nosec B404 — ffprobe orchestration, arg list only, never shell=True
import sys
from dataclasses import dataclass
from pathlib import Path

ERROR = "error"
WARN = "warn"

#: Tier names are matched by equality, never substring — this pattern is a second, independent
#: guard on anything that re-reads a tier id out of a report/suppression rather than comparing it
#: directly (the equality check alone already makes the classic "D\d single-digit capture reads
#: D10 as D1" collision impossible here; deck_lint.py:109-111 documents that trap for the inline-
#: comment form, which had no equality check to fall back on).
_TIER_RE = re.compile(r"\AV[1-9]\d*\Z")


@dataclass(frozen=True)
class Tier:
    id: str
    rule: str
    severity: str
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.severity == ERROR and len(self.evidence) < 2:
            raise ValueError(
                f"{self.id} is ERROR but names {len(self.evidence)} evidence entr"
                f"{'y' if len(self.evidence) == 1 else 'ies'} — a tier may not ship (or be "
                "promoted from WARN) as ERROR until it has caught a real defect on two separate "
                "assets, named here."
            )
        if not _TIER_RE.match(self.id):
            raise ValueError(
                f"{self.id!r} is not a valid tier id (expected V<digits>, no leading 0)"
            )


@dataclass(frozen=True)
class Finding:
    tier: str
    rule: str
    severity: str
    asset: str
    excerpt: str
    fix: str


@dataclass(frozen=True)
class SafeArea:
    ratio: str
    width: int
    height: int
    left: float  # reserved fraction of width, each edge
    right: float
    top: float  # reserved fraction of height
    bottom: float


#: Target frame dims + reserved caption margins per platform ratio. ``left``/``right`` at 0.06
#: matches the observed defect (a caption box touching both edges); ``bottom`` is wider than
#: ``top`` on every ratio — that is where a caption block actually sits.
SAFE_AREAS: dict[str, SafeArea] = {
    "9:16": SafeArea("9:16", 1080, 1920, 0.06, 0.06, 0.10, 0.14),
    "4:5": SafeArea("4:5", 1080, 1350, 0.06, 0.06, 0.08, 0.12),
    "1:1": SafeArea("1:1", 1080, 1080, 0.06, 0.06, 0.08, 0.12),
    "16:9": SafeArea("16:9", 1920, 1080, 0.05, 0.05, 0.08, 0.12),
}

#: Minimum acceptable video bitrate at 1080p — below this the file is visibly compressed on a
#: phone screen. Not fixture-provable at 200ms (a short CBR clip's measured rate is dominated by
#: its I-frame); honoured only via the pure layer's boundary tests plus the real-asset calibration.
MIN_BITRATE_1080P_BPS = 6_000_000
MIN_FPS_ACCEPTABLE = 24.0  # below this: ERROR. 24-29.9: WARN (cinematic-only). >=30: clean.
MIN_FPS_CLEAN = 30.0
#: Aspect-ratio equality tolerance for V2. NOT a pixel tolerance on the frame dims directly —
#: V1 already gates minimum SIZE, so V2 must check the frame's SHAPE independent of its size
#: (the real shipped asset was 720x1280: exactly 9:16, at the wrong resolution — V2 must stay
#: clean on it while V1 correctly errors). ~0.003 approximates a ±1-2px wobble at 1080x1920;
#: the soul_2 4:5->3:4 coercion this tier exists to catch differs by ~0.05, an order of
#: magnitude over this floor.
ASPECT_TOLERANCE = 0.003
PREDICTOR_MAX_DURATION_S = 14.9

#: Reading load. A caption screen the viewer cannot finish before it cuts is worse than no
#: caption: it converts the asset into a race. The per-screen ceiling is generous (a 4-word
#: screen may flash for 1s); the whole-asset ceiling is stricter because burned text competes
#: with a voice-over for the same attention. Both are words/second.
MAX_WORDS_PER_SEC_SCREEN = 4.0
MAX_WORDS_PER_SEC_ASSET = 3.0

#: Vertical band of the frame, as fractions of height, where a presenter's face sits. A caption
#: block overlapping this band lands ON the speaker. Per-ratio, for the same reason SAFE_AREAS is:
#: where a head sits in frame is a function of the frame's shape and the framing that shape forces.
#:
#: 4:5 / 1:1 calibrated against the original defect (2026-08-18): captions.py centred the block
#: inside the safe box, which for 4:5 put it at y=595 with h=106 in a 1350px frame — 0.44-0.52 of
#: frame height, dead centre over the face. The operator's second note was "text covering face".
#: The safe area alone cannot catch this: it only reserves EDGES, so a centred block is maximally
#: far from every margin and maximally wrong.
#:
#: 9:16 / 16:9 recalibrated 2026-08-28 against three-questions-p1. A 9:16 full-bleed presenter is
#: framed head-and-shoulders, not medium-close-up, so the head sits MUCH higher: crown ≈0.09,
#: eyes ≈0.22. Captions rendered at 0.100-0.147 (upper placement, inside the safe area) landed
#: squarely on the forehead and this tier passed clean, because the 4:5-derived 0.22 floor began
#: exactly where the eyes do. The band must start above the crown, not above the brow.
FACE_BANDS: dict[str, tuple[float, float]] = {
    "9:16": (0.06, 0.62),
    "16:9": (0.06, 0.72),
    "4:5": (0.22, 0.62),
    "1:1": (0.22, 0.62),
}

#: Fallback for a ratio not in the table. The 4:5 numbers, i.e. the historical global constants.
_DEFAULT_FACE_BAND = (0.22, 0.62)


def face_band(ratio: str) -> tuple[float, float]:
    """The (top, bottom) face band for a ratio, as fractions of frame height.

    An accessor rather than a bare dict lookup because ``gtm_core.captions`` shares these numbers
    — the renderer must place a caption using the same arithmetic the linter will judge it by,
    and a KeyError on an unknown ratio there would be a worse failure than falling back."""
    return FACE_BANDS.get(ratio, _DEFAULT_FACE_BAND)


#: Dead air. Not "is there an audio stream" — the defect asset HAD an AAC stream carrying nothing,
#: so ``Probe.has_audio`` was True and told nobody anything. These are measured against the signal.
#:
#: Two independent triggers, because one number cannot cover both shapes of the defect: a long
#: film with one dead stretch, and a short film that is mostly dead. A deliberate 3s beat before a
#: payoff clears both; five seconds of nothing does not, at any runtime.
SILENCE_FLOOR_DBFS = -50.0
SILENCE_MIN_RUN_S = 2.0  # detection floor: shorter gaps are inter-sentence pauses, not dead air
MAX_DEAD_AIR_FRACTION = 0.15
MAX_DEAD_AIR_RUN_S = 5.0

#: Burned type must clear WCAG AA (4.5:1) against whatever is actually behind it. Not a style
#: preference: a caption exists to be read on a phone, in daylight, with the sound off.
MIN_CAPTION_CONTRAST_RATIO = 4.5

#: Mean absolute inter-frame difference, normalised 0-1, below which a shot reads as motionless.
#: A slow push-in only registers as motion above roughly this floor — a 1.08x zoom spread across
#: 3s sits an order of magnitude under it and is imperceptible on a phone.
MIN_SHOT_MOTION = 0.006

_MIN_SUPPRESSION_REASON_CHARS = 12
_PLACEHOLDER_REASONS = frozenset({"n/a", "na", "ok", "tbd", "wontfix", "-", ""})

SHIPPED: tuple[Tier, ...] = (
    Tier(
        "V1",
        "resolution >= target ratio's frame size; fps >= 30 (warn 24-29); "
        f"bitrate >= {MIN_BITRATE_1080P_BPS / 1_000_000:g} Mbps @1080p",
        ERROR,
        evidence=(
            "2026-08-15 shipped asset measured 720x1280 (below the 1080x1920 target for 9:16)",
            "2026-08-15 shipped asset measured 24fps/1.42 Mbps (below the 30fps/6 Mbps floor)",
        ),
    ),
    Tier(
        "V2",
        "frame dims match the requested ratio's target exactly (±1px), not merely close",
        ERROR,
        evidence=(
            "identity/henry/IDENTITY.toml [render_behaviour]: soul_2 coerces a requested 4:5 "
            "render to an actual 3:4 frame",
            "2026-08-15 verification log: the same coercion is documented as needing a reframe "
            "pass in render-9x16.json's downstream consumers",
        ),
    ),
    Tier(
        "V3",
        "no caption glyph within the reserved safe-area margin on any edge",
        ERROR,
        evidence=(
            "2026-08-15 shipped asset: caption clipped at both frame edges at t≈10s "
            "(extracted frame, inspected)",
            "_make_captions_and_reframe.py:29-45 (superseded): make_caption_png applies no "
            "max-width, wrap, or shrink before drawing",
            "2026-08-28 three-questions-p1 (both masters): all 64 caption screens at a single "
            "fixed y=192-283 of 1920 (0.100-0.147) sat on the presenter's forehead and this tier "
            "passed clean — the 4:5-derived 0.22 band floor began exactly where the eyes do. "
            "Fixed by FACE_BANDS, not by a new tier.",
        ),
    ),
    Tier(
        "V4",
        "predictor input duration <= 14.9s (scoped by purpose='predictor', not by asset)",
        ERROR,
        evidence=(
            "render-9x16.json variant 1: virality_predictor returned analysis=null at "
            "duration=15.041667s",
            "render-9x16.json variant 2: identical failure at the identical duration — 4/4 "
            "observed, both variants",
        ),
    ),
    Tier(
        "V10",
        f"no dead air: silence <= {MAX_DEAD_AIR_FRACTION:.0%} of runtime and no single run "
        f">= {MAX_DEAD_AIR_RUN_S:g}s (measured signal, not stream presence)",
        ERROR,
        evidence=(
            "2026-08-28 three-questions-p1-9x16-final.mp4: 39.6s of 100.7s (39%) below -50 dBFS, "
            "in runs of 10.4s / 25.1s / 4.1s. Ten of eighteen shots were concatenated with no "
            "audio stream at all, so the entire dramatised call and the whole social cut played "
            "silent. Probe.has_audio was True throughout — the container had an AAC track "
            "carrying nothing, which is exactly why this tier measures the signal.",
            "2026-08-28 three-questions-p1-16x9-final.mp4: the same three runs on the landscape "
            "master, independently probed and independently linted — 39% of 100.8s.",
        ),
    ),
    Tier(
        "V11",
        f"burned caption type clears {MIN_CAPTION_CONTRAST_RATIO:g}:1 against the luminance "
        "actually behind it (WCAG AA)",
        ERROR,
        evidence=(
            "2026-08-28 three-questions-p1-9x16-final.mp4: unplated white glyphs over the bright "
            "wooden-desk opening plate measured 3.67:1 at t=1.0s — the film's first frame, below "
            "AA. The same caption style cleared on every darker shot (6.3-8.1:1 over the "
            "presenter, 12-20:1 over the navy graphics), which is exactly why an unplated style "
            "is a latent defect rather than an obvious one: it passes until the backdrop is "
            "bright, and nobody re-checks per shot.",
            "2026-08-28 three-questions-p1-16x9-final.mp4: the same opening plate measured "
            "independently at 3.5:1 on the landscape master.",
        ),
    ),
)

#: WARN-only candidates. Promoted to SHIPPED/ERROR only after a Tier() with >=2 evidence entries
#: can be constructed for it — see the module docstring.
CANDIDATES: tuple[Tier, ...] = (
    Tier(
        "V5",
        "audio ducking applied when a music bed is present alongside dialogue",
        WARN,
        evidence=(),
    ),
    Tier(
        "V6",
        "scene-change cadence is neither too sparse nor too frantic for the asset duration",
        WARN,
        evidence=(),
    ),
    Tier(
        "V7",
        f"caption reading load <= {MAX_WORDS_PER_SEC_SCREEN:g} words/s on any screen and "
        f"<= {MAX_WORDS_PER_SEC_ASSET:g} words/s across the asset",
        WARN,
        evidence=(
            "2026-08-18 shipped asset (agent-gateway-ciso-15s): 150 on-screen words across "
            "15.55s = 9.6 w/s, with individual screens at 14.5 and 17.3 w/s. Reading it at a "
            "comfortable rate needs ~60s; the asset runs 15.55s. Operator reported being unable "
            "to read it and feeling overloaded — the first defect they named.",
        ),
    ),
    Tier(
        "V8",
        "burned caption text is drawn from the spoken line, not a second independent text stream",
        WARN,
        evidence=(
            "2026-08-18 shipped asset (agent-gateway-ciso-15s): captions carried filing citations "
            "disjoint from the voice-over, so the viewer parsed two unrelated text streams at "
            "once. This was introduced deliberately, to 'save VO words', and reported as a virtue.",
        ),
    ),
    Tier(
        "V9",
        f"every shot carries visible motion (mean inter-frame delta >= {MIN_SHOT_MOTION:g})",
        WARN,
        evidence=(
            "2026-08-18 shipped asset (agent-gateway-ciso-15s): five card shots animated with a "
            "1.08x zoom spread over ~3s (~0.03%/frame) read as fully static; 7.95s of 15.55s "
            "(51%) was motionless type on a flat ground. V6 passed it because V6 counts CUTS, "
            "of which there were five — within-shot stillness is a different defect.",
        ),
    ),
)

_TIERS_BY_ID: dict[str, Tier] = {t.id: t for t in SHIPPED + CANDIDATES}


class ProbeUnavailable(RuntimeError):
    """ffprobe is not on PATH."""


class ProbeFailed(RuntimeError):
    """ffprobe ran but the input could not be probed (corrupt, zero-byte, not media)."""


class BadSuppression(ValueError):
    """A suppression entry is missing a real reason, or names an unknown tier."""


@dataclass(frozen=True)
class Probe:
    width: int
    height: int
    fps: float
    duration_s: float
    bit_rate: int | None  # bits/sec; None when neither stream nor format reports one
    has_audio: bool = True
    pix_fmt: str = ""

    @classmethod
    def from_ffprobe_json(cls, payload: dict) -> Probe:
        streams = payload.get("streams", [])
        # An attached cover image reports codec_type=video; it is not the picture track. Prefer a
        # real video stream and only fall back to the naive pick if that is all there is.
        vstreams = [s for s in streams if s.get("codec_type") == "video"]
        vstream = next(
            (s for s in vstreams if not (s.get("disposition") or {}).get("attached_pic")),
            next(iter(vstreams), None),
        )
        if vstream is None:
            raise ProbeFailed("no video stream in ffprobe output")
        fmt = payload.get("format", {})
        rate_raw = vstream.get("r_frame_rate", "0/1")
        num, _, den = rate_raw.partition("/")
        try:
            fps = float(num) / float(den) if den and float(den) != 0 else float(num)
        except ValueError:
            fps = 0.0
        bit_rate_raw = vstream.get("bit_rate") or fmt.get("bit_rate")
        duration_raw = fmt.get("duration") or vstream.get("duration")
        has_audio = any(s.get("codec_type") == "audio" for s in streams)
        try:
            return cls(
                width=int(vstream.get("width", 0)),
                height=int(vstream.get("height", 0)),
                fps=fps,
                duration_s=float(duration_raw) if duration_raw is not None else 0.0,
                bit_rate=int(bit_rate_raw) if bit_rate_raw is not None else None,
                has_audio=has_audio,
                pix_fmt=str(vstream.get("pix_fmt", "")),
            )
        except (TypeError, ValueError) as exc:
            raise ProbeFailed(f"malformed ffprobe stream/format fields: {exc}") from exc


def probe(path: Path) -> Probe:
    """The only ffprobe shell-out in this module. Raises ProbeUnavailable if ffprobe is not on
    PATH, ProbeFailed if the input cannot be probed (corrupt, zero-byte, not media, no video
    stream). Never returns a clean result for a file that could not actually be measured."""
    import shutil

    ffprobe_bin = shutil.which("ffprobe")
    if ffprobe_bin is None:
        raise ProbeUnavailable("ffprobe not found on PATH")
    try:
        out = subprocess.run(  # nosec B603 — arg list resolved via shutil.which, never shell=True
            [
                ffprobe_bin,
                "-v",
                "error",
                # NOT -select_streams v:0. It was, until 2026-08-28, and it filtered the audio
                # stream out of the payload — so ``has_audio`` was unconditionally False on every
                # asset ever probed. from_ffprobe_json picks the video stream itself, so the
                # selector was redundant for its stated purpose and wrong for this one.
                "-show_entries",
                "stream=width,height,r_frame_rate,bit_rate,codec_type,pix_fmt,duration,disposition",
                "-show_entries",
                "format=duration,bit_rate",
                "-show_streams",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProbeFailed(f"ffprobe could not run: {exc}") from exc
    if out.returncode != 0 or not out.stdout.strip():
        raise ProbeFailed(f"ffprobe exited {out.returncode}: {out.stderr.strip()[:500]}")
    try:
        payload = json.loads(out.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeFailed(f"ffprobe produced non-JSON output: {exc}") from exc
    return Probe.from_ffprobe_json(payload)


@dataclass(frozen=True)
class Suppression:
    tier: str
    asset: str
    reason: str


def _validate_suppressions(raw: list[dict]) -> list[Suppression]:
    out: list[Suppression] = []
    for entry in raw:
        # NOT stripped before the regex check: "V3 " must fail as malformed, not silently
        # normalize to "V3" — a suppression's tier id is compared by exact equality everywhere
        # else, so accepting whitespace-padded input here would be the one place that isn't true.
        tier = str(entry.get("tier", ""))
        asset = str(entry.get("asset", "")).strip()
        reason = str(entry.get("reason", "")).strip()
        if not _TIER_RE.match(tier):
            raise BadSuppression(f"suppression names an invalid tier id: {tier!r}")
        if tier not in _TIERS_BY_ID:
            raise BadSuppression(f"suppression names an unknown tier: {tier!r}")
        if not asset:
            raise BadSuppression("suppression is missing 'asset' — must be scoped to one file")
        if len(reason) < _MIN_SUPPRESSION_REASON_CHARS or reason.lower() in _PLACEHOLDER_REASONS:
            raise BadSuppression(
                f"suppression for {tier} on {asset!r} has no real reason "
                f"({reason!r}) — a suppression with no reason is worse than no rule"
            )
        out.append(Suppression(tier=tier, asset=asset, reason=reason))
    return out


def _safe_box(area: SafeArea, frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
    """(x0, y0, x1, y1) of the region a caption glyph may occupy, in pixels, computed from the
    ACTUAL probed frame dimensions (never the nominal target) — so a stale sidecar against a
    rescaled video is caught by a mismatched frame size, not silently trusted."""
    x0 = round(area.left * frame_w)
    x1 = frame_w - round(area.right * frame_w)
    y0 = round(area.top * frame_h)
    y1 = frame_h - round(area.bottom * frame_h)
    return x0, y0, x1, y1


def measure_cuts_and_motion(path: Path) -> tuple[list[float] | None, list[dict] | None]:
    """Impure. One ffmpeg pass that yields both V6's cut list and V9's per-shot motion.

    ``tblend=all_mode=difference`` turns each frame into its delta from the previous one, and
    ``signalstats`` then reports that delta's average luma (YAVG, 0-255). Averaged over a shot
    and normalised to 0-1, it is a serviceable "does anything move here" measure. Returns
    ``(None, None)`` if ffmpeg is missing or the pass fails — the caller degrades to the checks
    that do not need it rather than reporting a clean asset it could not measure."""
    import re
    import shutil

    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        return None, None
    try:
        out = subprocess.run(  # nosec B603 — arg list resolved via shutil.which, never shell=True
            [
                ffmpeg_bin,
                "-v",
                "info",
                "-i",
                str(path),
                "-vf",
                "select='gt(scene,0.3)',metadata=print:file=-",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    cuts = [float(m) for m in re.findall(r"pts_time:([0-9.]+)", out.stdout or "")]

    try:
        out2 = subprocess.run(  # nosec B603 — arg list resolved via shutil.which, never shell=True
            [
                ffmpeg_bin,
                "-v",
                "info",
                "-i",
                str(path),
                "-vf",
                "tblend=all_mode=difference,signalstats,metadata=print:file=-",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return (cuts or None), None

    samples: list[tuple[float, float]] = []
    pending_t: float | None = None
    for line in (out2.stdout or "").splitlines():
        m = re.search(r"pts_time:([0-9.]+)", line)
        if m:
            pending_t = float(m.group(1))
            continue
        m = re.search(r"lavfi\.signalstats\.YAVG=([0-9.]+)", line)
        if m and pending_t is not None:
            samples.append((pending_t, float(m.group(1)) / 255.0))
            pending_t = None
    if not samples:
        return (cuts or None), None

    bounds = [0.0] + sorted(cuts) + [samples[-1][0] + 1e-6]
    motion: list[dict] = []
    for i in range(len(bounds) - 1):
        lo, hi = bounds[i], bounds[i + 1]
        # skip the first sample after a cut: its delta is against the previous SHOT, not motion
        vals = [v for t, v in samples if lo < t < hi]
        if len(vals) > 1:
            vals = vals[1:]
        if not vals:
            continue
        motion.append({"index": i, "start": lo, "end": hi, "motion": sum(vals) / len(vals)})
    return (cuts or None), (motion or None)


def measure_audio(path: Path, *, duration_s: float | None = None) -> dict | None:
    """Impure. One ffmpeg pass yielding the MEASURED half of V5/V10's audio context.

    ``silencedetect`` reports every run at or below :data:`SILENCE_FLOOR_DBFS` lasting at least
    :data:`SILENCE_MIN_RUN_S`; ``ebur128`` reports integrated loudness and true peak in the same
    pass. Returns ``None`` if ffmpeg is missing or the pass fails — the caller degrades to the
    checks that do not need it rather than reporting a clean asset it could not measure.

    Only measurable facts come back. Whether a music bed exists, and whether it was ducked, are
    properties of the MIX that no analysis of the finished mono-sum can recover; those are
    declared by the producer in the finish sidecar and merged in by the caller."""
    import re
    import shutil

    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        return None
    try:
        out = subprocess.run(  # nosec B603 — arg list resolved via shutil.which, never shell=True
            [
                ffmpeg_bin,
                "-v",
                "info",
                "-i",
                str(path),
                "-af",
                f"silencedetect=noise={SILENCE_FLOOR_DBFS}dB:d={SILENCE_MIN_RUN_S},"
                "ebur128=peak=true",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    # ffmpeg writes filter diagnostics to stderr.
    log = (out.stderr or "") + (out.stdout or "")

    runs: list[dict] = []
    pending_start: float | None = None
    for m in re.finditer(
        r"silence_start:\s*(-?[0-9.]+)|silence_end:\s*(-?[0-9.]+)\s*\|\s*"
        r"silence_duration:\s*([0-9.]+)",
        log,
    ):
        if m.group(1) is not None:
            pending_start = float(m.group(1))
        elif m.group(2) is not None and m.group(3) is not None:
            end, dur = float(m.group(2)), float(m.group(3))
            start = pending_start if pending_start is not None else max(0.0, end - dur)
            runs.append({"start": round(start, 3), "end": round(end, 3), "duration": round(dur, 3)})
            pending_start = None
    # A run still open at EOF never gets a silence_end line.
    if pending_start is not None and duration_s:
        tail = duration_s - pending_start
        if tail >= SILENCE_MIN_RUN_S:
            runs.append(
                {
                    "start": round(pending_start, 3),
                    "end": round(duration_s, 3),
                    "duration": round(tail, 3),
                }
            )

    integrated = _last_float(log, r"^\s*I:\s*(-?[0-9.]+)\s*LUFS")
    true_peak = _last_float(log, r"^\s*Peak:\s*(-?[0-9.]+)\s*dBFS")

    silent_total = sum(r["duration"] for r in runs)
    fraction = (silent_total / duration_s) if duration_s else None

    return {
        "silent_runs": runs,
        "silent_total_s": round(silent_total, 3),
        "silent_fraction": (round(fraction, 4) if fraction is not None else None),
        "integrated_lufs": integrated,
        "true_peak_dbfs": true_peak,
        # -70 LUFS is ffmpeg's "effectively nothing" floor for integrated loudness.
        "has_voice": integrated is not None and integrated > -70.0,
    }


#: How many caption screens V11 samples. A 64-screen asset does not need 64 ffmpeg seeks to learn
#: that its caption style has no plate — the defect is a property of the style, not of one screen.
#: Reported in the finding as "N of M sampled" so the cap is never silent.
CONTRAST_SAMPLE_LIMIT = 12


def measure_caption_contrast(
    path: Path, manifest: dict, *, limit: int = CONTRAST_SAMPLE_LIMIT
) -> list[dict] | None:
    """Impure. Seek to each sampled caption's midpoint, crop its own box, and ask
    :func:`gtm_core.captions.contrast_against_backdrop` what the contrast ratio there is.

    The split is the module boundary: ffmpeg orchestration is this module's job (it already runs
    two other measurement passes), pixel work is captions.py's (see
    tests/media/test_captions_module_boundary.py). Returns ``None`` if ffmpeg or Pillow is
    unavailable, or the manifest carries no usable screens — the caller degrades rather than
    reporting a clean asset it could not measure."""
    import shutil

    try:
        from .captions import contrast_against_backdrop
    except ImportError:  # Pillow absent — normalize/cut/grade/encode still work without it
        return None

    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        return None

    screens = [s for s in (manifest.get("screens") or []) if s.get("box")]
    if not screens:
        return None
    step = max(1, len(screens) // limit)
    sampled = screens[::step][:limit]

    out: list[dict] = []
    for screen in sampled:
        box = screen["box"]
        w, h = int(box.get("w", 0)), int(box.get("h", 0))
        if w <= 0 or h <= 0:
            continue
        start, end = float(screen.get("start_s", 0.0)), float(screen.get("end_s", 0.0))
        at = start + (end - start) / 2 if end > start else start
        try:
            proc = subprocess.run(  # nosec B603 — arg list via shutil.which, never shell=True
                [
                    ffmpeg_bin,
                    "-v",
                    "error",
                    "-ss",
                    f"{at:.3f}",
                    "-i",
                    str(path),
                    "-vf",
                    f"crop={w}:{h}:{int(box.get('x', 0))}:{int(box.get('y', 0))}",
                    "-frames:v",
                    "1",
                    "-f",
                    "image2pipe",
                    "-vcodec",
                    "png",
                    "-",
                ],
                capture_output=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode != 0 or not proc.stdout:
            continue
        measured = contrast_against_backdrop(proc.stdout)
        if measured is not None:
            out.append({"index": screen.get("index"), "at_s": round(at, 3), **measured})
    return out or None


def _last_float(text: str, pattern: str) -> float | None:
    """Last match of a multiline float pattern — ebur128 prints a running summary, then a final
    one; the final block is the asset-level figure and the running ones must not win."""
    import re

    found = re.findall(pattern, text, re.MULTILINE)
    return float(found[-1]) if found else None


def _screens_phrase(idxs: list) -> str:
    """ "screen 7" / "screens 7-9" / "51 screens (7-63)" — a count, never a wall of identical
    lines. A report nobody reads is the same as no gate at all."""
    if len(idxs) == 1:
        return f"screen {idxs[0]}"
    span = f"{idxs[0]}-{idxs[-1]}"
    if len(idxs) <= 3:
        return f"screens {', '.join(str(i) for i in idxs)}"
    return f"{len(idxs)} screens ({span})"


def _face_band_fix(area: SafeArea, band_top: float) -> str:
    """The remedy, honestly scoped to this ratio.

    On a full-bleed 9:16 or 16:9 presenter the face band starts ABOVE the safe box, so ``upper``
    has negative room and offering it is an instruction the caller cannot follow. Say so instead
    of naming a placement that cannot exist here."""
    upper_room = area.height * band_top - area.height * area.top
    if upper_room < 1.0:
        return (
            "use placement='lower' (gtm_core.captions.render) — at this ratio the face band "
            f"starts {abs(upper_room):.0f}px ABOVE the safe box, so there is no upper placement "
            "clear of a presenter's head; the lower third is the only one"
        )
    return (
        "render captions with placement='upper' or 'lower' (gtm_core.captions.render) — "
        "centring inside the safe box puts text on the mouth"
    )


def evaluate(
    p: Probe,
    *,
    ratio: str,
    manifest: dict | None = None,
    edge_stats: dict | None = None,
    purpose: str | None = None,
    audio_context: dict | None = None,
    scene_changes: list[float] | None = None,
    spoken_text: str | None = None,
    motion_stats: list[dict] | None = None,
    identity_used: list[str] | None = None,
    caption_contrast: list[dict] | None = None,
) -> list[Finding]:
    """Pure. Takes a Probe (real or fabricated) plus optional context; returns findings with no
    I/O. ``manifest`` is a captions.json-shaped dict: {"frame": [w, h], "screens": [{"box": {"x",
    "y", "w", "h"}}, ...]}. ``edge_stats`` is {"left_ymax": float, "right_ymax": float, ...} —
    the V3 fallback for an asset with no caption sidecar. ``audio_context`` is {"has_music_bed":
    bool, "has_voice": bool, "ducking_applied": bool, "music_lufs": float|None,
    "voice_lufs": float|None} for V5. ``scene_changes`` is a list of cut timestamps in seconds
    for V6. ``spoken_text`` is the voice-over line the captions were cut from, for V8.
    ``motion_stats`` is [{"index": int, "start": float, "end": float, "motion": float}, ...] —
    one entry per shot, ``motion`` being that shot's mean normalised inter-frame delta — for V9.
    ``identity_used`` is the finish manifest's top-level list (["element", "voice"], …); a
    non-empty value means a rendered person is on screen, which promotes V3's face-band overlap
    from WARN to ERROR. It is passed separately rather than read off ``manifest`` because
    ``manifest`` is the *captions* sub-payload and never carried this field.
    ``audio_context`` additionally carries the MEASURED keys ``gtm_core.video_lint.measure_audio``
    produces ({"silent_runs", "silent_fraction", "integrated_lufs", ...}) for V10 — the declared
    keys above and the measured keys share one dict because they answer one question between them.
    ``caption_contrast`` is [{"index": int, "ratio": float, "bg_luma": float}, ...] — one entry
    per sampled caption screen — for V11."""
    if ratio not in SAFE_AREAS:
        raise ValueError(f"unknown ratio {ratio!r} — expected one of {sorted(SAFE_AREAS)}")
    area = SAFE_AREAS[ratio]
    findings: list[Finding] = []

    # V1a — resolution + fps (fixture-provable).
    if p.width < area.width or p.height < area.height:
        findings.append(
            Finding(
                tier="V1",
                rule="resolution",
                severity=ERROR,
                asset="",
                excerpt=f"{p.width}x{p.height} — below the {area.width}x{area.height} floor for {ratio}",
                fix=f"re-render or upscale to at least {area.width}x{area.height}",
            )
        )
    if p.fps < MIN_FPS_ACCEPTABLE:
        findings.append(
            Finding(
                tier="V1",
                rule="fps",
                severity=ERROR,
                asset="",
                excerpt=f"{p.fps:g}fps — below the {MIN_FPS_ACCEPTABLE:g}fps floor",
                fix=f"re-render or reframe at >= {MIN_FPS_CLEAN:g}fps",
            )
        )
    elif p.fps < MIN_FPS_CLEAN:
        findings.append(
            Finding(
                tier="V1",
                rule="fps",
                severity=WARN,
                asset="",
                excerpt=f"{p.fps:g}fps — cinematic-only, below the {MIN_FPS_CLEAN:g}fps clean floor",
                fix=f"prefer >= {MIN_FPS_CLEAN:g}fps unless the cinematic look is deliberate",
            )
        )

    # V1b — bitrate. Only asserted when a bitrate is actually reported; a short CBR fixture's
    # measured rate is dominated by its I-frame, so this sub-check is honestly meaningful only
    # against a real (or calibration-recorded) asset — see the module docstring.
    if p.bit_rate is not None and p.height >= area.height and p.bit_rate < MIN_BITRATE_1080P_BPS:
        findings.append(
            Finding(
                tier="V1",
                rule="bitrate",
                severity=ERROR,
                asset="",
                excerpt=f"{p.bit_rate / 1_000_000:.2f} Mbps — below the "
                f"{MIN_BITRATE_1080P_BPS / 1_000_000:g} Mbps floor @1080p",
                fix="re-encode at a higher target bitrate",
            )
        )

    # V2 — aspect exact. Checks SHAPE (width/height ratio), independent of SIZE (V1's job). This
    # is what catches the soul_2 4:5->3:4 coercion — a different aspect entirely, not merely a
    # low-resolution version of the right one.
    expected_ratio = area.width / area.height
    actual_ratio = p.width / p.height if p.height else 0.0
    if abs(actual_ratio - expected_ratio) > ASPECT_TOLERANCE:
        findings.append(
            Finding(
                tier="V2",
                rule="aspect_exact",
                severity=ERROR,
                asset="",
                excerpt=f"{p.width}x{p.height} (ratio {actual_ratio:.4f}) — requested {ratio} "
                f"(ratio {expected_ratio:.4f})",
                fix="reframe/crop to the exact requested aspect; do not ship the provider's raw output",
            )
        )

    # V3 — caption geometry. Primary: the sidecar bbox, pure arithmetic. Fallback: edge luma WARN.
    if manifest is not None:
        frame = manifest.get("frame")
        if frame and tuple(frame) != (p.width, p.height):
            findings.append(
                Finding(
                    tier="V3",
                    rule="stale_sidecar",
                    severity=ERROR,
                    asset="",
                    excerpt=f"captions.json frame {tuple(frame)} != probed frame {(p.width, p.height)}",
                    fix="regenerate captions against the finished asset's actual dimensions",
                )
            )
        else:
            x0, y0, x1, y1 = _safe_box(area, p.width, p.height)
            band_top, band_bottom = face_band(ratio)
            face_y0 = round(p.height * band_top)
            face_y1 = round(p.height * band_bottom)
            # Both V3 rules are properties of a caption's GEOMETRY, and captions.py places every
            # screen at one of very few positions — so a per-screen finding emits the same defect
            # 64 times and buries every other tier under it. Group by the offending geometry and
            # report the screen count instead: one line per distinct defect, which is what makes
            # the report readable enough to act on.
            outside: dict[tuple, list] = {}
            on_face: dict[tuple, list] = {}
            for screen in manifest.get("screens", []):
                box = screen.get("box", {})
                bx0, by0 = box.get("x", 0), box.get("y", 0)
                bx1, by1 = bx0 + box.get("w", 0), by0 + box.get("h", 0)
                idx = screen.get("index", "?")
                if bx0 < x0 or by0 < y0 or bx1 > x1 or by1 > y1:
                    outside.setdefault((bx0, by0, bx1, by1), []).append(idx)
                # A caption inside the safe area can still be squarely on the speaker's face —
                # the safe area reserves edges only, so "as far from every margin as possible"
                # and "on the mouth" are the SAME position. Severity depends on whether a
                # rendered identity is actually on screen to be covered.
                if by0 < face_y1 and by1 > face_y0:
                    on_face.setdefault((by0, by1), []).append(idx)

            for (bx0, by0, bx1, by1), idxs in sorted(outside.items()):
                findings.append(
                    Finding(
                        tier="V3",
                        rule="caption_outside_safe_area",
                        severity=ERROR,
                        asset="",
                        excerpt=f"{_screens_phrase(idxs)} box ({bx0},{by0},{bx1},{by1}) "
                        f"outside safe area ({x0},{y0},{x1},{y1})",
                        fix="re-fit via gtm_core.captions (shrink/wrap inside the safe area)",
                    )
                )

            has_identity = bool(identity_used)
            for (by0, by1), idxs in sorted(on_face.items()):
                findings.append(
                    Finding(
                        tier="V3",
                        rule="caption_over_face",
                        severity=ERROR if has_identity else WARN,
                        asset="",
                        excerpt=f"{_screens_phrase(idxs)} box y {by0}-{by1} overlaps the face "
                        f"band {face_y0}-{face_y1} "
                        f"({band_top:.0%}-{band_bottom:.0%} of frame height for {ratio})"
                        + (" with a rendered identity on screen" if has_identity else ""),
                        fix=_face_band_fix(area, band_top),
                    )
                )
    elif edge_stats is not None:
        threshold = edge_stats.get("threshold", 60.0)
        hot_edges = [
            edge
            for edge in ("left_ymax", "right_ymax", "top_ymax", "bottom_ymax")
            if edge_stats.get(edge, 0.0) > threshold
        ]
        if hot_edges:
            findings.append(
                Finding(
                    tier="V3",
                    rule="edge_luma_fallback",
                    severity=WARN,
                    asset="",
                    excerpt=f"no caption manifest — edge luma elevated on {', '.join(hot_edges)} "
                    "(possible caption/graphic bleeding to the frame edge)",
                    fix="attach the asset's captions.json sidecar for an exact geometry check, or "
                    "confirm the edge content is intentional and suppress with a reason",
                )
            )

    # V4 — predictor input duration. Scoped by purpose, not by asset: a long-form asset is
    # legitimately >14.9s and must not trip this.
    if purpose == "predictor" and p.duration_s > PREDICTOR_MAX_DURATION_S:
        findings.append(
            Finding(
                tier="V4",
                rule="predictor_duration_over_cap",
                severity=ERROR,
                asset="",
                excerpt=f"{p.duration_s:.6f}s — over the {PREDICTOR_MAX_DURATION_S:g}s predictor cap",
                fix="trim via gtm_core.video_finish predictor-trim (re-encode with -t 14.9; a "
                "keyframe-accurate stream copy will not move the duration)",
            )
        )

    # V5 — audio ducking (WARN candidate). Fires when a music bed is present alongside voice but
    # no sidechain ducking was applied, or when the bed is measured louder than the voice.
    if audio_context is not None:
        has_music = bool(audio_context.get("has_music_bed"))
        has_voice = bool(audio_context.get("has_voice"))
        ducking_applied = bool(audio_context.get("ducking_applied"))
        music_lufs = audio_context.get("music_lufs")
        voice_lufs = audio_context.get("voice_lufs")
        if has_music and has_voice and not ducking_applied:
            findings.append(
                Finding(
                    tier="V5",
                    rule="music_bed_without_ducking",
                    severity=WARN,
                    asset="",
                    excerpt="music bed present alongside dialogue without sidechain ducking",
                    fix="run gtm_core.video_finish.duck_music_bed() before final mux, or suppress "
                    "with a reason if the mix was controlled another way",
                )
            )
        elif (
            has_music
            and has_voice
            and ducking_applied
            and music_lufs is not None
            and voice_lufs is not None
            and music_lufs > voice_lufs - 6.0
        ):
            findings.append(
                Finding(
                    tier="V5",
                    rule="music_bed_too_loud",
                    severity=WARN,
                    asset="",
                    excerpt=f"music bed {music_lufs:.1f} LUFS within 6 dB of voice {voice_lufs:.1f} LUFS",
                    fix="increase duck ratio or lower the music bed level so dialogue sits clearly on top",
                )
            )

    # V6 — scene-change cadence (WARN candidate).
    if scene_changes is not None:
        changes = sorted(scene_changes)
        num_changes = len(changes)
        if p.duration_s > 15.0 and num_changes == 0:
            findings.append(
                Finding(
                    tier="V6",
                    rule="no_scene_changes",
                    severity=WARN,
                    asset="",
                    excerpt=f"no scene changes in a {p.duration_s:.1f}s asset",
                    fix="add at least one visual change (cut, camera move, or graphic) to hold attention",
                )
            )
        elif num_changes >= 2:
            avg_interval = (changes[-1] - changes[0]) / (num_changes - 1)
            if avg_interval < 1.5:
                findings.append(
                    Finding(
                        tier="V6",
                        rule="scene_changes_too_frequent",
                        severity=WARN,
                        asset="",
                        excerpt=f"scene changes average {avg_interval:.2f}s apart",
                        fix="lengthen shots or remove unnecessary cuts to reduce visual churn",
                    )
                )
        if p.duration_s > 30.0 and num_changes < p.duration_s / 30.0:
            findings.append(
                Finding(
                    tier="V6",
                    rule="scene_changes_too_sparse",
                    severity=WARN,
                    asset="",
                    excerpt=f"only {num_changes} scene change(s) across {p.duration_s:.1f}s "
                    f"(fewer than one per 30s)",
                    fix="introduce additional visual changes or shorten the asset to keep attention",
                )
            )

    # V7 — caption reading load (WARN candidate). Needs per-screen text AND timing; a screen
    # with no duration is skipped rather than guessed at, and the asset-wide check still runs.
    screens = (manifest or {}).get("screens") or []
    if screens:
        total_words = 0
        for screen in screens:
            text = str(screen.get("text", "") or "")
            words = len(text.split())
            total_words += words
            span = float(screen.get("end_s", 0.0) or 0.0) - float(screen.get("start_s", 0.0) or 0.0)
            if words and span > 0:
                wps = words / span
                if wps > MAX_WORDS_PER_SEC_SCREEN:
                    findings.append(
                        Finding(
                            tier="V7",
                            rule="caption_screen_too_dense",
                            severity=WARN,
                            asset="",
                            excerpt=f"screen {screen.get('index', '?')}: {words} words in "
                            f"{span:.2f}s = {wps:.1f} w/s (ceiling {MAX_WORDS_PER_SEC_SCREEN:g})",
                            fix="cut words from this screen or hold it longer",
                        )
                    )
        if total_words and p.duration_s > 0:
            asset_wps = total_words / p.duration_s
            if asset_wps > MAX_WORDS_PER_SEC_ASSET:
                findings.append(
                    Finding(
                        tier="V7",
                        rule="caption_load_too_high",
                        severity=WARN,
                        asset="",
                        excerpt=f"{total_words} on-screen words across {p.duration_s:.1f}s = "
                        f"{asset_wps:.1f} w/s (ceiling {MAX_WORDS_PER_SEC_ASSET:g}); reading it "
                        f"needs ~{total_words / MAX_WORDS_PER_SEC_ASSET:.0f}s",
                        fix="cut on-screen words, or lengthen the asset to match the reading load",
                    )
                )

    # V8 — caption/voice divergence (WARN candidate). Captions cut from the spoken line are a
    # subset of it; anything else is a second text stream competing for the same attention.
    if screens and spoken_text:
        spoken_words = {w.strip(".,;:!?\"'“”‘’()").lower() for w in spoken_text.split()}
        spoken_words.discard("")
        for screen in screens:
            caption_words = [
                w.strip(".,;:!?\"'“”‘’()").lower()
                for w in str(screen.get("text", "") or "").split()
            ]
            caption_words = [w for w in caption_words if w]
            if not caption_words:
                continue
            foreign = [w for w in caption_words if w not in spoken_words]
            # A stray word is normal (a burned-in unit or a stylised contraction); a screen that
            # is mostly foreign is a separate stream.
            if len(foreign) > len(caption_words) / 2:
                findings.append(
                    Finding(
                        tier="V8",
                        rule="caption_diverges_from_voice",
                        severity=WARN,
                        asset="",
                        excerpt=f"screen {screen.get('index', '?')}: {len(foreign)}/"
                        f"{len(caption_words)} words are not in the spoken line "
                        f"({', '.join(foreign[:4])}…)",
                        fix="cut captions from the spoken line; move citations to a persistent "
                        "lower-third or the post caption",
                    )
                )

    # V9 — within-shot motion (WARN candidate). V6 counts cuts; this counts whether anything
    # moves BETWEEN them. An asset can cut often and still be a slideshow.
    if motion_stats:
        still = [s for s in motion_stats if float(s.get("motion", 0.0)) < MIN_SHOT_MOTION]
        if still:
            still_s = sum(float(s.get("end", 0.0)) - float(s.get("start", 0.0)) for s in still)
            share = still_s / p.duration_s if p.duration_s > 0 else 0.0
            findings.append(
                Finding(
                    tier="V9",
                    rule="static_shots",
                    severity=WARN,
                    asset="",
                    excerpt=f"{len(still)} of {len(motion_stats)} shots are motionless "
                    f"({still_s:.1f}s, {share:.0%} of the asset); "
                    f"quietest {min(float(s.get('motion', 0.0)) for s in still):.4f} "
                    f"vs floor {MIN_SHOT_MOTION:g}",
                    fix="add real motion inside the shot (camera move, staged build, live "
                    "footage) — a sub-perceptual zoom does not count",
                )
            )

    # V10 — dead air. Checked in two independent ways because one number cannot describe both a
    # long film with one dead stretch and a short film that is mostly dead.
    if not p.has_audio:
        findings.append(
            Finding(
                tier="V10",
                rule="no_audio_stream",
                severity=ERROR,
                asset="",
                excerpt="the asset carries no audio stream at all",
                fix="mux a mix (voice, room tone, bed) before encoding — gtm_core.video_finish "
                "pads a shot with no audio to silence, so a missing stream means every shot "
                "was silent",
            )
        )
    elif audio_context is not None:
        runs = audio_context.get("silent_runs") or []
        fraction = audio_context.get("silent_fraction")
        longest = max((float(r.get("duration", 0.0)) for r in runs), default=0.0)
        if fraction is not None and float(fraction) > MAX_DEAD_AIR_FRACTION:
            findings.append(
                Finding(
                    tier="V10",
                    rule="dead_air_fraction",
                    severity=ERROR,
                    asset="",
                    excerpt=f"{float(fraction):.0%} of runtime is below "
                    f"{SILENCE_FLOOR_DBFS:g} dBFS ({audio_context.get('silent_total_s', '?')}s "
                    f"across {len(runs)} run{'' if len(runs) == 1 else 's'}), over the "
                    f"{MAX_DEAD_AIR_FRACTION:.0%} ceiling",
                    fix="give every shot an audio bed — room tone under everything, and a voice, "
                    "SFX or music cue where the shot is carrying an idea. Digital silence under "
                    "a shot is the loudest amateur tell there is",
                )
            )
        if longest >= MAX_DEAD_AIR_RUN_S:
            worst = max(runs, key=lambda r: float(r.get("duration", 0.0)))
            findings.append(
                Finding(
                    tier="V10",
                    rule="dead_air_run",
                    severity=ERROR,
                    asset="",
                    excerpt=f"{longest:.1f}s of continuous silence at "
                    f"{float(worst.get('start', 0.0)):.1f}-{float(worst.get('end', 0.0)):.1f}s, "
                    f"over the {MAX_DEAD_AIR_RUN_S:g}s ceiling",
                    fix="a deliberate beat is under two seconds; anything longer reads as a "
                    "broken file. Lay room tone or a bed under the stretch",
                )
            )

    # V11 — burned-type contrast. Geometry (V3) says the caption is in a legal place; this says
    # it can actually be read once it is there.
    if caption_contrast:
        failed = [
            c
            for c in caption_contrast
            if c.get("ratio") is not None and float(c["ratio"]) < MIN_CAPTION_CONTRAST_RATIO
        ]
        if failed:
            worst = min(failed, key=lambda c: float(c["ratio"]))
            findings.append(
                Finding(
                    tier="V11",
                    rule="caption_contrast",
                    severity=ERROR,
                    asset="",
                    excerpt=f"{len(failed)} of {len(caption_contrast)} sampled caption screens "
                    f"below {MIN_CAPTION_CONTRAST_RATIO:g}:1 against their own backdrop; worst "
                    f"screen {worst.get('index', '?')} at {float(worst['ratio']):.1f}:1",
                    fix="give the caption a scrim, plate or stroke (gtm_core.captions), or move "
                    "it over a darker part of frame — unplated white type over mid-luminance "
                    "footage never clears AA",
                )
            )

    return findings


def apply_suppressions(
    findings: list[Finding], suppressions: list[Suppression], *, asset: str
) -> tuple[list[Finding], dict[str, int]]:
    """Filter findings against suppressions scoped to (tier, asset). Returns the surviving
    findings AND a per-tier suppressed-count — suppressions are ALWAYS counted and reported, even
    at zero, never applied silently (deck_lint's suppressions were neither)."""
    suppressed_tiers = {s.tier for s in suppressions if s.asset == asset}
    counts: dict[str, int] = {}
    kept: list[Finding] = []
    for f in findings:
        if f.tier in suppressed_tiers:
            counts[f.tier] = counts.get(f.tier, 0) + 1
        else:
            kept.append(f)
    return kept, counts


def report(asset: str, findings: list[Finding], suppressed: dict[str, int]) -> None:
    if not findings:
        print(f"✓ {asset} — clean")
    else:
        print(f"\n{asset}")
        for f in sorted(findings, key=lambda f: (f.tier, f.rule)):
            mark = "✗" if f.severity == ERROR else "!"
            print(f"  {mark} [{f.tier} {f.rule}] {f.excerpt}")
            print(f"      → {f.fix}")
    total_suppressed = sum(suppressed.values())
    if total_suppressed:
        breakdown = ", ".join(f"{tier}:{n}" for tier, n in sorted(suppressed.items()))
        print(f"\n  suppressed: {total_suppressed} ({breakdown})")
    else:
        print("\n  suppressed: 0")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m gtm_core.video_lint")
    parser.add_argument("asset", type=Path)
    parser.add_argument("--ratio", required=True, choices=sorted(SAFE_AREAS))
    parser.add_argument("--purpose", choices=("predictor",), default=None)
    parser.add_argument(
        "--manifest",
        type=Path,
        help="finish-<ratio>.json — carries captions.json's frame/screens AND lint_suppressions",
    )
    parser.add_argument(
        "--no-suppress", action="store_true", help="ignore the manifest's suppressions"
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument(
        "--fast",
        action="store_true",
        help="skip the measurement ffmpeg passes (V6, V9, V10's signal checks and V11 will "
        "not run; V10 still catches a missing audio stream from the probe alone)",
    )
    args = parser.parse_args(argv)

    try:
        p = probe(args.asset)
    except ProbeUnavailable as exc:
        print(f"[video_lint] {exc}", file=sys.stderr)
        return 3
    except ProbeFailed as exc:
        print(f"[video_lint] {exc}", file=sys.stderr)
        return 3

    manifest_payload: dict | None = None
    suppressions: list[Suppression] = []
    if args.manifest:
        if not args.manifest.exists():
            print(f"[video_lint] no such manifest: {args.manifest}", file=sys.stderr)
            return 3
        try:
            raw = json.loads(args.manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"[video_lint] malformed manifest JSON: {exc}", file=sys.stderr)
            return 2
        manifest_payload = raw.get("captions")
        if not args.no_suppress:
            try:
                suppressions = _validate_suppressions(raw.get("lint_suppressions", []))
            except BadSuppression as exc:
                print(f"[video_lint] {exc}", file=sys.stderr)
                return 2

    # Context the CLI previously never assembled, so V5/V6 could not fire from the command line
    # at all and V7-V9 would have been dead on arrival. --fast skips the two ffmpeg passes.
    cuts = motion = None
    if not args.fast:
        cuts, motion = measure_cuts_and_motion(args.asset)

    raw_manifest: dict = {}
    if args.manifest and args.manifest.exists():
        try:
            raw_manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raw_manifest = {}

    # V5 needs facts about the MIX that only the producer knows (is there a bed, was it ducked);
    # V10 needs facts about the SIGNAL that only measurement knows. One dict, two sources, the
    # measured half last so a stale declaration can never mask what the file actually contains.
    contrast = None
    audio_context = dict(raw_manifest.get("audio_context") or {})
    if not args.fast:
        measured = measure_audio(args.asset, duration_s=p.duration_s)
        if measured:
            audio_context.update(measured)
        if manifest_payload:
            contrast = measure_caption_contrast(args.asset, manifest_payload)

    findings = evaluate(
        p,
        ratio=args.ratio,
        manifest=manifest_payload,
        purpose=args.purpose,
        audio_context=audio_context or None,
        scene_changes=cuts,
        spoken_text=(manifest_payload or {}).get("spoken_text") or raw_manifest.get("spoken_text"),
        motion_stats=motion,
        identity_used=raw_manifest.get("identity_used"),
        caption_contrast=contrast,
    )
    asset_name = args.asset.name
    findings = [Finding(f.tier, f.rule, f.severity, asset_name, f.excerpt, f.fix) for f in findings]
    kept, suppressed_counts = apply_suppressions(findings, suppressions, asset=asset_name)

    if args.as_json:
        print(
            json.dumps(
                {
                    "asset": asset_name,
                    "findings": [f.__dict__ for f in kept],
                    "suppressed": suppressed_counts,
                },
                indent=2,
            )
        )
    else:
        report(asset_name, kept, suppressed_counts)

    return 1 if any(f.severity == ERROR for f in kept) else 0


if __name__ == "__main__":
    raise SystemExit(main())
