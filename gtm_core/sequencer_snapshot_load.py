"""What the sending-figures writer will read, and where it may write: payload files and profiles.

Split out of :mod:`sequencer_snapshot` so the writer stays under the file-size ratchet; nothing
here writes. A payload file is read once, its *file* age is checked (a convenience guard, not
proof of when the figures inside were fetched), and its rows go through
:func:`sequence_payload_check.check_payload` before any of them is used.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from gtm_core import sequence_payload_check as rules
from gtm_core.page_inputs_io import printable
from gtm_core.paths import _safe_segment, resolve_content_root, resolve_profiles_root
from gtm_core.sequence_payload_check import check_payload
from gtm_core.sequence_snapshot_format import row_id
from gtm_core.sequencer_sends import sequence_totals

#: A payload file older than this is probably last week's export. It is the FILE's age, not the
#: data's: a convenience guard, not proof of when the figures were fetched.
PAYLOAD_MAX_AGE_S = 3600
#: A payload file dated ahead of the clock by more than this is refused — a future date would pass
#: the age gate forever. The small allowance is for clock skew between the machine that saved the
#: file and the one running the writer.
PAYLOAD_FUTURE_SKEW_S = 120


def read_payload(f: Path, now: datetime, stats: Path | None):
    """``(payload, bytes, None)`` for a payload file that may be used, else ``(_, _, reason)``."""
    try:
        st = f.stat()
        if st.st_size > rules.MAX_PAYLOAD_BYTES:
            limit_mb = rules.MAX_PAYLOAD_BYTES // (1024 * 1024)
            return (
                None,
                b"",
                f"{f}: payload file exceeds {limit_mb}MB limit ({st.st_size:,} bytes)",
            )
        data = f.read_bytes()
        payload = json.loads(data.decode("utf-8"))
        age = now.timestamp() - st.st_mtime
    except (OSError, ValueError, RecursionError):
        return None, b"", f"{f}: not readable as JSON"
    if stats is not None and f.resolve() == stats.resolve():
        return None, b"", f"{f}: this is the figures file itself, not a fetch — nothing was written"
    if age > PAYLOAD_MAX_AGE_S:
        return (
            None,
            b"",
            f"{f}: last saved more than {PAYLOAD_MAX_AGE_S // 60} minutes ago — "
            "fetch the figures again for this refresh. (This guard looks at the file's age "
            "only; it cannot tell when the figures inside were fetched.)",
        )
    if age < -PAYLOAD_FUTURE_SKEW_S:
        return (
            None,
            b"",
            f"{f}: the payload file's modified time is ahead of this machine's clock by more "
            f"than {PAYLOAD_FUTURE_SKEW_S} seconds — check the clock or re-save the file. "
            "(This guard looks at the file's age only; it cannot tell when the figures "
            "inside were fetched.)",
        )
    return payload, data, None


def _row_problem(f: Path, seq: dict, seen: set[str]) -> str | None:
    sid = printable(row_id(seq))
    if sequence_totals(seq) is None:
        return (
            f"{f}: sequence {sid} has neither emails.status.delivered nor "
            "prospects[].contacted — not written as zero"
        )
    if row_id(seq) in seen:
        return f"{f}: sequence {sid} appears twice in this refresh"
    seen.add(row_id(seq))
    return None


def load_payloads(
    files: list[Path], now: datetime, stats: Path | None = None
) -> tuple[list[tuple[dict, str]], str | None]:
    """``[(row, file_sha)]`` for every sequence in the files, or ``(_, reason)`` to refuse."""
    out: list[tuple[dict, str]] = []
    seen: set[str] = set()
    for f in files:
        payload, data, why = read_payload(f, now, stats)
        if why:
            return [], why
        seqs, why = check_payload(payload)
        if why:
            return [], f"{f}: {why}"
        if len(out) + len(seqs) > rules.MAX_ROWS:
            return [], (
                f"{f}: with this file the refresh would hold more than the {rules.MAX_ROWS} "
                "sequences a page reads — write fewer at a time"
            )
        sha = hashlib.sha256(data).hexdigest()
        for seq in seqs:
            if why := _row_problem(f, seq, seen):
                return [], why
            out.append((seq, sha))
    return out, None


def profile_problem(
    profile: str, content_root: Path | None, profiles_root: Path | None = None
) -> str | None:
    """Why this profile cannot be written to — None when it exists as the dashboard reads it.

    The dashboard accepts a profile that has a ``profiles/<p>`` folder or a ``content/<p>``
    folder (``render._validate_profile``), so a first-run tenant with only the first can render
    its page. The writer applies the same rule, and still never makes a profile: a typo
    (``--profile acmee``) would otherwise create a tenant tree, report success, and leave the real
    profile's figures as stale as before. A *file* named like the profile is not a profile.
    """
    try:
        name = _safe_segment(profile, "profile")
    except ValueError:
        return f"'{printable(profile)}' is not a usable profile name"
    content = (content_root or resolve_content_root()) / name
    profiles = (profiles_root or resolve_profiles_root()) / name
    if content.is_dir() or profiles.is_dir():
        return None
    shown = printable(name)
    return (
        f"there is no profile '{shown}' here (no folder '{shown}' under the content root or the "
        f"profiles root) — nothing was written. Create content/{shown}/ or profiles/{shown}/ "
        "first, or correct the name"
    )
