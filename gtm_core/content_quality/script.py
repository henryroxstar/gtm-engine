from __future__ import annotations

import re
from fractions import Fraction
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

#: The message-bearing share of a story script's runtime. 90% of the runtime is the story and the
#: message lands in the last tenth, in one sentence. The failure this bounds is erosion by
#: increments — no single added proof point looks fatal, and five of them turn a story into an ad —
#: so it is a ratio in the front block rather than a judgement call per beat.
_MESSAGE_SHARE_CEILING = Fraction(1, 10)

#: ``message_share: <message-bearing s>/<total s>``. Decimals allowed on both sides: a beat table
#: in quarter-seconds sums to 2.5/30 as honestly as it does to 5/60, and the two must compare equal.
_SHARE_RE = re.compile(r"^\s*(?P<m>\d+(?:\.\d+)?)\s*/\s*(?P<t>\d+(?:\.\d+)?)\s*$")

#: A cell counts as a declared message beat on any of these. Anything else — including an empty
#: cell, an em dash, or prose — is false. DECLARED, never detected: see ``_parse_caption_budget``.
_MESSAGE_TRUE = frozenset({"yes", "y", "true", "x", "✓", "✔"})

#: The first number in a duration cell, ignoring bold markers, a trailing unit, or an em-dash
#: placeholder. The real tables in the tree write ``**2.75**`` and ``2.75s`` and ``—``.
_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)")


def _parse_caption_budget(text: str) -> tuple[Fraction, Fraction, int, str | None]:
    """Sum the ``## Caption budget`` table into ``(message_seconds, total_seconds, rows, note)``.

    Columns are located BY NAME, not by position: the tables authored so far are
    ``| Beat | Duration | Caption words | w/s |`` and a story script adds a ``message`` column, so
    a fixed header would fail every script written before this and a positional index would break
    the first time someone adds a column. A table with no ``message`` column is not an error — it
    is a script that declared no message beat, which the caller reads as the mirror case.

    ``note`` is a human-readable reason the table could not be read, or ``None`` on success. This
    function never raises on malformed prose: a story script whose table is unreadable earns a
    warning naming that, not a traceback in a quality gate.

    Arithmetic is exact (``Fraction`` over the decimal text). A float sum of quarter-seconds
    compared against 0.10 decides the boundary case by representation error rather than by the
    rule, and 6/60 is exactly at the ceiling rather than over it.
    """
    section = re.search(
        r"^##+\s+Caption budget\s*$\n(.*?)(?=^##+\s|\Z)", text, re.MULTILINE | re.DOTALL
    )
    if section is None:
        return Fraction(0), Fraction(0), 0, "no '## Caption budget' section"

    rows = [ln.strip() for ln in section.group(1).splitlines() if ln.strip().startswith("|")]
    if not rows:
        return Fraction(0), Fraction(0), 0, "the '## Caption budget' section holds no table"

    def cells(line: str) -> list[str]:
        return [c.strip() for c in line.strip().strip("|").split("|")]

    header = [c.lower().replace("*", "").strip() for c in cells(rows[0])]
    duration_at = next((i for i, c in enumerate(header) if c.startswith("duration")), None)
    if duration_at is None:
        return Fraction(0), Fraction(0), 0, "the caption-budget table names no duration column"
    message_at = next((i for i, c in enumerate(header) if c == "message"), None)

    message_s = total_s = Fraction(0)
    counted = 0
    for line in rows[1:]:
        cols = cells(line)
        if len(cols) <= duration_at or set("".join(cols)) <= set("-: "):
            continue  # the |---|---| separator, or a short row
        found = _DURATION_RE.search(cols[duration_at].replace("*", ""))
        if not found:
            continue  # an em-dash or narrative cell: no duration to add either way
        seconds = Fraction(found.group(1))
        total_s += seconds
        counted += 1
        if message_at is not None and len(cols) > message_at:
            flag = cols[message_at].lower().replace("*", "").strip()
            if flag in _MESSAGE_TRUE:
                message_s += seconds
    if counted == 0:
        return Fraction(0), Fraction(0), 0, "no caption-budget row carries a duration"
    return message_s, total_s, counted, None


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


def _find_scripts(content_root: Path, profile: str, item_id: str) -> list[Path]:
    """Every script whose front block names ``item_id`` as its ``source_item``, oldest first.

    Matches on the ``source_item`` field rather than a filename convention: the filename is
    ``<date>-<slug>.md`` and carries no item id, so globbing for the id finds nothing. Same
    class of bug as the ``plan_id``/``source_item`` mismatch fixed in
    :func:`_find_video_manifests` on 2026-08-17.

    **Returns every match, because more than one is normal.** A re-cut item keeps its id, so a
    superseded cut and the live one both carry it. This used to return the FIRST match of a
    sorted glob, which is the oldest filename — on 2026-09-08 an item with six cuts was scored
    against a script superseded a week earlier, and the report said `proceed: true` without ever
    naming the file it read. The caller takes the newest and reports both the path and the count.
    """
    scripts_dir = content_root / _safe_segment(profile, "profile") / "scripts"
    if not scripts_dir.is_dir():
        return []
    found: list[Path] = []
    for path in sorted(scripts_dir.glob("*.md")):
        text = _load_text(path)
        if text is None:
            continue
        if _parse_front_block(text).get("source_item") == item_id:
            found.append(path)
    return found


def _find_script(content_root: Path, profile: str, item_id: str) -> Path | None:
    """The NEWEST script carrying ``item_id``, or None. See :func:`_find_scripts` for why newest.

    Kept as the single-answer form for callers that only need a path. A caller that reports to a
    human should use :func:`_find_scripts` instead and say how many candidates there were — a
    silently-chosen one of six is how the wrong cut got scored.
    """
    found = _find_scripts(content_root, profile, item_id)
    return found[-1] if found else None


def _message_share_check(
    item: dict,
    front: dict[str, str],
    text: str,
    name: str,
    warnings: list[str],
    checks: dict[str, Any],
) -> None:
    """Warn when a story script's message occupies too much (or none) of its runtime.

    Scoped to a story item — one whose plan item carries ``brief.protagonist`` — and silent
    otherwise: on a non-story script this writes NO ``checks`` key at all, so the gate's output for
    every existing script is byte-identical to what it was.

    Warns, never blocks, in both directions. Over the ceiling is an ad wearing a story's shape;
    exactly zero is story-washing's mirror — a piece that spends its whole runtime on feeling and
    never says the thing it was made to say. The 0/n case reads as clean to anyone scanning for a
    high number, which is why it warns for its own reason rather than passing quietly.

    Which beats are message-bearing is **declared** on the caption-budget row, never detected.
    A detector that regexes for product terms under-matches and returns a plausible number, and a
    plausible wrong number in a gate is worse than no number: the next reader trusts it.
    """
    protagonist = ((item.get("brief") or {}).get("protagonist") or "").strip()
    if not protagonist:
        return

    message_s, total_s, rows, note = _parse_caption_budget(text)
    if note is not None:
        checks["message_share_declared"] = False
        warnings.append(
            f"{name}: this is a story item (brief.protagonist is set) but its message_share "
            f"cannot be read — {note}. Add the per-beat table with a `message` column so the "
            "ratio is derived rather than asserted"
        )
        return

    checks["message_share_declared"] = True
    share = message_s / total_s if total_s else Fraction(0)
    within = share <= _MESSAGE_SHARE_CEILING and message_s > 0
    checks["message_share_within_ceiling"] = within

    if message_s == 0:
        warnings.append(
            f"{name}: message_share {message_s}/{total_s} — no beat is declared message-bearing. "
            "A story that never lands its message is story-washing's mirror; flag the beat that "
            "carries it with `message: yes`, or say why none does"
        )
    elif share > _MESSAGE_SHARE_CEILING:
        warnings.append(
            f"{name}: message_share {message_s}/{total_s} is above the {_MESSAGE_SHARE_CEILING} "
            f"ceiling across {rows} beat(s) — the message belongs in the last tenth, in one "
            "sentence. Cut a proof point rather than trimming each one"
        )

    declared = (front.get("message_share") or "").strip()
    if not declared:
        warnings.append(
            f"{name}: story script has no `message_share:` front-block line (expected "
            f"{message_s}/{total_s} from the caption budget). It must sit ABOVE the `# Title` "
            "line — the front-block parser stops at the first heading"
        )
        return
    match = _SHARE_RE.match(declared)
    if match is None:
        warnings.append(
            f"{name}: message_share {declared!r} is not '<message-bearing s>/<total s>'"
        )
        return
    stated_m, stated_t = Fraction(match.group("m")), Fraction(match.group("t"))
    stated = stated_m / stated_t if stated_t else Fraction(0)
    if stated != share:
        warnings.append(
            f"{name}: front block says message_share {declared}, the caption budget sums to "
            f"{message_s}/{total_s} — one of the two was edited without the other"
        )


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

    candidates = _find_scripts(content_root, profile, item_id)
    checks["script_found"] = bool(candidates)
    if not candidates:
        blocking.append(
            f"no script in content/{profile}/scripts/ carries source_item: {item_id} "
            "(video-script Step 2 writes this front-block field)"
        )
        return {"proceed": False, "blocking": blocking, "warnings": warnings, "checks": checks}

    script_path = candidates[-1]
    if len(candidates) > 1:
        warnings.append(
            f"{len(candidates)} scripts carry source_item: {item_id} — scored the newest, "
            f"{script_path.name}. The others are "
            + ", ".join(p.name for p in candidates[:-1])
            + ". Archive a superseded cut out of scripts/ so the live one is unambiguous."
        )

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

    # After the claims block on purpose: the four early returns above it (item missing, script
    # missing, claims_verified absent, claims_verified malformed) must keep firing first, so a new
    # warning can never mask an existing block.
    _message_share_check(item, front, text, script_path.name, warnings, checks)

    proceed = not blocking
    return {
        "proceed": proceed,
        "script": script_path.name,
        "blocking": blocking,
        "warnings": warnings,
        "checks": checks,
    }
