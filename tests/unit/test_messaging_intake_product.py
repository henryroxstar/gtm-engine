"""Unit tests for multi-product support and topic namespacing in gtm_core.messaging_intake."""

from __future__ import annotations

from pathlib import Path

from gtm_core.messaging_intake import (
    export_profile_to_markdown,
    stage_intake,
)

SAMPLE_PRODUCT_INTAKE = """\
# Messaging & Hook Intake — Widget Pro

## 1. Target Seats & Pains

### Seat: dev-lead
- **Title / Persona:** Lead Developer, Architect
- **Lead Pain:** Integrating custom auth logic per agent framework.
- **Gain:** One unified gateway boundary.
- **Forbidden Pains:** Budget approvals
- **Register:** technical

---

## 2. Capabilities & Claims

### Claim: agent-gateway-proxy
- **Group:** security
- **Status:** verified
- **Statement:** The gateway sits inline at the agent tool boundary with zero code rewrites.
- **Source:** products/widget-pro/PRODUCT.md:12
- **Do Not Say:** magic, instant
- **Boundary:** false

---

## 3. Proof Points & Benchmarks

### Proof: proxy-latency-anchor
- **Kind:** anchor
- **Market:** global
- **Figure Kind:** none
- **Statement:** Measured overhead is under 3 milliseconds per tool call.
- **Source:** references/benchmarks.md:5
- **Binding:** true

---

## 4. Narrative Angles & Hooks

### Angle: zero-rewrite-security
- **Seat:** dev-lead
- **Premise:** zero-rewrite
- **Claim:** agent-gateway-proxy
- **Proof:** proxy-latency-anchor
- **Opener Kind:** account-event
- **Summary:** Inline gateway for agent security with zero agent rewrite.
- **Status:** draft
- **Give-First Gift CTA:** 1-page architecture teardown
"""


def test_stage_intake_product_scoping(tmp_path: Path) -> None:
    profiles_root = tmp_path / "profiles"
    content_root = tmp_path / "content"

    # Set up multi-product profile "acme" with flagship "core" and second product "widget-pro"
    acme_dir = profiles_root / "acme"
    acme_dir.mkdir(parents=True, exist_ok=True)
    (acme_dir / "PROFILE.md").write_text(
        """\
```yaml
name: Acme
products:
  - slug: core
    name: Acme Core
    flagship: true
  - slug: widget-pro
    name: Widget Pro
```
""",
        encoding="utf-8",
    )
    prod_dir = acme_dir / "products" / "widget-pro"
    prod_dir.mkdir(parents=True, exist_ok=True)
    (prod_dir / "PRODUCT.md").write_text("# Widget Pro\n", encoding="utf-8")

    intake_file = tmp_path / "widget-pro-intake.md"
    intake_file.write_text(SAMPLE_PRODUCT_INTAKE, encoding="utf-8")

    staged_files, backup = stage_intake(
        "acme",
        intake_file,
        content_root=content_root,
        profiles_root=profiles_root,
        product="widget-pro",
        auto_backup=True,
    )

    # Topic keys must be namespaced with products/widget-pro/
    assert "products/widget-pro/claims.toml" in staged_files
    assert "products/widget-pro/proof.toml" in staged_files
    assert "products/widget-pro/angles.toml" in staged_files

    # File path on disk must be inside content/acme/knowledge-staging/products/widget-pro/
    claims_path = staged_files["products/widget-pro/claims.toml"]
    assert claims_path.is_file()
    assert "products/widget-pro" in str(claims_path)


def test_export_profile_to_markdown_product_scoping(tmp_path: Path) -> None:
    profiles_root = tmp_path / "profiles"

    acme_dir = profiles_root / "acme"
    acme_dir.mkdir(parents=True, exist_ok=True)
    (acme_dir / "PROFILE.md").write_text(
        """\
```yaml
name: Acme
products:
  - slug: core
    name: Acme Core
    flagship: true
  - slug: widget-pro
    name: Widget Pro
```
""",
        encoding="utf-8",
    )
    prod_dir = acme_dir / "products" / "widget-pro"
    prod_dir.mkdir(parents=True, exist_ok=True)
    (prod_dir / "PRODUCT.md").write_text("# Widget Pro\n", encoding="utf-8")
    (prod_dir / "claims.toml").write_text(
        """\
[[claim]]
id = "prod-claim-1"
group = "g"
status = "verified"
statement = "Product specific claim."
source = "doc.md"
do_not_say = []
boundary = false
""",
        encoding="utf-8",
    )

    md = export_profile_to_markdown("acme", profiles_root=profiles_root, product="widget-pro")
    assert "prod-claim-1" in md
    assert "Product specific claim." in md
