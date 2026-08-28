"""captions.py, cover_frame.py, and screen_ui.py are the ONLY modules in gtm_core/ permitted to
import Pillow (docstring invariant in each). Pixel-level image work therefore lives in exactly
three named, reviewed places — the AST check tier D wants for "no skill body and no other
gtm_core module draws/inspects a frame itself." cover_frame.py (Phase E) and screen_ui.py (the
intercut lane, P4) are each a deliberate, documented widening of the original one-module rule, the
same shape as captions.py's own widening of the SDK-free invariant for text rasterization — never
grow this set by accident.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GTM_CORE = REPO / "gtm_core"

#: The complete, deliberate allowlist. Adding a third entry is a boundary change — it needs the
#: same "why here, not in the ffmpeg-orchestration layer" reasoning captions.py's docstring and
#: cover_frame.py's docstring both carry, not just a passing test.
_ALLOWED_PIL_MODULES = frozenset({"captions.py", "cover_frame.py", "screen_ui.py"})


def _imports_pil(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "PIL" or alias.name.startswith("PIL.") for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and (node.module == "PIL" or node.module.startswith("PIL.")):
                return True
    return False


def test_only_the_allowlisted_modules_import_pil():
    offenders = [
        p.relative_to(REPO)
        for p in GTM_CORE.rglob("*.py")
        if p.name not in _ALLOWED_PIL_MODULES and _imports_pil(p)
    ]
    assert offenders == [], f"PIL imported outside {sorted(_ALLOWED_PIL_MODULES)}: {offenders}"


def test_every_allowlisted_module_does_import_pil():
    """The allowlist is a ceiling, not a promise — this catches drift the other direction (a
    stale entry for a module that stopped needing Pillow)."""
    for name in _ALLOWED_PIL_MODULES:
        assert _imports_pil(GTM_CORE / name), f"{name} is allowlisted but does not import PIL"
