"""``icp propose`` — add / amend / retire lines, in the ``lanes suggest-rules`` shape.

The operator's answer to a weak hand-me-down ICP. Three labelled sections; each line names
the **file**, the **edit**, and the **evidence it rests on**, and nothing it cannot support.

**It never writes.** Three mechanisms in this repo already propose and refuse to write
(``lanes suggest-rules``, ``hooks promote/demote --evidence``, ``knowledge_staging``'s
stage → diff → promote). Making this the first one that applies its own suggestion would mean
a model-adjacent command mutating the file that decides who the company sells to. Operator
promotion is the decision; the diff is what they review.

**A retire is an instruction, not a pasteable block.** ``lanes suggest-rules`` can emit
paste-ready TOML because it only ever proposes *additions*, and a deletion has no such form.
Emitting something patch-shaped that is not a patch would be worse than prose.

**Retire rests only on a measured structural property** — a hit count, a mappability, a score
spread — and **never on a reply rate**. At the volumes this repo currently has, a reply-rate
retire is noise wearing a decision's clothes.
"""

from __future__ import annotations

#: Sections in the order an operator acts on them: stop wasting the scorer's time first,
#: then fix what is half-working, then add what is missing.
_SECTIONS = ("RETIRE", "AMEND", "ADD")

_FOOTER = (
    "(the tool never writes any of these — edit the file, or stage it:\n"
    "  uv run python -m gtm_core.knowledge_staging stage --profile <p> --topic icp-scoring.toml ...)"
)


def _proposal_for(finding: str) -> tuple[str, str] | None:
    """Map one finding to ``(section, line)``, or ``None`` when it supports no proposal.

    Deliberately partial: a finding this cannot turn into a named edit produces **nothing**
    rather than a vague suggestion. A line an operator cannot act on without asking a question
    is the 2026-08-19 "unreadable warnings" shape at a smaller scale.
    """
    code, _, detail = finding.partition(": ")

    if code == "criterion-unqueryable":
        if "select nothing" in detail:
            return (
                "RETIRE",
                f"{detail}\n"
                f"           they match no account, so they only cost the scorer time — "
                f"removing them changes no account's score",
            )
        if "above the ceiling" in detail:
            return (
                "RETIRE",
                f"{detail}\n"
                f"           read the sample first: a phrase this broad is usually a category "
                f"label the market shares, not a cohort signal",
            )
        return None

    if code == "persona-unmapped":
        return (
            "ADD",
            f"{detail}\n"
            f"           either add a persona rule that resolves this label in "
            f"role-vocabulary.toml, or retire the matrix row — today it is copy owed to "
            f"nobody",
        )

    if code == "rubric-undiscriminating":
        return (
            "AMEND",
            f"{detail}\n"
            f"           give the cohorts that should win distinct weights, or add keywords "
            f"that actually fire on this backlog",
        )

    return None


def propose(findings: list[str]) -> str:
    """Render add/amend/retire proposals for a finding set. Byte-stable for a fixed input."""
    grouped: dict[str, list[str]] = {name: [] for name in _SECTIONS}
    for finding in findings:
        made = _proposal_for(finding)
        if made is not None:
            section, line = made
            grouped[section].append(line)

    if not any(grouped.values()):
        return (
            "no proposal — every criterion is queryable, every matrix persona maps to a seat, "
            "and the rubric separates the backlog.\n"
            "That is a structural result, not a verdict on whether this is the right ICP.\n"
        )

    out: list[str] = []
    for section in _SECTIONS:
        for line in grouped[section]:
            out.append(f"{section:<8} {line}")
    out.append("")
    out.append(_FOOTER)
    return "\n".join(out) + "\n"
