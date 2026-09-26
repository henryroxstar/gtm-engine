"""Shared operator-approved email-enrollment dispatch (A11).

The email analogue of ``agent/publish_dispatch.py``: when an operator approves the pack
graph's `sequence` gate, the *exact approved enrollment request* is dispatched here — in
Python, never the brain — to Saleshandy's lead/prospect enrollment endpoint. Nothing here
decides *what* to enroll; that was already fixed at approval time (the operator saw and
approved the drafted request — see ``agent.gate_actions.promote_enroll_draft``).

Invariants this module preserves:
  - The brain never calls ``add_leads_to_sequence``/``import_prospects_to_sequence`` itself
    — those leaves are denied outright by ``agent/permissions.py`` on every connector, and
    are allowed only inside the narrow ``email_context()`` window this module opens around
    the actual call.
  - A missing/unconfigured Saleshandy key fails closed (no dispatch, no exception) — never
    falls back to any other source of a key.
  - Enrolling does not send anything: sending stays structurally unrepresentable
    (``agent/mcp/saleshandy/server.py`` exposes no activate/resume tool at all). This module
    only closes the ENROLLMENT gate; the send gate needed nothing new.
  - Nobody is enrolled unless the paused sequence in Saleshandy still says exactly what was
    approved: the approval covers the copy as well as the people (client issue #244), so the
    sequence is read back first and any difference refuses the whole enrollment.
  - Every dispatch (success or failure) is audited via ``ledgers.append_history`` — a ledger
    write failure never blocks the caller from returning the outcome.
"""

from __future__ import annotations

import datetime
import html
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from agent.permissions import email_context

log = logging.getLogger(__name__)

#: The closed set of enrollment request shapes an approved draft may declare — mirrors
#: gtm_core.packs.loader's external_effect vocabulary in spirit: adding a third tool here
#: needs its own branch below, never an implicit fallthrough.
_ENROLL_TOOLS = frozenset({"add_leads_to_sequence", "import_prospects_to_sequence"})

#: ``GET /sequences`` lists every sequence in the account; the approved one must turn up
#: within this many pages, or enrollment is refused rather than assumed.
_SEQUENCE_PAGE_SIZE = 1000
_MAX_SEQUENCE_PAGES = 20

_BREAK_TAG = re.compile(r"<\s*/?\s*(?:br|p|div|li|ul|ol|tr|h[1-6])\b[^>]*>", re.IGNORECASE)
_ANY_TAG = re.compile(r"<[^>]*>")
_LINK = re.compile(r"""\b(?:href|src)\s*=\s*(["'])(.*?)\1""", re.IGNORECASE | re.DOTALL)


def _normalise_copy(text: Any) -> str:
    """The words a recipient reads: tags dropped (block tags and ``<br>`` as spaces),
    entities decoded, whitespace collapsed. Saleshandy re-wrapping approved HTML is not a
    difference; any change to the wording is."""
    text = _ANY_TAG.sub("", _BREAK_TAG.sub(" ", text if isinstance(text, str) else ""))
    return " ".join(html.unescape(text).split())


def _variant_words(payload: Any) -> tuple:
    """What a variant says: its subject, body and preheader wording, plus every link and image
    address in the body — dropping tags would otherwise hide a changed ``href``."""
    payload = payload if isinstance(payload, dict) else {}
    content = payload.get("content") if isinstance(payload.get("content"), str) else ""
    links = tuple(sorted(html.unescape(m.group(2)).strip() for m in _LINK.finditer(content)))
    words = tuple(_normalise_copy(payload.get(key)) for key in ("subject", "content", "preheader"))
    return (*words, links)


def _read_json(raw: str) -> Any:
    """A Saleshandy response body, or the ``[saleshandy-error] …`` string it came back as."""
    if raw.startswith("[saleshandy-error]"):
        return raw
    try:
        return json.loads(raw)
    except ValueError:
        return "[saleshandy-error] non-JSON response"


def _unreadable(what: str, body: Any) -> str:
    return f"could not read {what} from Saleshandy: {body if isinstance(body, str) else 'unexpected response'}"


async def _live_step_ids(api_key: str, sequence_id: str) -> tuple[list[str] | None, str | None]:
    """The approved sequence's step ids as Saleshandy lists them, or why they can't be used.

    The draft's ``sequence_id`` is model-written, so the sequence must also still be PAUSED:
    enrolling people into an active sequence sends to them, which no gate here approves.
    """
    from agent.mcp.saleshandy.server import _list_sequences_page_request

    for page in range(1, _MAX_SEQUENCE_PAGES + 1):
        body = _read_json(await _list_sequences_page_request(api_key, page, _SEQUENCE_PAGE_SIZE))
        rows = body.get("payload") if isinstance(body, dict) else None
        if not isinstance(rows, list):
            return None, _unreadable("the sequence", body)
        match = next((s for s in rows if isinstance(s, dict) and s.get("id") == sequence_id), None)
        if match is not None:
            if match.get("active") is not False:
                return None, f"sequence {sequence_id!r} is not paused in Saleshandy"
            steps = match.get("steps")
            if not isinstance(steps, list) or not all(isinstance(s, dict) for s in steps):
                return None, _unreadable("the sequence's steps", match)
            return [str(step.get("id")) for step in steps], None
        if len(rows) < _SEQUENCE_PAGE_SIZE:
            break
    return None, f"sequence {sequence_id!r} was not found in Saleshandy"


async def _live_copy_refusal(api_key: str, draft: dict) -> str | None:
    """Why the paused sequence in Saleshandy is not the copy the operator approved, or ``None``.

    The approval binds the draft's bytes — the people AND the copy — but the copy already sits
    in Saleshandy, staged before the gate. So read it back and refuse unless the sequence has
    exactly the approved steps, each with exactly the approved variants word for word and link
    for link: a step the approval never saw, a variant added or dropped, or wording or a link
    edited in Saleshandy after approval all stop the enrollment.
    """
    from agent.mcp.saleshandy.server import _get_step_variants_request

    steps = draft.get("steps")
    if not isinstance(steps, list) or not steps:
        return "the approved draft carries no sequence copy"
    sequence_id = draft["sequence_id"]
    live_ids, refusal = await _live_step_ids(api_key, sequence_id)
    if refusal is not None:
        return refusal
    approved = {str(step["step_id"]): step["variants"] for step in steps}
    if sorted(live_ids) != sorted(approved):
        return "the sequence's steps in Saleshandy are not the approved steps"
    for step_id, variants in approved.items():
        body = _read_json(await _get_step_variants_request(api_key, sequence_id, step_id))
        live = body.get("payload") if isinstance(body, dict) else body
        if not isinstance(live, list):
            return _unreadable(f"step {step_id!r}", body)
        live_words = sorted(_variant_words(v.get("payload")) for v in live if isinstance(v, dict))
        if live_words != sorted(_variant_words(v) for v in variants):
            return f"step {step_id!r} in Saleshandy does not match the approved copy"
    return None


async def _import_rows(api_key: str, prospects: list[dict]) -> tuple[list[dict] | None, str | None]:
    """Approved rows keyed by field label, in the documented import shape
    ``[{"fields": [{"id", "value"}]}]``. A label the account has no field for — or more than
    one — refuses the import: dropping it would enroll people without a value (a personalised
    ``Why Now`` line) that the approved copy relies on. Empty values are not sent."""
    from agent.mcp.saleshandy.server import _list_fields_request

    body = _read_json(await _list_fields_request(api_key))
    fields = body.get("payload") if isinstance(body, dict) else None
    if not isinstance(fields, list):
        return None, _unreadable("prospect fields", body)
    ids: dict[str, list] = {}
    for field in fields:
        if isinstance(field, dict):
            ids.setdefault(str(field.get("label", "")).strip().casefold(), []).append(
                field.get("id")
            )
    labels = sorted({label for row in prospects for label in row})
    unmatched = [label for label in labels if len(ids.get(label.strip().casefold(), [])) != 1]
    if unmatched:
        return None, f"Saleshandy has no single field for: {', '.join(unmatched)}"
    rows = [
        {
            "fields": [
                {"id": ids[label.strip().casefold()][0], "value": value}
                for label, value in row.items()
                if value.strip()
            ]
        }
        for row in prospects
    ]
    return rows, None


@dataclass(frozen=True)
class EnrollDispatchOutcome:
    """What the dispatch did. ``detail`` carries the raw Saleshandy response (or a
    ``[saleshandy-error] …`` string) for the operator/ledger — never a secret, the
    connector's own robustness contract guarantees the key is never echoed into it."""

    ok: bool
    status: str  # enrolled | enroll_failed | not_configured | dry_run
    detail: str = ""

    def operator_line(self) -> str:
        if self.status == "not_configured":
            return (
                "⚠️ No Saleshandy API key configured for this profile — enrollment gate "
                "approved, but nothing was sent."
            )
        if self.status == "dry_run":
            return "🧪 Dry run — no lead was enrolled."
        if not self.ok:
            return f"❌ Enrollment failed: {self.detail}"
        return "✅ Leads enrolled into the paused sequence."


#: Saleshandy field labels (what an approved draft is keyed by) → the lowercase column
#: names the lane gate reads. The draft speaks the import API's vocabulary because that is
#: what it will be POSTed as; the lane gate speaks the prospect CSV's. Without this
#: projection every row would carry no email and no identity, and the gate would match
#: nothing — a check that cannot discriminate is not a check (§R18).
_LANE_ROW_ALIASES = {
    "email": "email",
    "work email": "email",
    "company": "company",
    "company domain": "company_domain",
    "domain": "company_domain",
    "account id": "account_id",
    "lane": "lane",
    "id": "id",
}


def _lane_rows(prospect_list: list[dict]) -> list[dict]:
    """Project label-keyed approved rows onto the lane gate's row shape."""
    rows: list[dict] = []
    for row in prospect_list:
        projected: dict = {}
        for label, value in row.items():
            key = _LANE_ROW_ALIASES.get(str(label).strip().casefold())
            if key and str(value or "").strip():
                projected[key] = value
        rows.append(projected)
    return rows


def _lane_refusal(draft: dict, ledgers: Any, cfg: Any) -> str | None:
    """Re-run the lane/verdict/account-status gate at dispatch, from lanes-state on disk.

    Why here and not only in the CLI. ``gtm_core/enrollment_gate.py`` holds the rules that
    stop a hold/excluded/unrouted row being enrolled, but its only caller was
    ``account_integrity --require-verdict`` — a CLI the *brain* is told to run by skill
    prose. A gate that holds only when the model chooses to invoke it is not a gate
    (§R13: a permanent ban lives in code, not prose). This runs it on the dispatch path
    instead, beside the paused-sequence and copy-match checks, so it holds on every
    enrollment regardless of what any skill did or skipped.

    Recomputed from ``lanes-state.jsonl`` rather than read off the draft: the draft is
    model-written, the lane state is not.

    Fail-closed at every step — an unresolvable profile, an unprojectable row, or an
    unreadable lane state all refuse. This is a PII egress to a third-party processor;
    "could not check" must never mean "go ahead".
    """
    prospects = draft.get("prospect_list")
    if not isinstance(prospects, list) or not prospects:
        # add_leads_to_sequence carries opaque lead_ids with no rows to check. Those are
        # refused upstream by the copy match; there is nothing lane-shaped to verify here.
        return None

    profile = getattr(ledgers, "profile", None)
    if not profile:
        return (
            "cannot verify enrollment lanes: no profile on the ledger handed to the "
            "dispatcher — refusing rather than enrolling unchecked"
        )

    rows = _lane_rows(prospects)
    missing = sum(1 for r in rows if not r.get("email"))
    if missing:
        return (
            f"cannot verify enrollment lanes: {missing} of {len(rows)} approved rows carry no "
            f"recognisable email field, so they cannot be joined to lanes-state"
        )

    from gtm_core.enrollment_gate import check_account_status, check_enrollment_lanes
    from gtm_core.prospect_paths import evals_dir

    content_root = getattr(cfg, "content_root", None)

    # Bind only where the mechanism being guarded is actually in use. A tenant that never
    # ran the lane router has no lanes-state.jsonl and therefore no lane a row could
    # violate — refusing there would block a legitimate enrollment on the absence of a
    # feature. `check_enrollment_lanes` itself refuses on a missing file, which is right
    # for the CLI (the operator IS mid-lane-routing and is told to run `lanes route`) and
    # wrong here. Present-but-unreadable still refuses: that is a broken guard, not an
    # unused one.
    try:
        in_use = (evals_dir(profile, content_root) / "lanes-state.jsonl").is_file()
    except Exception:  # noqa: BLE001 — an unresolvable path is not a licence to enroll
        return "cannot verify enrollment lanes: lane state path did not resolve"
    if not in_use:
        log.info(
            "enrollment lane gate: no lanes-state.jsonl for profile=%s — lane routing not "
            "in use, lane checks skipped (copy-match and paused-sequence checks still ran)",
            profile,
        )
        return None

    fieldnames = sorted({k for r in rows for k in r})
    refusal, _lane = check_enrollment_lanes(
        rows, profile, "", fieldnames, want="send", content_root=content_root
    )
    if refusal:
        return refusal
    return check_account_status(rows, profile, content_root=content_root)


async def dispatch_approved_enrollment(
    cfg: Any,
    ledgers: Any,
    *,
    draft: dict,
    dry_run: bool = False,
) -> EnrollDispatchOutcome:
    """Enroll exactly the approved lead/prospect list, or refuse. Never raises.

    ``draft`` is the parsed, approved enrollment request — typically
    :func:`agent.gate_actions.promote_enroll_draft`'s return value, so it is already
    shape-validated (a recognised ``tool``, non-empty ``sequence_id``/``step_id``, the approved
    ``steps`` copy, and the matching lead/prospect list). Before any enroll call the paused
    sequence is read back and must match ``steps`` (:func:`_live_copy_refusal`), and prospect
    rows are mapped to Saleshandy field ids (:func:`_import_rows`); either failing refuses the
    enrollment with the reason. ``cfg`` supplies ``cfg.saleshandy_api_key`` — already
    workspace-scoped via BYOK on the backend (``backend/session.py``), or the VPS's own
    Doppler-injected key (``agent/config.py``). A missing key fails closed: no dispatch, no
    exception, no fallback to any other source. ``dry_run`` never dispatches — it is
    structurally impossible for it to reach Saleshandy, mirroring
    ``agent.publish_dispatch.dispatch_approved_publish``.
    """
    tool = draft.get("tool")
    if tool not in _ENROLL_TOOLS:
        return EnrollDispatchOutcome(
            ok=False, status="enroll_failed", detail=f"unrecognized draft tool {tool!r}"
        )

    api_key = getattr(cfg, "saleshandy_api_key", None)
    if not api_key:
        log.warning(
            "dispatch_approved_enrollment: no saleshandy_api_key configured — not enrolling"
        )
        return EnrollDispatchOutcome(ok=False, status="not_configured")

    # A dry run stops here: it must be structurally impossible for it to dispatch.
    if dry_run:
        return EnrollDispatchOutcome(ok=False, status="dry_run")

    from agent.mcp.saleshandy.server import (
        _add_leads_to_sequence_request,
        _import_prospects_to_sequence_request,
    )

    rows = None
    refusal = None
    expires_on = draft.get("expires_on")
    if expires_on:
        try:
            exp_date = datetime.date.fromisoformat(str(expires_on).strip())
            if datetime.date.today() > exp_date:
                refusal = f"draft expired on {expires_on}"
        except ValueError:
            refusal = f"draft has invalid expires_on {expires_on!r}"

    try:
        if refusal is None:
            refusal = _lane_refusal(draft, ledgers, cfg)
        if refusal is None:
            refusal = await _live_copy_refusal(api_key, draft)
        if refusal is None and tool == "import_prospects_to_sequence":
            rows, refusal = await _import_rows(api_key, draft["prospect_list"])
    except Exception as exc:  # noqa: BLE001 — an unexpected shape refuses, audited, never raises
        log.exception("dispatch_approved_enrollment: pre-enrollment check failed")
        refusal = f"could not verify the sequence before enrolling: {type(exc).__name__}"

    if refusal is not None:
        raw, is_error = f"nothing enrolled — {refusal}", True
    else:
        # The email-context flag permits the two Saleshandy enroll verbs only during this
        # approved dispatch window (mirrors publish_context() for Reap publish verbs). The
        # request functions below don't go through the MCP tool-call surface at all — this
        # is belt-and-suspenders, not the only guard — but it keeps the two paths symmetric.
        with email_context():
            if tool == "add_leads_to_sequence":
                raw = await _add_leads_to_sequence_request(
                    api_key,
                    draft["lead_ids"],
                    draft["sequence_id"],
                    draft["step_id"],
                    draft.get("tag_ids"),
                    draft.get("new_tags"),
                )
            else:  # import_prospects_to_sequence
                raw = await _import_prospects_to_sequence_request(
                    api_key,
                    rows,
                    draft.get("step_id", ""),
                    bool(draft.get("verify_prospects", False)),
                    draft.get("conflict_action", ""),
                )
        is_error = raw.startswith("[saleshandy-error]")
    event = "enroll_failed" if is_error else "enrolled"
    outcome = EnrollDispatchOutcome(ok=not is_error, status=event, detail=raw)

    # Audit, written by the component that actually dispatched — never trusted to the model.
    try:
        lead_count = len(draft.get("lead_ids") or draft.get("prospect_list") or [])
        ledgers.append_history(
            {
                "event": event,
                "skill": "email-sequence",
                "tool": tool,
                "sequence_id": draft.get("sequence_id"),
                "step_id": draft.get("step_id"),
                "lead_count": lead_count,
                "detail": raw if is_error else None,
            }
        )
    except Exception:  # noqa: BLE001 — a ledger write must never break the caller
        log.exception("Failed to write enrollment history")

    return outcome
