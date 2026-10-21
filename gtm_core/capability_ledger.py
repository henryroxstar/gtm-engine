"""Audit rows for the sequencer capability assertions (SC2/SC4) — recording, not judging.

Split from :mod:`gtm_core.email_compliance` on purpose, and the split is structural rather
than tidiness. That module is in the daily preflight report's roster
(:data:`gtm_core.preflight_report.ROSTER`), and a roster module must reach no model, no
network, and no ledger: the report runs it every day to *judge* piped payloads, and a
judging pass that writes is a judging pass with a side effect
(``tests/test_preflight_report.py::test_no_roster_module_reaches_a_model_or_the_network``).

So the preflight decides, and this records what it decided. Only the CLI calls these —
never :func:`~gtm_core.email_compliance.check_capabilities`, which stays pure.
"""

from __future__ import annotations

from typing import Any


def _ledgers(profile: str) -> Any:
    from gtm_core.ledgers import Ledgers
    from gtm_core.paths import PathConfig

    return Ledgers(PathConfig.from_env(), profile)


def record_asserted(
    profile: str,
    *,
    provider: str,
    sequence_id: str | None,
    status: str,
    attested: list[str],
    detail: list[str],
    overall: str | None = None,
    failed_checks: list[str] | None = None,
    step_ids: list[str] | None = None,
) -> None:
    """Append a ``capability_asserted`` row: was this sequence checked, and when.

    Written for a FAIL as well as a pass — a refused staging attempt is exactly the event
    somebody later wants to find, and "no row" would make a refusal indistinguishable from
    a run that never happened. Answerable from ``ledger_cli`` with no provider call.

    ``status`` is the capability check's own verdict. ``overall`` is the verdict of the WHOLE
    preflight (postal address, opt-out, market as well), which is what the CLI's exit code
    reports; ``gtm_core.load_preconditions`` refuses a load when either failed. ``step_ids`` are
    the sequence's step ids, so a hosted import that names only a step can be traced back to the
    sequence it loads. All three are written only when given, so a caller that predates them
    produces the row it always did. A ``sequence_id`` of None records a check that was bound to
    no sequence: it is on the ledger but satisfies no load.
    """
    row: dict[str, Any] = {
        "event": "capability_asserted",
        "skill": "email-compliance",
        "provider": provider,
        "sequence_id": sequence_id,
        "status": status,
        "attested": sorted(attested),
        "detail": detail,
    }
    if overall is not None:
        row["overall"] = overall
        row["failed_checks"] = list(failed_checks or [])
    if step_ids:
        row["step_ids"] = list(step_ids)
    _ledgers(profile).append_history(row)


def record_autoset(
    profile: str,
    *,
    provider: str,
    capability: str,
    setting_code: int,
    value_before: str | None,
    value_after: str | None,
) -> None:
    """Append a ``capability_autoset`` row carrying BOTH values.

    Both, because the row alone must be enough to reconstruct the previous state and
    reverse the change (test plan §3.E). A row recording only the new value would say a
    setting was changed without saying to what from, which is not an audit trail.
    """
    _ledgers(profile).append_history(
        {
            "event": "capability_autoset",
            "skill": "email-compliance",
            "provider": provider,
            "capability": capability,
            "setting_code": setting_code,
            "value_before": value_before,
            "value_after": value_after,
        }
    )
