"""Contract test for the status skill."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL_MD = REPO / "plugin" / "skills" / "status" / "SKILL.md"


def test_status_skill_manifest():
    from gtm_core.skills.status import SKILL
    from gtm_core.tiers import Tier

    assert SKILL.name == "status"
    assert SKILL.capability_tier == Tier.CORE
    assert SKILL.phase == "UX"
    assert "where do i stand" in SKILL.description.lower()
    assert "status" in SKILL.description.lower()


def test_status_skill_contract():
    assert SKILL_MD.is_file(), f"{SKILL_MD} must exist"
    text = SKILL_MD.read_text(encoding="utf-8")

    assert "gtm_core.prospect_status_cli" in text
    assert "email_campaign_dashboard" in text
    assert "--check-fresh" in text
    assert "For the record" in text
    assert "You haven't run prospecting yet" in text

    # Strip <details>...</details> blocks and assert 'content/' not in remaining text
    stripped = re.sub(r"<details.*?</details>", "", text, flags=re.DOTALL)
    assert "content/" not in stripped, "content/ path leaked outside <details> in status skill"
