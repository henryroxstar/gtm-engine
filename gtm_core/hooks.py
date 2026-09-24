"""Hook Library engine — tenant-scoped hook bank schema, persistence, and guards.

A hook is a portable unit of attention: `{ angle, opening_beat, payoff_promise,
format_affinity, proof_required }`. Tenant-specific hooks live in
`profiles/<tenant>/knowledge/hooks.toml`; this module reads/writes that file and
enforces the invariants that let the same hook render across text, image, and
video.
"""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import outcomes as oc
from .paths import resolve_knowledge_file, resolve_profiles_root


@dataclass
class OpeningBeat:
    """One format-specific execution of a hook.

    ``pattern_id`` is optional and names the X post structure this beat uses (see
    `gtm_core.tweet_patterns` — a `single`/`thread` closed vocabulary, distinct from
    `hook_score.py`'s unrelated "pattern" scoring component). Beat-level, not hook-level: a
    hook's `single` and `thread` executions are different shapes and may use different
    structures.
    """

    format: str
    variant: str = "A"
    text: str | None = None
    video: str | None = None
    image: str | None = None
    pattern_id: str | None = None

    @classmethod
    def from_raw(cls, raw: dict) -> OpeningBeat:
        return cls(
            format=str(raw.get("format", "")),
            variant=str(raw.get("variant", "A")),
            text=raw.get("text"),
            video=raw.get("video"),
            image=raw.get("image"),
            pattern_id=raw.get("pattern_id"),
        )

    def to_raw(self) -> dict:
        out: dict[str, Any] = {"format": self.format, "variant": self.variant}
        if self.text is not None:
            out["text"] = self.text
        if self.video is not None:
            out["video"] = self.video
        if self.image is not None:
            out["image"] = self.image
        if self.pattern_id is not None:
            out["pattern_id"] = self.pattern_id
        return out


@dataclass
class HistoryEvent:
    """Append-only promotion/demotion record for a hook."""

    event: str  # "promoted", "demoted", "revived", "candidate_added"
    at: str
    evidence: str

    @classmethod
    def from_raw(cls, raw: dict) -> HistoryEvent:
        return cls(
            event=str(raw.get("event", "")),
            at=str(raw.get("at", _utc_now_iso())),
            evidence=str(raw.get("evidence", "")),
        )

    def to_raw(self) -> dict:
        return {"event": self.event, "at": self.at, "evidence": self.evidence}


@dataclass
class Hook:
    """Schema for one hook row in `hooks.toml`.

    `id` is stable forever. `status` is one of: candidate, test, proven, banned.
    """

    id: str
    angle: str
    payoff_promise: str
    proof_required: list[str] = field(default_factory=list)
    formats: list[str] = field(default_factory=list)
    tone: str | None = None
    max_impressions: int = 0
    fatigue_window_days: int = 30
    created_at: str = field(default_factory=lambda: _utc_now_iso()[:10])
    status: str = "test"
    opening_beats: list[OpeningBeat] = field(default_factory=list)
    history: list[HistoryEvent] = field(default_factory=list)
    source_signal: str | None = None
    pillar: str | None = None
    goal: str | None = None

    @classmethod
    def from_raw(cls, raw: dict) -> Hook:
        return cls(
            id=str(raw.get("id", "")),
            angle=str(raw.get("angle", "")),
            payoff_promise=str(raw.get("payoff_promise", "")),
            proof_required=_str_list(raw.get("proof_required")),
            formats=_str_list(raw.get("formats")),
            tone=raw.get("tone"),
            max_impressions=int(raw.get("max_impressions", 0) or 0),
            fatigue_window_days=int(raw.get("fatigue_window_days", 30) or 30),
            created_at=str(raw.get("created_at", _utc_now_iso()[:10])),
            status=str(raw.get("status", "test")),
            opening_beats=[OpeningBeat.from_raw(b) for b in raw.get("opening_beats") or []],
            history=[HistoryEvent.from_raw(h) for h in raw.get("history") or []],
            source_signal=raw.get("source_signal"),
            pillar=raw.get("pillar"),
            goal=raw.get("goal"),
        )

    def to_raw(self) -> dict:
        out: dict[str, Any] = {
            "id": self.id,
            "angle": self.angle,
            "payoff_promise": self.payoff_promise,
        }
        if self.proof_required:
            out["proof_required"] = self.proof_required
        if self.formats:
            out["formats"] = self.formats
        if self.tone is not None:
            out["tone"] = self.tone
        if self.max_impressions:
            out["max_impressions"] = self.max_impressions
        if self.fatigue_window_days != 30:
            out["fatigue_window_days"] = self.fatigue_window_days
        if self.created_at:
            out["created_at"] = self.created_at
        if self.status != "test":
            out["status"] = self.status
        if self.opening_beats:
            out["opening_beats"] = [b.to_raw() for b in self.opening_beats]
        if self.history:
            out["history"] = [h.to_raw() for h in self.history]
        if self.source_signal is not None:
            out["source_signal"] = self.source_signal
        if self.pillar is not None:
            out["pillar"] = self.pillar
        if self.goal is not None:
            out["goal"] = self.goal
        return out


@dataclass
class BannedConfig:
    """Tenant-declared banned stems/angles enforced by hooks_lint."""

    stems: list[str] = field(default_factory=list)
    angles: list[str] = field(default_factory=list)

    @classmethod
    def from_raw(cls, raw: dict | None) -> BannedConfig:
        if not raw:
            return cls()
        return cls(stems=_str_list(raw.get("stems")), angles=_str_list(raw.get("angles")))

    def to_raw(self) -> dict:
        out: dict[str, Any] = {}
        if self.stems:
            out["stems"] = self.stems
        if self.angles:
            out["angles"] = self.angles
        return out


@dataclass
class HookBank:
    """The full in-memory representation of `hooks.toml`."""

    hooks: list[Hook]
    banned: BannedConfig = field(default_factory=BannedConfig)
    authoritative: str = "hooks.toml"  # "hooks.toml" | "hook-matrix.md"
    migrated_from_sha: str | None = None

    def by_id(self, hook_id: str) -> Hook | None:
        for h in self.hooks:
            if h.id == hook_id:
                return h
        return None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _str_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        return [value]
    return []


def _toml_escape(value: str) -> str:
    """Basic TOML string escaping for quoted values."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _render_toml(bank: HookBank) -> str:
    """Hand-rolled TOML writer so gtm_core stays stdlib-only.

    tomllib reads; this writes. Output is deterministic and minimal.
    """
    lines: list[str] = [
        "# Hook bank — generated or hand-edited. This file is the authoritative hook library.",
        f"# authoritative_source: {bank.authoritative}",
    ]
    if bank.migrated_from_sha:
        lines.append(f"# migrated_from_sha: {bank.migrated_from_sha}")
    lines.append("")

    banned = bank.banned.to_raw()
    if banned:
        lines.append("[banned]")
        for key in ("stems", "angles"):
            if key in banned:
                lines.append(f"{key} = {toml_inline_list(banned[key])}")
        lines.append("")

    for hook in bank.hooks:
        lines.append("[[hook]]")
        lines.append(f'id = "{_toml_escape(hook.id)}"')
        lines.append(f'angle = "{_toml_escape(hook.angle)}"')
        lines.append(f'payoff_promise = "{_toml_escape(hook.payoff_promise)}"')
        if hook.proof_required:
            lines.append(f"proof_required = {toml_inline_list(hook.proof_required)}")
        if hook.formats:
            lines.append(f"formats = {toml_inline_list(hook.formats)}")
        if hook.tone is not None:
            lines.append(f'tone = "{_toml_escape(hook.tone)}"')
        if hook.max_impressions:
            lines.append(f"max_impressions = {hook.max_impressions}")
        if hook.fatigue_window_days != 30:
            lines.append(f"fatigue_window_days = {hook.fatigue_window_days}")
        if hook.created_at:
            lines.append(f'created_at = "{_toml_escape(hook.created_at)}"')
        if hook.status != "test":
            lines.append(f'status = "{_toml_escape(hook.status)}"')
        if hook.source_signal is not None:
            lines.append(f'source_signal = "{_toml_escape(hook.source_signal)}"')
        if hook.pillar is not None:
            lines.append(f'pillar = "{_toml_escape(hook.pillar)}"')
        if hook.goal is not None:
            lines.append(f'goal = "{_toml_escape(hook.goal)}"')

        if hook.opening_beats:
            lines.append("")
            lines.append("# Format-specific opening-beat variants")
            for beat in hook.opening_beats:
                lines.append("[[hook.opening_beats]]")
                lines.append(f'format = "{_toml_escape(beat.format)}"')
                lines.append(f'variant = "{_toml_escape(beat.variant)}"')
                if beat.text is not None:
                    lines.append(f'text = "{_toml_escape(beat.text)}"')
                if beat.video is not None:
                    lines.append(f'video = "{_toml_escape(beat.video)}"')
                if beat.image is not None:
                    lines.append(f'image = "{_toml_escape(beat.image)}"')
                if beat.pattern_id is not None:
                    lines.append(f'pattern_id = "{_toml_escape(beat.pattern_id)}"')
                lines.append("")

        if hook.history:
            lines.append("")
            lines.append("# Append-only promotion/demotion history")
            for ev in hook.history:
                lines.append("[[hook.history]]")
                lines.append(f'event = "{_toml_escape(ev.event)}"')
                lines.append(f'at = "{_toml_escape(ev.at)}"')
                lines.append(f'evidence = "{_toml_escape(ev.evidence)}"')
                lines.append("")
        lines.append("")

    return "\n".join(lines) + "\n"


def toml_inline_list(items: list[str]) -> str:
    if not items:
        return "[]"
    return "[" + ", ".join(f'"{_toml_escape(i)}"' for i in items) + "]"


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def hooks_toml_path(
    profiles_root: Path,
    profile: str,
    product: str | None = None,
    overlay: str | None = None,
) -> Path:
    """Resolve the hooks.toml path using the standard knowledge-file fallback.

    ``overlay`` is the messaging experiment rung, admitted since 2026-09-23. Explicit and
    never ambient, exactly like ``product``: an experiment that could be bound from the
    environment would outlive the run that asked for it, and a week of copy would quietly
    be written against a hook bank nobody chose that morning. Whether the experiment exists
    and may override this file is decided once by ``gtm_core.experiments.admit``; this is a
    path resolver and validates nothing.
    """
    return resolve_knowledge_file(
        profiles_root, profile, "hooks.toml", product=product, overlay=overlay
    )


def hooks_matrix_path(
    profiles_root: Path,
    profile: str,
    product: str | None = None,
    overlay: str | None = None,
) -> Path:
    return resolve_knowledge_file(
        profiles_root, profile, "hook-matrix.md", product=product, overlay=overlay
    )


def _read_toml(path: Path) -> dict:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def load_hooks(
    profiles_root: Path,
    profile: str,
    *,
    product: str | None = None,
    content_root: Path | None = None,
    overlay: str | None = None,
) -> HookBank:
    """Load the hook bank for `profile`.

    Reads `hooks.toml` if it exists and is authoritative. Falls back to parsing
    `hook-matrix.md` if `hooks.toml` is absent. Existing tenants keep working
    unchanged during the migration window.

    The fallback **refuses** a `hook-matrix.md` generated from `angles.toml`
    (`ValueError`, see `_parse_hook_matrix`) rather than returning an empty bank:
    a tenant on the generated matrix with no `hooks.toml` has no hooks, and that
    must be said rather than silently returned as a bank of zero.
    """
    toml_path = hooks_toml_path(profiles_root, profile, product=product, overlay=overlay)
    if toml_path.is_file():
        data = _read_toml(toml_path)
        return HookBank(
            hooks=[Hook.from_raw(h) for h in data.get("hook", [])],
            banned=BannedConfig.from_raw(data.get("banned")),
            authoritative=str(data.get("authoritative", "hooks.toml")),
            migrated_from_sha=data.get("migrated_from_sha"),
        )

    matrix_path = hooks_matrix_path(profiles_root, profile, product=product, overlay=overlay)
    if matrix_path.is_file():
        return _parse_hook_matrix(matrix_path)

    return HookBank(hooks=[], authoritative="hook-matrix.md")


def save_hooks(
    profiles_root: Path,
    profile: str,
    bank: HookBank,
    *,
    product: str | None = None,
) -> Path:
    """Write `bank` to `profiles/<profile>/knowledge/hooks.toml`.

    The immutability guard is enforced separately by callers that mutate a hook.
    """
    target = hooks_toml_path(profiles_root, profile, product=product)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_render_toml(bank), encoding="utf-8")
    return target


# --------------------------------------------------------------------------- #
# hook-matrix.md fallback parser
# --------------------------------------------------------------------------- #


def _parse_hook_matrix(path: Path) -> HookBank:
    """Best-effort parse of a markdown hook-matrix table into a HookBank.

    The legacy `hook-matrix.md` uses tables with columns `id`, `Persona`,
    `Signal to open on`, `Hook angle`. We map those to `id`, `angle`, and stash
    the persona/signal in `source_signal`. Status defaults to `test`; formats is
    empty (to be filled by the operator during migration).

    **A GENERATED matrix is refused, not parsed.** Since FR2 (2026-09-24)
    `gtm_core.messaging.matrix_view` renders `hook-matrix.md` from `angles.toml`
    on a seat × (premise × opener_kind) axis, with no `id` column at all — so the
    `table_re` below matched nothing and this returned `HookBank(hooks=[])`: zero
    hooks, no defect, and every hook gate downstream passing by finding nothing.
    A tenant that adopts the generated matrix needs a real `hooks.toml`; the two
    files answer different questions and one is not a fallback for the other.
    Detected with `matrix_view.is_generated`, so there is one definition of "this
    file is generated" rather than a second banner test here. Imported inside the
    function because that package pulls the registry in with it, and a hook bank
    load must not depend on it at import time.
    """
    from .messaging.matrix_view import is_generated

    text = path.read_text(encoding="utf-8")
    if is_generated(text):
        raise ValueError(
            f"{path}: this hook-matrix.md is GENERATED from angles.toml (seat × premise) and "
            "carries no hook ids — it is not a hook bank. Author "
            f"{path.parent / 'hooks.toml'} instead; an empty bank here would read as "
            "'no hooks configured' everywhere downstream."
        )
    hooks: list[Hook] = []

    # Match markdown tables with an id column.
    table_re = re.compile(
        r"\|\s*id\s*\|[^\n]*\n\|[-:\s|]+\n((?:\|[^\n]*\|\n?)+)",
        re.IGNORECASE,
    )
    for table_match in table_re.finditer(text):
        body = table_match.group(1)
        for line in body.splitlines():
            if not line.strip() or not line.startswith("|"):
                continue
            cells = [c.strip().strip('"').strip() for c in line.split("|")][1:-1]
            if not cells or not cells[0]:
                continue
            hook_id = cells[0]
            angle = cells[-1] if cells else ""
            signal = " | ".join(cells[1:-1]) if len(cells) > 2 else ""
            hooks.append(
                Hook(
                    id=hook_id,
                    angle=angle,
                    payoff_promise="",
                    status="test",
                    source_signal=signal or None,
                )
            )

    return HookBank(hooks=hooks, authoritative="hook-matrix.md")


# --------------------------------------------------------------------------- #
# Library operations
# --------------------------------------------------------------------------- #


def list_hooks(
    bank: HookBank,
    *,
    format: str | None = None,
    pillar: str | None = None,
    goal: str | None = None,
    status: str | None = None,
    include_candidates: bool = False,
) -> list[Hook]:
    """Return hooks matching all provided filters.

    By default candidates are excluded from rotation; pass
    `include_candidates=True` to see them.
    """
    out: list[Hook] = []
    for h in bank.hooks:
        if status is not None and h.status != status:
            continue
        if not include_candidates and h.status == "candidate":
            continue
        if format is not None and format not in h.formats:
            continue
        if pillar is not None and h.pillar != pillar:
            continue
        if goal is not None and h.goal != goal:
            continue
        out.append(h)
    out.sort(key=lambda h: h.id)
    return out


def _outcome_uses_hook(content_root: Path, profile: str, hook_id: str) -> bool:
    """True if any outcome row tags this hook — the immutability guard trigger."""
    rows = oc.read_outcomes(content_root, profile)
    tag = f"hook:{hook_id}"
    for row in rows:
        tags = row.get("tags") or []
        if isinstance(tags, list) and tag in tags:
            return True
    return False


def promote_hook(
    profiles_root: Path,
    content_root: Path,
    profile: str,
    hook_id: str,
    evidence: str,
    *,
    product: str | None = None,
) -> Hook:
    """Promote a hook to `proven` (or `test` from `candidate`) with append-only history."""
    bank = load_hooks(profiles_root, profile, product=product, content_root=content_root)
    hook = bank.by_id(hook_id)
    if hook is None:
        raise ValueError(f"hook {hook_id!r} not found")
    if hook.status == "proven":
        raise ValueError(f"hook {hook_id!r} is already proven")

    hook.status = "proven"
    hook.history.append(
        HistoryEvent(
            event="promoted",
            at=_utc_now_iso(),
            evidence=evidence,
        )
    )
    save_hooks(profiles_root, profile, bank, product=product)
    return hook


def demote_hook(
    profiles_root: Path,
    content_root: Path,
    profile: str,
    hook_id: str,
    evidence: str,
    *,
    product: str | None = None,
) -> Hook:
    """Demote a hook to `test` with append-only history. Refuses to mutate a `hook_id`
    that already has outcome rows referencing it.
    """
    bank = load_hooks(profiles_root, profile, product=product, content_root=content_root)
    hook = bank.by_id(hook_id)
    if hook is None:
        raise ValueError(f"hook {hook_id!r} not found")
    if _outcome_uses_hook(content_root, profile, hook_id):
        raise ValueError(
            f"hook {hook_id!r} has outcome rows — its id is immutable; "
            "retire it instead of changing its status"
        )
    if hook.status == "test":
        raise ValueError(f"hook {hook_id!r} is already test")

    hook.status = "test"
    hook.history.append(
        HistoryEvent(
            event="demoted",
            at=_utc_now_iso(),
            evidence=evidence,
        )
    )
    save_hooks(profiles_root, profile, bank, product=product)
    return hook


def add_candidate(
    profiles_root: Path,
    profile: str,
    hook: Hook,
    *,
    product: str | None = None,
    content_root: Path | None = None,
) -> Path:
    """Append a new `candidate` hook to the bank.

    Candidates never enter rotation until promoted to `test`.
    """
    bank = load_hooks(profiles_root, profile, product=product, content_root=content_root)
    if bank.by_id(hook.id):
        raise ValueError(f"hook {hook.id!r} already exists")
    hook.status = "candidate"
    hook.history.append(
        HistoryEvent(
            event="candidate_added",
            at=_utc_now_iso(),
            evidence=hook.source_signal or "radar candidate",
        )
    )
    bank.hooks.append(hook)
    return save_hooks(profiles_root, profile, bank, product=product)


def _latest_revive_ts(hook: Hook) -> float | None:
    """Return the most recent `revived` history timestamp as seconds since epoch, if any."""
    latest: float | None = None
    for event in hook.history:
        if event.event != "revived":
            continue
        try:
            ts = datetime.strptime(event.at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()
        except ValueError:
            continue
        if latest is None or ts > latest:
            latest = ts
    return latest


def is_fatigued(
    content_root: Path,
    profile: str,
    hook_id: str,
    bank: HookBank | None = None,
    *,
    now: datetime | None = None,
) -> bool:
    """True if impressions for `hook_id` in `outcomes.jsonl` exceed `max_impressions`
    inside the fatigue window.

    A ``revived`` history event resets the window from its timestamp, so an operator
    can explicitly re-allow a fatigued hook without mutating immutable outcome rows.
    """
    if bank is None:
        bank = load_hooks(resolve_profiles_root(), profile, content_root=content_root)
    hook = bank.by_id(hook_id)
    if hook is None or hook.max_impressions <= 0:
        return False

    now = now or datetime.now(UTC)
    revive_ts = _latest_revive_ts(hook)
    window_start = max(
        now.timestamp() - hook.fatigue_window_days * 86400,
        revive_ts or 0.0,
    )
    rows = oc.read_outcomes(content_root, profile)
    impressions = 0.0
    for row in rows:
        tags = row.get("tags") or []
        if not isinstance(tags, list) or f"hook:{hook_id}" not in tags:
            continue
        outcome = str(row.get("outcome", "")).strip().lower()
        if outcome not in oc.IMPRESSION_OUTCOMES:
            continue
        ts = row.get("ts", "")
        if not isinstance(ts, str):
            continue
        try:
            row_ts = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()
        except ValueError:
            continue
        if row_ts >= window_start:
            impressions += float(row.get("value", 1) or 0)

    return impressions >= hook.max_impressions


def revive_hook(
    profiles_root: Path,
    content_root: Path,
    profile: str,
    hook_id: str,
    evidence: str,
    *,
    product: str | None = None,
) -> Hook:
    """Revive a fatigued hook by appending a ``revived`` history event.

    The event resets the fatigue window from its timestamp; impressions before the
    latest revive are no longer counted toward fatigue. The hook's configured
    ``max_impressions`` and ``fatigue_window_days`` are preserved.
    """
    bank = load_hooks(profiles_root, profile, product=product, content_root=content_root)
    hook = bank.by_id(hook_id)
    if hook is None:
        raise ValueError(f"hook {hook_id!r} not found")

    hook.history.append(
        HistoryEvent(
            event="revived",
            at=_utc_now_iso(),
            evidence=evidence,
        )
    )
    save_hooks(profiles_root, profile, bank, product=product)
    return hook


# --------------------------------------------------------------------------- #
# Migration CLI
# --------------------------------------------------------------------------- #


def migrate_from_matrix(
    profiles_root: Path,
    profile: str,
    *,
    product: str | None = None,
) -> Path:
    """Generate a proposed `hooks.toml` from the existing `hook-matrix.md`.

    Proven status is assigned only when the matrix row is explicitly marked
    tested/verified; everything else becomes `test`. The operator reviews the
    draft before promotion.
    """
    matrix_path = hooks_matrix_path(profiles_root, profile, product=product)
    if not matrix_path.is_file():
        raise FileNotFoundError(f"no hook-matrix.md for profile {profile!r}")

    bank = _parse_hook_matrix(matrix_path)
    # Heuristic: rows with "tested" in the source signal text are proven.
    for hook in bank.hooks:
        signal = (hook.source_signal or "").lower()
        if "tested" in signal or "proven" in signal or "confirmed" in signal:
            hook.status = "proven"
        hook.payoff_promise = hook.angle  # best-effort fallback

    # Record SHA of the matrix file at migration time.
    import hashlib

    bank.migrated_from_sha = hashlib.sha256(matrix_path.read_bytes()).hexdigest()
    bank.authoritative = "hooks.toml (draft)"

    target = hooks_toml_path(profiles_root, profile, product=product)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_render_toml(bank), encoding="utf-8")
    return target


def promote_hooks_toml(
    profiles_root: Path,
    profile: str,
    *,
    product: str | None = None,
) -> Path:
    """Make `hooks.toml` authoritative after operator review.

    Validates schema and flips the authoritative flag. Does NOT overwrite
    `hook-matrix.md`; that remains generated output.
    """
    from . import hooks_lint

    toml_path = hooks_toml_path(profiles_root, profile, product=product)
    if not toml_path.is_file():
        raise FileNotFoundError(f"no hooks.toml for profile {profile!r}")

    bank = load_hooks(profiles_root, profile, product=product)
    errors = hooks_lint.lint_bank(bank)
    if errors:
        raise ValueError("hooks.toml validation failed:\n" + "\n".join(f"  - {e}" for e in errors))

    bank.authoritative = "hooks.toml"
    save_hooks(profiles_root, profile, bank, product=product)
    return toml_path


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--profile", required=True)
    shared.add_argument("--product", default=None)
    shared.add_argument("--profiles-root", default=None)
    shared.add_argument("--content-root", default=None)

    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.hooks",
        description="Hook Library engine: load, save, migrate, and manage hooks.toml.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("migrate", parents=[shared], help="draft hooks.toml from hook-matrix.md")
    sub.add_parser("promote-hooks-toml", parents=[shared], help="make hooks.toml authoritative")

    list_p = sub.add_parser("list", parents=[shared], help="list hooks")
    list_p.add_argument("--status", default=None)
    list_p.add_argument("--format", default=None)
    list_p.add_argument("--include-candidates", action="store_true")

    add_p = sub.add_parser("add-candidate", parents=[shared], help="append a candidate hook")
    add_p.add_argument("--json", required=True, help="hook JSON payload")

    sub.add_parser("fatigued", parents=[shared], help="list fatigued hooks")

    promote_p = sub.add_parser("promote", parents=[shared], help="promote a hook to proven")
    promote_p.add_argument("--hook", required=True)
    promote_p.add_argument("--evidence", required=True)

    demote_p = sub.add_parser("demote", parents=[shared], help="demote a hook to test")
    demote_p.add_argument("--hook", required=True)
    demote_p.add_argument("--evidence", required=True)

    revive_p = sub.add_parser("revive", parents=[shared], help="revive a fatigued hook")
    revive_p.add_argument("--hook", required=True)
    revive_p.add_argument("--evidence", default="operator revived from cockpit")

    args = parser.parse_args(argv)

    profiles_root = (
        Path(args.profiles_root).expanduser().resolve()
        if args.profiles_root
        else resolve_profiles_root()
    )
    content_root = Path(args.content_root).expanduser().resolve() if args.content_root else None

    if args.cmd == "migrate":
        path = migrate_from_matrix(profiles_root, args.profile, product=args.product)
        print(f"wrote draft {path}")
        return 0

    if args.cmd == "promote-hooks-toml":
        path = promote_hooks_toml(profiles_root, args.profile, product=args.product)
        print(f"hooks.toml is now authoritative: {path}")
        return 0

    bank = load_hooks(profiles_root, args.profile, product=args.product)

    if args.cmd == "list":
        hooks = list_hooks(
            bank,
            status=args.status,
            format=args.format,
            include_candidates=args.include_candidates,
        )
        for h in hooks:
            print(f"{h.id}\t{h.status}\t{','.join(h.formats)}\t{h.angle[:80]}")
        return 0

    if args.cmd == "add-candidate":
        if content_root is None:
            raise SystemExit("[hooks] --content-root required for add-candidate")
        payload = json.loads(args.json)
        hook = Hook.from_raw(payload)
        path = add_candidate(
            profiles_root, args.profile, hook, product=args.product, content_root=content_root
        )
        print(f"added candidate {hook.id}: {path}")
        return 0

    if args.cmd == "fatigued":
        if content_root is None:
            raise SystemExit("[hooks] --content-root required for fatigued check")
        for h in bank.hooks:
            if h.status == "candidate":
                continue
            if is_fatigued(content_root, args.profile, h.id, bank):
                print(f"{h.id}\tfatigued")
        return 0

    if args.cmd == "promote":
        if content_root is None:
            raise SystemExit("[hooks] --content-root required for promote")
        hook = promote_hook(
            profiles_root,
            content_root,
            args.profile,
            args.hook,
            args.evidence,
            product=args.product,
        )
        print(f"{hook.id}\tpromoted to {hook.status}")
        return 0

    if args.cmd == "demote":
        if content_root is None:
            raise SystemExit("[hooks] --content-root required for demote")
        hook = demote_hook(
            profiles_root,
            content_root,
            args.profile,
            args.hook,
            args.evidence,
            product=args.product,
        )
        print(f"{hook.id}\tdemoted to {hook.status}")
        return 0

    if args.cmd == "revive":
        if content_root is None:
            raise SystemExit("[hooks] --content-root required for revive")
        hook = revive_hook(
            profiles_root,
            content_root,
            args.profile,
            args.hook,
            args.evidence,
            product=args.product,
        )
        print(f"{hook.id}\trevived")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
