"""Refusal auditing — one append-only ``denials.jsonl`` row per caller-admission refusal,
naming who was refused and why.
"""

from __future__ import annotations

import logging

from .principal import Principal

logger = logging.getLogger(__name__)


def record_refusal(
    cfg,
    profile: str,
    principal: Principal | None,
    route: str,
    code: str,
) -> None:
    """Append one refusal to ``denials.jsonl`` under ``cfg``'s content root; never raises.

    Writes through ``Ledgers.append_denial`` directly rather than
    ``agent.denial_log.record_permission_event``, whose fixed record shape keeps only
    tool/decision/outcome/detail/source — the principal and the refusal code would be
    dropped, and a row that cannot say who was refused is not the audit PRD §7 asks for.
    The row keeps that shape (so ``ledger_cli denials`` reads it unchanged) and adds
    ``principal_kind``, ``principal_id`` and ``code``. ``principal_id`` is a user id or an
    ``api_keys.id``, never key material.
    """
    try:
        from gtm_core.ledgers import Ledgers

        Ledgers(cfg, profile).append_denial(
            {
                "tool": f"REST:{route}",
                "decision": "deny",
                "outcome": "denied",
                "detail": code,
                "source": "backend-admission",
                "principal_kind": principal.kind if principal else None,
                "principal_id": principal.subject if principal else None,
                "code": code,
            }
        )
    except Exception:  # noqa: BLE001 — observability must never turn a refusal into a 500
        logger.warning(
            "denials.jsonl append failed (route=%s, code=%s)", route, code, exc_info=True
        )
