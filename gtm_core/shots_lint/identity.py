from __future__ import annotations

import re

from .. import render_engines


def _lint_presenter_engine(
    shot: dict,
    prefix: str,
    errors: list[str],
    *,
    disclosed: bool = False,
    live_action: bool = False,
) -> None:
    """A shot of a real person SPEAKING must resolve an engine that can actually do that.

    The check that makes the August 2026 failure unrepresentable, run before any spend. A talking
    head on a general image-to-video model drifts the face (identity cannot cross the image→video
    boundary) and cannot lip-sync (mouth motion comes from prose, not from the audio).

    ``disclosed`` comes from the shot list's top-level ``synthetic_disclosure`` and reaches the
    registry unchanged. It defaults to ``False``, so a shot list that never mentions disclosure is
    linted against the closed answer — the engine that can serve this role today is available for
    DISCLOSED renders only (gtm_core/render_engines.toml, [engines.heygen_avatar]).
    """
    if live_action:
        # The "engine" is a camera and a human. There is no synthetic render to gate.
        return
    if str(shot.get("role", "") or "").strip() != "presenter":
        return
    # Q5: High emotional load beats cannot use prompt-only synthetic avatars
    if str(shot.get("emotional_load", "") or "").strip().lower() == "high":
        errors.append(
            f"{prefix} has emotional_load='high' but specifies role='presenter'. "
            "High emotional load beats cannot use prompt-only synthetic avatars. Choose one of three routes: (1) real footage (primary, no Article 50 duty), (2) performance transfer from a driving clip, or (3) the faceless format (b-roll, VO, burned captions)."
        )
        return
    speaks = bool(str(shot.get("spoken", "") or "").strip())
    if not speaks:
        return
    try:
        render_engines.engine_for_shot_role("presenter", speaks=True, disclosed=disclosed)
    except render_engines.EngineError as exc:
        errors.append(f"{prefix} is a speaking presenter shot and no engine can serve it. {exc}")


def _lint_voice_grade(shot: dict, prefix: str, errors: list[str], *, voice_grade: str) -> None:
    """A shot with spoken lines may not ship on an INSTANT voice clone.

    An instant clone is produced from a short sample and the provider does not train a custom
    model for it — it makes an educated guess from prior training data. Flat, not-quite-right
    output is that grade's DOCUMENTED behaviour, not a tuning failure, and it is what the operator
    heard on 2026-08-19 ("voice doesn't really sound like me"). A professional clone fine-tunes a
    dedicated model on 30 min-3 h of clean audio.

    Fail-closed on an unrecorded grade: a voice whose provenance nobody wrote down is not evidence
    of a good one.
    """
    if not str(shot.get("spoken", "") or "").strip():
        return
    if voice_grade == "professional":
        return
    detail = (
        f"voice_grade={voice_grade!r}"
        if voice_grade
        else "no voice_grade recorded in the brand kit"
    )
    errors.append(
        f"{prefix} has a spoken line but {detail} — a shipped VO requires a PROFESSIONAL voice "
        "clone (30 min-3 h of clean audio). An instant clone does not train a custom model at "
        "all; it guesses from prior training data, which is why it does not sound like the "
        "speaker. Record the grade with: python -m gtm_core.brandkit --profile <p> "
        "--set identity.voice_grade --value professional --note '<when/how cloned>'"
    )


#: Orientation a look's native pixels imply, vs the orientation each delivery ratio needs.
_RATIO_ORIENTATION = {"9:16": "portrait", "4:5": "portrait", "1:1": "square", "16:9": "landscape"}


def _orientation_of(width: int, height: int) -> str:
    if width > height:
        return "landscape"
    if height > width:
        return "portrait"
    return "square"


def _lint_look_ratio(doc: dict, errors: list[str], warnings: list[str]) -> None:
    """A look whose native pixels contradict the delivery ratio cannot fill the frame.

    2026-08-28: a 608x1080 portrait avatar look was rendered for a 16:9 master, leaving 608px of
    1920 as real picture and 68% of the frame as a blurred copy of itself. Six native 1920x1080
    landscape looks existed in the same avatar group at identical cost. The shot list already
    records the look's source pixels, so this is arithmetic, not a judgement call."""
    ratios = doc.get("deliverable_ratios") or doc.get("ratios")
    bindings = doc.get("identity_bindings")
    if not isinstance(bindings, dict) or not isinstance(ratios, list) or not ratios:
        return
    for name, binding in bindings.items():
        if not isinstance(binding, dict):
            continue
        px = str(binding.get("source_px", "") or "")
        m = re.search(r"(\d+)\s*[x×]\s*(\d+)", px)
        if not m:
            continue
        have = _orientation_of(int(m.group(1)), int(m.group(2)))
        for ratio in ratios:
            need = _RATIO_ORIENTATION.get(str(ratio))
            if need and need != have and "square" not in (need, have):
                errors.append(
                    f"identity_bindings.{name} source is {m.group(1)}x{m.group(2)} ({have}) but "
                    f"{ratio} needs {need}. Rendering it anyway fills the frame with a scaled "
                    f"copy of itself — a portrait look in a 16:9 frame leaves ~32% real picture. "
                    "Pick a look whose native orientation matches (list_avatar_looks reports "
                    "`preferred_orientation` and `image_width`/`image_height` per look), or "
                    "compose that ratio as a deliberate layout and say so here."
                )


#: The reserved key inside ``identity_bindings`` that is NOT a role — free prose about the
#: casting/identity decisions as a whole. It exists so a note does not have to masquerade as a
#: binding, which is exactly what a bare string under a role key used to be.
_BINDINGS_NOTES_KEY = "notes"

#: Fields that NAME what a binding resolved to. A binding object must carry at least one, or it
#: records a role and identifies nothing. Pinned to schemas/shots.schema.json's own declared
#: properties by a contract test, in both directions — that pin is what stops the schema and this
#: gate drifting apart again, which is how the element path came to be unrepresentable in one and
#: unchecked in the other.
_IDENTITY_HANDLE_FIELDS = frozenset(
    {"soul_id", "element_id", "voice_id", "look_id", "avatar_look_id"}
)


def _lint_identity_bindings(doc: dict, errors: list[str], warnings: list[str]) -> None:
    """Every identity binding is an OBJECT keyed by role, and names a handle it resolved to.

    2026-09-03: ``video-script`` wrote ``identity_bindings: {"element": "<uuid>"}`` — a bare
    Higgsfield Reference Element id, with the handle KIND as the key, where every other shot list
    keys by ROLE ("henry", "caller_a_john") and stores an object. ``schemas/shots.schema.json``
    declared an object and had no field for an element id at all, so jsonschema refused the file
    while ``shots_lint`` reported ``ok: true`` — the schema and the enforced gate disagreed, and
    the schema was dead weight for the element path. Nothing read the id either way.

    Two things are lost by the bare form, which is why it is refused rather than accommodated.
    A string cannot carry ``source_px``, so :func:`_lint_look_ratio` — the one check that is
    arithmetic rather than judgement, and the reason this record exists — can never run on it;
    before this rule, it silently skipped any non-object binding, so the bare form read as clean.
    And overloading the key from role to handle-kind means two bindings of the same kind (a Soul
    and an element for the same shot, two elements) cannot both be written down.

    The SHAPE rules here are errors; a binding that names no handle is only a warning, because
    a subject composited locally from stills legitimately has no provider-side id to record.
    """
    bindings = doc.get("identity_bindings")
    if bindings is None:
        return
    if not isinstance(bindings, dict):
        errors.append(
            "identity_bindings must be an object mapping each role/part name to a binding "
            f"object, but it is {type(bindings).__name__}."
        )
        return

    for name, binding in bindings.items():
        if name == _BINDINGS_NOTES_KEY:
            if not isinstance(binding, dict) or not all(
                isinstance(v, str) for v in binding.values()
            ):
                errors.append(
                    "identity_bindings.notes is the reserved annotation key and must be an "
                    "object of short-name → prose string. Per-binding notes belong inside that "
                    "binding's own object."
                )
            continue

        if not isinstance(binding, dict):
            errors.append(
                f"identity_bindings.{name} is not a binding object (got "
                f"{type(binding).__name__}). Key by the ROLE the identity plays and store an "
                'object naming the handle, e.g. {"bao": {"element_id": "<id>", "engine": '
                '"<engine>", "source_px": "<WxH orientation>"}}. A bare id string cannot carry '
                "source_px, so the pre-spend ratio check can never run on it, and it overloads "
                "the key to mean the handle KIND rather than the role. If this is a decision "
                "note rather than a binding, move it under the reserved `notes` key."
            )
            continue

        if not (_IDENTITY_HANDLE_FIELDS & binding.keys()):
            # Advisory, not a block. The SHAPE rule above is unambiguous — a bare string is
            # always wrong. This one is not: a subject composited locally from stills has no
            # provider-side handle to record, and that is legitimate. What it costs is
            # reproducibility, so it is surfaced rather than refused.
            warnings.append(
                f"identity_bindings.{name} names no identity handle — none of "
                f"{sorted(_IDENTITY_HANDLE_FIELDS)} is present. Nothing here pins what this "
                "role resolved to, so the binding is not reproducible from the file alone."
            )


def _lint_synthetic_disclosure(
    doc: dict,
    errors: list[str],
    *,
    live_action: bool,
    disclosure_line: str | None,
) -> bool:
    """A declared disclosure must be the tenant's actual line, not a handle kind.

    Returns whether the list is DISCLOSED, which is what opens the presenter engine
    (:func:`_lint_presenter_engine`); an omitted key linted as ``False`` is the closed default.

    ``synthetic_disclosure`` declares the EU AI Act Art. 50 line the finished render will carry
    (``BRAND.toml`` ``[disclosure].line``). The binding checks are downstream —
    ``gtm_core.video_finish`` burns the text it is handed, and ``agent/publish.py``'s
    ``validate_disclosure`` refuses a post that does not carry a configured line — but both run
    after the render is paid for, so a list declaring something that is not a disclosure line at
    all passed the free pre-spend gate. On 2026-09-03 one shipped reading ``"element"``: a
    Higgsfield identity-handle KIND, the same token confusion that malformed ``identity_bindings``
    in the same file. The film was disclosed correctly because a human passed the real line to
    ``video-finish`` by hand; the record claimed something else.

    ``disclosure_line`` is the resolved kit line and carries the same three-state contract as
    ``voice_grade``/``voice_id``: ``None`` means "not supplied by this caller" and skips the match
    (a list linted with no kit in hand must not fail on a fact the caller never had), while ``""``
    means the kit WAS read and configures no line — which fails closed, exactly as
    ``validate_disclosure`` does at the gate, because a tenant who never set one has not opted out
    of the duty.
    """
    declared_raw = doc.get("synthetic_disclosure")
    if live_action and str(declared_raw or "").strip():
        errors.append(
            "synthetic_disclosure is declared on a live_action shot list. Nothing here is "
            "synthesised — the footage is a real camera pointed at a real person — so the EU AI "
            "Act Art. 50 duty does not attach, and claiming it anyway is a FALSE statement about "
            "provenance rather than a cautious one. Remove the key, or set "
            'capture_mode = "rendered" if this list really does describe a synthetic render.'
        )
        return False
    if "synthetic_disclosure" not in doc:
        return False
    if not isinstance(declared_raw, str) or not declared_raw.strip():
        errors.append(
            "synthetic_disclosure is present but empty — declare the disclosure line the "
            "finished render will carry, or omit the key entirely. A present-but-blank "
            "declaration reads as an attempt to open the presenter engine without accepting "
            "the Art. 50 duty that opens it."
        )
        return False

    declared = declared_raw.strip()
    if disclosure_line is None:
        return True
    if not disclosure_line.strip():
        errors.append(
            f"synthetic_disclosure declares {declared!r} but the brand kit configures no "
            "[disclosure].line. A tenant who never set one has not opted out of the EU AI Act "
            "Art. 50 duty — they have not configured how to meet it — so this fails closed here "
            "exactly as agent/publish.py:validate_disclosure would at the gate, but before the "
            "render is paid for. The line lives in the profile's BRAND.toml [disclosure] table, "
            "which is set at onboarding (setup / profile-onboard) or by the operator — there is "
            "no CLI verb for it yet, and it is not something to edit mid-run. Stop and report."
        )
        return True
    expected = disclosure_line.strip()
    if declared != expected:
        errors.append(
            f"synthetic_disclosure declares {declared!r}, which is not the tenant's configured "
            f"[disclosure].line {expected!r}. This field is the LINE the finished render will "
            "carry, verbatim — not a handle kind, not a shorthand, not a paraphrase. Copy it "
            "from: python -m gtm_core.brandkit --profile <p> [--product <s>] --key "
            "disclosure.line"
        )
    return True
