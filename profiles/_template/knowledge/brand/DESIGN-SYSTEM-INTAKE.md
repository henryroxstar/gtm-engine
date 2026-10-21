---
source: manual
refreshed: 2026-10-19
review: 180d
---
# Design System Intake Format (`DESIGN-SYSTEM-INTAKE.md`)

To prevent partial updates across `BRAND.toml`, `products/*/BRAND.toml`, `brand/tokens.css`, `brand/brand-generation-source.md`, and `brand-notes.md`, GTM Engine provides a deterministic **Design System Intake** workflow (`python -m gtm_core.brand_intake`).

## 1. Quickstart Workflow

```bash
# 1. Export the current live design system into a ready-to-edit TOML spec
python -m gtm_core.brand_intake export --profile <slug> --out /tmp/design-system.toml

# 2. Edit /tmp/design-system.toml (or generate it from a Figma / CSS / markdown brief)

# 3. Stage all derived files and run the 5-check consistency gate (DS-SCHEMA-01 .. DS-RENDER-04)
python -m gtm_core.brand_intake stage --profile <slug> --spec /tmp/design-system.toml

# 4. Inspect the unified diff across all staged files before promoting
python -m gtm_core.brand_intake diff --profile <slug>

# 5. Promote atomically after operator confirmation (with automatic snapshot rollback on failure)
python -m gtm_core.brand_intake promote --profile <slug> --source "design-system-intake"
```

---

## 2. Canonical Intake Spec Schema (`.toml` or ```toml fenced block in `.md`)

`python -m gtm_core.brand_intake stage` accepts either a standalone `.toml` file or a `.md` file containing a ` ```toml ` code block with the following sections:

```toml
[design_system]
archetype = "mission-control"                    # "mission-control" | "editorial-grid" | "light-saas" | "custom"
product_override_policy = "suite_gradient_and_logo_only"  # "none" | "suite_gradient_and_logo_only" | "full_subbrand"
panel_style = "glass-hairline"                   # "glass-hairline" | "solid-surface" | "bordered-card"
background_plates_in_ui = false
require_2d_telemetry = true
forbid_gradient_fill_buttons = true
denied_fonts = ["Legacy Serif", "Old Mono"]
denied_hexes = ["#112233", "#445566"]

[palette]
canvas = "#000000"
surface = "#0A182A"
primary = "#3464FD"
accent = "#72F5FA"
ink = "#F4F6FB"
muted = "#9EABC0"
rule = "#1B2A3E"
warn = "#EAB308"
good = "#22C55E"
risk = "#EF4444"
gradient = "core"

[typography]
display = "Tenon"
body = "Tenon"
mono = "SF Mono"
typekit_url = ""
fallback_stack = ["Tenon", "Standin Sans", "system-ui", "sans-serif"]

[layout]
grid = "1px hairline grid; 12px card radius; 6px control radius"
safe_zone = "top:12% bottom:18% sides:6%"
density = "generous whitespace; max 3 data points per frame"

[motion]
pacing = "cubic-bezier(0.22, 1, 0.36, 1); 180-520ms"
glow_on_focal = false

# Optional per-product sections:
# Under product_override_policy = "suite_gradient_and_logo_only", only `palette.gradient` and
# logo lockup keys under `[assets]` may be specified.
[products.example-product.palette]
gradient = "fabric"

[products.example-product.assets]
product_logo_dark = "brand/logos/example-product.svg"
```

---

## 3. What `brand_intake stage` Compiles & Validates

1. **`knowledge/BRAND.toml`**: Updates `[design_system]`, `[palette]`, `[typography]`, `[layout]`, `[motion]`, and `[assets]` while preserving `[identity]`, `[voice]`, and `[imagery]` sections.
2. **`products/<slug>/BRAND.toml`**: Updates or validates product overrides against `design_system.product_override_policy`.
3. **`knowledge/brand/tokens.css`**: Deterministically compiled via `render_tokens_css(company_kit)` so CSS custom properties never drift from `BRAND.toml`.
4. **`knowledge/brand/brand-generation-source.md` & `knowledge/brand-notes.md`**: Synchronizes the canonical token tables and sweeps any newly added `denied_fonts` or `denied_hexes`.
5. **Pre-Promotion Gate (`check_brand_consistency(..., staged=True)`)**: Runs `DS-SCHEMA-01`, `DS-POLICY-01`, `DS-TOKENS-02`, `DS-DRIFT-03`, and `DS-RENDER-04` against the staged tree. If any check fails, `stage` exits non-zero and prints the exact remediation.
