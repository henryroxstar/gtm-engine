"""Deterministic messaging matrix hook_cell derivation.

Derives messaging matrix coordinates (segment|signal) from segment and signal.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .hook_coverage.matrix import Matrix


from .paths import resolve_knowledge_file, resolve_profiles_root


def parse_hook_cell(coordinate: str) -> tuple[str, str]:
    """Parse a 'segment|signal' hook cell coordinate into (segment, signal).

    Fails loudly with ValueError on malformed coordinates (e.g. 'invalid:format').
    """
    raw = (coordinate or "").strip()
    if "|" not in raw or raw.count("|") != 1:
        raise ValueError(
            f"malformed hook_cell coordinate: {coordinate!r} (expected 'segment|signal')"
        )
    segment, signal = raw.split("|", 1)
    seg_clean, sig_clean = segment.strip(), signal.strip()
    if not seg_clean or not sig_clean:
        raise ValueError(
            f"malformed hook_cell coordinate: {coordinate!r} (both segment and signal must be non-empty)"
        )
    return seg_clean, sig_clean


def validate_hook_cell(hook_cell: str, matrix: Matrix | None = None) -> tuple[bool, str]:
    """Validate a hook_cell coordinate string.

    Returns (is_valid, error_message).
    """
    try:
        seg, sig = parse_hook_cell(hook_cell)
    except ValueError as e:
        return False, str(e)

    if matrix is not None and getattr(matrix, "ok", False):
        from .hook_coverage.fit import _norm_segment, signal_columns_for_segment

        norm_seg = _norm_segment(seg)
        cols = signal_columns_for_segment(matrix, norm_seg)
        if not cols:
            return False, f"no signal columns found for segment {seg!r}"
        if not any(col.strip().lower() == sig.lower() for col in cols):
            return False, f"signal {sig!r} not in matrix for segment {seg!r}"

    return True, ""


def derive_hook_cell(
    segment: str,
    signal_category: str,
    profile: str = "",
    product: str = "",
    *,
    matrix: Matrix | None = None,
    profiles_root: Path | None = None,
    overlay: str | None = None,
) -> tuple[str, str]:
    """Deterministically derive the messaging matrix cell coordinate (segment|signal).

    Returns (coordinate, error_or_hold_reason).
    - On success: (f"{segment}|{signal}", "")
    - On graceful fallback: (f"{segment}|generic", "")
    - On refusal: ("", f"Add '{signal_category}' or 'generic' column to {segment} row in hook-matrix.md")
    """
    clean_signal = (signal_category or "").strip()
    clean_segment = (segment or "").strip()

    if not clean_segment:
        return (
            "",
            f"Add '{clean_signal}' or 'generic' column to {clean_segment} row in hook-matrix.md",
        )

    if matrix is None:
        if not profile:
            return (
                "",
                f"Add '{clean_signal}' or 'generic' column to {clean_segment} row in hook-matrix.md",
            )
        root = profiles_root or resolve_profiles_root()
        path = resolve_knowledge_file(
            root, profile, "hook-matrix.md", product=product or None, overlay=overlay
        )
        if not path.is_file():
            return (
                "",
                f"Add '{clean_signal}' or 'generic' column to {clean_segment} row in hook-matrix.md",
            )
        from .hook_coverage.matrix import parse_matrix

        matrix = parse_matrix(path, profile=profile)

    if not getattr(matrix, "ok", False):
        return (
            "",
            f"Add '{clean_signal}' or 'generic' column to {clean_segment} row in hook-matrix.md",
        )

    from .hook_coverage.fit import _norm_segment, signal_columns_for_segment

    norm_seg = _norm_segment(clean_segment)
    cols = signal_columns_for_segment(matrix, norm_seg)

    # 1. Match specific signal if non-empty
    if clean_signal:
        for col in cols:
            if col.strip().lower() == clean_signal.lower():
                return (f"{norm_seg}|{clean_signal.lower()}", "")

    # 2. Graceful fallback to generic if generic column exists for that segment
    if any(col.strip().lower() == "generic" for col in cols):
        return (f"{norm_seg}|generic", "")

    # 3. Missing cell hold
    return (
        "",
        f"Add '{clean_signal}' or 'generic' column to {clean_segment} row in hook-matrix.md",
    )


def main() -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Deterministically derive hook_cell coordinate")
    parser.add_argument("--segment", required=True, help="Segment (enterprise|startup)")
    parser.add_argument("--signal", default="", help="Observed signal category")
    parser.add_argument("--profile", required=True, help="Active profile")
    parser.add_argument("--product", default="", help="Active product")
    parser.add_argument("--overlay", default=None, help="Experiment overlay")

    args = parser.parse_args()
    cell, err = derive_hook_cell(
        args.segment, args.signal, profile=args.profile, product=args.product, overlay=args.overlay
    )
    if err:
        sys.stderr.write(f"Refusal: {err}\n")
        return 1
    sys.stdout.write(f"{cell}\n")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
