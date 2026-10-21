"""Unit tests for Angle CTA persistence and non-destructive gift-artifacts.txt sync in gtm_core.messaging_intake."""

from __future__ import annotations

import tomllib
from pathlib import Path

from gtm_core.messaging_intake import stage_intake

INTAKE_WITH_CTAS = """\
# Messaging & Hook Intake

## 2. Capabilities & Claims

### Claim: verifiable-trace
- **Group:** security
- **Status:** verified
- **Statement:** Produces signed audit trails.
- **Source:** doc.md:1
- **Do Not Say:** []
- **Boundary:** false

---

## 3. Proof Points & Benchmarks

### Proof: global-stat
- **Kind:** stat
- **Market:** global
- **Figure Kind:** none
- **Statement:** 80% of audits require traceable logs.
- **Source:** stat.md:1
- **Binding:** false

---

## 4. Narrative Angles & Hooks

### Angle: audit-angle-1
- **Seat:** sec-lead
- **Premise:** compliance
- **Claim: verifiable-trace
- **Proof: global-stat
- **Opener Kind:** account-event
- **Summary:** Attested audit records.
- **Status:** draft
- **Give-First Gift CTA:** 1:1 indicative security scorecard

### Angle: audit-angle-2
- **Seat:** sec-lead
- **Premise:** compliance
- **Claim: verifiable-trace
- **Proof: global-stat
- **Opener Kind:** account-event
- **Summary:** Readiness brief for agent security.
- **Status:** draft
- **Give-First Gift CTA:** blueprint alliance readiness brief
"""


def test_cta_persisted_in_angles_toml_and_synced_to_gift_artifacts(tmp_path: Path) -> None:
    profiles_root = tmp_path / "profiles"
    content_root = tmp_path / "content"

    acme_dir = profiles_root / "acme"
    acme_dir.mkdir(parents=True, exist_ok=True)
    (acme_dir / "PROFILE.md").write_text("name: Acme\n", encoding="utf-8")

    # Seed existing gift-artifacts.txt with manual entries
    kdir = acme_dir / "knowledge"
    kdir.mkdir(parents=True, exist_ok=True)
    existing_gift = kdir / "gift-artifacts.txt"
    existing_gift.write_text(
        "# Pre-existing gift artifacts\none-pager\nreference architecture\n",
        encoding="utf-8",
    )

    intake_file = tmp_path / "intake.md"
    intake_file.write_text(INTAKE_WITH_CTAS, encoding="utf-8")

    staged_files, _ = stage_intake(
        "acme",
        intake_file,
        content_root=content_root,
        profiles_root=profiles_root,
        auto_backup=False,
    )

    # 1. Assert angles.toml contains cta field
    angles_file = staged_files["angles.toml"]
    assert angles_file.is_file()
    parsed_angles = tomllib.loads(angles_file.read_text(encoding="utf-8"))
    angle_records = parsed_angles["angle"]
    ctas = {a["id"]: a.get("cta") for a in angle_records}
    assert ctas["audit-angle-1"] == "1:1 indicative security scorecard"
    assert ctas["audit-angle-2"] == "blueprint alliance readiness brief"

    # 2. Assert gift-artifacts.txt is staged and contains BOTH existing items and new CTAs
    assert "gift-artifacts.txt" in staged_files
    staged_gift = staged_files["gift-artifacts.txt"]
    assert staged_gift.is_file()
    gift_content = staged_gift.read_text(encoding="utf-8")

    # Non-destructive check: pre-existing items MUST survive
    assert "one-pager" in gift_content
    assert "reference architecture" in gift_content

    # New items from angles must be present
    assert "1:1 indicative security scorecard" in gift_content
    assert "blueprint alliance readiness brief" in gift_content
