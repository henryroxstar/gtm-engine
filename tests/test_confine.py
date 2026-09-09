"""gtm_core.confine — the one shared path-confinement predicate (2026-09-06).

The predicate had been written five times across the tree before a sixth caller arrived: a
file-path parameter on an MCP worker that ships the bytes to a third party. That is the caller
where a loosened copy is an exfiltration primitive, so the predicate moved into one module and
the ``video_finish`` guards became wrappers. These tests pin the predicate's behaviour once, and
pin that the wrappers changed nothing — type, message, or exit-code class — for the callers that
already depended on them.

Every refusal here sits beside the accepting case (docs/RULES.md §R12): a confinement suite with
no positive control passes on a helper that accepts nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_core import confine
from gtm_core.confine import (
    ConfinementError,
    confined_dir,
    confined_output_path,
    confined_source_file,
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "content"
    (r / "acme").mkdir(parents=True)
    return r


# ── read side ─────────────────────────────────────────────────────────────────


def test_a_file_inside_the_root_is_returned_resolved(root: Path):
    """Positive control. Without this, every refusal below passes on a helper that refuses all."""
    inside = root / "acme" / "ok.png"
    inside.write_bytes(b"png")
    assert confined_source_file(inside, content_root=root) == inside.resolve()
    # A relative-to-root spelling and a `~`-free string spelling resolve to the same file.
    assert confined_source_file(str(inside), content_root=root) == inside.resolve()


def test_a_sibling_path_outside_the_root_is_refused(root: Path, tmp_path: Path):
    outside = tmp_path / "elsewhere" / "leak.png"
    outside.parent.mkdir()
    outside.write_bytes(b"png")
    with pytest.raises(ConfinementError, match="outside the resolved content root"):
        confined_source_file(outside, content_root=root)


def test_dot_dot_traversal_that_escapes_after_resolution_is_refused(root: Path, tmp_path: Path):
    """The string starts inside the root; the resolved path does not. Resolution runs first."""
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"png")
    with pytest.raises(ConfinementError, match="outside the resolved content root"):
        confined_source_file(root / "acme" / ".." / ".." / "secret.png", content_root=root)


def test_a_symlink_inside_the_root_pointing_outside_is_refused_on_its_target(
    root: Path, tmp_path: Path
):
    """The check is about where the bytes live, not what the path is called."""
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"secret")
    link = root / "acme" / "innocent.png"
    link.symlink_to(outside)
    with pytest.raises(ConfinementError, match="outside the resolved content root"):
        confined_source_file(link, content_root=root)


def test_a_symlink_from_outside_pointing_into_the_root_is_accepted(root: Path, tmp_path: Path):
    """The inverse, pinned deliberately: `resolve()` lands inside, so the bytes ARE tenant bytes.

    Recorded as an accepted property in SECURITY-SELF-ASSESSMENT rather than left for someone to
    rediscover as a hole.
    """
    inside = root / "acme" / "real.png"
    inside.write_bytes(b"png")
    link = tmp_path / "shortcut.png"
    link.symlink_to(inside)
    assert confined_source_file(link, content_root=root) == inside.resolve()


def test_the_read_side_honours_an_arbitrary_root_not_only_the_content_root(tmp_path: Path):
    """`video_finish.narration` confines to a shot-list base dir. The helper must not quietly
    swap that for `resolve_content_root()` — it would widen that caller's boundary."""
    base = tmp_path / "shotlist"
    base.mkdir()
    asset = base / "vo.wav"
    asset.write_bytes(b"wav")
    assert confined_source_file(asset, content_root=base) == asset.resolve()
    other = tmp_path / "content" / "acme" / "vo.wav"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"wav")
    with pytest.raises(ConfinementError):
        confined_source_file(other, content_root=base)


def test_max_bytes_refuses_an_oversized_file_by_name_and_size(root: Path):
    big = root / "acme" / "big.png"
    big.write_bytes(b"x" * 100)
    with pytest.raises(ConfinementError) as exc:
        confined_source_file(big, content_root=root, max_bytes=99)
    msg = str(exc.value)
    assert "big.png" in msg and "100 bytes" in msg and "99" in msg, (
        "an over-cap refusal must name the file and both numbers, or the operator cannot act on it"
    )
    # The cap is a ceiling, not a strict bound, and None means no cap at all.
    assert confined_source_file(big, content_root=root, max_bytes=100) == big.resolve()
    assert confined_source_file(big, content_root=root, max_bytes=None) == big.resolve()


def test_a_missing_file_and_a_directory_are_both_refused(root: Path):
    with pytest.raises(ConfinementError, match="does not exist"):
        confined_source_file(root / "acme" / "nope.png", content_root=root)
    with pytest.raises(ConfinementError, match="does not exist"):
        confined_source_file(root / "acme", content_root=root)


def test_the_action_verb_lands_in_the_refusal(root: Path, tmp_path: Path):
    outside = tmp_path / "x.png"
    outside.write_bytes(b"png")
    with pytest.raises(ConfinementError, match="refusing to polish a file outside"):
        confined_source_file(outside, content_root=root, action="polish a file")


# ── write side ────────────────────────────────────────────────────────────────


def test_output_path_checks_the_parent_and_dir_checks_itself(root: Path, tmp_path: Path):
    out = confined_output_path(root / "acme" / "new.mp4", content_root=root)
    assert out.parent == (root / "acme").resolve()
    with pytest.raises(ConfinementError, match="refusing to write outside"):
        confined_output_path(tmp_path / "elsewhere" / "new.mp4", content_root=root)

    assert confined_dir(root / "beats", content_root=root) == (root / "beats").resolve()
    with pytest.raises(ConfinementError, match="refusing to write outside"):
        confined_dir(tmp_path / "elsewhere", content_root=root)


def test_content_root_none_falls_back_to_the_env_resolved_root(tmp_path: Path, monkeypatch):
    """Eight `video_finish` CLI sites pass `args.content_root`, which defaults to None."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    (a / "p").mkdir(parents=True)
    (b / "p").mkdir(parents=True)

    monkeypatch.setenv("GTM_CONTENT_ROOT", str(a))
    assert confined_output_path(a / "p" / "x.mp4", content_root=None).parent == (a / "p").resolve()
    with pytest.raises(ConfinementError):
        confined_output_path(b / "p" / "x.mp4", content_root=None)

    # Moving the env moves the boundary — a path legal under one root is refused under the other.
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(b))
    assert confined_dir(b / "p", content_root=None) == (b / "p").resolve()
    with pytest.raises(ConfinementError):
        confined_dir(a / "p", content_root=None)


# ── exception class ───────────────────────────────────────────────────────────


def test_confinement_error_is_not_a_value_error():
    """`video_finish/cli.py` maps ValueError to exit 4 and PolishError to exit 2. A refusal that
    could be caught by the ValueError arm would change a golden-pinned exit code."""
    assert not issubclass(ConfinementError, ValueError)
    assert issubclass(ConfinementError, Exception)


def test_the_module_is_stdlib_only():
    """It sits under the §R6 egress scan; a stray HTTP import here would be a new egress point."""
    import inspect

    src = inspect.getsource(confine)
    for banned in ("httpx", "requests", "urllib", "aiohttp"):
        assert banned not in src, f"gtm_core/confine.py must not import {banned}"


# ── the video_finish wrappers changed nothing ────────────────────────────────


def test_video_finish_wrappers_keep_their_type_and_their_exact_messages(root: Path, tmp_path: Path):
    """The delegation must be invisible to every existing caller: same class, same string."""
    from gtm_core import video_finish as vf

    outside_file = tmp_path / "elsewhere" / "x.mp4"
    outside_file.parent.mkdir()
    outside_file.write_bytes(b"mp4")
    r = root.resolve()

    with pytest.raises(vf.PolishError) as e1:
        vf._safe_asset_path(outside_file, content_root=root)
    assert str(e1.value) == (
        f"refusing to polish a file outside the resolved content root: "
        f"{outside_file.resolve()} (root: {r})"
    )

    with pytest.raises(vf.PolishError) as e2:
        vf._safe_asset_path(root / "acme" / "missing.mp4", content_root=root)
    assert (
        str(e2.value) == f"source file does not exist: {(root / 'acme' / 'missing.mp4').resolve()}"
    )

    with pytest.raises(vf.PolishError) as e3:
        vf._confined_output(outside_file, content_root=root)
    assert str(e3.value) == (
        f"refusing to write outside the resolved content root: {outside_file.resolve()} (root: {r})"
    )

    with pytest.raises(vf.PolishError) as e4:
        vf._confined_dir(tmp_path / "elsewhere", content_root=root)
    assert str(e4.value) == (
        f"refusing to write outside the resolved content root: "
        f"{(tmp_path / 'elsewhere').resolve()} (root: {r})"
    )

    # And the accepting case still accepts, through the wrapper.
    inside = root / "acme" / "ok.mp4"
    inside.write_bytes(b"mp4")
    assert vf._safe_asset_path(inside, content_root=root) == inside.resolve()


def test_video_finish_still_exports_all_three_names():
    """`video_finish/__init__.py:96` is how every existing test reaches them."""
    from gtm_core import video_finish as vf

    for name in ("_confined_dir", "_confined_output", "_safe_asset_path"):
        assert callable(getattr(vf, name)), f"video_finish no longer exports {name}"
