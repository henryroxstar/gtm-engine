"""Brand kit reader — company kit + product kit, merged product-over-company.

The brand kit (``BRAND.toml``) turns "consistent branding" from a discipline problem into a
lookup: palette, typography, asset paths, imagery style, caption style, the disclosure string,
and the render-identity handles (``soul_id`` / ``reference_element_ids`` / ``voice_id`` /
``restyle_preset_id`` / ``consent_note``) all live in tenant data, so a render is on-brand by
config rather than by remembering to prompt for it.

WHY THIS MODULE EXISTS
----------------------
:func:`gtm_core.paths.resolve_knowledge_file` returns exactly ONE path — the product file or
the company file, never both. That is correct for prose knowledge (a product's ICP *replaces*
the company's), but wrong for a brand kit: a product that overrides one palette key would
silently drop every other key, so product kits have to be self-contained and shared keys get
duplicated by hand. That duplication is precisely the drift failure the documentation-map rule
exists to prevent.

So this module calls the resolver TWICE — once profile-wide, once product-bound — and merges.
It builds no paths of its own: every segment still goes through ``_safe_segment`` inside the
resolver, so this adds NO new traversal surface (CLAUDE.md tenant boundary).

MERGE SEMANTICS
---------------
Recursive per-key merge, product wins. A product kit carries only its deltas::

    company [palette] {canvas="#0E0E0E", primary="#E8E8E8", rule="#2A2A2A"}
    product [palette] {primary="#A8512F"}
    merged  [palette] {canvas="#0E0E0E", primary="#A8512F", rule="#2A2A2A"}

Note this differs from whole-file override: ``primary`` is replaced, ``canvas`` and ``rule``
survive. Lists are replaced wholesale, not concatenated — a product that declares
``fallback_stack`` means *that* stack, not the company's plus its own.

Both files are optional. Missing company kit → the product kit alone; missing product kit →
the company kit alone; neither → ``{}``. A profile with no brand kit at all is not an error,
it just has nothing to look up.

CLI (skills are markdown and cannot import Python, so they shell out — same pattern as
``gtm_core.resolve_knowledge``)::

    python -m gtm_core.brandkit --profile P [--product S]                # merged kit as JSON
    python -m gtm_core.brandkit --profile P --product S --key palette.primary   # one value, raw
    python -m gtm_core.brandkit --profile P [--product S] \\
        --set identity.soul_id --value chr_abc123 [--note "…"] [--create]       # WRITE one identity key

IDENTITY WRITES (§5.6 — the ``identity-kit`` skill)
----------------------------------------------------
``[identity]`` holds the opaque handles this module ALSO writes — the authoritative list is
:data:`_WRITABLE_IDENTITY_KEYS` (it has grown four times since this paragraph was written, so
read it there rather than from a count here). Every one is a plain string except
``reference_element_ids``, which is a list of strings. ``voice_engine`` selects which model/engine
renders ``voice_id`` — either a ``variant`` of Higgsfield's own ``text2speech_v2`` model, or (for
``"qwen_audio"``) an entirely different TTS model chosen specifically because it accepts a
per-call emotional-direction ``instruction`` the others don't — empty/absent means "use the
provider's default"; a non-default value is restricted to a fixed enum (see
``_VOICE_ENGINE_VALUES``) so a typo can't silently fall through to a provider error at render
time. Every other section of a brand kit is read-only through this module — writes are
scoped to exactly this one table, on purpose: ``identity-kit`` is the only skill in the
onboarding family allowed to touch ``profiles/<active>/``, and it does so ONLY through
:func:`set_identity_value` (never an ``Edit``/``Write`` tool on the TOML directly), which keeps
``_safe_segment`` in the loop via :func:`brand_kit_paths` exactly like every read here, and
verifies the write round-trips before it lands (a failed verify leaves the file untouched).
The write is a targeted TEXT edit of the ``[identity]`` table only — every other section,
comment, and key ordering in the file survives byte-for-byte.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .paths import PathConfig, _safe_segment, resolve_knowledge_file

#: The brand kit's bare filename. Flat by necessity — ``_safe_segment`` rejects any resolved
#: filename containing "/", which is the tenant traversal guard. Never make this a subpath.
BRAND_FILENAME = "BRAND.toml"

#: The ONLY ``[identity]`` subkeys this module will write. Bare names (no ``identity.``
#: prefix) — the CLI accepts the dotted form and strips it before checking membership.
_WRITABLE_IDENTITY_KEYS = frozenset(
    {
        "soul_id",
        "voice_id",
        "restyle_preset_id",
        "consent_note",
        "reference_element_ids",
        "voice_engine",
        "voice_grade",
        "heygen_avatar_id",
        # A SECOND voice handle, deliberately not folded into `voice_id`.
        #
        # `voice_id` names the Higgsfield voice element; this names the HeyGen clone. They are
        # different providers, different clone grades, and — the reason this is two keys rather
        # than one — `voice_grade` gates whether a voice may carry a shipped VO. With a single
        # slot, recording "professional" for the HeyGen clone would also mark the Higgsfield
        # instant clone as cleared to ship, which is the exact inversion of the truth the gate
        # exists to prevent. Added 2026-08-20 when a professional HeyGen clone made the
        # collision real.
        "heygen_voice_id",
        "heygen_voice_grade",
        # The operator-approved avatar LOOK, keyed by orientation. TWO keys, not one, and the
        # split is the point rather than an accident of naming.
        #
        # `heygen_avatar_id` says WHOSE face renders. Neither of these says that — they say which
        # of that avatar group's looks the operator actually approves of, which is a question
        # about wardrobe, framing and how they come across, and it had no home at all until
        # 2026-08-29. Without one, a look was picked by optimising orientation and resolution,
        # and a verification render shipped on the pinned digital twin while 40+ photo-avatar
        # looks sat unexamined in the same group at identical cost.
        #
        # WHY PER ORIENTATION: no single look serves both a 16:9 master and a 9:16 cut. A
        # 608x1080 portrait look rendered landscape fills 608 of 1920 columns and pads the rest;
        # that shipped on 2026-08-27. `video-avatar`'s own dual-ratio rule already says the two
        # are separate decisions, so the storage says it too — a single key would force exactly
        # the padding the rule forbids, and a cross-orientation fallback would reintroduce it
        # the moment only one orientation had been approved.
        #
        # WHAT THIS IS NOT: a default that applies itself. The value is a PROPOSAL the router
        # asks about at routing time, every run (`gtm_core.video_preflight.look_proposals`).
        # Storage exists to make the ask CHEAP — a one-line confirm naming the look — not to
        # skip it. The 2026-08-29 render was wrong because nobody was asked, so a stored look
        # that applied itself would reproduce that failure with a config file in front of it.
        # An empty value is therefore never a blocker; it changes the ask from a confirm to a
        # pick, and `LANE_REQUIREMENTS` deliberately lists neither key as a lane precondition.
        "heygen_look_landscape",
        "heygen_look_portrait",
    }
)
#: reference_element_ids is a list of strings; every other writable key is a plain string.
_LIST_VALUED_KEYS = frozenset({"reference_element_ids"})
#: Allowed values for identity.voice_engine — "" means "use the provider's default engine".
#: The first seven are Higgsfield's own engines: seed_audio (the default model) plus
#: text2speech_v2's five `variant` options. `qwen_audio` is a DIFFERENT model
#: (`qwen_audio_tts`, Alibaba) added 2026-08-19 because it is the only engine in this set that
#: exposes a real per-call `instruction` param for emotional/style direction — none of the other
#: six accept anything beyond voice_type + voice_id. Verified live: the same cloned voice
#: element (voice_type="element") that renders through text2speech_v2 also carries
#: `supported_models: [..., "qwen_audio"]` in its own metadata, so switching engines does not
#: require a second voice clone. A value outside this set is refused at write time rather than
#: surfacing as a provider error mid-render.
_VOICE_ENGINE_VALUES = frozenset(
    {
        "",
        "seed_audio",
        "elevenlabs",
        "minimax",
        "seed_speech",
        "vibe_voice",
        "cozy_voice",
        "qwen_audio",
    }
)

#: Allowed values for identity.voice_grade — how the cloned voice was produced, which is
#: ORTHOGONAL to ``voice_engine`` (which engine renders it). Do not conflate them: the same
#: voice_id can be instant-grade and render through ElevenLabs.
#:
#: "instant"      — cloned from a short sample (<2 min). The provider does NOT train a custom
#:                  model for this; it makes an educated guess from prior training data. Flat,
#:                  slightly-wrong output is the DOCUMENTED behaviour of this grade, not a
#:                  tuning failure — which is what the operator heard on 2026-08-19 ("voice
#:                  doesn't really sound like me").
#: "professional" — a dedicated model fine-tuned on 30 min–3 h of clean audio. This is the only
#:                  grade allowed to carry a shipped asset's VO (enforced by
#:                  gtm_core.shots_lint._lint_voice_grade).
#: ""             — unknown/unrecorded. Treated as NOT professional, fail-closed: a voice whose
#:                  provenance nobody wrote down is not evidence of a good one.
_VOICE_GRADE_VALUES = frozenset({"", "instant", "professional"})

_IDENTITY_HEADER_RE = re.compile(r"^\[identity\][ \t]*$", re.MULTILINE)
_SECTION_HEADER_RE = re.compile(r"^\[[^\]]+\][ \t]*$", re.MULTILINE)
#: Control characters (incl. newline) — an id/note is a single line by definition; rejecting
#: these keeps a value from smuggling a fake TOML key/section into the write.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")


def _load_toml(path: Path) -> dict[str, Any]:
    """Parse ``path``, or return ``{}`` if it does not exist."""
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def merge_kits(company: dict[str, Any], product: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``product`` over ``company``; product wins on every conflict.

    Nested tables merge key-by-key. Any non-table value (including lists) is replaced whole.
    Neither input is mutated.
    """
    merged = dict(company)
    for key, value in product.items():
        base = merged.get(key)
        if isinstance(base, dict) and isinstance(value, dict):
            merged[key] = merge_kits(base, value)
        else:
            merged[key] = value
    return merged


def brand_kit_paths(
    profiles_root: Path, profile: str, product: str | None = None
) -> tuple[Path, Path | None]:
    """Return ``(company_path, product_path)`` for the kit, product path ``None`` when absent.

    Delegates both lookups to :func:`resolve_knowledge_file`, which owns the traversal guard.
    ``product_path`` is ``None`` when no product was requested, or when the resolver fell back
    to the company file because the product kit does not exist — so the caller never merges a
    file with itself.
    """
    company = resolve_knowledge_file(profiles_root, profile, BRAND_FILENAME)
    if product is None:
        return company, None
    candidate = resolve_knowledge_file(profiles_root, profile, BRAND_FILENAME, product)
    return company, (None if candidate == company else candidate)


def load_brand_kit(profiles_root: Path, profile: str, product: str | None = None) -> dict[str, Any]:
    """The merged brand kit for ``profile`` (optionally product-bound). ``{}`` if none exists.

    Raises ``ValueError`` for an unsafe profile/product segment (from the resolver) and
    ``tomllib.TOMLDecodeError`` for a malformed kit — a broken kit is a hard failure, not a
    silently-empty one, because an empty kit renders off-brand rather than not at all.
    """
    company_path, product_path = brand_kit_paths(profiles_root, profile, product)
    company = _load_toml(company_path)
    if product_path is None:
        return company
    return merge_kits(company, _load_toml(product_path))


def lookup(kit: dict[str, Any], dotted: str) -> Any:
    """Fetch a dotted key (``"palette.primary"``) from ``kit``. Raises ``KeyError`` if absent."""
    node: Any = kit
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(dotted)
        node = node[part]
    return node


def _toml_scalar(value: Any) -> str:
    """Render a Python value as a TOML literal for exactly the writable identity types
    (a string, or a list of strings). Anything else is a programming error, not user input —
    every caller of :func:`set_identity_value` already validated the shape before this runs."""
    if isinstance(value, list):
        return "[" + ", ".join(_toml_scalar(v) for v in value) + "]"
    if isinstance(value, str):
        # Basic-string escaping (backslash, double-quote); control chars are rejected by the
        # caller before this is ever reached, so no further escaping is needed for a single line.
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    raise TypeError(f"unsupported identity value type: {type(value)!r}")  # pragma: no cover


def set_identity_value(
    path: Path,
    key: str,
    value: str | list[str],
    *,
    note: str | None = None,
    create: bool = False,
    now: str | None = None,
) -> None:
    """Write ``identity.<key>`` into the ``[identity]`` table of the kit at ``path``.

    A targeted TEXT edit, not a full TOML rewrite: locates (or creates) the ``[identity]``
    section and replaces just the one ``key = ...`` line inside it (appending it if absent),
    leaving every other section, key, and comment in the file untouched. The new line carries
    a dated trailing comment (``# identity-kit YYYY-MM-DD[: note]``) — the write IS the audit
    trail; an id that lives only in a chat transcript is an id the next session loses.

    Verifies before writing: the candidate text is parsed with ``tomllib`` and the target key
    must read back exactly ``value``. On any failure (bad TOML, wrong value, disallowed key)
    this raises and the file on disk is **never touched** — the write is all-or-nothing.

    Raises ``ValueError`` for: an unsafe/malformed ``key`` (must be ``identity.<writable>``),
    a value containing control characters, or a post-write verify mismatch. Raises
    ``FileNotFoundError`` if ``path`` does not exist and ``create`` is not set.
    """
    if not key.startswith("identity.") or key.count(".") != 1:
        raise ValueError(f"key must be 'identity.<subkey>', got {key!r}")
    subkey = key.removeprefix("identity.")
    if subkey not in _WRITABLE_IDENTITY_KEYS:
        raise ValueError(
            f"identity.{subkey} is not writable — allowed: "
            f"{sorted('identity.' + k for k in _WRITABLE_IDENTITY_KEYS)}"
        )
    is_list_key = subkey in _LIST_VALUED_KEYS
    if is_list_key:
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError(f"identity.{subkey} takes a list of strings")
        bad = [v for v in value if _CONTROL_CHAR_RE.search(v)]
    else:
        if not isinstance(value, str):
            raise ValueError(f"identity.{subkey} takes a string")
        bad = [value] if _CONTROL_CHAR_RE.search(value) else []
    if bad:
        raise ValueError(f"identity.{subkey} value contains control characters — refused")
    if subkey == "voice_engine" and value not in _VOICE_ENGINE_VALUES:
        raise ValueError(
            f"identity.voice_engine must be one of {sorted(_VOICE_ENGINE_VALUES)!r}, got {value!r}"
        )
    if subkey in ("voice_grade", "heygen_voice_grade") and value not in _VOICE_GRADE_VALUES:
        raise ValueError(
            f"identity.{subkey} must be one of {sorted(_VOICE_GRADE_VALUES)}, got {value!r}. "
            "This records HOW the voice was cloned, not which engine renders it — the two are "
            "orthogonal and must not be conflated."
        )
    if note is not None and _CONTROL_CHAR_RE.search(note):
        raise ValueError("--note contains control characters — refused")

    if not path.is_file():
        if not create:
            raise FileNotFoundError(f"{path} does not exist (pass create=True to make one)")
        text = '[meta]\nsource = "identity-kit"\n\n[identity]\n'
    else:
        text = path.read_text(encoding="utf-8")

    date = now or datetime.now(UTC).strftime("%Y-%m-%d")
    comment = f"  # identity-kit {date}" + (f": {note}" if note else "")
    new_line = f"{subkey} = {_toml_scalar(value)}{comment}"

    header_match = _IDENTITY_HEADER_RE.search(text)
    if header_match is None:
        # No [identity] table at all — append a fresh one at EOF.
        sep = "" if text.endswith("\n") or not text else "\n"
        new_text = f"{text}{sep}\n[identity]\n{new_line}\n"
    else:
        body_start = header_match.end() + 1  # skip the header line's own newline
        next_header = _SECTION_HEADER_RE.search(text, pos=body_start)
        body_end = next_header.start() if next_header else len(text)
        body = text[body_start:body_end]

        key_line_re = re.compile(rf"^[ \t]*{re.escape(subkey)}[ \t]*=.*$", re.MULTILINE)
        existing = key_line_re.search(body)
        if existing:
            new_body = body[: existing.start()] + new_line + body[existing.end() :]
        else:
            sep = "" if body.endswith("\n") or not body else "\n"
            new_body = f"{body}{sep}{new_line}\n"
        new_text = text[:body_start] + new_body + text[body_end:]

    # Verify BEFORE writing — a bad write must leave the file exactly as it was.
    try:
        parsed = tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"write would produce invalid TOML: {exc}") from exc
    if lookup(parsed, key) != value:
        raise ValueError(f"post-write verify failed for {key} — file left untouched")

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(new_text, encoding="utf-8")
    os.replace(tmp, path)


def identity_write_target(profiles_root: Path, profile: str, product: str | None) -> Path:
    """The path :func:`set_identity_value` should target: ``products/<product>/BRAND.toml``
    when ``product`` is given, else ``knowledge/BRAND.toml``.

    Mirrors :func:`gtm_core.paths.resolve_knowledge_file`'s own segment construction (same
    ``_safe_segment`` guards, same layout) but WITHOUT its existence-based fallback — a write
    must land at the path the caller asked for, not wherever a file already happens to exist
    (existence-based fallback is a READ concern: it lets a profile with no product override
    still resolve; a WRITE has to be able to CREATE that override in the first place).
    """
    base = profiles_root / _safe_segment(profile, "profile")
    if product is not None:
        return base / "products" / _safe_segment(product, "product") / BRAND_FILENAME
    return base / "knowledge" / BRAND_FILENAME


def _run_set(args: argparse.Namespace, profiles_root: Path) -> int:
    """WRITE-mode dispatch for ``--set``. Exit codes match the read path's convention:
    2 = malformed input (unsafe segment, bad key/value, verify failure); 3 = target file
    absent and ``--create`` wasn't passed."""
    if args.value is None:
        print("[brandkit] --set requires --value", file=sys.stderr)
        return 2

    subkey = args.set_key.removeprefix("identity.")
    if subkey in _LIST_VALUED_KEYS:
        try:
            value: str | list[str] = json.loads(args.value)
        except json.JSONDecodeError as exc:
            print(
                f"[brandkit] --value for {args.set_key} must be a JSON array: {exc}",
                file=sys.stderr,
            )
            return 2
    else:
        value = args.value

    try:
        target = identity_write_target(profiles_root, args.profile, args.product)
    except ValueError as exc:
        print(f"[brandkit] {exc}", file=sys.stderr)
        return 2

    try:
        set_identity_value(target, args.set_key, value, note=args.note, create=args.create)
    except FileNotFoundError as exc:
        print(f"[brandkit] {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"[brandkit] {exc}", file=sys.stderr)
        return 2

    print(f"wrote {args.set_key} to {target}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.brandkit",
        description="Resolve a profile's brand kit, merging the product kit over the company kit.",
    )
    parser.add_argument("--profile", required=True, help="active profile slug")
    parser.add_argument(
        "--product", default=None, help="active product slug; omit for company-level only"
    )
    parser.add_argument(
        "--key",
        default=None,
        help="dotted key to print raw instead of the whole kit, e.g. palette.primary",
    )
    parser.add_argument(
        "--profiles-root",
        default=None,
        help="override profiles root (defaults to GTM_PROFILES_ROOT / repo)",
    )
    parser.add_argument(
        "--set",
        dest="set_key",
        default=None,
        metavar="identity.<key>",
        # Derived, never restated: the hand-written list here named six keys while the frozenset
        # held nine, so `--help` advertised a surface that had grown past it twice over.
        help="WRITE mode: identity subkey to set — one of "
        + ", ".join(sorted(_WRITABLE_IDENTITY_KEYS))
        + ". Requires --value.",
    )
    parser.add_argument(
        "--value",
        default=None,
        help="value for --set — a plain string, or (for reference_element_ids only) a JSON "
        'array of strings, e.g. \'["el_1","el_2"]\'',
    )
    parser.add_argument("--note", default=None, help="dated audit comment recorded with the write")
    parser.add_argument(
        "--create", action="store_true", help="create the kit file if it does not exist yet"
    )
    args = parser.parse_args(argv)

    profiles_root = (
        Path(args.profiles_root).expanduser().resolve()
        if args.profiles_root
        else PathConfig.from_env().profiles_root
    )

    if args.set_key is not None:
        return _run_set(args, profiles_root)

    try:
        kit = load_brand_kit(profiles_root, args.profile, args.product)
    except ValueError as exc:  # unsafe segment — same exit code as resolve_knowledge
        print(f"[brandkit] {exc}", file=sys.stderr)
        return 2
    except tomllib.TOMLDecodeError as exc:
        print(f"[brandkit] malformed BRAND.toml: {exc}", file=sys.stderr)
        return 2

    if not kit:
        print(f"[brandkit] no BRAND.toml for profile {args.profile!r}", file=sys.stderr)
        return 3

    if args.key is None:
        print(json.dumps(kit, indent=2, ensure_ascii=False))
        return 0

    try:
        value = lookup(kit, args.key)
    except KeyError:
        print(f"[brandkit] key not in kit: {args.key}", file=sys.stderr)
        return 3

    # Scalars print raw so a skill can interpolate directly; tables/lists print as JSON.
    print(json.dumps(value, ensure_ascii=False) if isinstance(value, dict | list) else value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
