"""CLI for gtm_core.diagrams.

Usage:
    python -m gtm_core.diagrams render --input <path> --out <path> [--format svg|html] [--profile <p>]
    python -m gtm_core.diagrams extract <path> [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .ir import DiagramEdge, DiagramIR, DiagramNode
from .mermaid_extract import load_blocks, parse_block
from .render import render_html, render_svg


def _load_ir_from_file(path: Path) -> DiagramIR:
    """Extract DiagramIR from .mmd, .mermaid, .excalidraw, or .json file."""
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")

    if suffix in (".mmd", ".mermaid") or "```mermaid" in text:
        blocks = load_blocks(path)
        if not blocks:
            raise ValueError(f"No valid mermaid block found in {path}")
        d = parse_block(blocks[0])
        ir = DiagramIR(
            title=path.stem.replace("-", " ").title(),
            slug=path.stem,
            direction=getattr(d, "direction", "LR") or "LR",
        )
        for n in d.nodes:
            sub = n.fields[0] if getattr(n, "fields", None) else ""
            ir.nodes.append(
                DiagramNode(
                    id=n.id,
                    label=n.label or n.id,
                    sublabel=sub,
                    kind="focal" if getattr(n, "shape", "") == "circle" else "backend",
                )
            )
        for e in d.edges:
            ir.edges.append(
                DiagramEdge(
                    source=e.source,
                    target=e.target,
                    label=e.label or "",
                    kind="accent" if getattr(e, "is_accent", False) else "default",
                )
            )
        return ir

    if suffix in (".json", ".excalidraw"):
        data = json.loads(text)
        if "nodes" in data:
            return DiagramIR.from_dict(data)
        # Handle raw excalidraw scene
        from .excalidraw_extract import parse_scene

        scene = parse_scene(path, data)
        ir = DiagramIR(
            title=path.stem.replace("-", " ").title(),
            slug=path.stem,
        )
        for n in scene.nodes:
            ir.nodes.append(
                DiagramNode(
                    id=str(n.id),
                    label=str(n.label or n.id),
                    x=float(n.x),
                    y=float(n.y),
                    width=float(n.width),
                    height=float(n.height),
                )
            )
        for e in scene.edges:
            ir.edges.append(
                DiagramEdge(
                    source=str(e.source),
                    target=str(e.target),
                    label=str(e.label or ""),
                )
            )
        return ir

    if suffix in (".drawio", ".xml") or "<mxfile" in text or "<mxGraphModel" in text:
        from .drawio_extract import parse_file

        pages = parse_file(path)
        if not pages:
            raise ValueError(f"No pages found in drawio file: {path}")
        p = pages[0]
        ir = DiagramIR(
            title=path.stem.replace("-", " ").title(),
            slug=path.stem,
        )
        for n in p.nodes:
            ir.nodes.append(
                DiagramNode(
                    id=str(n.id),
                    label=str(n.label or n.id),
                    x=float(n.x),
                    y=float(n.y),
                    width=float(n.w if n.w > 0 else 120.0),
                    height=float(n.h if n.h > 0 else 80.0),
                )
            )
        for e in p.edges:
            if e.source and e.target:
                ir.edges.append(
                    DiagramEdge(
                        source=str(e.source),
                        target=str(e.target),
                        label=str(e.label or ""),
                    )
                )
        return ir

    raise ValueError(f"Unsupported file format for diagram extraction: {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gtm_core.diagrams", description="Diagram Design CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Render command
    render_p = subparsers.add_parser("render", help="Render diagram to SVG or HTML")
    render_p.add_argument("--input", required=True, type=Path, help="Input diagram file")
    render_p.add_argument("--out", required=False, type=Path, help="Output destination")
    render_p.add_argument("--format", choices=["svg", "html"], default=None, help="Output format")
    render_p.add_argument("--profile", default=None, help="Tenant profile for brand kit")
    render_p.add_argument("--product", default=None, help="Product within profile")
    render_p.add_argument("--title", default=None, help="Diagram title")
    render_p.add_argument("--desc", default=None, help="Diagram accessibility description")

    # Extract command
    extract_p = subparsers.add_parser("extract", help="Extract IR from diagram file")
    extract_p.add_argument("file", type=Path, help="Input file")
    extract_p.add_argument("--json", action="store_true", help="Output as JSON")
    extract_p.add_argument("--out", type=Path, default=None, help="Output file")

    args = parser.parse_args(argv)

    try:
        if args.command == "render":
            ir = _load_ir_from_file(args.input)
            if args.title:
                ir.title = args.title
            if args.desc:
                ir.description = args.desc

            fmt = args.format
            if not fmt:
                fmt = "svg" if args.out and args.out.suffix.lower() == ".svg" else "html"

            if fmt == "svg":
                content = render_svg(ir, profile=args.profile, product=args.product)
            else:
                content = render_html(ir, profile=args.profile, product=args.product)

            if args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(content, encoding="utf-8")
            else:
                sys.stdout.write(content)
            return 0

        if args.command == "extract":
            ir = _load_ir_from_file(args.file)
            if args.json:
                out_text = json.dumps(ir.to_dict(), indent=2)
            else:
                out_text = f"Diagram: {ir.title} ({len(ir.nodes)} nodes, {len(ir.edges)} edges)\n"
                for n in ir.nodes:
                    out_text += f"  - [{n.kind}] {n.id}: {n.label} ({n.sublabel})\n"
                for e in ir.edges:
                    out_text += f"  - {e.source} -> {e.target} [{e.label}]\n"

            if args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(out_text, encoding="utf-8")
            else:
                sys.stdout.write(out_text)
            return 0
    except Exception as exc:
        print(f"gtm_core.diagrams: error: {exc}", file=sys.stderr)
        return 2

    return 1


if __name__ == "__main__":
    sys.exit(main())
