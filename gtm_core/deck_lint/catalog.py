from __future__ import annotations

# ══════════════════════════════════════════════════════════════════════════════════════
# Catalog — what the deck-theme actually ships. A name outside this set will not render.
# ══════════════════════════════════════════════════════════════════════════════════════

COMPONENTS: frozenset[str] = frozenset(
    {
        "AskBox",
        "Banner",
        "BrandMark",
        "BrandTag",
        "BreakTests",
        "CalloutRow",
        "CanvasAmbience",
        "ChatWindow",
        "ChipRow",
        "DuoGrid",
        "EntityCrossing",
        "Eyebrow",
        "FlowSequence",
        "FlowTrack",
        "GateFunnel",
        "GlassCard",
        "GradientText",
        "HeroTitle",
        "ImpactRow",
        "MeshAurora",
        "ParticleField",
        "PillarCard",
        "ProofContrast",
        "PulseHalo",
        "ReqAnchors",
        "ReuseTrack",
        "ScopeMap",
        "SpeakerCard",
        "StackDiagram",
        "StatBlock",
        "StatsRow",
        "SurfaceGrid",
        "TerminalWindow",
    }
)

LAYOUTS: frozenset[str] = frozenset(
    {"chapter", "cover", "default", "end", "handoff", "pillars", "statement", "two-pane"}
)

# Components that give a slide something to look at. A Banner is a caption, not a visual.
VISUAL_COMPONENTS: frozenset[str] = frozenset(
    {
        "BreakTests",
        "CalloutRow",
        "ChatWindow",
        "ChipRow",
        "DuoGrid",
        "EntityCrossing",
        "FlowSequence",
        "FlowTrack",
        "GateFunnel",
        "GlassCard",
        "ImpactRow",
        "PillarCard",
        "ProofContrast",
        "ReqAnchors",
        "ReuseTrack",
        "ScopeMap",
        "StackDiagram",
        "StatBlock",
        "StatsRow",
        "SurfaceGrid",
        "TerminalWindow",
    }
)

# The strict subset that draws a STRUCTURE — a path, a span, a boundary, a constriction.
# The distinction is not decorative: a GlassCard grid is something to look at, but only a
# schematic can carry an explanation that would otherwise be a paragraph. Slides built on
# one of these are allowed a larger word budget (D10), because a label sitting inside a
# diagram is read at a glance and a sentence is not.
DIAGRAM_COMPONENTS: frozenset[str] = frozenset(
    {
        "EntityCrossing",
        "FlowSequence",
        "FlowTrack",
        "GateFunnel",
        "ProofContrast",
        "ReqAnchors",
        "ReuseTrack",
        "ScopeMap",
        "StackDiagram",
    }
)

# Classes the theme styles globally; a slide may use them without a scoped rule.
THEME_CLASSES: frozenset[str] = frozenset(
    {
        "anim-fade-up",
        "anim-scale-in",
        "anim-soft-fade",
        "askbox",
        "banner",
        "bk",
        "bl",
        "brk-head",
        "brks",
        "caps",
        "caps-wide",
        "caps-wider",
        "clean-list",
        "close-for",
        "close-gain",
        "display-callout",
        "divider",
        "font-display",
        "font-mono",
        "font-sans",
        "gradient-text",
        "gradient-text-product",
        "highlight",
        "lede",
        "mini-kicker",
        "ql",
        "scrim-bottom",
        "scrim-left",
        "scrim-radial",
        "slidev-code",
        "slidev-layout",
        "stagger",
    }
)


# ══════════════════════════════════════════════════════════════════════════════════════
# D9 — render weight (theme-level, not per-deck)
# ══════════════════════════════════════════════════════════════════════════════════════
#
# The print-mode guard lives in styles/slidev-overrides.css, keyed off `html.deck-export`
# (set by setup/main.ts). Two escape hatches are already wired through it:
#   - `backdrop-filter: var(--card-blur)` — the token is redefined to `none` under the guard.
#   - the ambient/gradient-text classnames below — hidden or flattened under the guard.
# A new component that writes a raw `blur()`/`backdrop-filter` outside those two hatches will
# render fine on screen and silently reintroduce the bug on the next export. That is what this
# tier exists to catch — before anyone builds a deck with it.

GUARDED_AMBIENT_CLASSES: frozenset[str] = frozenset(
    {
        "pulse-halo",
        "mesh-aurora",
        "canvas-ambience",
        "orb",
        "grid-plane",
        "pulse-ring",
        "scan-line",
        "flow-beam",  # FlowTrack's connector line — aria-hidden, hidden under html.deck-export
        "slidev-vclick-hidden",  # Slidev's own pre-reveal state — neutralized under html.deck-export
    }
)
# Elements whose `filter:` sits on top of a real background photo (`$frontmatter.image`) — the
# photo rasterizes regardless of the filter, so the filter adds no marginal raster cost and does
# not need to be stripped in print mode the way a purely-decorative layer does.
PHOTO_BOUND_CLASSES: frozenset[str] = frozenset({"bg-image"})
GUARDED_GRADIENT_TEXT_CLASSES: frozenset[str] = frozenset(
    {
        "gradient-text",
        "gradient-text-product",
        "gt",
        "gt-product",
        "gt-animated",
        # Both set `color: transparent` with no visible fallback, so their print-mode rules in
        # slidev-overrides.css must supply a colour as well as undoing the clip — see the comment
        # there. `.numeral` alone was ~2MB of raster on every chapter divider.
        "numeral",
        "surface-index",
        # The remaining seven, guarded 2026-08-29. Same shape as `surface-index`: the
        # exemption is keyed on the LEAF classname, the print-mode rule is keyed on the
        # variant that actually carries the gradient (`.callout.kind-highlight .phrase`,
        # `.flow-node.success .flow-label`). A new variant reusing one of these leaf
        # classes with a gradient is therefore exempt here but NOT guarded there — if you
        # add one, add its rule to slidev-overrides.css by hand.
        "cc-icon",
        "phrase",
        "flow-label",
        "impact-value",
        "number-stripe",
        "trigger-word",
        "stat-number",
    }
)
