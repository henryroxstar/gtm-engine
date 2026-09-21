import re
from pathlib import Path


def test_pending_md_sections_not_fully_closed():
    """
    Enforces the archiving rules for PENDING.md:
    1. Fully closed sections must be archived.
    2. Sections marked as (CLOSED) or (✅ complete) must be archived.
    3. Large epics with many completed items should have their narrative archived and follow-ups extracted.
    """
    repo_root = Path(__file__).parent.parent.parent
    pending_path = repo_root / "PENDING.md"

    if not pending_path.exists():
        return

    content = pending_path.read_text()

    # Split by ## or ###
    parts = re.split(r"^(#{2,3}\s+.*)", content, flags=re.MULTILINE)

    violations = []

    for i in range(1, len(parts), 2):
        header = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""

        # Skip already archived stubs
        if "> **Archived" in body or "> **Closed" in body and len(body.splitlines()) < 20:
            if not ("[ ]" in body or "[x]" in body):
                continue

        open_count = body.count("[ ]")
        closed_count = body.count("[x]")
        lines = len(body.splitlines())

        # 1. Explicitly closed in header
        if re.search(r"\(CLOSED|\(✅ complete", header, re.IGNORECASE):
            violations.append(f"{header} (Header marked closed but not properly archived)")
            continue

        # 2. All checkboxes closed
        if closed_count > 0 and open_count == 0:
            violations.append(f"{header} (All tasks closed, needs archiving)")
            continue

        # 3. Extract Follow-ups Rule: Large sections that are mostly done
        if lines > 150 and closed_count > open_count:
            violations.append(
                f"{header} (Epic is {lines} lines with {closed_count} closed tasks. Extract the {open_count} follow-ups and archive the narrative)"
            )
            continue

    assert not violations, (
        "Found sections in PENDING.md that violate archiving rules.\n"
        "Rule: 'Extract Follow-ups Rule: When a major initiative is shipped, do not leave its narrative here just because minor follow-ups remain. Extract the open checkboxes and archive the completed epic.'\n\n"
        "Sections to fix:\n" + "\n".join(f"- {v}" for v in violations)
    )
