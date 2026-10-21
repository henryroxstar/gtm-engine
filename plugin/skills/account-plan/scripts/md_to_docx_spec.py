#!/usr/bin/env python3
"""
md_to_docx_spec.py — convert an account-plan markdown file into the JSON block
spec consumed by render_account_plan_pydocx.py.

The account plan is authored as markdown (the canonical artifact); the .docx is a
rendering of it. Converting mechanically — rather than hand-transcribing the plan
into JSON — keeps the two from drifting: re-running this after any edit to the .md
reproduces a matching .docx.

Handles the subset of markdown this skill's own output actually uses:
  # / ## / ###      -> title / heading / subheading
  | a | b |         -> table (with --- separator row); a 2-col table whose header
                       is a generic label/value pair renders as facts_table
  - item            -> bullets (consecutive items grouped into one block)
  > quote           -> italic paragraph
  ---               -> ignored (section rules are drawn by the renderer)
  *text*  (whole line, italic) -> italic paragraph
  everything else   -> paragraph
Inline **bold** and [text](url) are preserved verbatim and parsed by the renderer.

Usage:  uv run python scripts/md_to_docx_spec.py <plan.md> <spec.json> [--closing "..."]
"""

import argparse
import json
import re

_TABLE_SEP_RE = re.compile(r"^\|[\s:\-|]+\|$")


def _split_row(line):
    # Strip the leading/trailing pipe, then split. Cells never contain a raw '|'
    # in this skill's output (a literal pipe would be escaped), so a plain split is safe.
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [c.strip() for c in inner.split("|")]


def _is_generic_kv_header(header):
    """A 2-column table whose header is a generic key/value pair reads better as a
    shaded label/value facts_table than as a bordered data table."""
    if len(header) != 2:
        return False
    left, right = header[0].strip().lower(), header[1].strip().lower()
    return left in {"field", "element", "stage", "ask", "item", "lever", "value lever"} or (
        right in {"value", "detail", "details"}
    )


def convert(md_text):
    lines = md_text.split("\n")
    blocks = []
    i = 0
    pending_bullets = []
    seen_title = False

    def flush_bullets():
        nonlocal pending_bullets
        if pending_bullets:
            blocks.append({"type": "bullets", "items": pending_bullets})
            pending_bullets = []

    while i < len(lines):
        raw = lines[i]
        line = raw.strip()

        # table
        if (
            line.startswith("|")
            and i + 1 < len(lines)
            and _TABLE_SEP_RE.match(lines[i + 1].strip())
        ):
            flush_bullets()
            header = _split_row(line)
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_split_row(lines[i].strip()))
                i += 1
            if _is_generic_kv_header(header):
                blocks.append({"type": "facts_table", "rows": [r[:2] for r in rows if r]})
            else:
                blocks.append({"type": "table", "header": header, "rows": rows})
            continue

        if not line:
            flush_bullets()
            i += 1
            continue

        # horizontal rule — the renderer draws its own section rules
        if line in {"---", "***", "___"}:
            flush_bullets()
            i += 1
            continue

        # headings
        if line.startswith("### "):
            flush_bullets()
            blocks.append({"type": "subheading", "text": line[4:].strip()})
            i += 1
            continue
        if line.startswith("## "):
            flush_bullets()
            blocks.append({"type": "heading", "text": line[3:].strip()})
            i += 1
            continue
        if line.startswith("# "):
            flush_bullets()
            text = line[2:].strip()
            if not seen_title:
                # Consume the immediately-following non-blank lines as subtitle/meta.
                subtitle, meta = None, None
                j = i + 1
                collected = []
                while (
                    j < len(lines)
                    and lines[j].strip()
                    and not lines[j].strip().startswith(("#", "|", ">", "-"))
                ):
                    collected.append(lines[j].strip())
                    j += 1
                if collected:
                    subtitle = collected[0]
                if len(collected) > 1:
                    meta = " · ".join(collected[1:])
                blk = {"type": "title", "text": text}
                if subtitle:
                    blk["subtitle"] = subtitle
                if meta:
                    blk["meta"] = meta
                blocks.append(blk)
                seen_title = True
                i = j
                continue
            blocks.append({"type": "heading", "text": text})
            i += 1
            continue

        # blockquote -> italic paragraph
        if line.startswith(">"):
            flush_bullets()
            quote = line.lstrip(">").strip()
            j = i + 1
            while j < len(lines) and lines[j].strip().startswith(">"):
                quote += " " + lines[j].strip().lstrip(">").strip()
                j += 1
            blocks.append({"type": "paragraph", "text": quote, "italic": True})
            i = j
            continue

        # bullets (- or numbered)
        m = re.match(r"^(?:[-*]|\d+\.)\s+(.*)$", line)
        if m:
            pending_bullets.append(m.group(1).strip())
            i += 1
            continue

        # whole-line italic
        if line.startswith("*") and line.endswith("*") and not line.startswith("**"):
            flush_bullets()
            blocks.append({"type": "paragraph", "text": line.strip("*").strip(), "italic": True})
            i += 1
            continue

        flush_bullets()
        blocks.append({"type": "paragraph", "text": line})
        i += 1

    flush_bullets()
    return blocks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("md_path")
    parser.add_argument("spec_path")
    parser.add_argument("--closing", default="Internal — not for customer distribution.")
    args = parser.parse_args()

    with open(args.md_path, encoding="utf-8") as f:
        md = f.read()

    spec = {"closingLine": args.closing, "blocks": convert(md)}
    with open(args.spec_path, "w", encoding="utf-8") as f:
        json.dump(spec, f, indent=2, ensure_ascii=False)
    print(f"wrote {args.spec_path} ({len(spec['blocks'])} blocks)")


if __name__ == "__main__":
    main()
