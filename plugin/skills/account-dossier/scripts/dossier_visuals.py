#!/usr/bin/env python3
"""
dossier_visuals.py — on-brand PNG generator for the account-dossier skill.

Emits 1–2 simple, readable visuals for the dossier:
  - gap.png   : a "problem / gap" three-card visual
  - flow.png  : a horizontal left-to-right process / flow diagram of our product

Colors are passed in (zero hardcoded company strings). If omitted, the flags
default to the account-dossier fallback palette so the script always runs:
  navy #0A0E27 background, electric blue #3E7BFA, purple #B57BFF, cyan #22D3EE.

Both images are sized for ~6in render width in the Word doc.

Usage:
  python dossier_visuals.py --out ./work --which both \\
      --bg "#0A0E27" --accent1 "#3E7BFA" --accent2 "#B57BFF" --accent3 "#22D3EE"

  # custom labels (semicolon-separated — REQUIRED; derive from the active profile's product.md)
  python dossier_visuals.py --out ./work --which gap \\
      --gap-title "Three gaps we close" \\
      --gap-cards "No data visibility;No access control;No audit trail"
  python dossier_visuals.py --out ./work --which flow \\
      --flow-steps "Discover;Design;Deliver;Measure"
"""

import argparse
import os

import matplotlib

matplotlib.use("Agg")  # headless / no display
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

# Deterministic faint "constellation" dots — fixed so output is reproducible
# (no random import, so the same call always produces the same image).
_STAR_POINTS = [
    (0.06, 0.82),
    (0.14, 0.33),
    (0.21, 0.91),
    (0.27, 0.12),
    (0.33, 0.58),
    (0.39, 0.86),
    (0.46, 0.22),
    (0.52, 0.74),
    (0.58, 0.41),
    (0.63, 0.95),
    (0.69, 0.17),
    (0.74, 0.63),
    (0.80, 0.29),
    (0.86, 0.88),
    (0.91, 0.49),
    (0.95, 0.71),
    (0.11, 0.55),
    (0.30, 0.78),
    (0.49, 0.07),
    (0.67, 0.84),
    (0.83, 0.13),
    (0.18, 0.69),
    (0.43, 0.49),
    (0.71, 0.36),
    (0.88, 0.62),
]
_STAR_SIZES = [6, 3, 5, 2, 4, 7, 3, 5, 2, 6, 3, 4, 2, 6, 3, 5, 2, 4, 3, 6, 2, 4, 3, 5, 2]

WHITE = "#FFFFFF"
MUTED = "#AEB6D0"


def _new_canvas(bg, width_in=6.0, height_in=2.6):
    """Dark canvas with faint constellation dots; returns (fig, ax) on a 0..1 grid."""
    fig, ax = plt.subplots(figsize=(width_in, height_in), dpi=200)
    fig.patch.set_facecolor(bg)
    ax.set_facecolor(bg)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    for (x, y), s in zip(_STAR_POINTS, _STAR_SIZES, strict=True):
        ax.scatter(x, y, s=s, c=WHITE, alpha=0.10, edgecolors="none", zorder=0)
    return fig, ax


def _rounded_card(ax, x, y, w, h, edge, fill=None, lw=2.2):
    """Rounded-rectangle card outlined in `edge` (faint `fill` inside)."""
    if fill:
        ax.add_patch(
            FancyBboxPatch(
                (x, y),
                w,
                h,
                boxstyle="round,pad=0.012,rounding_size=0.04",
                linewidth=0,
                facecolor=fill,
                alpha=0.16,
                zorder=1,
            )
        )
    ax.add_patch(
        FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle="round,pad=0.012,rounding_size=0.04",
            linewidth=lw,
            edgecolor=edge,
            facecolor="none",
            zorder=2,
        )
    )


def _wrap(text, width=18):
    """Cheap word-wrap for short card labels."""
    words, lines, cur = text.split(), [], ""
    for word in words:
        if len(cur) + len(word) + 1 <= width:
            cur = (cur + " " + word).strip()
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def gap_cards(out_dir, bg, accents, title, cards):
    """Three rounded accent cards — the 'problem / gap' visual."""
    fig, ax = _new_canvas(bg, height_in=2.6)
    ax.text(0.5, 0.90, title, ha="center", va="center", color=WHITE, fontsize=13, fontweight="bold")

    n = 3
    gap = 0.04
    margin = 0.05
    card_w = (1 - 2 * margin - (n - 1) * gap) / n
    card_h = 0.50
    card_y = 0.18
    for i in range(n):
        x = margin + i * (card_w + gap)
        edge = accents[i % len(accents)]
        _rounded_card(ax, x, card_y, card_w, card_h, edge, fill=edge)
        label = cards[i] if i < len(cards) else ""
        ax.text(
            x + card_w / 2,
            card_y + card_h / 2,
            _wrap(label, 16),
            ha="center",
            va="center",
            color=WHITE,
            fontsize=10.5,
            fontweight="bold",
            linespacing=1.25,
        )

    path = os.path.join(out_dir, "gap.png")
    fig.savefig(path, facecolor=bg, bbox_inches="tight", pad_inches=0.12)
    plt.close(fig)
    return path


def process_flow(out_dir, bg, accents, steps):
    """Horizontal left-to-right flow of rounded nodes joined by arrows."""
    fig, ax = _new_canvas(bg, height_in=1.9)
    n = max(1, len(steps))
    gap = 0.035
    margin = 0.04
    node_w = (1 - 2 * margin - (n - 1) * gap) / n
    node_h = 0.46
    node_y = 0.30
    centers = []
    for i in range(n):
        x = margin + i * (node_w + gap)
        edge = accents[i % len(accents)]
        _rounded_card(ax, x, node_y, node_w, node_h, edge, fill=edge)
        ax.text(
            x + node_w / 2,
            node_y + node_h / 2,
            _wrap(steps[i], 12),
            ha="center",
            va="center",
            color=WHITE,
            fontsize=10.5,
            fontweight="bold",
            linespacing=1.2,
        )
        centers.append((x, x + node_w))

    arrow_color = accents[-1] if accents else WHITE
    for i in range(n - 1):
        x0 = centers[i][1] + 0.004
        x1 = centers[i + 1][0] - 0.004
        ax.add_patch(
            FancyArrowPatch(
                (x0, node_y + node_h / 2),
                (x1, node_y + node_h / 2),
                arrowstyle="-|>",
                mutation_scale=14,
                linewidth=1.8,
                color=arrow_color,
                zorder=3,
            )
        )

    path = os.path.join(out_dir, "flow.png")
    fig.savefig(path, facecolor=bg, bbox_inches="tight", pad_inches=0.12)
    plt.close(fig)
    return path


def main():
    p = argparse.ArgumentParser(description="On-brand PNGs for the account-dossier skill.")
    p.add_argument("--out", required=True, help="output directory")
    p.add_argument("--which", choices=["gap", "flow", "both"], default="both")
    p.add_argument("--bg", default="#0A0E27", help="background hex")
    p.add_argument("--accent1", default="#3E7BFA", help="electric blue")
    p.add_argument("--accent2", default="#B57BFF", help="purple")
    p.add_argument("--accent3", default="#22D3EE", help="cyan")
    p.add_argument("--gap-title", default="Where the gaps are today")
    p.add_argument(
        "--gap-cards",
        default=None,
        required=False,
        help="semicolon-separated, up to 3 card labels — derive from product.md and always pass explicitly",
    )
    p.add_argument(
        "--flow-steps",
        default=None,
        required=False,
        help="semicolon-separated process steps — derive from product.md and always pass explicitly",
    )
    args = p.parse_args()

    if args.which in ("gap", "both") and not args.gap_cards:
        p.error("--gap-cards is required when --which includes 'gap'; derive from product.md")
    if args.which in ("flow", "both") and not args.flow_steps:
        p.error("--flow-steps is required when --which includes 'flow'; derive from product.md")

    os.makedirs(args.out, exist_ok=True)
    accents = [args.accent1, args.accent2, args.accent3]
    cards = [c.strip() for c in (args.gap_cards or "").split(";") if c.strip()]
    steps = [s.strip() for s in (args.flow_steps or "").split(";") if s.strip()]

    written = []
    if args.which in ("gap", "both"):
        written.append(gap_cards(args.out, args.bg, accents, args.gap_title, cards))
    if args.which in ("flow", "both"):
        written.append(process_flow(args.out, args.bg, accents, steps))

    for path in written:
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
