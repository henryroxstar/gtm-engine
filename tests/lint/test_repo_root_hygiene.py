"""Repository root hygiene gate (§R12, §R13).

Enforces that the repository root never accumulates ad-hoc agent scratch scripts
(``patch_*.py``, ``scratch_*.py``, ``test_script.py``), temporary comment drafts
(``comment_*.md``), or scratch directories (``scratch/``, ``scratchpad/``).

Previously, root scratch files were masked via wildcard rules in ``.gitignore``,
which prevented ``git status`` from surfacing them while allowing dead scripts
and potential tenant residue to accumulate on disk. Per §R13, the prohibition
lives here in code and inspects the filesystem directly (not ``git ls-files``),
so gitignored clutter still fails the gate.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Closed allowlist of non-hidden files permitted directly in the repository root
#: across both the private tree and the public OSS carve.
ALLOWED_ROOT_FILES: frozenset[str] = frozenset(
    {
        # Documentation & operator guides
        "AGENTS.md",
        "CHANGELOG.md",
        "CLAUDE.md",
        "CODEOWNERS",
        "CODE_OF_CONDUCT.md",
        "CONTRIBUTING.md",
        "END-USER-ONBOARDING.md",
        "IMAGEGEN.md",
        "LICENSE",
        "NOTICE",
        "PENDING.md",
        "PRESALES.md",
        "PROSPECTING.md",
        "README.md",
        *(f"README.{lang}.md" for lang in ("de", "es", "ja", "ko", "zh-CN")),
        "SECURITY.md",
        "VERSION",
        "VIDEOGEN.md",
        # Build, packaging & runtime configuration
        "Dockerfile",
        "actionlint",
        "demo.tape",
        "docker-compose.override.yml",
        "docker-compose.override.yml.example",
        "docker-compose.yml",
        "doppler.yaml",
        "pyproject.toml",
        "start.sh",
        "uv.lock",
    }
)

#: Directories that must never exist at the repository root (ephemeral work belongs in tmp/).
FORBIDDEN_ROOT_DIRS: frozenset[str] = frozenset(
    {
        "__pycache__",
        "scratch",
        "scratchpad",
    }
)


def check_root_hygiene(root: Path) -> list[str]:
    """Return human-readable violation strings for any disallowed entry in ``root``."""
    violations: list[str] = []

    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        name = entry.name

        if name.startswith(".claude-scratch"):
            violations.append(
                f"{name}: agent scratch file in repo root (use tmp/ and clean up before completion)"
            )
            continue

        # Hidden dotfiles/dotdirs (.git, .venv, .ruff_cache, .env, etc.) are tool/VCS state
        if name.startswith("."):
            continue

        if entry.is_dir():
            if name in FORBIDDEN_ROOT_DIRS:
                violations.append(
                    f"{name}/: forbidden directory in repo root (use tmp/ for ephemeral scratch work)"
                )
            continue

        if entry.suffix == ".py":
            violations.append(
                f"{name}: Python script in repo root (no .py files belong at root; "
                "use native edit tools instead of patch_*.py, or tmp/ for ephemeral scripts)"
            )
            continue

        if name not in ALLOWED_ROOT_FILES:
            violations.append(
                f"{name}: unrecognized file in repo root (move to tmp/, scripts/, or docs/, "
                "or add to ALLOWED_ROOT_FILES in tests/lint/test_repo_root_hygiene.py if canonical)"
            )

    return violations


def test_repo_root_has_no_clutter() -> None:
    violations = check_root_hygiene(REPO_ROOT)
    assert not violations, (
        "Repository root contains disallowed files or scratch directories:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


def test_positive_control_catches_root_py_and_scratch(tmp_path: Path) -> None:
    """§R12 positive control: prove the scanner rejects root .py files, scratch dirs, and stray notes."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    assert check_root_hygiene(tmp_path) == []

    # Plant known-bad artifacts matching historical root clutter
    (tmp_path / "patch_auth.py").write_text("print('patch')\n", encoding="utf-8")
    (tmp_path / "scratch_refine.py").write_text("print('scratch')\n", encoding="utf-8")
    (tmp_path / "comment_317.md").write_text("draft comment\n", encoding="utf-8")
    (tmp_path / ".claude-scratch-123").write_text("temp\n", encoding="utf-8")
    (tmp_path / "scratchpad").mkdir()
    (tmp_path / "__pycache__").mkdir()

    hits = check_root_hygiene(tmp_path)
    assert len(hits) == 6, f"Expected 6 violations, got {hits}"
    assert any("patch_auth.py" in h for h in hits)
    assert any("scratch_refine.py" in h for h in hits)
    assert any("comment_317.md" in h for h in hits)
    assert any(".claude-scratch-123" in h for h in hits)
    assert any("scratchpad/" in h for h in hits)
    assert any("__pycache__/" in h for h in hits)
