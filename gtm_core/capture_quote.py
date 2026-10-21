"""Is a recorded quote really on the stored page it cites? A yes/no that never raises and never softens."""

from __future__ import annotations

from pathlib import Path

from .signal_sources import (
    _markdown_to_text,
    get_latest_capture,
    normalise_quote_whitespace,
    page_text,
)


def quote_in_latest_capture(
    evidence: str,
    url: str,
    *,
    sources_dir: Path | str | None = None,
    profile: str | None = None,
) -> bool:
    """True only when the newest stored capture of ``url`` carries ``evidence`` verbatim.

    The same comparison as ``signal_sources.validate_source_evidence``, as a yes/no: no place to
    look, no capture, a damaged index, an unreadable page or a quote the page does not hold are all
    ``False``. Used where a recorded quote must be shown to exist before it may stand as evidence
    (the record route, ``hook_coverage.source_attest``).
    """
    stripped = (evidence or "").strip()
    if len(stripped) < 20 or not (url or "").strip():
        return False
    if sources_dir is None and not (profile or "").strip():
        return False
    try:
        capture = get_latest_capture(url, sources_dir=sources_dir, profile=profile)
        if capture is None:
            return False
        norm_ev = normalise_quote_whitespace(_markdown_to_text(stripped))
        norm_cap = normalise_quote_whitespace(_markdown_to_text(page_text(capture.text)))
    except (ValueError, OSError):
        return False
    return norm_ev in norm_cap
