"""Contract test for the learn skill."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL_MD = REPO / "plugin" / "skills" / "learn" / "SKILL.md"


def test_learn_skill_contract():
    assert SKILL_MD.is_file(), f"{SKILL_MD} must exist"
    text = SKILL_MD.read_text(encoding="utf-8")

    assert "gtm_core.material_intake" in text
    assert "gtm_core.knowledge_staging diff" in text
    assert "promote" in text

    # "show the exact changes" and "approve" must appear before any promote
    assert "show the exact changes" in text
    assert "approve" in text

    promote_idx = text.find("promote")
    show_idx = text.find("show the exact changes")
    approve_idx = text.find("approve")

    assert show_idx < promote_idx, "'show the exact changes' must appear before promote"
    assert approve_idx < promote_idx, "'approve' must appear before promote"

    # never contains a content/ path outside <details>
    # Strip <details>...</details> blocks and assert 'content/' not in remaining text
    stripped = re.sub(r"<details.*?</details>", "", text, flags=re.DOTALL)
    assert "content/" not in stripped, "content/ path leaked outside <details> in learn skill"
