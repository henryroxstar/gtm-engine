"""SC2/SC4 — asserting what the SEQUENCER does and what this workspace switched on.

Split out of :mod:`gtm_core.email_compliance` on 2026-09-21 because it is a different
question, not because the file got long. That module judges what THIS SEQUENCE carries —
a postal address in the signature, a working opt-out, every lead inside the profile's
markets. This one judges what the PROVIDER does and what somebody toggled in its UI: two
facts that live in another company's product, that no amount of reading our own files can
settle, and that were therefore believed rather than read until three real opt-outs sat
unmirrored for six weeks.

It is deliberately NOT a second gate. :func:`check_capabilities` returns the same
:class:`~gtm_core.email_compliance.Result` the other three checks return, joins the same
list, and shares the one exit status — a second gate is a gate somebody runs separately,
which is to say occasionally.

Pure: it reads no file the caller did not hand it, makes no network call, and writes no
ledger row (recording lives in :mod:`gtm_core.capability_ledger`). The registry it resolves
against is :mod:`gtm_core.sequencers`.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import minischema
from .email_compliance import Result, _strip_quotes, extract_settings  # noqa: F401
from .paths import resolve_profiles_root
from .sequencers import Capability, resolve_capability

# --------------------------------------------------------------------------- capabilities
#
# SC2/SC4. The three checks above judge what THIS sequence carries. This one judges what the
# PROVIDER does and what this workspace has switched on — the two facts that were believed
# rather than read, and that let three real opt-outs sit unmirrored for six weeks.
#
# It is deliberately the same gate, not a second one: the ladder below returns Results that
# join `_preflight`'s single list and so its single exit status. A second gate is a gate
# somebody runs separately, which is to say occasionally.


#: Which capabilities gate enrollment and which merely inform it. A `blocking` capability can
#: FAIL the preflight; an `advisory` one is capped at WARN and can never block — it reports a
#: fact the operator should know (SC10's routing witness, SC3's unresolved contradiction)
#: without holding up a send window over something that is not itself a compliance duty.
CAPABILITY_POLICY: dict[str, str] = {
    "stop_on_reply": "blocking",
    "ooo_auto_pause": "blocking",
    "reply_categories": "advisory",
    "reply_text_unsubscribe": "advisory",
}

#: Ladder verdict -> Result status, per policy. ATTESTED is never PASS: a human saying a setting
#: is on is not the same evidence as reading it, and a table that renders the two identically
#: hides which one happened (test plan §6.B).
_POLICY_VERDICT_MAP: dict[str, dict[str, str]] = {
    # "PASS" is a ladder verdict, not a credential — hence the nosec on both rows.
    "blocking": {"PASS": "PASS", "ATTESTED": "WARN", "FAIL": "FAIL"},  # nosec B105
    "advisory": {"PASS": "PASS", "ATTESTED": "WARN", "FAIL": "WARN"},  # nosec B105
}

#: capability name -> its numbered code in `get_sequence_settings`, once one has been READ from a
#: live payload and dated in the registry. An entry is earned by a toggle-and-diff, never guessed
#: from the numbering pattern or inferred from how many booleans happen to be on: `stop_on_reply`
#: = 3 was established 2026-09-22 by flipping the named UI toggle on a never-sent sequence and
#: observing that exactly one code moved, then restoring it and byte-comparing the payload.
#: `ooo_auto_pause` has no entry and cannot get one — it is an account-level setting with no
#: documented read path at all, which is why its row carries `readable_via_api = false` and the
#: ladder can only take a per-run attestation for it.
SETTING_CODES: dict[str, int] = {"stop_on_reply": 3}

#: Setting codes an auto-set may write, and ONLY those. Hardcoded (never a CLI flag, a profile
#: value or a brain output) and confined to config-not-send keys: a switch that changes WHO gets
#: contacted or WHETHER a follow-up goes out is a human's to flip, whatever the env says.
#: EMPTY TODAY for the same reason `SETTING_CODES` is — nothing has been read, so nothing has
#: been classified. An empty allowlist is not a bug; a populated one with no dated live read is.
AUTOSET_ALLOWLIST: frozenset[int] = frozenset()

#: The closed list of live values that count as "this setting is ON". Anything else — "0", "",
#: "true", None, a missing code — is not on. Same rule as the registry's `_grants`: only a
#: recognised granting value grants, so an unrecognised one blocks instead of passing.
_SETTING_ON = ("1",)

#: The kill switch. Auto-set writes to a tenant's live provider config, so it is opt-in, and the
#: closed list of enabling values is the same strict parse the publish/schedule switches use —
#: an unrecognised value leaves it OFF rather than guessing that a typo meant yes.
_AUTOSET_ENV = "GTM_CAPABILITY_AUTOSET_ENABLED"


def autoset_enabled() -> bool:
    """True only if :data:`_AUTOSET_ENV` is set to a recognised true. Default: off."""
    return (os.getenv(_AUTOSET_ENV) or "").strip().lower() in {"true", "1", "yes", "on"}


#: Shape a `get_sequence_settings` payload must have before it is read (§R5 structured output:
#: never trust the shape). A truncated or object-shaped response REFUSES; it never yields a
#: smaller answer that then reads as "no objection found".
_SETTINGS_PAYLOAD_SCHEMA = {
    "type": "array",
    "items": {"type": "object", "required": ["code"]},
}


def read_email_tool(profile: str, profiles_root: Path | None = None) -> str:
    """Parse ``email_tool:`` out of a profile's PROFILE.md (mirrors `read_target_markets`).

    Returns the bare provider slug (``saleshandy``/``apollo``/``gmass``/``manual``). Raises
    FileNotFoundError if the profile has no PROFILE.md and ValueError if the key is absent —
    an unnamed sequencer cannot be asserted against, and guessing one would assert the wrong
    vendor's capabilities.
    """
    root = profiles_root or resolve_profiles_root()
    path = root / profile / "PROFILE.md"
    if not path.is_file():
        raise FileNotFoundError(f"no PROFILE.md for profile {profile!r} at {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("email_tool:"):
            continue
        value = _strip_quotes(line.split(":", 1)[1].split("#", 1)[0].strip())
        if value:
            return value.lower()
    raise ValueError(f"{path} has no `email_tool:` assignment — cannot resolve a sequencer")


def _label_for(verdict: str, policy: str) -> str:
    """The word a human reads next to the status.

    Four words, one meaning each, because a table where `ATTESTED` reads like `PASS` hides that
    nothing was read, and one where an advisory reads like `FAIL` trains people to skim past
    real blocks (test plan §6.B). Only `BLOCKS` blocks.
    """
    if verdict == "PASS":
        return "verified by a live read"
    if verdict == "ATTESTED":
        return "ATTESTED — an operator confirmed it, nothing was read"
    return "BLOCKS" if policy == "blocking" else "advisory — does not block"


def _ladder_live_read(
    *,
    live_value: str | None,
    setting_code: int | None,
    autoset_enabled: bool,
    autoset_allowlist: frozenset[int],
    ui: str,
) -> tuple[str, str]:
    """Rungs 2 and 3 — the registry says this setting reads back, so the live value decides."""
    if setting_code is None:
        return (
            "FAIL",
            "the registry says this is readable via the API but no provider setting code "
            "is registered for it (SETTING_CODES) — a registry/implementation mismatch "
            "refuses; it is never a pass",
        )
    if live_value is None:
        return (
            "FAIL",
            f"setting code {setting_code} was not present in the settings payload — "
            "an absent value is unknown, never 'no objection found'",
        )
    if live_value in _SETTING_ON:
        return "PASS", f"live read: setting code {setting_code} is ON"
    if autoset_enabled and setting_code in autoset_allowlist:
        return (
            "ATTESTED",
            f"AUTOSET PLAN: setting code {setting_code} is {live_value!r}; auto-set is "
            "enabled and this code is allowlisted — flip it, then record it with "
            "`record-autoset`. Nothing has been written yet and nothing is confirmed on",
        )
    return (
        "FAIL",
        f"live read: setting code {setting_code} is {live_value!r}, not on — turn it on{ui}",
    )


def _capability_ladder(
    cap: Capability,
    *,
    live_value: str | None,
    setting_code: int | None,
    attested: frozenset[str],
    autoset_enabled: bool,
    autoset_allowlist: frozenset[int],
) -> tuple[str, str]:
    """The five-rung assertion ladder (PRD §3.A) for one capability.

    Returns ``(verdict, detail)`` where verdict is PASS / ATTESTED / FAIL — the raw rung
    outcome, before :data:`_POLICY_VERDICT_MAP` decides whether it can block. Pure: it reads
    no file, makes no call, and every rung's default is the refusing one.
    """
    # Rung 1 — the vendor fact. No granted registry row, nothing else matters.
    if not cap.granted:
        return "FAIL", f"the registry does not grant this capability — {cap.reason}"

    ui = f" (UI: {cap.ui_path})" if cap.ui_path else ""

    # Rungs 2 & 3 — the live setting can be read back, so read it.
    if cap.readable_via_api is True:
        return _ladder_live_read(
            live_value=live_value,
            setting_code=setting_code,
            autoset_enabled=autoset_enabled,
            autoset_allowlist=autoset_allowlist,
            ui=ui,
        )

    # Rung 4 — nothing can read it back, so the only honest check is a per-run attestation.
    if cap.readable_via_api is False:
        if cap.name in attested:
            return (
                "ATTESTED",
                "no API or CLI read path is documented, so this run rests on the operator's "
                f"`--attest {cap.name}`, not on a read{ui}",
            )
        return (
            "FAIL",
            "no API or CLI read path is documented and this run carries no attestation — "
            f"confirm it in the provider UI and pass `--attest {cap.name}`{ui}",
        )

    # Rung 5 — `readable_via_api` is "unknown" or absent. Nobody has established whether this
    # can be read at all, so neither a read nor an attestation is available to stand on.
    return (
        "FAIL",
        f"readable_via_api is {cap.readable_via_api!r} — nobody has yet read this setting back "
        "and dated the row, so it can neither pass nor be attested (SC2 resolves it)"
        f"{ui}",
    )


def check_capabilities(
    provider: str,
    settings_payload=None,
    *,
    attested: frozenset[str] | set[str] | None = None,
    overrides: dict[str, object] | None = None,
    autoset_enabled: bool = False,
    autoset_allowlist: frozenset[int] | None = None,
    setting_codes: dict[str, int] | None = None,
    registry_path: Path | None = None,
) -> Result:
    """Assert every policy-governed capability for ``provider`` (SC2/SC4).

    ``settings_payload`` is the RAW ``get_sequence_settings`` response (or None when the run
    piped none). It is schema-checked before it is read: a wrong-shaped payload FAILs the check
    rather than raising, so a malformed provider response reaches the operator as a verdict with
    a next step instead of a traceback.

    Makes no network call — it judges what was piped in, exactly as the other three checks do.
    """
    attested = frozenset(attested or ())
    allowlist = AUTOSET_ALLOWLIST if autoset_allowlist is None else autoset_allowlist
    codes = SETTING_CODES if setting_codes is None else setting_codes

    detail: list[str] = []
    settings: dict[int, str] | None = None
    payload_error: str | None = None
    if settings_payload is not None:
        node = settings_payload
        if isinstance(node, dict):
            node = node.get("payload", node)
        if isinstance(node, dict):
            node = node.get("settings", node)
        errors = minischema.validate(node, _SETTINGS_PAYLOAD_SCHEMA)
        if errors:
            payload_error = errors[0]
            detail.append(
                f"FAIL settings payload is not the documented shape ({payload_error}) — "
                "refusing rather than reading a smaller answer out of it"
            )
        else:
            settings = {
                int(s["code"]): (s.get("value") or "")
                for s in node
                if str(s.get("code", "")).lstrip("-").isdigit()
            }

    unknown_attests = attested - set(CAPABILITY_POLICY)
    if unknown_attests:
        detail.append(
            f"FAIL --attest names capability/capabilities nothing asserts: "
            f"{sorted(unknown_attests)}"
        )

    statuses: list[str] = []
    for name, policy in sorted(CAPABILITY_POLICY.items()):
        cap = resolve_capability(provider, name, overrides=overrides, registry_path=registry_path)
        code = codes.get(name)
        # A payload that failed validation is the same as no payload: unreadable, never "off".
        live = settings.get(code) if (settings is not None and code is not None) else None
        if cap.name in attested and cap.readable_via_api is not False:
            # An attestation is only ever admissible where nothing can be read back. Accepting
            # one for a readable setting would let a human's say-so overwrite an actual read.
            detail.append(
                f"FAIL [attest-refused] {provider}/{name}: `--attest {name}` is only accepted "
                f"for a capability whose registry row says readable_via_api = false; this one "
                f"says {cap.readable_via_api!r}"
            )
            statuses.append("FAIL" if policy == "blocking" else "WARN")
            continue
        verdict, msg = _capability_ladder(
            cap,
            live_value=live,
            setting_code=code,
            attested=attested,
            autoset_enabled=autoset_enabled,
            autoset_allowlist=allowlist,
        )
        status = _POLICY_VERDICT_MAP[policy][verdict]
        detail.append(f"{status} [{_label_for(verdict, policy)}] {provider}/{name}: {msg}")
        statuses.append(status)

    if payload_error or unknown_attests:
        statuses.append("FAIL")
    if "FAIL" in statuses:
        overall = "FAIL"
    elif "WARN" in statuses:
        overall = "WARN"
    else:
        overall = "PASS"
    return Result("sequencer capabilities", overall, detail)


def capability_rows(
    provider: str,
    *,
    overrides: dict[str, object] | None = None,
    registry_path: Path | None = None,
) -> list[Capability]:
    """Every policy-governed capability for ``provider``, resolved — the shared input the
    terminal table, the gate preview and the dashboard panel all render (test plan §4.5)."""
    return [
        resolve_capability(provider, name, overrides=overrides, registry_path=registry_path)
        for name in sorted(CAPABILITY_POLICY)
    ]
