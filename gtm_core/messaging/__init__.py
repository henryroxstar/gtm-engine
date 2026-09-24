"""Outbound facts as tenant data: claims, proof, angles, and the seats they address.

The package surface is deliberately thin — :func:`load` and the types it returns. Every
consumer (the matrix view, the angle resolver, the outreach linter) re-derives from the
**same** loader, so a fact can never be true on one surface and false on another.
"""

from __future__ import annotations

from .angle_status import (
    EVIDENCE_FIELDS,
    HISTORY_PREFIX,
    AngleStatusError,
    parse_evidence,
    promote,
    retire,
)
from .matrix_view import (
    BANNER,
    BANNER_MARK,
    MATRIX_FILE,
    MatrixError,
    is_generated,
    matrix_path,
    matrix_state,
    render,
    write_matrix,
)
from .registry import (
    ANGLE_STATUSES,
    ANGLES_FILE,
    CLAIM_STATUSES,
    CLAIMS_FILE,
    FIGURE_KINDS,
    OPENER_KINDS,
    PROOF_FILE,
    PROOF_KINDS,
    VERIFIED,
    Angle,
    Claim,
    Proof,
    Registry,
    RegistryError,
    load,
)
from .resolve import (
    NO_ANCHOR_FOR_MARKET,
    NO_VERIFIED_CLAIM,
    PREMISE_UNSUPPORTED,
    REFUSALS,
    SEAT_UNRESOLVED,
    AngleResolution,
    angle_for,
)

__all__ = [
    "ANGLES_FILE",
    "ANGLE_STATUSES",
    "BANNER",
    "BANNER_MARK",
    "CLAIMS_FILE",
    "CLAIM_STATUSES",
    "EVIDENCE_FIELDS",
    "FIGURE_KINDS",
    "HISTORY_PREFIX",
    "MATRIX_FILE",
    "NO_ANCHOR_FOR_MARKET",
    "NO_VERIFIED_CLAIM",
    "OPENER_KINDS",
    "PREMISE_UNSUPPORTED",
    "PROOF_FILE",
    "PROOF_KINDS",
    "REFUSALS",
    "SEAT_UNRESOLVED",
    "VERIFIED",
    "Angle",
    "AngleResolution",
    "AngleStatusError",
    "Claim",
    "MatrixError",
    "Proof",
    "Registry",
    "RegistryError",
    "angle_for",
    "is_generated",
    "load",
    "matrix_path",
    "matrix_state",
    "parse_evidence",
    "promote",
    "render",
    "retire",
    "write_matrix",
]
