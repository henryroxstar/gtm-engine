"""The creator brief — nine pre-spend decisions for one video run, as machine-readable data.

Block C of the 2026-08-28 video brief template, for the video lanes. It sits between *what to say*
(the plan item's own ``brief`` field, untouched by this module) and *shot by shot how it goes*
(``video-script``), and it is written **before** anything is generated, to
``content/<active>/video/<script-slug>/brief.json``.

Four properties this module exists to hold:

**It is data, not prose.** A brief downstream skills cannot read is prose that gets ignored — the
"declared contract nobody runs" failure this repo has already paid for. The markdown twin beside it
is *rendered from* the JSON and is never the source.

**Every decision carries its provenance.** ``source ∈ {operator, profile_default, derived, model}``.
A decision with a default is filled and labelled, never asked, which is how a routine run stays one
question long; a brief that is complete because every field defaulted is legal and **says so**.

**It cross-examines, it does not self-certify** (§R11). ``check_against_shotlist`` corroborates the
brief against an independent witness — the emitted shot list — and fails closed on a contradiction.
A brief declaring ``capture_mode: live_action`` beside a shot list that resolves a render engine is
a refusal, not a warning. The checker lives here rather than in skill prose because a rule in prose
is a rule that gets skipped (§R13).

**It adds no gate.** Gate 1 (plan) and Gate 2 (publish) are unchanged and permanent; the brief is an
artifact produced *under* the existing Gate 1 envelope. Nothing here emits a gate marker, and there
is no ``brief`` gate kind anywhere in the tree.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import minischema
from .paths import _safe_segment, resolve_content_root

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "creator-brief.schema.json"

BRIEF_FILENAME = "brief.json"
TWIN_FILENAME = "brief.md"

#: The nine decisions, in the order the operator meets them and the twin renders them.
DECISIONS: tuple[str, ...] = (
    "cover",
    "outlier_structure",
    "slot_schema",
    "cheapest_medium",
    "capture_mode",
    "invariant",
    "broll_list",
    "sampling_curve",
    "visual_hook",
)

#: One line each, for the markdown twin a non-marketer reads on a phone.
_DECISION_TITLES: dict[str, str] = {
    "cover": "Cover (designed first)",
    "outlier_structure": "Outlier structure",
    "slot_schema": "Slot schema",
    "cheapest_medium": "Cheapest medium",
    "capture_mode": "Capture contract",
    "invariant": "The invariant",
    "broll_list": "B-roll selection",
    "sampling_curve": "Sampling budget",
    "visual_hook": "The visual hook",
}

_SOURCES = ("operator", "profile_default", "derived", "model")


class BriefError(Exception):
    """A brief is malformed, contradicts its shot list, or would be written outside the root."""


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def run_dir(
    profile: str,
    script_slug: str,
    *,
    content_root: Path | None = None,
) -> Path:
    """``content/<profile>/video/<script-slug>/`` — the run folder the brief is the first artifact of.

    The tree has spelled this path by convention in five skill bodies and nothing minted it, which is
    how ``video-avatar`` came to spell it one way and everything else another. Both segments pass
    ``_safe_segment``: a profile or slug carrying ``/``, ``\\``, NUL, ``.`` or ``..`` is refused
    before any path is built, because this path is where tenant PII lands.
    """
    root = resolve_content_root() if content_root is None else Path(content_root)
    return (
        root
        / _safe_segment(profile, "profile")
        / "video"
        / _safe_segment(script_slug, "script slug")
    )


def brief_path(profile: str, script_slug: str, *, content_root: Path | None = None) -> Path:
    return run_dir(profile, script_slug, content_root=content_root) / BRIEF_FILENAME


def validate(doc: object) -> list[str]:
    """Schema errors plus the provenance rule the schema alone cannot express.

    The schema requires ``value`` and ``source`` on each decision. This adds the reading that makes
    an all-default brief honest: a decision present with neither a value nor a source is refused
    rather than treated as "not decided yet", because a downstream skill reading this file has no
    way to tell those apart.
    """
    errors = list(minischema.validate(doc, load_schema()))
    if not isinstance(doc, dict):
        return errors
    decisions = doc.get("decisions")
    if not isinstance(decisions, dict):
        return errors
    for name in DECISIONS:
        entry = decisions.get(name)
        if entry is None:
            continue
        if not isinstance(entry, dict):
            errors.append(f"decisions.{name} is not a decision object")
            continue
        if entry.get("value") in (None, "", [], {}) or not entry.get("source"):
            errors.append(
                f"decisions.{name} carries no value and/or no source — a decision nobody made "
                "and a decision made by default must not read the same to the next skill"
            )
    return errors


def write(path: Path, doc: dict, *, content_root: Path | None = None) -> Path:
    """Validate, then write. A brief that does not validate leaves no file behind.

    Refused at WRITE time rather than read time on purpose: a half-written brief on disk is read by
    ``video-script`` as a complete one, and the contradiction surfaces after the spend it exists to
    precede.
    """
    root = (resolve_content_root() if content_root is None else Path(content_root)).resolve()
    resolved = Path(path).expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise BriefError(
            f"refusing to write a brief outside the resolved content root: {resolved} (root: {root})"
        ) from None

    errors = validate(doc)
    if errors:
        raise BriefError("brief does not validate:\n  " + "\n  ".join(errors))

    resolved.parent.mkdir(parents=True, exist_ok=True)
    tmp = resolved.with_suffix(resolved.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, resolved)
    return resolved


def all_defaulted(doc: dict) -> bool:
    """True when no decision was made by a person — what the twin has to say out loud."""
    decisions = doc.get("decisions") or {}
    sources = {
        (decisions.get(name) or {}).get("source")
        for name in DECISIONS
        if isinstance(decisions.get(name), dict)
    }
    return bool(sources) and sources <= {"profile_default", "derived", "model"}


def _render_value(name: str, value: object) -> str:
    if name == "cover" and isinstance(value, dict):
        return f"{value.get('headline', '')} — {value.get('subject', '')}"
    if name == "outlier_structure" and isinstance(value, dict):
        return f"{value.get('name', '')} ({' → '.join(value.get('beats') or [])})"
    if name == "cheapest_medium" and isinstance(value, dict):
        return f"{value.get('chosen', '')} — {value.get('reason', '')}"
    if name == "invariant" and isinstance(value, dict):
        return f"{value.get('look', '')} @ {value.get('aspect_ratio', '')}"
    if name == "sampling_curve" and isinstance(value, dict):
        curve = ", ".join(f"shot {r.get('shot')}×{r.get('n')}" for r in value.get("per_shot") or [])
        return f"{curve} — picked by {value.get('criterion', '')}"
    if name == "visual_hook" and isinstance(value, dict):
        # The expression rides along when there is one: it is the only face direction in the whole
        # brief, and a twin that silently drops it is how "nobody chose a face" reads as a choice.
        hook = f"{value.get('subject', '')}, {value.get('framing', '')}"
        return f"{hook} — {value['expression']}" if value.get("expression") else hook
    if isinstance(value, list):
        return "; ".join(str(v) for v in value)
    return str(value)


def markdown_twin(doc: dict) -> str:
    """Nine lines, one per decision, each with its source — what a non-marketer reads on a phone.

    Derived from the JSON every time it is asked for. Editing the twin changes nothing any skill
    reads, which is the property that keeps one of them from becoming a second source of truth.
    """
    decisions = doc.get("decisions") or {}
    lines = [
        f"# Creator brief — {doc.get('script_slug', '')}",
        "",
        f"Item `{doc.get('item_id', '')}` · profile `{doc.get('profile', '')}`"
        + (f" · product `{doc['product']}`" if doc.get("product") else "")
        + f" · written {doc.get('written_on', '')}",
        "",
        "<!-- GENERATED from brief.json by gtm_core.creator_brief — edits here change nothing. -->",
        "",
    ]
    for name in DECISIONS:
        entry = decisions.get(name)
        if not isinstance(entry, dict):
            continue
        rendered = _render_value(name, entry.get("value"))
        lines.append(f"- **{_DECISION_TITLES[name]}** — {rendered}  _({entry.get('source')})_")
    lines.append("")
    if all_defaulted(doc):
        lines.append(
            "> Every decision here came from the profile or was derived. Nobody chose any of it — "
            "read it as a starting point, not as a settled brief."
        )
        lines.append("")
    return "\n".join(lines)


def write_twin(doc: dict, *, path: Path) -> Path:
    resolved = Path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(markdown_twin(doc), encoding="utf-8")
    return resolved


def check_against_shotlist(brief: dict, shots: dict) -> list[str]:
    """Corroborate the brief against the shot list it governs (§R11). Empty means consistent.

    The witness is a different artifact written by a different step, so a brief field that is simply
    wrong cannot pass by restating itself. Each contradiction is a refusal, not a warning, because
    it means the run is about to spend against a decision nobody actually took. (This docstring
    counted them until 2026-09-07; a count in prose beside a list that grows goes stale on the
    next commit, so the list is left to speak for itself.)
    """
    problems: list[str] = []
    decisions = brief.get("decisions") or {}

    declared_mode = ((decisions.get("capture_mode") or {}).get("value")) or "rendered"
    shots_mode = str(shots.get("capture_mode") or "rendered")
    if declared_mode != shots_mode:
        problems.append(
            f"the brief decided capture_mode={declared_mode!r} and the shot list says "
            f"{shots_mode!r} — one of them is about to be shot the wrong way"
        )

    shot_list = shots.get("shots") if isinstance(shots.get("shots"), list) else []
    if declared_mode == "live_action":
        engine_bearing = [
            i
            for i, shot in enumerate(shot_list, 1)
            if isinstance(shot, dict) and (shot.get("engine") or shot.get("provider_model"))
        ]
        if engine_bearing:
            problems.append(
                "the brief decided capture_mode='live_action' but shot(s) "
                f"{engine_bearing} name a render engine — a live-action lane resolves no engine, "
                "so this list would be linted under the wrong contract"
            )
        if shots.get("synthetic_disclosure"):
            problems.append(
                "the brief decided capture_mode='live_action' but the shot list declares "
                "synthetic_disclosure — nothing on this lane is synthesised, and claiming it is a "
                "false statement about provenance"
            )

    invariant = (decisions.get("invariant") or {}).get("value") or {}
    ratio = invariant.get("aspect_ratio")
    ratios = shots.get("deliverable_ratios")
    if ratio and isinstance(ratios, list) and ratios and ratio not in ratios:
        problems.append(
            f"the brief's invariant chose aspect_ratio={ratio!r} and the shot list delivers "
            f"{ratios} — the composition was framed for a ratio nothing renders"
        )

    look = invariant.get("look")
    scaffold = shots.get("style_scaffold") or {}
    scaffold_look = str(scaffold.get("look") or "")
    if look and scaffold_look and look.strip().lower() not in scaffold_look.strip().lower():
        problems.append(
            f"the brief's invariant look ({look!r}) does not appear in style_scaffold.look "
            f"({scaffold_look!r}) — the constant the operator chose is not the one being rendered"
        )

    curve = (decisions.get("sampling_curve") or {}).get("value") or {}
    n_shots = len(shot_list)
    if n_shots:
        over = [
            row.get("shot")
            for row in curve.get("per_shot") or []
            if isinstance(row.get("shot"), int) and row["shot"] > n_shots
        ]
        if over:
            problems.append(
                f"the sampling curve budgets shot(s) {over} and the list has {n_shots} — "
                "the spend was planned against a shot that does not exist"
            )

    # The hook frame is the one shot both artifacts describe, so it is the only place a face
    # direction can be corroborated at all. ABSENCE only: the two are written by different steps
    # in different words, and comparing their text would refuse every legitimate rewording. An
    # unspecified expression inherits whatever the identity anchor's source photo holds, which is
    # the documented cause of a first frame that renders flat.
    hook = (decisions.get("visual_hook") or {}).get("value") or {}
    if shot_list and str(hook.get("expression") or "").strip():
        first = shot_list[0] if isinstance(shot_list[0], dict) else {}
        if not str(first.get("expression") or "").strip():
            problems.append(
                "the brief specifies an expression for the first frame and shot 1 of the list "
                "carries none — the hook renders with whatever face the identity anchor's source "
                "photo happens to hold, which is not the one that was decided"
            )
    return problems


#: What a hollow brief looks like: schema-valid, every field filled, and nobody decided anything.
_HOLLOW_STRUCTURE_WORDS = frozenset(
    {"usual", "standard", "normal", "typical", "generic", "default", "same", "tbd", "todo"}
)


def hollow_findings(doc: dict) -> list[dict]:
    """Deterministic "this validates and is still empty" findings. Diagnostic, never a gate.

    A brief can be schema-valid and bad — a cover nobody would tap, an "outlier structure" that is
    the average. This is the half that runs with no API key; the fresh-context reader
    (``score_drafts(kind="brief")``) is the half that has judgement.
    """
    findings: list[dict] = []
    decisions = doc.get("decisions") or {}

    structure = (decisions.get("outlier_structure") or {}).get("value") or {}
    name = str(structure.get("name") or "").strip()
    if not name or set(name.lower().split()) & _HOLLOW_STRUCTURE_WORDS:
        findings.append(
            {
                "axis": "structure_is_named_not_described",
                "fix": f"{name!r} is not a structure — name the beat order you are committing to "
                "(e.g. 'cold open → reversal → proof → ask'), or mine outliers again",
            }
        )
    if len(structure.get("beats") or []) < 2:
        findings.append(
            {
                "axis": "structure_is_named_not_described",
                "fix": "a structure with fewer than two beats is a label, not an order",
            }
        )

    cover = (decisions.get("cover") or {}).get("value") or {}
    if not str(cover.get("headline") or "").strip() or not str(cover.get("subject") or "").strip():
        findings.append(
            {
                "axis": "cover_is_a_promise",
                "fix": "the cover needs a subject and a headline — it is the promise the video "
                "then has to keep, and it is designed before the content, not after it",
            }
        )

    invariant = (decisions.get("invariant") or {}).get("value") or {}
    if len(str(invariant.get("look") or "").split()) < 3:
        findings.append(
            {
                "axis": "the_invariant_is_visible",
                "fix": "the invariant must be something a viewer could SEE holding across shots "
                "(grade, depth of field, camera character) — one word is not visible",
            }
        )

    hook = (decisions.get("visual_hook") or {}).get("value") or {}
    if not str(hook.get("subject") or "").strip() or not str(hook.get("framing") or "").strip():
        findings.append(
            {
                "axis": "first_frame_is_recognisable",
                "fix": "the first frame is a shot spec, not a vibe — subject and framing at least",
            }
        )

    medium = (decisions.get("cheapest_medium") or {}).get("value") or {}
    if len(str(medium.get("reason") or "").split()) < 4:
        findings.append(
            {
                "axis": "cheapest_medium_reason_holds",
                "fix": "say why video beats a carousel or a still for THIS piece — "
                "'because it is a video' is the answer that makes the question pointless",
            }
        )
    return findings


def _read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _dispatch(args: argparse.Namespace) -> int:
    """Run one parsed subcommand.

    Split from :func:`main` so the verb table and the error table each stay small enough to read
    in one go; ``main`` owns the exit-code mapping, this owns the work.
    """
    if args.cmd == "write":
        doc = _read_json(args.doc)
        slug = args.script_slug or str(doc.get("script_slug") or "")
        path = brief_path(args.profile, slug)
        written = write(path, doc)
        twin = write_twin(doc, path=written.parent / TWIN_FILENAME)
        print(json.dumps({"brief": str(written), "twin": str(twin)}, indent=2))
        return 0

    if args.cmd == "validate":
        errors = validate(_read_json(args.path))
        print(json.dumps({"path": str(args.path), "ok": not errors, "errors": errors}, indent=2))
        return 2 if errors else 0

    if args.cmd == "twin":
        print(markdown_twin(_read_json(args.path)), end="")
        return 0

    if args.cmd == "check":
        problems = check_against_shotlist(_read_json(args.brief), _read_json(args.shots))
        print(json.dumps({"ok": not problems, "problems": problems}, indent=2))
        return 2 if problems else 0

    if args.cmd == "hollow":
        findings = hollow_findings(_read_json(args.path))
        print(json.dumps({"findings": findings}, indent=2))
        return 0

    return 1


def main(argv: list[str] | None = None) -> int:
    """CLI. Every write goes through here — never Edit/Write on a brief.

    Exit codes: 0 ok; 1 a real error (unreadable input); 2 the brief is invalid, contradicts its
    shot list, or would land outside the content root.
    """
    parser = argparse.ArgumentParser(
        prog="uv run python -m gtm_core.creator_brief",
        description="Write, render and cross-examine a creator brief (free, pre-spend).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_write = sub.add_parser(
        "write",
        help="validate a brief JSON document and write it + its twin",
        description="Validate a brief and write it, with its markdown twin, into the run folder.",
    )
    p_write.add_argument("--profile", required=True)
    p_write.add_argument("--doc", required=True, type=Path, help="the brief document to write")
    p_write.add_argument("--script-slug", default="", help="defaults to the doc's script_slug")

    p_validate = sub.add_parser(
        "validate",
        help="schema + provenance check, no write",
        description="Schema and provenance check on a brief. Writes nothing.",
    )
    p_validate.add_argument("path", type=Path)

    p_twin = sub.add_parser(
        "twin",
        help="render the markdown twin to stdout",
        description="Render the markdown twin of a brief to stdout. Derived; never a source.",
    )
    p_twin.add_argument("path", type=Path)

    p_check = sub.add_parser(
        "check",
        help="cross-examine a brief against its shot list (§R11)",
        description=(
            "Cross-examine a brief against the shot list it governs. Exit 2 on a contradiction, "
            "so a caller fails closed rather than warning."
        ),
    )
    p_check.add_argument("--brief", required=True, type=Path)
    p_check.add_argument("--shots", required=True, type=Path)

    p_hollow = sub.add_parser(
        "hollow",
        help="deterministic 'validates and is still empty' findings",
        description="Deterministic findings for a brief that validates and is still empty.",
    )
    p_hollow.add_argument("path", type=Path)

    args = parser.parse_args(argv)

    try:
        return _dispatch(args)
    except BriefError as exc:
        print(f"[creator-brief] {exc}", file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[creator-brief] {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:  # _safe_segment
        print(f"[creator-brief] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
