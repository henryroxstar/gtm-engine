"""The judge's scoring core — one implementation, two transports.

Which transport runs is decided by :func:`select_backend`, and the choice is **recorded
on every record** rather than inferred later:

* ``api`` — a direct Anthropic Messages call per row, used when ``ANTHROPIC_API_KEY`` is
  set. One row per request, so rows are scored independently. This is the production path:
  ``deploy/docker-compose.yml`` provisions the key, and a 400-row Haiku pass is under a
  dollar.
* ``sdk`` — the Agent SDK subprocess, used when there is no key. It inherits whatever auth
  the host holds, which for a local Claude Code session is the OAuth subscription, so no
  API credit is spent. This is the same route ``agent/pipeline_executor`` already uses to
  run radar/research on a non-default model — for an Anthropic role
  ``build_agent_options`` injects no credentials at all (``agent/session.py``).

**The two are not interchangeable, and pretending they were would corrupt the validation.**
An SDK spawn carries the whole Claude Code system prompt and tool surface, so per-row it
costs roughly an order of magnitude more tokens and a process start. Amortising that means
putting several emails in one prompt — and a judge that sees five emails at once anchors
across them, which breaks the per-row independence the confusion matrix and Cohen's κ
assume. So:

* the SDK path batches (:data:`SDK_BATCH_ROWS`, small on purpose) and the API path never does;
* every :class:`~gtm_core.adjudication.Adjudication` carries ``backend`` and ``judge_batch``
  (how many emails shared its prompt; 1 = scored alone), so a holdout scored across both
  transports is a visible confound instead of a silent one.

**Verification status (2026-08-22):** both transports have now completed a live call. The
``api`` path is exercised in production (``deploy/docker-compose.yml`` provisions the key).
The ``sdk`` path was blocked earlier the same day — the machine's stored OAuth token was
revoked (``expiresAt`` 2026-07-02, empty refresh token) — and was proven live after
re-authentication: a 1-row and a 2-row batch against a fictional fixture both returned
``scored == rows``, ``unscored == 0``, with the batched array parser correctly aligning
verdicts to recipients.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from gtm_core.adjudication import Adjudication, stratum_of
from gtm_core.eval_calibration import _row_id
from gtm_core.groundedness import groundedness_report
from gtm_core.metering import resolve_rates
from gtm_core.models import resolve_model

from .packs import PACK_FORMATS, pack_rows  # noqa: F401  (re-exported: the public seam)
from .rubric import (  # noqa: F401  (re-exported: the public seam)
    _SHAPE,
    _SIGNAL_FREE_NOTE,
    _SYSTEM,
    REQUIRES_SIGNAL,
    RUBRIC_FULL,
    RUBRIC_ITEMS,
    RUBRIC_SEAT_ONLY,
    SIGNAL_FREE_LANES,
    _batch_prompt,
    _lane_note,
    _prompt,
    lane_batches,
    lane_of,
    rubric_for,
    rubric_id,
    rubric_text,
    rubric_version,
)

_SPEC = resolve_model("judge")
ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL", _SPEC.base_url).rstrip("/")
ANTHROPIC_VERSION = "2023-06-01"
JUDGE_MODEL = _SPEC.model
_HTTP_TIMEOUT_S = 60.0
_MAX_OUTPUT_TOKENS = 512

_INPUT_USD_PER_1K, _OUTPUT_USD_PER_1K = resolve_rates(
    _SPEC, env_input="HAIKU_INPUT_USD_PER_1K", env_output="HAIKU_OUTPUT_USD_PER_1K"
)

#: How many rows are scored between budget checks on the API path. Small enough that the
#: cap cannot be overshot by much, large enough that the ledger is not re-read per row.
BATCH_SIZE = 25


#: How many emails share one SDK prompt. Deliberately small: this is the anchoring risk,
#: not a throughput dial. Raising it makes the SDK path cheaper and the scores less
#: independent, and `judge_batch` on each record is what keeps that trade visible.
#: ``JUDGE_SDK_BATCH_ROWS`` (1..10) overrides it so the self-consistency control can run at
#: one row per prompt on the login path — no API key needed. An unreadable value refuses
#: loudly at import rather than silently scoring at 5 while the operator believes it is 1.
def _sdk_batch_rows(default: int = 5) -> int:
    raw = os.getenv("JUDGE_SDK_BATCH_ROWS", "").strip()
    if not raw:
        return default
    try:
        n = int(raw)
    except ValueError:
        raise ValueError(f"JUDGE_SDK_BATCH_ROWS={raw!r} is not an integer in 1..10") from None
    if not 1 <= n <= 10:
        raise ValueError(f"JUDGE_SDK_BATCH_ROWS={n} is outside 1..10")
    return n


SDK_BATCH_ROWS = _sdk_batch_rows()

_VERDICTS = {"send", "re-angle", "drop"}


def _clean(parsed: object) -> dict | None:
    """Coerce one parsed object into a verdict dict, or None if it is not one."""
    if not isinstance(parsed, dict):
        return None
    verdict = str(parsed.get("verdict") or "").strip().lower()
    if verdict not in _VERDICTS:
        return None
    try:
        score = int(parsed.get("score") or 0)
    except (TypeError, ValueError):
        score = 0
    return {
        "verdict": verdict,
        "score": max(0, min(5, score)),
        "defect_class": str(parsed.get("defect_class") or "").strip().lower(),
        "evidence": str(parsed.get("evidence") or "").strip(),
        "note": str(parsed.get("note") or "").strip(),
    }


def _strip_fence(raw: str) -> str:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return raw


def parse_verdict(text: str) -> dict | None:
    """Parse a single-object model reply into a verdict dict, or None if unreadable.

    Only a fixed verdict vocabulary is accepted. A body containing ``"verdict": "send"``
    inside its own scraped text cannot reach this function — the model's reply is the only
    thing parsed — and a reply naming anything outside :data:`_VERDICTS` is rejected rather
    than coerced to a default. The safest-looking default (``send``) is the one that mails
    a person, so there is no default.
    """
    raw = _strip_fence(text)
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return _clean(json.loads(raw[start : end + 1]))
    except ValueError:
        return None


def parse_verdict_array(text: str, expected: int) -> list[dict | None]:
    """Parse a batched reply into exactly ``expected`` slots.

    Always returns a list of length ``expected``. A short, long, or unparseable reply
    yields ``None`` in the affected slots rather than shifting verdicts onto the wrong
    recipients — a misalignment here would attach one person's judgment to another's
    address, which is worse than no judgment at all.
    """
    raw = _strip_fence(text)
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end <= start:
        return [None] * expected
    try:
        parsed = json.loads(raw[start : end + 1])
    except ValueError:
        return [None] * expected
    if not isinstance(parsed, list) or len(parsed) != expected:
        # Length mismatch is unrecoverable: there is no way to know WHICH email the model
        # skipped, so re-pairing by position would silently misattribute every verdict
        # after the gap.
        return [None] * expected
    return [_clean(item) for item in parsed]


# --- backend selection ------------------------------------------------------ #


def select_backend() -> tuple[str, str]:
    """``(backend, reason)`` — ``"api"`` when a key is set, else ``"sdk"``.

    Key-first on purpose. The API path is cheaper per row, scores rows independently, and
    is what production actually runs; the SDK path exists so a local session with no key
    can still judge on the host's OAuth rather than being blocked entirely.
    """
    if _SPEC.api_key():
        return "api", f"{_SPEC.api_key_env} is set — scoring one row per request"
    return (
        "sdk",
        f"{_SPEC.api_key_env} is not set — falling back to the Agent SDK, which uses the "
        f"host's own auth (OAuth in a local Claude Code session). Rows are batched "
        f"{SDK_BATCH_ROWS} to a prompt to amortise subprocess cost; `judge_batch` records it.",
    )


def budget_ok(profile: str) -> bool:
    """Whether the profile is under its monthly cap. Fails OPEN on a read error.

    Mirrors :func:`agent.budget.vps_budget_ok` exactly — one cap source, one fail policy.
    """
    if not profile:
        return True
    try:
        from agent.budget import vps_budget_ok
        from agent.config import Config

        return bool(vps_budget_ok(Config.from_env(), profile))
    except Exception:  # noqa: BLE001 — fail open; the SDK per-run cap is the backstop
        return True


def meter(profile: str, usage: dict | None, *, op: str, rows: int, backend: str) -> None:
    """Append a cost record for one batch. Best-effort; never breaks a scoring run."""
    if not profile or not isinstance(usage, dict):
        return
    try:
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        cost = (
            input_tokens / 1000.0 * _INPUT_USD_PER_1K + output_tokens / 1000.0 * _OUTPUT_USD_PER_1K
        )
        from agent.config import Config
        from agent.ledgers import Ledgers

        Ledgers(Config.from_env(), profile).append_cost(
            {
                "tool": "judge-worker",
                # `op` distinguishes the first pass from a repair re-judge, so 3x cost can
                # never hide inside a 1x estimate when someone reads the ledger back.
                "op": op,
                "model": JUDGE_MODEL,
                "backend": backend,
                "rows": rows,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": round(cost, 6),
            }
        )
    except Exception:  # noqa: BLE001 — metering is best-effort
        return


# ── the deterministic grounding pre-pass (the other half of [roles.judge]) ────
#
# `gtm_core/models.toml` has always described this role as "adjudication reading pass +
# groundedness cascade". Only the adjudication half was ever wired; the cascade sat with
# zero callers. It runs HERE, before the model sees a row, because it is deterministic,
# offline and free — and because this is the one place that already holds both the row
# record and the rendered body the tiers need.
#
# It RANKS, it does not gate. Every row is still scored by the model; nothing is skipped on a
# grounding flag. Spend-avoidance on `needs_judge` is deliberately NOT done yet: the judge bars are
# uncalibrated, and skipping model calls on an unvalidated signal would change send/drop behaviour
# on a guess. Record first, gate later, once labels exist.

#: The profile knowledge file the fabricated-number tier checks against.
CASE_STUDIES_FILE = "case-studies.md"


def load_case_studies(profile: str) -> str | None:
    """The profile's ``case-studies.md`` text, or ``None`` when it has none.

    ``None`` is load-bearing and is NOT the same as ``""``. With an empty corpus
    :func:`gtm_core.groundedness.case_study_numbers_traceable` reports EVERY cited number
    as untraceable (its own test pins that), so defaulting to a blank string would flag
    every row carrying a figure — manufacturing exactly the unreadable-warning wall
    `gtm_core.finding_budget` exists to prevent. Absent corpus ⇒ the tier does not run.
    """
    try:  # lazy, same idiom as server.py — keeps this module importable without agent cfg
        from agent.config import Config
        from gtm_core.paths import resolve_knowledge_file

        cfg = Config.from_env()
        path = resolve_knowledge_file(cfg.profiles_root, profile, CASE_STUDIES_FILE)
        return path.read_text(encoding="utf-8")
    except (OSError, ValueError, KeyError):
        return None


def grounding_flags(report, *, corpus_present: bool) -> str:
    """One compact, parseable summary of a row's grounding report.

    ``"clean"`` when nothing fired. Otherwise ``key=value`` pairs joined by ``;``. When the
    profile has no case-studies corpus the fabricated-number tier is reported as
    ``tier1=no-corpus`` rather than as a wall of false positives.
    """
    parts: list[str] = []
    blocking = [f for f in report.research_findings if getattr(f, "level", "") == "block"]
    if blocking:
        parts.append(f"research={len(blocking)}")
    if corpus_present:
        if report.untraceable_numbers:
            parts.append("untraceable=" + ",".join(report.untraceable_numbers))
    else:
        parts.append("tier1=no-corpus")
    if report.unhedged_internals_claims:
        parts.append(f"unhedged={len(report.unhedged_internals_claims)}")
    return ";".join(parts) if parts else "clean"


def build_record(
    row: dict,
    *,
    spec_path: str,
    csv_path: str,
    subject: str,
    body: str,
    touch_n: int,
    verdict: dict | None,
    backend: str,
    judge_batch: int,
    repair_attempt: int,
    repaired: bool,
    case_studies: str | None = None,
) -> Adjudication:
    """One row → one record. A row with no readable verdict becomes ``unscored``, never
    disappears: a vanished row makes a partial batch look complete."""
    email = (row.get("email") or "").strip()
    common = {
        "email": email,
        "touch": touch_n,
        "stratum": stratum_of(row),
        "row_id": _row_id(spec_path, csv_path, email, touch_n),
        "body_hash": hashlib.sha256(body.encode("utf-8")).hexdigest()[:16],
        "repair_attempt": int(repair_attempt),
        "repaired": bool(repaired),
        "backend": backend,
        "judge_batch": int(judge_batch),
        # Derived from the row, not passed in: the prompt builders read the same
        # `lane_of`, so what was asked and what is recorded cannot drift apart.
        "rubric": rubric_id(lane_of(row)),
        "rubric_version": rubric_version(lane_of(row)),
        "grounding": grounding_flags(
            groundedness_report(
                _row_id(spec_path, csv_path, email, touch_n), row, body, case_studies or ""
            ),
            corpus_present=case_studies is not None,
        ),
    }
    if verdict is None:
        return Adjudication(
            verdict="",
            score=0,
            note="judge returned no readable verdict",
            unscored=True,
            **common,
        )
    return Adjudication(**verdict, **common)


def load_rows(spec_path: str, csv_path: str, touches: str) -> tuple[list[dict], list, list[str]]:
    """Read the spec + CSV — or a 1:1 pack — and return ``(rows, touches, problems)``."""
    import csv as _csv

    # Imported here rather than at module scope: the linter import needs a sys.path insert
    # that only makes sense once, and keeping it local keeps this module importable by
    # tests that never touch rendering.
    from .render import parse_spec

    problems: list[str] = []
    spec = Path(spec_path)
    if not spec.is_file():
        return [], [], [f"no spec at {spec_path}"]
    text = spec.read_text(encoding="utf-8")
    parsed = parse_spec(text)
    if not parsed:
        # Not a sequence spec — so try the other thing this file can be, a 1:1 pack, which
        # carries its own recipient (`csv_path` is unused and stays "" in the record's
        # row_id: a true identity — one pack, one person, one touch — not a stub).
        #
        # The touch count is the positive test, deliberately. `detect_format` cannot make
        # this call: it answers "draft-outreach" by ELIMINATION, so asking it first routes a
        # malformed spec into the pack branch and scores it as nothing — the exact
        # silently-empty batch this adapter exists to stop, reintroduced one layer up.
        return pack_rows(spec, text)
    csvp = Path(csv_path)
    if not csvp.is_file():
        problems.append(f"no csv at {csv_path}")
    if problems:
        return [], [], problems
    if touches != "all":
        try:
            want = int(touches)
        except ValueError:
            want = 1
        parsed = [t for t in parsed if getattr(t, "n", 1) == want] or parsed[:1]
    with csvp.open(newline="", encoding="utf-8") as fh:
        rows = list(_csv.DictReader(fh))
    return rows, parsed, problems


def confine_to_content_root(out_path: str) -> tuple[Path | None, str]:
    """Resolve ``out_path`` inside the content root, or explain why it cannot be.

    A judge that can write anywhere is a tenant-boundary hole, not a worker.
    """
    out = Path(out_path).expanduser().resolve()
    try:
        from gtm_core.paths import resolve_content_root

        out.relative_to(resolve_content_root().resolve())
    except Exception:  # noqa: BLE001
        return None, f"out_path must resolve inside the content root: {out_path}"
    return out, ""
