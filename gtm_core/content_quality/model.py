from __future__ import annotations

try:
    from ..video_lint import SAFE_AREAS
except ModuleNotFoundError:
    # Same reason as the guarded import in post.py: the video tier is withheld from the
    # public cut, and this package's TEXT half must keep working there. The only use of
    # SAFE_AREAS here is deriving _RATIO_SLUG_TO_COLON below, which the video path alone
    # consults — and that path refuses outright when the tier is absent.
    SAFE_AREAS = {}

_BLOCK = "block"
_WARN = "warn"

#: Video/reel formats that may trigger synthetic-identity disclosure requirements.
#:
#: ``short`` added 2026-09-04. It was missing while ``hooks.toml``, ``format-router`` and the
#: creator packs all treated it as a video format, so a ``format: short`` item took the *text*
#: post-check and skipped the ``disclosure.line`` requirement entirely — a fail-OPEN hole in an
#: EU AI Act Art. 50 gate (CLAUDE.md "Synthetic media must be disclosed"). Two shipped acme
#: scripts carry ``format: short``. Adding it is monotone-stricter: it can only add a blocking
#: disclosure requirement to items that previously had none.
_VIDEO_FORMATS = frozenset({"reel", "short", "clip"})

#: FinishManifest.ratio is stored filename-safe ("9x16", matching gtm_core.video_finish's
#: _ratio_slug and the finish-<ratio>.json filename convention), but SAFE_AREAS and
#: video_lint.evaluate() key on the colon form ("9:16"). Reverse-lookup built from SAFE_AREAS
#: itself rather than a blind ":"<->"x" string replace, so it only remaps ratios that are
#: actually known. Fixed 2026-08-17 — this silently no-op'd the caption-safe-area check
#: (checks["captions_inside_safe_area"] stayed None forever, read as "nothing to report" rather
#: than "never checked") and crashed the V1-V4 video-lint call outright, for every video item,
#: once the two upstream manifest-discovery bugs stopped masking it.
_RATIO_SLUG_TO_COLON = {ratio.replace(":", "x"): ratio for ratio in SAFE_AREAS}

#: LinkedIn/X/Instagram/Facebook optimization playbooks.
_PLATFORM_PLAYBOOKS = {
    "linkedin": "docs/linkedin-optimization.md",
    "x": "docs/x-optimization.md",
    "instagram": "docs/instagram-optimization.md",
    "facebook": "docs/facebook-optimization.md",
}
