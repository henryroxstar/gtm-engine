from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .. import hooks as hk
from ..paths import _safe_segment, resolve_content_root, resolve_profiles_root
from .sources import _find_item, _load_text

#: Front-block keys a video script must carry before it may reach a paid stage. Each was added
#: after a specific defect shipped; ``claims_verified`` is the newest (2026-08-19) and exists
#: because a script asserted an incident mechanism it had only ever read in an internal harvest
#: file's paraphrase, never in the primary source. Four factual defects reached the draft.
_SCRIPT_REQUIRED_FRONT_KEYS = ("source_item", "claims_verified")

#: ``claims_verified: <verified>/<total> · log: <pointer|none: reason>``. The separator is a
#: middot in authored files; tolerate a plain ASCII pipe or semicolon too rather than failing a
#: script on punctuation.
_CLAIMS_RE = re.compile(
    r"^\s*(?P<verified>\d+)\s*/\s*(?P<total>\d+)\s*(?:[·|;]\s*log\s*:\s*(?P<log>.+?))?\s*$"
)

#: A ``hook_id`` front-block value must be a bare kebab-case id and nothing else. Mirrors
#: ``hooks_lint._ID_RE``. A shipped acme script wrote
#: ``hook_id: acme-... (PARTIAL — see note)``; a parenthetical caveat in an attribution key
#: is a note to a human that no join can read, so it fails here rather than joining to nothing.
_HOOK_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _hook_format_check(
    front: dict[str, str],
    script_name: str,
    profile: str,
    content_root: Path,
    blocking: list[str],
    warnings: list[str],
    checks: dict[str, bool | None],
) -> None:
    """Block when the script's ``hook_id`` does not declare its ``format``, or is fatigued.

    ``hook_score`` already refuses an undeclared hook x format pair — but ``video-script``
    caught that as its "Part A-only fallback" and carried on, so the composite gate was
    skipped silently on exactly the scripts that most needed it. Two shipped acme ``short``
    scripts cite a hook declaring only ``linkedin-text``/``carousel``. The remedy is one line in
    ``hooks.toml`` (declare the format, author its opening beat) or a different hook, so this is
    a block with a named fix rather than a warning nobody reads.

    Fatigue is checked here for the same reason: ``format-router`` stops on a fatigued hook, but
    the six dedicated video graphs never run ``format-router``, so nothing enforced it on the
    video lane.
    """
    hook_id = front.get("hook_id")
    fmt = front.get("format")

    if not hook_id:
        # An ad-hoc operator script legitimately has no plan item behind it. Losing attribution
        # is a cost to name, not a reason to refuse the script.
        checks["hook_declares_format"] = None
        warnings.append(
            f"{script_name}: no hook_id in the front block — this script cannot be attributed "
            "to a hook, so it can never earn a prior or be scored by hook_score"
        )
        return

    if not _HOOK_ID_RE.match(hook_id):
        checks["hook_declares_format"] = False
        blocking.append(
            f"{script_name}: hook_id {hook_id!r} is not a bare kebab-case hook id — the front "
            "block is a join key, so a caveat or comment there joins to nothing"
        )
        return

    bank = hk.load_hooks(resolve_profiles_root(), profile, content_root=content_root)
    hook = bank.by_id(hook_id)
    if hook is None:
        checks["hook_declares_format"] = False
        blocking.append(
            f"{script_name}: hook_id {hook_id!r} is not in the hook bank "
            f"(profiles/{profile}/knowledge/hooks.toml)"
        )
        return

    if not fmt:
        checks["hook_declares_format"] = None
        warnings.append(
            f"{script_name}: front block has no 'format', so the hook x format pair cannot be "
            "checked and hook_score cannot be run"
        )
        return

    checks["hook_declares_format"] = fmt in hook.formats
    if fmt not in hook.formats:
        blocking.append(
            f"{script_name}: hook {hook_id!r} does not declare format {fmt!r} "
            f"(declares: {', '.join(hook.formats) or 'none'}) — hook_score cannot run, so this "
            "script would reach render with no composite gate. Declare the format on the hook "
            "with an opening beat for it, or script against a hook that already declares it"
        )
        return

    fatigued = hk.is_fatigued(content_root, profile, hook_id, bank=bank)
    checks["hook_not_fatigued"] = not fatigued
    if fatigued:
        blocking.append(
            f"{script_name}: hook {hook_id!r} is fatigued (over {hook.max_impressions} "
            f"impressions inside {hook.fatigue_window_days}d) — revive it explicitly "
            f"(python -m gtm_core.hooks revive) or pick another hook"
        )


def _parse_front_block(text: str) -> dict[str, str]:
    """Parse the leading ``key: value`` lines of a script file.

    The block ends at the first blank line or the first markdown heading, whichever comes
    first — scripts written by ``video-script`` put the join-key block above the ``# Title``
    line with no fence around it, so there is no delimiter to key on.
    """
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            break
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if key and " " not in key:
            out[key] = value.strip()
    return out


def _find_script(content_root: Path, profile: str, item_id: str) -> Path | None:
    """Locate the script whose front block names ``item_id`` as its ``source_item``.

    Matches on the ``source_item`` field rather than a filename convention: the filename is
    ``<date>-<slug>.md`` and carries no item id, so globbing for the id finds nothing. Same
    class of bug as the ``plan_id``/``source_item`` mismatch fixed in
    :func:`_find_video_manifests` on 2026-08-17.
    """
    scripts_dir = content_root / _safe_segment(profile, "profile") / "scripts"
    if not scripts_dir.is_dir():
        return None
    for path in sorted(scripts_dir.glob("*.md")):
        text = _load_text(path)
        if text is None:
            continue
        if _parse_front_block(text).get("source_item") == item_id:
            return path
    return None


def script_check(profile: str, item_id: str, content_root: Path | None = None) -> dict[str, Any]:
    """Gate a written script before it reaches storyboard/render spend.

    Enforces the front-block contract ``video-script`` Step 2 writes, in particular
    ``claims_verified`` — the field that records whether every external claim reaching a viewer
    as VO or caption was checked against a source. The count being *present* is what this can
    verify mechanically; whether the log is honest is a human/model judgement. That is the same
    division of labour as ``visual_template``: making the field mandatory is the lever, because
    the failure being prevented is silence, not a wrong number.

    Returns ``{"proceed": bool, "blocking": [...], "warnings": [...], "checks": {...}}``.
    """
    content_root = content_root or resolve_content_root()
    blocking: list[str] = []
    warnings: list[str] = []
    checks: dict[str, bool | None] = {}

    item = _find_item(content_root, profile, item_id)
    if item is None:
        blocking.append(f"ContentItem {item_id!r} not found in content/{profile}/plans/")
        return {"proceed": False, "blocking": blocking, "warnings": warnings, "checks": checks}

    script_path = _find_script(content_root, profile, item_id)
    checks["script_found"] = script_path is not None
    if script_path is None:
        blocking.append(
            f"no script in content/{profile}/scripts/ carries source_item: {item_id} "
            "(video-script Step 2 writes this front-block field)"
        )
        return {"proceed": False, "blocking": blocking, "warnings": warnings, "checks": checks}

    text = _load_text(script_path) or ""
    front = _parse_front_block(text)

    _hook_format_check(front, script_path.name, profile, content_root, blocking, warnings, checks)

    missing = [k for k in _SCRIPT_REQUIRED_FRONT_KEYS if k not in front]
    checks["front_block_complete"] = not missing
    for key in missing:
        blocking.append(f"{script_path.name}: front block missing required key {key!r}")

    raw_claims = front.get("claims_verified")
    if raw_claims is None:
        checks["claims_verified_present"] = False
        checks["claims_verified_parsed"] = None
        checks["verification_log_present"] = None
        return {
            "proceed": False,
            "blocking": blocking,
            "warnings": warnings,
            "checks": checks,
        }

    checks["claims_verified_present"] = True
    match = _CLAIMS_RE.match(raw_claims)
    if match is None:
        checks["claims_verified_parsed"] = False
        checks["verification_log_present"] = None
        blocking.append(
            f"{script_path.name}: claims_verified {raw_claims!r} is not "
            "'<verified>/<total> · log: <pointer|none: reason>'"
        )
        return {
            "proceed": False,
            "blocking": blocking,
            "warnings": warnings,
            "checks": checks,
        }

    checks["claims_verified_parsed"] = True
    verified = int(match.group("verified"))
    total = int(match.group("total"))
    log_note = (match.group("log") or "").strip()

    if verified > total:
        blocking.append(
            f"{script_path.name}: claims_verified {verified}/{total} verifies more claims "
            "than the script makes"
        )

    has_log_section = re.search(r"^##+\s+Verification log\s*$", text, re.MULTILINE) is not None
    checks["verification_log_present"] = has_log_section

    if total > 0:
        if not has_log_section:
            blocking.append(
                f"{script_path.name}: claims_verified says {total} external claim(s) but the "
                "script has no '## Verification log' section recording source and quote per claim"
            )
        if verified < total:
            # Not a hard block by design: shipping a marked-unverified claim is a legitimate,
            # audited choice. Silence about it is not.
            warnings.append(
                f"{script_path.name}: {total - verified} of {total} claim(s) unverified — each "
                "must be cut or explicitly marked unverified in the script, and named in the "
                "handoff report"
            )
    elif not log_note.lower().startswith("none"):
        # 0/0 is legitimate but rare; it must say why rather than leaving the reader to guess
        # whether verification was skipped or genuinely not applicable.
        warnings.append(
            f"{script_path.name}: claims_verified 0/0 should record why "
            "(e.g. 'log: none: no external claims')"
        )

    proceed = not blocking
    return {"proceed": proceed, "blocking": blocking, "warnings": warnings, "checks": checks}
