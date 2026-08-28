"""The avatar panel: a PRE-REGISTERED blind evaluation, decided before any data exists.

Why this module exists, and why it exists *now*
-----------------------------------------------
The question P3 asks is not answerable by a metric this pipeline can compute. It is: *would people
who know this person find the avatar uncanny?* Only people who know him can answer that, and the
answer is worthless if the pass mark is chosen after the scores are in.

So the thresholds live here, in code, committed **before the first clip is rendered**
(:data:`PASS_REAL_RATE_MIN`, :data:`PASS_NATURALNESS_MIN`, :data:`KILL_TELL_SHARE`). Moving one
later is a visible diff with a reviewer, not a judgement call made while staring at a
disappointing number.

This design is aimed squarely at the repo's own recorded failure mode. On 2026-08-19 a lip-sync
A/B was "won" on credits, frame rate, and the presence of an embedded audio track — **none of
which is lip sync**. The winning model's mouth was closed in 9 of 10 sampled frames. The lesson
recorded then was: judge the thing you actually care about, with a positive control, and never let
a convenient proxy stand in for it. Here the thing we care about is a human judgement, so the
harness collects human judgements — and refuses to compute a verdict from a sheet that is too
small, too lopsided, or missing its blinding.

What "blind" means here
-----------------------
Judges see clips in a shuffled order with the condition hidden. The mapping from display position
to condition is the **key**, and :func:`build_sheet` returns it separately from the sheet so the
sheet itself can be handed over without leaking the answer. A judge who can tell which clips are
supposed to be synthetic is not testing the avatar, they are testing their own expectations.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

__all__ = [
    "CONDITIONS",
    "PASS_REAL_RATE_MIN",
    "PASS_NATURALNESS_MIN",
    "KILL_TELL_SHARE",
    "MIN_JUDGES",
    "MIN_CLIPS_PER_CONDITION",
    "PanelError",
    "Clip",
    "Judgement",
    "Verdict",
    "build_sheet",
    "render_sheet",
    "score_panel",
]

# ── the pre-registration ────────────────────────────────────────────────────────────────────────
# These five numbers ARE the experiment. Changing one after clips exist invalidates the run; the
# git history of this file is the audit trail.

#: An avatar clip must be judged "real" at least this often to pass. Chance is 0.5 on a
#: forced-choice task, so this is deliberately set AT chance: the bar is "indistinguishable",
#: not "usually convincing". Anything below means judges can tell.
PASS_REAL_RATE_MIN = 0.50

#: Mean naturalness (1-5) across avatar clips. Set at 3.5 rather than 3.0 because 3 is the
#: shrug — "fine, I guess" is not a bar worth shipping a founder's face on.
PASS_NATURALNESS_MIN = 3.5

#: If more than this share of judges independently name the SAME tell ("the mouth", "the
#: gestures", "the pacing"), the avatar fails regardless of the other two numbers. A defect most
#: people spot unprompted is a defect the audience will spot, and averages hide it: a clip can
#: clear the naturalness bar while every judge notices the same wrong thing.
KILL_TELL_SHARE = 0.60

#: Fewer judges than this cannot support the claim. Five is not a statistical threshold; it is the
#: point below which one person's taste dominates the result.
MIN_JUDGES = 5

#: Per condition. A panel with one avatar clip is testing one render, not the avatar.
MIN_CLIPS_PER_CONDITION = 3

#: The three conditions. `real` is the control and it is NOT optional: without it the panel
#: measures how suspicious the judges are today, not how good the avatar is. `hybrid` is the
#: "proof of human" pattern (a short real intro, then avatar) that practitioners report performs
#: best on LinkedIn — it is on trial here alongside the pure avatar, not assumed.
CONDITIONS = ("real", "avatar", "hybrid")


class PanelError(ValueError):
    """The panel is malformed, or too thin to support a verdict."""


@dataclass(frozen=True)
class Clip:
    """One stimulus. ``condition`` is the hidden truth; ``slot`` is what the judge sees."""

    clip_id: str
    condition: str
    slot: int = 0

    def __post_init__(self) -> None:
        if self.condition not in CONDITIONS:
            raise PanelError(f"unknown condition {self.condition!r}; expected one of {CONDITIONS}")


@dataclass(frozen=True)
class Judgement:
    """One judge's response to one slot."""

    judge_id: str
    slot: int
    #: The judge's forced choice. True = "this is a real recording".
    called_real: bool
    #: 1-5. How natural the person seemed, independent of the real/synthetic call.
    naturalness: int
    #: Free text: what tipped them off. Normalised to a tell tag by the caller, or left blank.
    tell: str = ""

    def __post_init__(self) -> None:
        if not 1 <= self.naturalness <= 5:
            raise PanelError(f"naturalness must be 1-5, got {self.naturalness}")


@dataclass
class Verdict:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    real_rate: float | None = None
    mean_naturalness: float | None = None
    dominant_tell: tuple[str, float] | None = None
    judges: int = 0
    clips_by_condition: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> dict:
        d = asdict(self)
        if self.dominant_tell is not None:
            d["dominant_tell"] = {"tell": self.dominant_tell[0], "share": self.dominant_tell[1]}
        return d


def _shuffle_index(clip_id: str, salt: str) -> str:
    """Deterministic ordering key.

    Deliberately not `random.shuffle`: a panel must be reproducible from its inputs so a disputed
    result can be re-derived, and this repo's scripts cannot call `random`/`Date.now` anyway.
    """
    return hashlib.sha256(f"{salt}:{clip_id}".encode()).hexdigest()


def build_sheet(clips: list[Clip], *, salt: str) -> tuple[list[Clip], dict[int, str]]:
    """Assign shuffled display slots. Returns ``(sheet_clips, key)``.

    The key maps slot -> condition and is returned SEPARATELY so the sheet can be handed to judges
    without leaking it. Do not write both into the same file.
    """
    if not clips:
        raise PanelError("no clips supplied")
    if not salt:
        raise PanelError("a salt is required so the ordering is reproducible but not guessable")

    counts = Counter(c.condition for c in clips)
    thin = {
        cond: counts.get(cond, 0)
        for cond in CONDITIONS
        if counts.get(cond, 0) < MIN_CLIPS_PER_CONDITION
    }
    if thin:
        raise PanelError(
            f"every condition needs >= {MIN_CLIPS_PER_CONDITION} clips; too few: {thin}. "
            "A panel with one avatar clip tests one render, not the avatar — and dropping the "
            "`real` control entirely would measure how suspicious the judges are today."
        )

    ordered = sorted(clips, key=lambda c: _shuffle_index(c.clip_id, salt))
    sheet = [
        Clip(clip_id=c.clip_id, condition=c.condition, slot=i + 1) for i, c in enumerate(ordered)
    ]
    key = {c.slot: c.condition for c in sheet}
    return sheet, key


def render_sheet(sheet: list[Clip]) -> str:
    """The judge-facing sheet. Carries NO condition column, by construction."""
    lines = [
        "# Avatar panel — blind scoring sheet",
        "",
        "You know this person. That is the whole point: you are the only kind of judge whose",
        "answer means anything here.",
        "",
        "For each clip, answer both columns. Do not go back and change earlier answers after you",
        "think you have spotted the pattern — first impressions are what a scrolling viewer gives.",
        "",
        "- **Real?** — your best guess: is this a real recording of him, or generated? Guess even",
        "  when unsure; there is no 'not sure' option on purpose.",
        "- **Natural (1-5)** — how natural did he seem, *regardless* of your real/generated call.",
        "  1 = obviously off, 5 = exactly how he comes across in person.",
        "- **What tipped you off?** — free text, only if something did. Be specific ('mouth on the",
        "  s sounds', 'hands repeat', 'talks too fast'). Blank is a perfectly good answer.",
        "",
        "| Clip | Real? (real / generated) | Natural (1-5) | What tipped you off? |",
        "|---|---|---|---|",
    ]
    lines.extend(f"| {c.slot} | | | |" for c in sheet)
    lines += ["", f"Judge ID: ______   (clips: {len(sheet)})"]
    return "\n".join(lines)


def score_panel(sheet: list[Clip], judgements: list[Judgement]) -> Verdict:
    """Apply the pre-registered thresholds. Returns PASS or KILL with reasons.

    Only ``avatar`` clips are scored against the bar — `real` and `hybrid` are context. The real
    control is still *required* (enforced in :func:`build_sheet`): a panel of judges who call
    genuine footage synthetic half the time is measuring its own suspicion, and this function
    reports that rather than silently crediting it to the avatar.
    """
    by_slot = {c.slot: c for c in sheet}
    unknown = sorted({j.slot for j in judgements} - set(by_slot))
    if unknown:
        raise PanelError(f"judgements reference slots not on the sheet: {unknown}")

    judges = {j.judge_id for j in judgements}
    reasons: list[str] = []
    verdict = Verdict(
        passed=False,
        judges=len(judges),
        clips_by_condition=dict(Counter(c.condition for c in sheet)),
    )

    if len(judges) < MIN_JUDGES:
        reasons.append(
            f"only {len(judges)} judge(s); {MIN_JUDGES} required. Below this one person's taste "
            "dominates the result."
        )

    avatar = [j for j in judgements if by_slot[j.slot].condition == "avatar"]
    if not avatar:
        reasons.append("no judgements on any avatar clip — nothing to score")
        verdict.reasons = reasons
        return verdict

    verdict.real_rate = sum(1 for j in avatar if j.called_real) / len(avatar)
    verdict.mean_naturalness = sum(j.naturalness for j in avatar) / len(avatar)

    # A tell counts once per judge, however many clips they named it on: this measures how many
    # PEOPLE noticed it, not how talkative one person was.
    tells_by_judge: dict[str, set[str]] = {}
    for j in avatar:
        tag = j.tell.strip().lower()
        if tag:
            tells_by_judge.setdefault(j.judge_id, set()).add(tag)
    tell_counts = Counter(tag for tags in tells_by_judge.values() for tag in tags)
    if tell_counts and judges:
        tag, count = tell_counts.most_common(1)[0]
        verdict.dominant_tell = (tag, count / len(judges))

    if verdict.real_rate < PASS_REAL_RATE_MIN:
        reasons.append(
            f"avatar clips were called real {verdict.real_rate:.0%} of the time; the bar is "
            f"{PASS_REAL_RATE_MIN:.0%} (chance on a forced choice). Judges can tell."
        )
    if verdict.mean_naturalness < PASS_NATURALNESS_MIN:
        reasons.append(
            f"mean naturalness {verdict.mean_naturalness:.2f} is below the {PASS_NATURALNESS_MIN} "
            "bar. 3 is the shrug; this needs to be better than 'fine, I guess'."
        )
    if verdict.dominant_tell is not None and verdict.dominant_tell[1] > KILL_TELL_SHARE:
        tag, share = verdict.dominant_tell
        reasons.append(
            f"{share:.0%} of judges independently named the same tell ({tag!r}), over the "
            f"{KILL_TELL_SHARE:.0%} ceiling. A defect most people spot unprompted is one the "
            "audience will spot, and an average hides it."
        )

    # Control readout — never a pass/fail on its own, but it changes how to read the rest.
    real = [j for j in judgements if by_slot[j.slot].condition == "real"]
    if real:
        control_rate = sum(1 for j in real if j.called_real) / len(real)
        if control_rate < 0.7:
            reasons.append(
                f"CONTROL WARNING: genuine footage was only called real {control_rate:.0%} of the "
                "time. The panel is primed to suspect everything, so the avatar's score is not "
                "trustworthy in either direction — re-run with judges who have not been told what "
                "the study is about."
            )

    verdict.reasons = reasons
    verdict.passed = not reasons
    return verdict


def write_key(key: dict[int, str], path: Path) -> Path:
    """Persist the slot->condition key. Keep this file away from the judges."""
    path.write_text(json.dumps({str(k): v for k, v in sorted(key.items())}, indent=2) + "\n")
    return path
