"""The Two-Door video routing classification engine (PRD 3 Axis 1).

Classifies operator intent into:
  - Door 1: "I have footage" (video-footage DAGs: repurpose-clips, demo-clips, restyle-shorts)
  - Door 2: "Create from an idea" (generative synthesis: short-form-video, presenter-video, live-action-video)

Classifications are data-grounded:
  - URLs (YouTube, Vimeo) or local video file inputs -> Door 1
  - Duration > 180s -> repurpose-clips
  - Screen recordings / .mov -> demo-clips
  - Topic/idea/concept text -> Door 2 (evaluates profile entitlements for presenter vs faceless vs live-action)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .video_preflight import Preflight


@dataclass(frozen=True)
class RouteDecision:
    door: int  # 1 or 2
    door_name: str
    recommended_lane: str
    reason: str
    requires_clarification: bool = False
    clarification_question: str | None = None


_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com|youtu\.be|vimeo\.com|loom\.com)/[^\s]+", re.IGNORECASE
)
_VIDEO_EXTS = frozenset({".mp4", ".mov", ".mkv", ".webm"})


def classify_input(
    prompt_or_path: str,
    preflight: Preflight,
    *,
    duration_sec: float | None = None,
    is_screen_recording: bool = False,
) -> RouteDecision:
    """Classify user intent into Door 1 or Door 2 based on input and profile entitlements."""
    text = prompt_or_path.strip()

    # Check for Door 1 (Footage / URL / File)
    has_url = bool(_URL_RE.search(text))
    is_file = False
    first_token = text.split()[0] if text.split() else ""
    if Path(text).suffix.lower() in _VIDEO_EXTS or Path(first_token).suffix.lower() in _VIDEO_EXTS:
        is_file = True

    if (
        has_url
        or is_file
        or "footage" in text.lower()
        or "clip this" in text.lower()
        or "recording" in text.lower()
    ):
        # Door 1: Footage-first
        if is_screen_recording or "demo" in text.lower() or "screen" in text.lower():
            target = "demo-clips"
            reason = "Input is a product screen recording -> demo-clips"
        elif (
            duration_sec is not None
            and duration_sec > 180
            or "webinar" in text.lower()
            or "long-form" in text.lower()
        ):
            target = "repurpose-clips"
            reason = "Input is long-form recording (>3 min) -> repurpose-clips"
        elif "restyle" in text.lower():
            target = "restyle-shorts"
            reason = "Input requests visual restyling -> restyle-shorts"
        else:
            target = "repurpose-clips"
            reason = "Default footage ingestion -> repurpose-clips"

        return RouteDecision(
            door=1,
            door_name="I have footage",
            recommended_lane=target,
            reason=reason,
        )

    # Ambiguous prompt handling: if prompt contains no concept words and no footage pointers
    words = text.split()
    if len(words) <= 2 and not any(
        w in text.lower() for w in ("idea", "script", "about", "make", "video", "create")
    ):
        return RouteDecision(
            door=2,
            door_name="Clarification Needed",
            recommended_lane=preflight.default_lane or "short-form-video",
            reason="Ambiguous operator request",
            requires_clarification=True,
            clarification_question=(
                "Do you have existing footage/recordings to clip (Door 1), "
                "or are we creating a new video from an idea (Door 2)?"
            ),
        )

    # Door 2: Concept-first
    # Evaluate entitlements from preflight
    ready_lanes = {ls.variant: ls for ls in preflight.ready_lanes}

    # If item has story protagonist or live action requested
    if (
        "shoot" in text.lower()
        or "live action" in text.lower()
        or (preflight.constraints.story_capture is not None)
    ):
        target = "live-action-video"
        reason = "Story protagonist or physical shoot detected -> live-action-video"
    elif "presenter-video" in ready_lanes and (
        "presenter" in text.lower() or "talking head" in text.lower() or "avatar" in text.lower()
    ):
        target = "presenter-video"
        reason = "Digital twin presenter approved and requested -> presenter-video"
    elif "presenter-video" in ready_lanes:
        # Profile has presenter ready, but default to faceless unless asked or presenter preferred
        target = "short-form-video"
        reason = "New idea -> default faceless short-form-video (presenter available on request)"
    else:
        target = "short-form-video"
        reason = "New idea on bare/unconfigured profile -> faceless short-form-video"

    return RouteDecision(
        door=2,
        door_name="Create from an idea",
        recommended_lane=target,
        reason=reason,
    )
