"""The email-judge MCP server (FastMCP, stdio) — two transports behind one tool.

The tool is ``score_emails``. Which transport it uses is decided by
:func:`agent.mcp.judge.scoring.select_backend` and recorded on every record; see that
module for why the two are not interchangeable and what that costs.

Properties enforced here rather than in the skill prompt that calls this:

* **Every row gets a record.** A row the model could not be read for is written with
  ``unscored: true``, never dropped. A batch that scored 380 of 400 must not print like one
  that scored 400 — ``adjudication check-complete`` is the reader of this guarantee.
* **The budget is checked before every batch, not once per run.** 400 rows at up to three
  repair attempts is four times the naive estimate, and the historical failure here is a
  month's spend that never reached the cap because the cap was consulted once at the top.
* **Cost is recorded by the code that spends it** (NIST AU-12), tagged with the backend, so
  an API-billed run and a subscription-billed one are distinguishable in the ledger.

Robustness contract: the tool returns a JSON string. On any failure (no auth, bad path,
HTTP error, malformed response) it returns a payload carrying ``error`` rather than
raising — the SDK ↔ MCP connection never breaks, and a judge outage degrades to "the
operator reads the list themselves".
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

from gtm_core.adjudication import Adjudication, write_records
from gtm_core.eval_calibration import is_calibrated

from .drafts import score_document
from .render import render
from .scoring import (
    _HTTP_TIMEOUT_S,
    _MAX_OUTPUT_TOKENS,
    _SPEC,
    _SYSTEM,
    ANTHROPIC_BASE_URL,
    ANTHROPIC_VERSION,
    BATCH_SIZE,
    JUDGE_MODEL,
    SDK_BATCH_ROWS,
    _batch_prompt,
    _prompt,
    budget_ok,
    build_record,
    confine_to_content_root,
    lane_batches,
    lane_of,
    load_case_studies,
    load_rows,
    meter,
    parse_verdict,
    parse_verdict_array,
    select_backend,
)

mcp = FastMCP("judge-worker")


def _tally(labels) -> dict[str, int]:
    """Count labels into a plain dict, in first-seen order."""
    out: dict[str, int] = {}
    for label in labels:
        out[label] = out.get(label, 0) + 1
    return out


def _rendered(row: dict, touch, extra: dict | None = None) -> tuple[str, str, dict, int]:
    """One row rendered against one touch → ``(subject, body, context, touch_n)``.

    The context is DATA (§R5) and, since 2026-09-03, carries the row's fact clause, the first
    300 characters of its evidence, and — via ``extra`` — the spec's declared capability and
    the profile's capability groups. Until then the judge saw only title / company / segment,
    so its note could say an argument was wrong but never which one would fit. A context
    change, not a rubric revision: the items and the output shape are unchanged.

    ``touch.number`` is the field (``Touch`` in the merge-render linter); the old
    ``getattr(touch, "n", 1)`` read a name that never existed, so every record said touch 1
    and ``touches="all"`` was inert.
    """
    context = {
        "title": row.get("title"),
        "company": row.get("company"),
        "segment": row.get("segment"),
        "signal_clause": (row.get("signal_clause") or "").strip(),
        "signal_evidence": (row.get("signal_evidence") or "").strip()[:300],
    }
    # A 1:1 pack carries its whole research dossier; a CSV row carries a 300-char evidence blob.
    # Passed untruncated because a pack batch is six rows, not four hundred, and because the
    # verdicts it changes are exactly the "you asserted that without evidence" ones.
    if (row.get("dossier") or "").strip():
        context["dossier"] = row["dossier"]
    if extra:
        context.update(extra)
    return (
        render(getattr(touch, "subject", ""), row),
        render(getattr(touch, "body", ""), row),
        context,
        int(getattr(touch, "number", getattr(touch, "n", 1)) or 1),
    )


def judge_context(spec_path: str, profile: str) -> dict:
    """The spec's ``capability:`` and the profile's capability groups, for the judge's context.
    Empty when the profile has no capability vocabulary — never a guess."""
    try:
        from gtm_core.hook_coverage.premise import capability_vocab, declared_capability

        text = Path(spec_path).read_text(encoding="utf-8") if Path(spec_path).is_file() else ""
        vocab = capability_vocab(profile) if profile else {}
        return {
            "capability": declared_capability(text, vocab) if text else "",
            "capability_groups": sorted(vocab),
        }
    except Exception:  # noqa: BLE001 — context is advisory; a vocab failure must not block scoring
        return {}


async def _score_api(
    rows,
    touch,
    *,
    profile,
    reverse,
    spec_path,
    csv_path,
    repair_attempt,
    repaired,
    op,
    extra=None,
) -> tuple[list[Adjudication], str]:
    """One request per row. Independent scoring; the production path."""
    # Read once per pass, not per row: the fabricated-number tier needs the profile's
    # case-studies corpus, and None (no such file) must stay distinct from "" (empty),
    # which would flag every cited number. See scoring.load_case_studies.
    case_studies = load_case_studies(profile)
    key = _SPEC.api_key()
    headers = {
        "x-api-key": key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    records: list[Adjudication] = []
    usage_acc = {"input_tokens": 0, "output_tokens": 0}
    batch_rows = 0
    stopped = ""

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
        for index, row in enumerate(rows):
            if index % BATCH_SIZE == 0:
                if batch_rows:
                    meter(profile, dict(usage_acc), op=op, rows=batch_rows, backend="api")
                    usage_acc = {"input_tokens": 0, "output_tokens": 0}
                    batch_rows = 0
                if not budget_ok(profile):
                    stopped = (
                        f"stopped at row {index}: profile {profile!r} is at or over its "
                        f"monthly cost cap"
                    )
                    break

            subject, body, context, touch_n = _rendered(row, touch, extra)
            payload = {
                "model": JUDGE_MODEL,
                "max_tokens": _MAX_OUTPUT_TOKENS,
                "system": _SYSTEM,
                # A grader must be reproducible: re-scoring the same row must give the same
                # verdict, or the confusion matrix and Cohen's κ are measuring sampling noise
                # rather than judgement. Measured 2026-08-22 on 12 rows scored twice each:
                # API default (1.0) 11/12 stable, temperature 0 12/12. This also fixes the
                # PRD §3.2 flip-rate control, which cannot attribute a flip to rubric order
                # while plain re-runs already flip on their own.
                "temperature": 0,
                "messages": [
                    {
                        "role": "user",
                        "content": _prompt(
                            subject, body, context, reverse=reverse, lane=lane_of(row)
                        ),
                    }
                ],
            }
            verdict = None
            try:
                resp = await client.post(
                    f"{ANTHROPIC_BASE_URL}/v1/messages", json=payload, headers=headers
                )
                resp.raise_for_status()
                result = resp.json()
                text = next(b["text"] for b in result["content"] if b.get("type") == "text")
                verdict = parse_verdict(text)
                usage = result.get("usage") or {}
                usage_acc["input_tokens"] += int(usage.get("input_tokens") or 0)
                usage_acc["output_tokens"] += int(usage.get("output_tokens") or 0)
            except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, StopIteration):
                verdict = None
            batch_rows += 1
            records.append(
                build_record(
                    row,
                    spec_path=spec_path,
                    csv_path=csv_path,
                    subject=subject,
                    body=body,
                    touch_n=touch_n,
                    verdict=verdict,
                    backend="api",
                    judge_batch=1,
                    repair_attempt=repair_attempt,
                    repaired=repaired,
                    case_studies=case_studies,
                )
            )

    if batch_rows:
        meter(profile, dict(usage_acc), op=op, rows=batch_rows, backend="api")
    return records, stopped


async def _score_sdk(
    rows,
    touch,
    *,
    profile,
    reverse,
    spec_path,
    csv_path,
    repair_attempt,
    repaired,
    op,
    extra=None,
) -> tuple[list[Adjudication], str]:
    """Batched through the Agent SDK, on the host's own auth. No API key involved.

    Batching is the price of the subprocess: one spawn per SDK_BATCH_ROWS emails rather
    than per email. `judge_batch` records the batch width on every record so the reduced
    independence is measurable rather than assumed away.
    """
    # Read once per pass, not per row: the fabricated-number tier needs the profile's
    # case-studies corpus, and None (no such file) must stay distinct from "" (empty),
    # which would flag every cited number. See scoring.load_case_studies.
    case_studies = load_case_studies(profile)
    from claude_agent_sdk import AssistantMessage, TextBlock

    from agent.config import Config
    from agent.session import build_agent_options, stream_brain_messages

    # `op` is accepted for signature parity with the api path and deliberately unused: the
    # SDK reports usage per message rather than in one envelope, and the run's spend lands on
    # the subscription rather than on API credit, so there is no per-token charge to
    # attribute. The `backend` tag on each record is what tells a later reader why an sdk run
    # has no cost rows.
    #
    # This `del` was previously the last statement INSIDE the batch loop, which unbound the
    # name after the first batch and raised UnboundLocalError on the second. Every list
    # longer than SDK_BATCH_ROWS (5) therefore failed, and the transport had only ever been
    # exercised on 1- and 2-row batches, so nothing caught it.
    del op

    cfg = Config.from_env()
    options = build_agent_options(cfg, profile or "default", role="judge")
    stopped = ""

    batches = lane_batches(rows, SDK_BATCH_ROWS)
    scored: dict[int, Adjudication] = {}
    done = 0
    for lane, idxs in batches:
        if not budget_ok(profile):
            stopped = (
                f"stopped at row {done}: profile {profile!r} is at or over its monthly cost cap"
            )
            break
        chunk = [rows[i] for i in idxs]
        rendered = [_rendered(row, touch, extra) for row in chunk]
        prompt = _batch_prompt([(s, b, c) for s, b, c, _ in rendered], reverse=reverse, lane=lane)

        text = ""
        try:
            async for msg in stream_brain_messages(options, prompt):
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            text += block.text
        except Exception:  # noqa: BLE001 — an SDK/auth failure must not kill the batch
            text = ""
        verdicts = parse_verdict_array(text, len(chunk))

        for i, row, (subject, body, _ctx, touch_n), verdict in zip(
            idxs, chunk, rendered, verdicts, strict=True
        ):
            scored[i] = build_record(
                row,
                spec_path=spec_path,
                csv_path=csv_path,
                subject=subject,
                body=body,
                touch_n=touch_n,
                verdict=verdict,
                backend="sdk",
                judge_batch=len(chunk),
                repair_attempt=repair_attempt,
                repaired=repaired,
                case_studies=case_studies,
            )
        done += len(chunk)

    # Input order, not batch order. A budget stop leaves the unreached rows out entirely,
    # exactly as the pre-grouping loop did — `scored + unscored == rows` is asserted by the
    # caller against what came back, and `stopped` is what says why the count is short.
    return [scored[i] for i in sorted(scored)], stopped


@mcp.tool()
async def score_emails(
    spec_path: str,
    csv_path: str,
    out_path: str,
    profile: str = "",
    touches: str = "1",
    limit: int = 0,
    repair_attempt: int = 0,
    repaired: bool = False,
    reverse_rubric: bool = False,
    backend: str = "",
) -> str:
    """Score EVERY row of a staged sequence and write one adjudication record per row.

    Runs the rendered email past a cheap pinned model (``claude-haiku-4-5``) asking
    whether the operator would send it. Writes JSONL that
    ``gtm_core.adjudication rank`` / ``check-complete`` / ``write-verdicts`` read.

    This RANKS, it does not block. The verdict lands in a data column; the deterministic
    ``account_integrity --require-verdict send`` is what refuses a row at enrollment.

    Transport is chosen automatically: the Anthropic API when ``ANTHROPIC_API_KEY`` is set
    (one request per row, independent scoring), otherwise the Agent SDK on the host's own
    auth — an OAuth subscription in a local session — which batches rows to amortise
    subprocess cost. Both are recorded per row as ``backend`` and ``judge_batch``, because
    a holdout scored across both is a confound that must stay visible.

    Args:
        spec_path: The sequence spec (the same file the merge-render gate lints).
        csv_path: The prospect list rendered against it.
        out_path: Where to write the adjudication JSONL. Must be under the content root.
        profile: Active profile — scopes the cost ledger and the monthly budget check.
        touches: ``"1"`` (default, matching the eval sheet) or ``"all"``.
        limit: Score at most N rows (0 = all). For a cheap smoke test, not a real run.
        repair_attempt: Which repair pass this is. 0 is the first, cold pass.
        repaired: True when scoring bodies the repair loop re-composed.
        reverse_rubric: Reverse rubric item order — PRD §3.2's flip-rate stability control.
        backend: Force ``"api"`` or ``"sdk"``. Leave empty to auto-select; set it only to
            reproduce a prior run's transport exactly.

    Returns a JSON string: ``{rows, scored, unscored, backend, out, verdicts: {...}}``, or a
    payload carrying ``error``. ``scored + unscored == rows`` always.
    """
    chosen, reason = select_backend()
    if backend:
        if backend not in {"api", "sdk"}:
            return json.dumps({"error": f"unknown backend {backend!r} — use 'api' or 'sdk'"})
        chosen, reason = backend, f"forced by caller: {backend}"
    if chosen == "api" and not _SPEC.api_key():
        return json.dumps({"error": f"backend 'api' forced but {_SPEC.api_key_env} is not set"})

    rows, parsed_touches, problems = load_rows(spec_path, csv_path, touches)
    if problems:
        return json.dumps({"error": "; ".join(problems)})
    if not rows or not parsed_touches:
        return json.dumps({"error": "spec or csv produced no renderable rows"})

    out, why = confine_to_content_root(out_path)
    if out is None:
        return json.dumps({"error": why})

    if limit > 0:
        rows = rows[:limit]

    runner = _score_api if chosen == "api" else _score_sdk
    records, stopped = await runner(
        rows,
        parsed_touches[0],
        profile=profile,
        reverse=reverse_rubric,
        spec_path=spec_path,
        csv_path=csv_path,
        repair_attempt=repair_attempt,
        repaired=repaired,
        op="re-judge" if repair_attempt else "score_emails",
        extra=judge_context(spec_path, profile),
    )

    # Has this judge ever been measured against a human? Stamped on every record BEFORE the
    # write, so an unvalidated verdict is unvalidated on disk and not merely in a banner the
    # caller may not print. See `gtm_core.eval_calibration.sealed_holdouts` for the 2026-08-27
    # run this exists to prevent.
    #
    # No profile means the question was never asked, which is `None` — recording `False`
    # there would claim the judge was measured and found wanting, and the disposal audit
    # counts that as a real finding.
    calibrated = is_calibrated(profile) if profile else None
    for rec in records:
        rec.calibrated = calibrated

    write_records(records, out)
    tally: dict[str, int] = {}
    for rec in records:
        label = "unscored" if rec.unscored else rec.verdict
        tally[label] = tally.get(label, 0) + 1
    payload = {
        "rows": len(rows),
        "records": len(records),
        "scored": sum(1 for r in records if not r.unscored),
        "unscored": sum(1 for r in records if r.unscored),
        "backend": chosen,
        "backend_reason": reason,
        "out": str(out),
        "verdicts": tally,
        "model": JUDGE_MODEL,
        "reverse_rubric": bool(reverse_rubric),
        "calibrated": calibrated,
        # Which rubric each row was scored against. In the payload, not only on the
        # records, because a caller comparing two runs needs to see that the question
        # changed before it reads anything into the verdicts moving.
        "rubrics": _tally(rec.rubric for rec in records),
        # WHICH VERSION of those rubrics — the fingerprint of the exact questions asked.
        # `rubrics` says "full" across a change to what "full" MEANS; this does not. More
        # than one value in a pooled comparison is the confound, stated.
        "rubric_versions": _tally(rec.rubric_version for rec in records),
        "verdicts_by_rubric": {
            r: _tally(
                ("unscored" if rec.unscored else rec.verdict) for rec in records if rec.rubric == r
            )
            for r in sorted({rec.rubric for rec in records})
        },
    }
    if not calibrated:
        payload["UNCALIBRATED"] = (
            "No PASSING score on a sealed holdout exists for "
            f"{profile or '(no profile given)'} — this judge has either never been measured "
            "against a human label or was measured and failed the bars, so these verdicts "
            "have no known error rate. Treat them as a "
            "RANKING, never as a decision: a `drop` here usually means the COPY is wrong, "
            "not that the account is bad, and discarding accounts on it will disqualify most "
            "of a healthy list. Do not retire accounts, and do not report rejected rows as "
            "'failed', until `email-quality` report mode has cleared the bar "
            "(TPR>=0.90, TNR>=0.80, kappa>=0.6). Rows the judge rejects are a re-angle QUEUE, "
            "not a graveyard."
        )
    if stopped:
        payload["stopped"] = stopped
    if chosen == "sdk" and payload["scored"] == 0 and payload["rows"]:
        payload["hint"] = (
            "the SDK backend scored nothing. If this machine's Claude Code OAuth token is "
            "expired or revoked, re-authenticate — or set ANTHROPIC_API_KEY to use the API "
            "backend instead."
        )
    return json.dumps(payload)


@mcp.tool()
async def score_drafts(kind: str, path: str, profile: str = "") -> str:
    """Read ONE creative document with fresh context and return craft NOTES on named axes.

    Notes mode only: it never ranks, never selects and never gates. A reader that ranks owes a
    measured agreement with what a human would have picked, and none has been measured here yet, so
    "what I would look at again" is the honest output. Its result is diagnostic — act on a note or
    do not; nothing downstream reads it as a verdict.

    ``kind="brief"`` reads a creator brief on five named craft axes, which is the cheapest point in
    the video lane to be wrong. ``draft`` and ``frame`` are reserved and refuse rather than falling
    through. Without an API key it returns ``unavailable`` and makes NO network call, so a caller can
    say the brief went unread instead of implying it was read.
    """
    return json.dumps(await score_document(kind, path, profile=profile))
