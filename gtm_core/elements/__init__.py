"""The element library — named, reusable reference subjects in the tenant's own content tree.

See :mod:`gtm_core.elements.model` for what an element is and why its pose names are a closed
vocabulary, and :mod:`gtm_core.elements.store` for the layout and the write discipline.
"""

from __future__ import annotations

from .cli import main
from .legacy_import import plan_import, ratio_of, read_png_size
from .model import (
    DEPICTS,
    IDENTITY_POSES_CHARACTER,
    IDENTITY_POSES_OTHER,
    KINDS,
    POSE_NAMESPACES,
    Element,
    ElementError,
    Pose,
    validate_pose_name,
)
from .readme import render_readme
from .refs import references_to
from .store import element_dir, element_path, list_slugs, load, poses_dir, resolve_pose_files, write

__all__ = [
    "DEPICTS",
    "Element",
    "ElementError",
    "IDENTITY_POSES_CHARACTER",
    "IDENTITY_POSES_OTHER",
    "KINDS",
    "POSE_NAMESPACES",
    "Pose",
    "element_dir",
    "element_path",
    "list_slugs",
    "load",
    "main",
    "plan_import",
    "poses_dir",
    "ratio_of",
    "read_png_size",
    "references_to",
    "render_readme",
    "resolve_pose_files",
    "validate_pose_name",
    "write",
]
