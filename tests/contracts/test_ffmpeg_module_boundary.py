"""Only three modules in ``gtm_core/`` may EXECUTE the ffmpeg/ffprobe binaries.

Pixel, container and loudness work therefore lives in exactly three named, reviewed places, the
same shape as ``tests/media/test_captions_module_boundary.py`` does for Pillow.

WHY THIS EXISTS (2026-08-18). A raw ``ffmpeg`` subprocess is invisible to every other gate in this
repo: the §R6 semgrep rule is ``languages: [python]`` and matches *import statements*, so a
subprocess never trips it (the blind spot already documented in ``gtm_core/media_fetch.py``), and
before this date ``ffmpeg`` was absent from ``_DANGEROUS_PROGRAMS``. A session assembled a
shippable video by hand-authoring an ffmpeg filter chain in Bash and reimplementing
``gtm_core.captions`` in a scratch script — bypassing ``video_finish``'s single-grade invariant,
its safe-area caption placement and its -14 LUFS normalisation, and shipping an unpolished
artifact. The Bash floor now denies the binaries; this test denies the same thing one layer down,
in code, so the rule survives a future edit to the denylist.

DELIBERATELY NOT IN ``tests/media/``: that package's ``conftest.py`` skips collection when ffmpeg
is absent from the host. This check is pure AST and must run everywhere — a guard that disarms
itself on the machines least likely to have the pipeline installed is not a guard.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GTM_CORE = REPO / "gtm_core"

#: The binaries whose invocation is confined. ``argv[0]`` form only — a module that merely names
#: ffmpeg in prose (``media_fetch.py``, ``render_manifest.py``, the skill manifests) is not
#: executing it and is not in scope.
_MEDIA_BINARIES = frozenset({"ffmpeg", "ffprobe"})

#: The complete, deliberate allowlist, keyed by path relative to ``gtm_core/``. Adding a fourth
#: entry is a boundary change: it needs the same "why here, and why not inside video_finish's
#: orchestration" reasoning the existing three carry in their own docstrings — not just a green
#: test. Removing the hand-rolled-chain prohibition from a skill body is a separate regression,
#: pinned by ``test_video_finish_prohibition_wired.py``.
_ALLOWED_MEDIA_MODULES = frozenset(
    {
        "video_finish.py",  # owns grade / stitch / mux / loudness — the single-grade invariant
        "video_lint.py",  # owns probe-based verification (ffprobe only)
        "cover_frame.py",  # owns single-frame extraction for cover stills
    }
)


def _resolved_binaries(path: Path) -> set[str]:
    """Return the media binaries this module resolves for execution.

    Matches the repo's one real invocation shape — ``shutil.which("ffmpeg")`` — plus a bare
    ``subprocess.*([...])`` argv whose first element is one of the binaries, so a future caller
    that skips ``which`` is still caught.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # shutil.which("ffmpeg") / which("ffprobe")
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name == "which":
            for arg in node.args:
                if isinstance(arg, ast.Constant) and arg.value in _MEDIA_BINARIES:
                    found.add(arg.value)
        # subprocess.run(["ffmpeg", ...]) — argv[0] literal
        for arg in node.args:
            if isinstance(arg, (ast.List, ast.Tuple)) and arg.elts:
                head = arg.elts[0]
                if isinstance(head, ast.Constant) and head.value in _MEDIA_BINARIES:
                    found.add(head.value)
    return found


def _modules() -> list[tuple[str, Path]]:
    return [(str(p.relative_to(GTM_CORE)), p) for p in sorted(GTM_CORE.rglob("*.py"))]


def test_only_the_allowlisted_modules_execute_ffmpeg():
    offenders = {
        rel: sorted(found)
        for rel, path in _modules()
        if rel not in _ALLOWED_MEDIA_MODULES and (found := _resolved_binaries(path))
    }
    assert offenders == {}, (
        "ffmpeg/ffprobe executed outside "
        f"{sorted(_ALLOWED_MEDIA_MODULES)}: {offenders}. Route the work through "
        "gtm_core.video_finish (produce) or gtm_core.video_lint (verify) instead of adding a "
        "second orchestration site."
    )


def test_every_allowlisted_module_does_execute_ffmpeg():
    """The allowlist is a ceiling, not a promise — catches drift the other direction (a stale
    entry for a module that stopped shelling out)."""
    for rel in sorted(_ALLOWED_MEDIA_MODULES):
        path = GTM_CORE / rel
        assert path.exists(), f"{rel} is allowlisted but does not exist"
        assert _resolved_binaries(path), f"{rel} is allowlisted but resolves no media binary"


def test_media_binaries_are_on_the_bash_deny_floor():
    """The code boundary and the Bash floor have to agree, or an agent can still hand-roll a
    chain in a shell while this file stays green."""
    from agent.permissions import _DANGEROUS_PROGRAMS

    missing = sorted(_MEDIA_BINARIES - set(_DANGEROUS_PROGRAMS))
    assert missing == [], f"media binaries missing from the Bash deny floor: {missing}"
