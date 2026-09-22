"""Single-hit validation, freshness, subject-relevance, and agent-kind classification for
:mod:`gtm_core.web_sweep` (PSK-016/017/018/020/021's shared core).

Split out of ``web_sweep.py`` to keep that module under the §R10 500-line ratchet — this is
a coherent, single-purpose block (everything :func:`gtm_core.web_sweep.normalize_sweep` needs
to evaluate ONE raw hit) with no dependency on the query-generation side of the sweep.

``parse_date``, ``normalize_hit``, ``_determine_agent_kind``, and ``_check_freshness`` are
re-exported from ``gtm_core.web_sweep`` so existing callers and tests are unaffected; the rest
(``_evaluate_hit``, ``_shape_check``, ``_resolve_subject``, ...) are this module's own helpers,
consumed by :func:`gtm_core.web_sweep.normalize_sweep`.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from gtm_core.merge_hygiene import clean_company
from gtm_core.web_sweep_urls import is_valid_source_url

# Freshness thresholds in days
ENTERPRISE_MAX_AGE_DAYS = 90
STARTUP_FUNDING_MAX_AGE_DAYS = 540  # 18 months — visibility window for context only
GENERAL_MAX_AGE_DAYS = 210  # the universal ceiling the load gate enforces on signal_observed

_VALID_SEGMENTS = ("enterprise", "startup")

_SHAPE_REJECT_REASONS = frozenset(
    {"not-an-object", "missing-url", "missing-date", "missing-evidence"}
)


def parse_date(date_str: str) -> dt.date | None:
    """Parse ISO YYYY-MM-DD date."""
    if not date_str:
        return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(date_str))
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _validate_segment(segment: str) -> str:
    seg = (segment or "").strip().lower()
    if seg not in _VALID_SEGMENTS:
        raise ValueError(f"unknown segment {segment!r}; expected one of {_VALID_SEGMENTS}")
    return seg


def _check_freshness(hit_type: str, segment: str, age_days: int) -> bool:
    """Legacy visibility window: is this hit fresh enough to be shown at all."""
    seg = _validate_segment(segment)
    if age_days < 0:
        return False
    if hit_type == "funding" and seg == "startup":
        return age_days <= STARTUP_FUNDING_MAX_AGE_DAYS
    if seg == "enterprise":
        return age_days <= ENTERPRISE_MAX_AGE_DAYS
    return age_days <= GENERAL_MAX_AGE_DAYS


def _within_why_now_window(age_days: int) -> bool:
    """The universal ceiling the downstream load gate enforces on `signal_observed`
    (PSK-021) — independent of type/segment. A hit can be legacy-fresh yet still fail
    this: exactly the startup-funding carve-out, which is visible as context only."""
    return age_days <= GENERAL_MAX_AGE_DAYS


# --- agent-kind classification (PSK-016: word-bounded, not a substring test) ---------------

_AI_VOCAB_RE = re.compile(
    r"\bai\b|\bllm\b|\bmcp\b|\bagentic\b|\bcopilot\b|"
    r"\bmachine learning\b|\bml platforms?\b|"
    r"\bagent platforms?\b|\bagent marketplaces?\b|\bautonomous agents?\b",
    re.IGNORECASE,
)
_AGENT_WORD_RE = re.compile(r"\bagents?\b", re.IGNORECASE)
_HUMAN_AGENT_CONTEXT_RE = re.compile(
    r"\binsurance\b|\breal[- ]estate\b|\bproperty\b|\bcall (?:center|centre)\b|"
    r"\bcustomer service\b|\bstaffing\b|\brecruit(?:ing|ment)?\b|\btravel\b|"
    r"\bsales agents?\b|\bappointed\b.*\bagents?\b|\bhires?\b.*\bagents?\b",
    re.IGNORECASE,
)


def _determine_agent_kind(text: str) -> str:
    """Classify agent kind: ai, human, unclear, or none — word-bounded matching only, so
    'ai' never matches inside 'said'/'retail'/'raised'/'maintain'/'email' and 'agent' inside
    an insurer's or staffing firm's own vocabulary is 'human', not 'ai'."""
    if _AI_VOCAB_RE.search(text):
        return "ai"
    if not _AGENT_WORD_RE.search(text):
        return "none"
    if _HUMAN_AGENT_CONTEXT_RE.search(text):
        return "human"
    return "unclear"


# --- subject relevance (PSK-017: a hit's evidence must be ABOUT the account) ----------------

_GENERIC_CORP_WORDS = frozenset(
    {
        "inc",
        "ltd",
        "pte",
        "group",
        "holdings",
        "corp",
        "company",
        "co",
        "llc",
        "limited",
        "plc",
        "gmbh",
    }
)


def _company_mention_tokens(cleaned_co: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9]+", cleaned_co)
    distinctive = [w for w in words if w.lower() not in _GENERIC_CORP_WORDS]
    return distinctive or words


def _mentions_company(haystack: str, cleaned_co: str) -> bool:
    if not cleaned_co:
        return False
    haystack_lower = haystack.lower()
    if cleaned_co.lower() in haystack_lower:
        return True
    tokens = _company_mention_tokens(cleaned_co)
    if not tokens:
        return False
    lead = tokens[0].lower()
    if len(lead) < 3:
        return False
    return bool(re.search(r"\b" + re.escape(lead) + r"\b", haystack_lower))


def _resolve_subject(
    raw: dict[str, Any], evidence: str, title: str, cleaned_co: str
) -> tuple[str | None, bool]:
    """Return (signal_subject, is_mismatch).

    An explicit `subject` always wins and is reported verbatim, even when it names a
    different entity — a mismatch is never silently relabelled as the account. Absent an
    explicit subject, the account must be named (or its distinctive leading token found) in
    the evidence/title, or the hit is rejected as not being about this account.
    """
    explicit = raw.get("subject")
    if explicit:
        explicit = str(explicit).strip()
        if explicit:
            if explicit.lower() == cleaned_co.lower():
                return cleaned_co, False
            return explicit, True
    if _mentions_company(f"{title} {evidence}", cleaned_co):
        return cleaned_co, False
    return None, True


def _resolve_strength(raw: dict[str, Any], hit_type: str, evidence: str) -> str:
    strength = str(raw.get("strength") or "").upper()
    if strength in ("H", "M", "L"):
        return strength
    # Heuristic default: newsroom / eng / incident with genuine AI-agent content -> H, else M.
    if hit_type in ("newsroom", "eng", "incident") and _determine_agent_kind(evidence) == "ai":
        return "H"
    return "M"


def _build_hit(
    hit_type: str,
    date_val: dt.date,
    age_days: int,
    url: str,
    strength: str,
    evidence: str,
    title: str,
) -> dict[str, Any]:
    date_iso = date_val.isoformat()
    return {
        "type": hit_type,
        "date": date_iso,
        "age_days": age_days,
        "url": url,
        "strength": strength,
        "tag": f"[{hit_type} | {date_iso} | {url} | {strength}]",
        "evidence": evidence,
        "title": title,
    }


def _shape_check(raw: Any) -> tuple[dict[str, Any] | None, str | None]:
    """First-pass shape/type validation shared by `_evaluate_hit`.

    Returns `(fields, None)` on success — `fields` carries `url`, `date_val`, `evidence`,
    `title` — or `(None, reason)` with one of the PSK-018 shape/value reason codes.
    """
    if not isinstance(raw, dict):
        return None, "not-an-object"

    url = str(raw.get("url") or raw.get("source_url") or "").strip()
    if not url:
        return None, "missing-url"
    if not is_valid_source_url(url):
        return None, "invalid-url"

    date_raw = raw.get("date") or raw.get("observed")
    if not date_raw:
        return None, "missing-date"
    date_val = parse_date(date_raw)
    if not date_val:
        return None, "unparseable-date"

    evidence = str(raw.get("evidence") or raw.get("snippet") or "").strip()
    if not evidence:
        return None, "missing-evidence"

    fields = {
        "url": url,
        "date_val": date_val,
        "evidence": evidence,
        "title": str(raw.get("title") or ""),
    }
    return fields, None


def _evaluate_hit(
    raw: Any,
    cleaned_co: str,
    segment: str,
    ref_date: dt.date | None,
) -> tuple[dict[str, Any] | None, str | None, dict[str, Any] | None, str | None]:
    """Validate and normalize one raw hit (PSK-018/017/021/020's shared core).

    Returns (hit, reject_reason, context_hit, stranger_subject):
      - `hit`: a normalized dict usable as a why-now candidate, or None.
      - `reject_reason`: a reason code (see the `web_sweep` module docstring) when `hit` is
        None.
      - `context_hit`: set only for a legacy-fresh startup-funding hit too old for the
        universal why-now ceiling (PSK-021) — visible for background only.
      - `stranger_subject`: an explicit `subject` naming a different entity (PSK-017), so
        the sweep can report the truth instead of the account's own name.
    """
    fields, reason = _shape_check(raw)
    if fields is None:
        return None, reason, None, None

    today = ref_date or dt.datetime.now(dt.UTC).date()
    age_days = (today - fields["date_val"]).days
    if age_days < 0:
        return None, "future-dated", None, None

    subject, mismatch = _resolve_subject(raw, fields["evidence"], fields["title"], cleaned_co)
    if mismatch:
        return None, "subject-mismatch", None, subject

    hit_type = str(raw.get("type") or "newsroom").lower()
    strength = _resolve_strength(raw, hit_type, fields["evidence"])

    if not _check_freshness(hit_type, segment, age_days):
        return None, "stale", None, None

    if not _within_why_now_window(age_days):
        # Legacy-fresh (startup funding, <=540d) but past the load gate's 210-day ceiling:
        # visible as context, never selectable as the why-now signal.
        context_hit = _build_hit(
            hit_type,
            fields["date_val"],
            age_days,
            fields["url"],
            strength,
            fields["evidence"],
            fields["title"],
        )
        return None, "stale", context_hit, None

    hit = _build_hit(
        hit_type,
        fields["date_val"],
        age_days,
        fields["url"],
        strength,
        fields["evidence"],
        fields["title"],
    )
    return hit, None, None, None


def normalize_hit(
    raw: dict[str, Any],
    company: str,
    segment: str = "startup",
    ref_date: dt.date | None = None,
) -> dict[str, Any] | None:
    """Normalize and validate a single hit. Returns None if invalid, stale, or off-subject.

    A thin, backward-compatible wrapper over :func:`_evaluate_hit`, which also carries the
    reason code, the context-hit bucket, and the stranger-subject tracking that
    :func:`gtm_core.web_sweep.normalize_sweep` needs and this single-hit contract does not
    expose.
    """
    cleaned_co = clean_company(company)
    hit, _reason, _context, _stranger = _evaluate_hit(raw, cleaned_co, segment, ref_date)
    return hit
