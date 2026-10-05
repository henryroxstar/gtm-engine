"""Send windows configuration: maps country to sending schedule.

PRD 2026-10-02 (Static-Email Mode):
Saleshandy enforces one schedule per sequence and one time zone per schedule.
To avoid sending emails to US prospects at 02:00 local time, static mode
splits prospects by region using `knowledge/send-windows.toml`.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .paths import _safe_segment, resolve_profiles_root

FILE = "send-windows.toml"


@dataclass(frozen=True)
class ScheduleInfo:
    id: str
    name: str = ""
    timezone: str = ""


@dataclass(frozen=True)
class SendWindows:
    default_schedule: str
    schedules: dict[str, ScheduleInfo] = field(default_factory=dict)
    countries: dict[str, str] = field(default_factory=dict)

    def schedule_for(self, country: str) -> str:
        code = (country or "").strip().upper()
        # Common ISO conversions for human-written country names
        if code in ("UNITED STATES", "USA", "UNITED STATES OF AMERICA", "U.S.", "U.S.A."):
            code = "US"
        elif code in ("CANADA", "CAN"):
            code = "CA"
        elif code in ("SINGAPORE", "SG", "SGP"):
            code = "SG"
        elif code in ("UNITED KINGDOM", "UK", "GREAT BRITAIN", "GBR"):
            code = "GB"
        return self.countries.get(code, self.default_schedule)


def parse_send_windows(text: str) -> SendWindows:
    data = tomllib.loads(text)
    default_sched = str(data.get("default_schedule") or "").strip()
    if not default_sched:
        raise ValueError("send-windows.toml: `default_schedule` is required")

    scheds: dict[str, ScheduleInfo] = {}
    for sid, info in (data.get("schedules") or {}).items():
        if isinstance(info, dict):
            scheds[sid] = ScheduleInfo(
                id=sid,
                name=str(info.get("name") or ""),
                timezone=str(info.get("timezone") or ""),
            )
        else:
            scheds[sid] = ScheduleInfo(id=sid)

    countries = {
        str(c).strip().upper(): str(sid).strip() for c, sid in (data.get("countries") or {}).items()
    }
    return SendWindows(default_schedule=default_sched, schedules=scheds, countries=countries)


def load_send_windows(profile: str, profiles_root: Path | None = None) -> SendWindows:
    root = profiles_root or resolve_profiles_root()
    path = root / _safe_segment(profile, "profile") / "knowledge" / FILE
    if not path.is_file():
        # Fallback default: empty schedule id requiring caller configuration
        return SendWindows(default_schedule="")
    return parse_send_windows(path.read_text(encoding="utf-8"))
