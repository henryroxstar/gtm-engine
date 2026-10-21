"""cockpit.epoch — the boot epoch carried by every inline button (F2).

A ``callback_query`` carries no timestamp, and a legitimate approval can be tapped hours after its
message — so a button is judged by *which boot issued it*, never by age. Every button's
``callback_data`` ends with ``@<epoch>``; a press whose epoch is not this boot's is refused and the
gate is re-sent (see :mod:`cockpit.guard`).

``stamp`` is a no-op until the cockpit has minted an epoch (:func:`set_epoch`), so a keyboard built in a
unit test or by a headless caller is byte-identical to before. Telegram caps ``callback_data`` at 64
bytes; ``tests/cockpit/test_guard.py`` asserts the longest real stamped value fits.
"""

from __future__ import annotations

_SEP = "@"
_epoch: str = ""


def set_epoch(epoch: str) -> None:
    global _epoch
    _epoch = epoch


def current() -> str:
    return _epoch


def stamp(data: str) -> str:
    """Return ``data`` carrying this boot's epoch (unchanged when no epoch is set)."""
    return f"{data}{_SEP}{_epoch}" if _epoch else data


def split(data: str) -> tuple[str, str | None]:
    """Split ``stamped`` callback data into ``(clean, epoch)``; ``epoch`` is None when absent."""
    clean, sep, epoch = data.rpartition(_SEP)
    if not sep or not clean or not epoch:
        return data, None
    return clean, epoch
