"""Does a REUSED REAL ASSET show a product name the script has not said yet?

THE BUG THIS PREVENTS. A shot at 1:48 reused a real product-console screenshot whose visible DID
string read `…:fabric-gateway-…`, naming the product 75 seconds before the script introduces it
out loud at 3:02. Every existing gate passed it, and correctly so: the asset was real, on-brand,
current, and exactly the right kind of evidence for that beat. What no gate asked was what the
picture SAYS. A generated card only ever carries text this pipeline wrote; a real screenshot
carries text nobody here wrote and nobody here read.

THE SPLIT. The perception is a model's job — the `vision` MCP tool's `extract_text` reads the
pixels, cheaply, on Haiku. Everything after that is deterministic and lives here: build the
spoken timeline from the shot list, decide which vocabulary terms are visible, and find each
term's FIRST verbal mention. A judgement ("is an 8px string in a URL bar really a spoiler?")
stays with the operator, at the storyboard gate.

THE VOCABULARY IS CLOSED, AND THAT IS THE SECURITY PROPERTY. Terms come from the brand kit, the
product registry, and explicit `--extra-term` flags — never from the extracted text. A screenshot
therefore cannot introduce a term, widen the check, or steer it. The OCR is SEARCHED, never
believed (§R5): its text is data, and the only thing done with it is a membership test.

The extraction is passed as a FILE PATH, never as an argv string: it is untrusted, arbitrary
length, and may contain newlines and `--`-prefixed lines. In output, `evidence` is truncated and
stripped of control characters, so a directive written inside a screenshot cannot dress itself up
as tool output.

CLI::

    python -m gtm_core.spoiler_check --shots <shots.json> --shot-n 7 \\
        --extracted-text-file <extract.txt> [--kit-json <kit>] [--extra-term "Fabric Gateway"] \\
        [--min-lead-s 0] [--json]
    python -m gtm_core.spoiler_check accept --profile P --slug S --shot-n 7 \\
        --term "Fabric Gateway" --by <who> --at <ISO-8601> --reason "<why>"

Exit 0 clean, 1 findings, 2 bad or EMPTY input — an asset that was not read is not a pass.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

#: How much of the extracted line is quoted back as evidence.
EVIDENCE_CHARS = 120


class SpoilerCheckError(ValueError):
    """The check could not be run — which is never the same as the check passing."""


@dataclass(frozen=True)
class SpokenBeat:
    n: int
    shot_id: str
    start_s: float
    end_s: float
    text: str


@dataclass(frozen=True)
class Spoiler:
    term: str
    slug: str
    shot_n: int
    shot_id: str
    shot_start_s: float
    shot_tc: str
    first_spoken_s: float | None
    first_spoken_shot_n: int | None
    first_spoken_tc: str | None
    #: first_spoken_s - shot_start_s. Positive means the picture is ahead of the voice.
    lead_s: float | None
    #: "spoiler" | "never_spoken"
    severity: str
    evidence: str


def format_tc(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


# ── normalisation: one algorithm for the picture and for the voice ───────────────────────

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slug_tokens(text: str) -> list[str]:
    """Text -> its `-`-separated tokens, CamelCase split first.

    This is what catches the reported bug. ``arn:aws:iam::123:role/fabric-gateway-prod`` becomes
    ``[arn, aws, iam, 123, role, fabric, gateway, prod]``, which CONTAINS the run
    ``[fabric, gateway]`` — and so do ``FabricGateway``, ``Fabric_Gateway``, ``fabric gateway``
    and ``FABRIC-GATEWAY``. Run equality is what a raw substring search would lose: it also means
    ``prefabric-gateway`` (tokens ``[prefabric, gateway]``) is correctly NOT a match.
    """
    spaced = _CAMEL.sub(" ", text)
    return [t for t in _NON_ALNUM.sub("-", spaced.lower()).split("-") if t]


def _contains_run(haystack: Sequence[str], needle: Sequence[str]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    n = len(needle)
    return any(list(haystack[i : i + n]) == list(needle) for i in range(len(haystack) - n + 1))


def term_slug(term: str) -> str:
    return "-".join(slug_tokens(term))


# ── the spoken timeline ──────────────────────────────────────────────────────────────────


def beats_from_shotlist(doc: dict) -> list[SpokenBeat]:
    """Cumulative start/end per shot, in shot order.

    Prefers the MEASURED ``vo_seconds`` over the asked-for ``duration_s`` wherever it exists —
    the same preference ``gtm_core.shots_lint``'s parity check already relies on. A timeline built
    from planned durations drifts from the film that actually exists, and this check is about
    when a viewer HEARS something.
    """
    shots = doc.get("shots")
    if not isinstance(shots, list) or not shots:
        raise SpoilerCheckError("shot list has no non-empty 'shots' array")
    beats: list[SpokenBeat] = []
    cursor = 0.0
    for i, shot in enumerate(shots):
        # `.get(a, default)` returns None for an EXPLICIT null, so a shot written as
        # {"vo_seconds": null, "duration_s": 5.0} used to be refused despite carrying a perfectly
        # usable duration. Prefer the measured value only when it is actually a value.
        raw = shot.get("vo_seconds")
        if raw is None:
            raw = shot.get("duration_s")
        try:
            length = float(raw)
        except (TypeError, ValueError):
            raise SpoilerCheckError(
                f"shot[{i}] has neither a numeric 'vo_seconds' nor 'duration_s' — a timeline "
                "cannot be built past it, and a partial timeline would silently report every "
                "later shot at the wrong time"
            ) from None
        beats.append(
            SpokenBeat(
                n=i,
                shot_id=str(shot.get("id") or shot.get("n") or f"shot-{i:02d}"),
                start_s=cursor,
                end_s=cursor + length,
                text=str(shot.get("spoken") or ""),
            )
        )
        cursor += length
    return beats


def first_spoken(beats: Sequence[SpokenBeat], slug: str) -> tuple[int, float] | None:
    """(beat index, start_s) of the first beat whose spoken line contains this term, or None.

    Beat-level, not word-level: the answer is "in which beat is it first heard". A words.json
    sidecar (:mod:`gtm_core.vo_timings`) would tighten this from +/-duration_s to +/-50ms, and the
    signature is shaped so that is an additive change rather than a rewrite.
    """
    needle = slug_tokens(slug)
    for beat in beats:
        if beat.text and _contains_run(slug_tokens(beat.text), needle):
            return beat.n, beat.start_s
    return None


# ── the vocabulary, which is closed ──────────────────────────────────────────────────────


def product_vocabulary(kit: dict, *, extra: Sequence[str] = ()) -> list[str]:
    """The terms this check may look for — from the profile's OWN kit, plus explicit extras.

    Never from the extracted text. That is the property that makes acting on this output safe: a
    screenshot can contain anything, and nothing it contains becomes a term.
    """
    terms: list[str] = []

    def _add(value: object) -> None:
        if isinstance(value, str) and value.strip():
            terms.append(value.strip())
        elif isinstance(value, list):
            for v in value:
                _add(v)

    products = kit.get("products")
    if isinstance(products, dict):
        for slug, entry in products.items():
            _add(slug)
            if isinstance(entry, dict):
                _add(entry.get("name"))
                _add(entry.get("aliases"))
    elif isinstance(products, list):
        for entry in products:
            _add(entry if isinstance(entry, str) else (entry or {}).get("name"))

    brand = kit.get("brand")
    if isinstance(brand, dict):
        _add(brand.get("product_names"))
        _add(brand.get("aliases"))
    _add(list(extra))

    seen, out = set(), []
    for t in terms:
        s = term_slug(t)
        if s and s not in seen:
            seen.add(s)
            out.append(t)
    return out


# ── the check ────────────────────────────────────────────────────────────────────────────


def _evidence_line(extracted_text: str, needle: Sequence[str]) -> str:
    for line in extracted_text.splitlines():
        if _contains_run(slug_tokens(line), needle):
            clean = "".join(ch for ch in line if ch.isprintable()).strip()
            return clean[:EVIDENCE_CHARS]
    return ""


def check_shot(
    *,
    extracted_text: str,
    shot_n: int,
    beats: Sequence[SpokenBeat],
    vocabulary: Sequence[str],
    min_lead_s: float = 0.0,
) -> list[Spoiler]:
    """Which vocabulary terms this shot's asset shows before the script says them."""
    if shot_n < 0 or shot_n >= len(beats):
        raise SpoilerCheckError(f"shot {shot_n} is not in this shot list (0..{len(beats) - 1})")
    if not extracted_text.strip():
        raise SpoilerCheckError(
            "the extracted text is empty — the asset was not read, and that is NOT a pass. "
            "`extract_text` never raises; it returns '[vision-error] ...' or nothing at all, so "
            "an unread asset and a clean one look identical unless this refuses."
        )
    if extracted_text.lstrip().startswith("[vision-error]"):
        raise SpoilerCheckError(
            f"the vision pass failed and returned: {extracted_text.strip()[:200]!r}. "
            "This shot is NOT CHECKED — report it as such rather than as clear."
        )

    shot = beats[shot_n]
    visible = slug_tokens(extracted_text)
    findings: list[Spoiler] = []
    for term in vocabulary:
        needle = slug_tokens(term)
        if not _contains_run(visible, needle):
            continue
        hit = first_spoken(beats, term)
        if hit is None:
            findings.append(
                Spoiler(
                    term=term,
                    slug=term_slug(term),
                    shot_n=shot_n,
                    shot_id=shot.shot_id,
                    shot_start_s=shot.start_s,
                    shot_tc=format_tc(shot.start_s),
                    first_spoken_s=None,
                    first_spoken_shot_n=None,
                    first_spoken_tc=None,
                    lead_s=None,
                    severity="never_spoken",
                    evidence=_evidence_line(extracted_text, needle),
                )
            )
            continue
        beat_n, spoken_s = hit
        if spoken_s > shot.start_s + min_lead_s:
            findings.append(
                Spoiler(
                    term=term,
                    slug=term_slug(term),
                    shot_n=shot_n,
                    shot_id=shot.shot_id,
                    shot_start_s=shot.start_s,
                    shot_tc=format_tc(shot.start_s),
                    first_spoken_s=spoken_s,
                    first_spoken_shot_n=beat_n,
                    first_spoken_tc=format_tc(spoken_s),
                    lead_s=round(spoken_s - shot.start_s, 3),
                    severity="spoiler",
                    evidence=_evidence_line(extracted_text, needle),
                )
            )
    return findings


# ── shipping anyway is a recorded decision ───────────────────────────────────────────────


def mirror_acceptance(
    storyboard_path: Path, finding: Spoiler, reason: str, *, by: str, at: str
) -> None:
    """Write the acceptance into the storyboard entry the approval gate actually reads.

    WITHOUT THIS THE LOOP IS BROKEN, and broken in the worst direction: `gtm_core.storyboard
    approve` refuses on `entry["spoiler_check"]["findings"]` that carry no matching entry in
    `["accepted"]`, while :func:`record_accepted` writes only to `outcomes.jsonl`. An operator
    following Step 2.6 would run `--accept`, watch it succeed, and then be refused at approval
    with nothing explaining why their acceptance did not count — and the only way through would
    be `--allow-spoilers`, a DIFFERENT escape hatch that records a different thing.

    Two writes, on purpose: `outcomes.jsonl` is the durable audit trail, and the storyboard entry
    is what the renderer's own gate reads. Neither substitutes for the other.
    """
    doc = json.loads(storyboard_path.read_text(encoding="utf-8"))
    entries = doc.get("entries") or []
    hit = None
    for entry in entries:
        block = entry.get("spoiler_check") if isinstance(entry, dict) else None
        if not isinstance(block, dict):
            continue
        if any(
            isinstance(f, dict) and str(f.get("term", "")).strip().lower() == finding.term.lower()
            for f in block.get("findings") or []
        ):
            hit = block
            break
    if hit is None:
        raise ValueError(
            f"{storyboard_path} carries no spoiler_check finding for {finding.term!r}; there is "
            "nothing there to accept. Run the check and record its findings on the entry first."
        )
    accepted = hit.setdefault("accepted", [])
    if any(str(a.get("term", "")).strip().lower() == finding.term.lower() for a in accepted):
        return
    accepted.append(
        {"term": finding.term, "by": by.strip(), "at": at.strip(), "reason": reason.strip()}
    )
    storyboard_path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def record_accepted(
    content_root: Path,
    profile: str,
    finding: Spoiler,
    reason: str,
    *,
    by: str,
    at: str,
    slug: str,
) -> None:
    """Append an audited ``spoiler_accepted`` row, the same shape
    :func:`gtm_core.hook_score.record_override` uses for its own fail-closed override.

    A blank reason is refused: the decision must be auditable, and "we looked at it" is not a
    record. ``at`` is when the OPERATOR decided, not when this command ran.

    This is the AUDIT half only. The gate half is :func:`mirror_acceptance`, which writes into the
    storyboard entry `gtm_core.storyboard approve` reads — an acceptance recorded here alone does
    not unblock a render.
    """
    from . import outcomes as oc

    if not reason.strip():
        raise ValueError("--reason must not be blank — the decision must be audited")
    if not by.strip() or not at.strip():
        raise ValueError("--by and --at are required; --at is when the operator decided")
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "video",
            "outcome": "spoiler_accepted",
            "value": 1,
            "tags": [
                f"slug:{slug}",
                f"shot:{finding.shot_n}",
                f"term:{finding.slug}",
                f"severity:{finding.severity}",
            ],
            "meta": {
                "by": by.strip(),
                "at": at.strip(),
                "reason": reason.strip(),
                "shot_tc": finding.shot_tc,
                "first_spoken_tc": finding.first_spoken_tc,
                "lead_s": finding.lead_s,
                "evidence": finding.evidence,
            },
        },
    )


# ── CLI ──────────────────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m gtm_core.spoiler_check")
    parser.add_argument("--shots", type=Path, help="the shot list JSON")
    parser.add_argument("--shot-n", type=int, help="0-based index of the shot being checked")
    parser.add_argument(
        "--extracted-text-file",
        type=Path,
        help="the `vision` extract_text output, saved verbatim. A PATH, never argv: the text is "
        "untrusted, unbounded, and may contain newlines and --flag-shaped lines.",
    )
    parser.add_argument("--kit-json", type=Path, default=None, help="resolved brand kit JSON")
    parser.add_argument("--extra-term", action="append", default=[])
    parser.add_argument("--min-lead-s", type=float, default=0.0)
    parser.add_argument("--json", action="store_true", dest="as_json")

    accept = parser.add_argument_group("accept mode")
    accept.add_argument("--accept", action="store_true", help="record a ship-anyway decision")
    accept.add_argument("--profile", default=None)
    accept.add_argument("--slug", default=None)
    accept.add_argument("--term", default=None)
    accept.add_argument("--by", default=None)
    accept.add_argument("--at", default=None)
    accept.add_argument("--reason", default=None)
    accept.add_argument("--content-root", type=Path, default=None)
    accept.add_argument(
        "--storyboard",
        type=Path,
        default=None,
        help="storyboard.json to mirror the acceptance into. Without it the decision is audited "
        "but the approval gate still refuses, because the gate reads the storyboard entry",
    )

    args = parser.parse_args(argv)

    for required in ("shots", "shot_n", "extracted_text_file"):
        if getattr(args, required) is None:
            print(f"spoiler-check: --{required.replace('_', '-')} is required", file=sys.stderr)
            return 2
    try:
        doc = json.loads(args.shots.read_text(encoding="utf-8"))
        extracted = args.extracted_text_file.read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError) as exc:
        print(f"spoiler-check: {exc}", file=sys.stderr)
        return 2
    kit = {}
    if args.kit_json is not None:
        try:
            kit = json.loads(args.kit_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"spoiler-check: could not read --kit-json: {exc}", file=sys.stderr)
            return 2

    vocabulary = product_vocabulary(kit, extra=args.extra_term)
    if not vocabulary:
        print(
            "spoiler-check: the vocabulary is empty — nothing to look for, which is not the same "
            "as nothing being visible. Pass --kit-json, or name terms with --extra-term.",
            file=sys.stderr,
        )
        return 2

    try:
        beats = beats_from_shotlist(doc)
        findings = check_shot(
            extracted_text=extracted,
            shot_n=args.shot_n,
            beats=beats,
            vocabulary=vocabulary,
            min_lead_s=args.min_lead_s,
        )
    except SpoilerCheckError as exc:
        print(f"spoiler-check: {exc}", file=sys.stderr)
        return 2

    if args.accept:
        if not all([args.profile, args.slug, args.term, args.by, args.at, args.reason]):
            print(
                "spoiler-check --accept needs --profile --slug --term --by --at --reason",
                file=sys.stderr,
            )
            return 2
        target = next((f for f in findings if f.slug == term_slug(args.term)), None)
        if target is None:
            # An acceptance is only meaningful against a finding — the analogue of
            # record_override's prior_has_data guard.
            print(
                f"spoiler-check: no finding for {args.term!r} on shot {args.shot_n}; there is "
                "nothing to accept. An acceptance recorded against an unchecked or clean shot is "
                "a decision about nothing.",
                file=sys.stderr,
            )
            return 2
        from .paths import resolve_content_root

        root = args.content_root or resolve_content_root()
        try:
            record_accepted(
                root, args.profile, target, args.reason, by=args.by, at=args.at, slug=args.slug
            )
            if args.storyboard is not None:
                mirror_acceptance(args.storyboard, target, args.reason, by=args.by, at=args.at)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            print(f"spoiler-check: {exc}", file=sys.stderr)
            return 2
        print(f"spoiler-check: recorded spoiler_accepted for {target.slug} on shot {args.shot_n}")
        if args.storyboard is None:
            print(
                "spoiler-check: NOTE — no --storyboard given, so this acceptance is audited but "
                "does NOT unblock `gtm_core.storyboard approve`, which reads the storyboard "
                "entry. Pass --storyboard, or approve with --allow-spoilers and its own reason.",
                file=sys.stderr,
            )
        return 0

    if args.as_json:
        print(
            json.dumps(
                {
                    "shot_n": args.shot_n,
                    "vocabulary": vocabulary,
                    "findings": [asdict(f) for f in findings],
                },
                indent=2,
            )
        )
    elif not findings:
        print(f"spoiler-check: shot {args.shot_n} clean against {len(vocabulary)} terms")
    else:
        for f in findings:
            when = (
                f"first spoken at {f.first_spoken_tc} (+{f.lead_s:.1f}s)"
                if f.severity == "spoiler"
                else "NEVER spoken in this script"
            )
            print(f"shot {f.shot_n} @ {f.shot_tc}: {f.term!r} visible; {when}")
            if f.evidence:
                print(f"    evidence: {f.evidence!r}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
