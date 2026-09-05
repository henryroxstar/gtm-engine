"""The defect report, the regeneration cap, and the staging precondition — feedback that
reaches the NEXT spec instead of stopping at a verdict word.

* :func:`defect_report` — per source (a judge JSONL is one cell/spec): verdict mix, the top
  normalised classes with a few evidence phrases, novel vs covered classes against the linter's
  rule list, and the operator's own guidance from the hold sheet's notes and salvage chips. The
  drafting skill reads the newest report before it writes; the regeneration session reads only
  this, never the old body.
* :func:`regeneration_count` — a spec's regeneration number, from its front block
  (``regeneration: N``) or by walking its ``regenerated_from:`` chain. Refused at
  :data:`REGENERATION_CAP`, in code, like the per-row repair cap.
* :func:`require_qa` — a spec may stage only when a merge-render QA record exists whose
  ``spec_sha256`` is the spec's CURRENT bytes and whose verdict is a PASS.

Evidence phrases are body text and bodies name people: the report lives under the content
root only, and the CLI refuses any other output path.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path

from .defects import defect_scope, normalize_defect_class
from .model import Adjudication

REGENERATION_CAP = 3
_REGEN_RE = re.compile(r"^regeneration:\s*(\d+)\s*$", re.MULTILINE | re.IGNORECASE)
_FROM_RE = re.compile(r"^regenerated_from:\s*(\S+)\s*$", re.MULTILINE | re.IGNORECASE)
_DATE_SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}(\.jsonl)?$")


def source_name(path: Path) -> str:
    """``sweep-normal-startup-2026-09-01.jsonl`` → ``sweep-normal-startup``."""
    return _DATE_SUFFIX.sub("", Path(path).name).removesuffix(".jsonl")


def defect_report(
    groups: dict[str, Sequence[Adjudication]],
    *,
    known: Iterable[str] = (),
    feedback: Sequence[dict] = (),
    lane_counts: dict[str, int] | None = None,
    max_evidence: int = 3,
    evidence_chars: int = 120,
) -> str:
    """Markdown. One section per source, then the operator-guidance section, which the
    regeneration session reads ABOVE the judge's notes."""
    have = {normalize_defect_class(k) for k in known if k and k.strip()}
    lines = ["# Defect report", ""]
    if lane_counts:
        lines.append("Lanes: " + " · ".join(f"{lane} {n}" for lane, n in lane_counts.items()) + "")
        lines.append("")
    for name in sorted(groups):
        recs = [r for r in groups[name] if not r.unscored]
        mix = Counter(r.verdict for r in recs)
        lines += [
            f"## {name}",
            "",
            "verdict mix: "
            + " · ".join(f"{v} {n}" for v, n in sorted(mix.items()))
            + f" (n={len(recs)})",
            "",
        ]
        classes: Counter[str] = Counter()
        evidence: dict[str, list[str]] = {}
        for r in recs:
            cls = normalize_defect_class(r.defect_class)
            if not cls or r.verdict == "send":
                continue
            classes[cls] += 1
            phrase = (r.evidence or r.note or "").strip().replace("\n", " ")
            if phrase and len(evidence.setdefault(cls, [])) < max_evidence:
                evidence[cls].append(phrase[:evidence_chars])
        if classes:
            lines.append("top defect classes (normalised · scope):")
            for cls, n in classes.most_common(8):
                lines.append(f"- `{cls}` · {defect_scope(cls)} · {n}")
                for ph in evidence.get(cls, []):
                    lines.append(f"  - “{ph}”")
            novel = [c for c in classes if have and c not in have]
            covered = [c for c in classes if c in have]
            if novel:
                lines.append(f"- novel (no rule covers): {', '.join(sorted(novel))}")
            if covered:
                lines.append(
                    f"- INERT GATES (a rule covers these and the judge found them anyway): {', '.join(sorted(covered))}"
                )
        else:
            lines.append("no defect classes on non-send rows")
        lines.append("")
    lines += ["## Operator guidance", ""]
    guidance = _guidance(feedback)
    lines += guidance or ["(no hold decisions with notes or salvage chips yet)"]
    lines.append("")
    lines += [
        "## How to read this",
        "",
        "- scope `argument` → the SPEC changes (a different fact, argument or capability); never paraphrase a failing sentence.",
        "- scope `contact` → the LIST changes (re-resolve the person); the copy is not the problem.",
        "- scope `account` → a human decided (hold sheet); do not argue it in copy.",
        "- Operator guidance outranks the judge's notes wherever they disagree.",
        "",
    ]
    return "\n".join(lines)


def _guidance(feedback: Sequence[dict]) -> list[str]:
    by_trigger: dict[str, Counter] = {}
    chips: Counter[str] = Counter()
    notes: list[str] = []
    for row in feedback:
        trig = str(row.get("trigger") or "")
        dec = str(row.get("decision") or "")
        if trig and dec:
            by_trigger.setdefault(trig, Counter())[dec] += 1
        kind = str(row.get("salvage_kind") or "")
        if kind:
            chips[kind] += 1
        note = str(row.get("note") or "").strip()
        if note and len(notes) < 20:
            notes.append(f"- [{trig or 'note'}] {note[:160]}")
    out: list[str] = []
    for trig, c in sorted(by_trigger.items()):
        out.append(f"- {trig}: " + ", ".join(f"{d} ×{n}" for d, n in c.most_common()))
    for kind, n in chips.most_common():
        out.append(f"- salvage asked for `{kind}` ×{n}")
    out += notes
    return out


def spec_diff(
    old_text: str,
    new_text: str,
    *,
    old_name: str,
    new_name: str,
    classes: Iterable[str] = (),
    before: dict[str, int] | None = None,
    after: dict[str, int] | None = None,
) -> str:
    """One page: the classes the regeneration targeted, the verdict mix before/after, and the
    unified diff of the two specs. Reported, never gated."""
    lines = [f"# Spec diff — {old_name} → {new_name}", ""]
    if classes:
        lines += ["targets: " + ", ".join(f"`{c}`" for c in classes), ""]
    if before is not None or after is not None:
        fmt = lambda d: " · ".join(f"{k} {v}" for k, v in sorted((d or {}).items())) or "—"  # noqa: E731
        lines += [f"verdict mix before: {fmt(before)}", f"verdict mix after:  {fmt(after)}", ""]
    lines += ["```diff"]
    lines += list(
        difflib.unified_diff(
            old_text.splitlines(),
            new_text.splitlines(),
            fromfile=old_name,
            tofile=new_name,
            lineterm="",
            n=2,
        )
    )
    lines += ["```", ""]
    return "\n".join(lines)


def regeneration_count(spec: Path, *, max_hops: int = 10) -> int:
    """How many times this spec has been regenerated. Reads ``regeneration: N``; otherwise walks
    ``regenerated_from:`` (each hop is one regeneration) — renaming a file does not reset it."""
    text = Path(spec).read_text(encoding="utf-8")
    m = _REGEN_RE.search(text)
    declared = int(m.group(1)) if m else 0
    hops = 0
    current = Path(spec)
    seen = {current.resolve()}
    while hops < max_hops:
        m = _FROM_RE.search(current.read_text(encoding="utf-8"))
        if not m:
            break
        parent = (
            (current.parent / m.group(1))
            if not Path(m.group(1)).is_absolute()
            else Path(m.group(1))
        )
        hops += 1
        if not parent.is_file() or parent.resolve() in seen:
            break
        seen.add(parent.resolve())
        current = parent
    return max(declared, hops)


def spec_sha16(spec: Path) -> str:
    return hashlib.sha256(Path(spec).read_bytes()).hexdigest()[:16]


def require_qa(spec: Path, qa_dir: Path) -> tuple[bool, str]:
    """``(ok, why)`` — a merge-render QA record for the spec's CURRENT bytes with a PASS."""
    want = spec_sha16(spec)
    stale = 0
    for path in sorted(Path(qa_dir).glob("*.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if rec.get("spec_sha256") != want:
            if Path(str(rec.get("spec", ""))).name == Path(spec).name:
                stale += 1
            continue
        if str(rec.get("verdict", "")).upper() == "PASS" and not rec.get("errors"):
            return True, str(path)
        return (
            False,
            f"{path.name} matches the spec's bytes but is not a PASS (errors={rec.get('errors')})",
        )
    if stale:
        return (
            False,
            f"{stale} QA record(s) name this spec but were linted against OTHER bytes — re-run the merge-render gate",
        )
    return (
        False,
        "no merge-render QA record for this spec's current bytes — run the gate with --json first",
    )
