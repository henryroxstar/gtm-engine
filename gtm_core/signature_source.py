"""Where an email's closing signature comes from: the body, or the mailbox.

The outreach linter's ``sign-off`` rule asks that an email body end with the sender's bare name.
That is right for a mailbox with no signature of its own. It is wrong for one that appends a
signature to everything it sends: the body's sign-off and the mailbox's then stack, and every email
reads "Regards, Dana" followed by "Regards, Dana Ortega / Title / phone / address" — two sign-offs,
which the 2026-10-02 post-mortem listed among the things that make the mail read as bulk.

A profile says which it is with one line in ``profiles/<tenant>/PROFILE.md`` (next to
``email_signature:``)::

    signature_source: mailbox        # body | mailbox

* ``body`` — the default, and what every profile that does not set the line gets. The body ends
  with the bare sign-off name and the linter requires it.
* ``mailbox`` — the sequencer appends the signature. The ``sign-off`` rule inverts: a body whose
  last line is the sign-off name (or a bare valediction such as ``Regards,``) is an ERROR, and a
  body that ends without one is clean.

An unknown value is refused (``ValueError``) rather than read as ``body``: a typo that quietly
restored the old rule would hide the double sign-off the line was written to remove. A profile with
no PROFILE.md, or one that does not set the line, is ``body``.

Stdlib plus :func:`gtm_core.paths.resolve_profiles_root`.
"""

from __future__ import annotations

from pathlib import Path

from gtm_core.paths import _safe_segment, resolve_profiles_root

BODY = "body"
MAILBOX = "mailbox"
VALUES = (BODY, MAILBOX)
KEY = "signature_source"


def read_signature_source(profile: str, profiles_root: Path | None = None) -> str:
    """``"body"`` or ``"mailbox"`` for ``profile``; ``"body"`` when the profile does not say.

    Only an assignment line counts (prose mentioning the key is skipped by requiring the line to
    start with it), and the first one wins, as for every other PROFILE.md key.
    """
    path = (profiles_root or resolve_profiles_root()) / _safe_segment(profile, "profile")
    path = path / "PROFILE.md"
    if not path.is_file():
        return BODY
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith(f"{KEY}:"):
            continue
        value = line.split(":", 1)[1].split("#", 1)[0].strip().strip("\"'").strip().lower()
        if value in VALUES:
            return value
        raise ValueError(
            f"{path} sets `{KEY}: {value}`, which is not one of {' | '.join(VALUES)} — "
            "fix the line; an unknown value is not read as the default"
        )
    return BODY
