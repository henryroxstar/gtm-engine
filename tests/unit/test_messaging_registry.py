"""The outbound fact registry refuses a tenant it cannot trust, and says why.

Every refusal test here carries its negative control **in the same test body**: the same
fixture minus the defect loads cleanly. A refusal test with no control cannot tell you
whether the loader refused the defect or refused the fixture (§R18 — a check that cannot
discriminate is not a check).

All tenant data in these fixtures is fictional and came from ``gtm_core.fictionalize``
(§R9). Real companies and people live only under ``profiles/``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core.messaging import registry

# --- fixture tenant -------------------------------------------------------------------
#
# One profile slug, one seat, one premise, one claim, one proof, one angle: the smallest
# registry that exercises every cross-reference. Each test copies it and breaks exactly
# one thing, so the message it asserts on can only have come from that one defect.

_PROFILE = "marlowe"

_VOCABULARY_TOML = """\
default_persona = "ciso"
segments = ["enterprise", "unspecified"]

[[persona]]
name = "ciso"
cues = ["ciso", "head of security"]

[[seat]]
name = "security"
personas = ["ciso"]
stakes = ["breach", "audit"]
"""

_PREMISE_TOML = """\
schema = 1

[premise.multi-framework]
claim = "the reader runs agents on more than one framework"
min_distinct = 2
terms = ["langgraph", "autogen"]
"""


def _claim(**over) -> dict:
    block = {
        "id": "audit-signed",
        "group": "observability",
        "status": "verified",
        "statement": "Each audit entry is signed.",
        "source": "knowledge/references/ledger-notes.md:12",
        "do_not_say": ["tamper-proof"],
    }
    block.update(over)
    return {k: v for k, v in block.items() if v is not None}


def _proof(**over) -> dict:
    block = {
        "id": "regulator-note-2-1-2",
        "kind": "anchor",
        "market": "Singapore",
        "figure_kind": "none",
        "statement": "A verifiable identity per agent, tied to an accountable human.",
        "source": "knowledge/guidance/regulator-notes.md:4",
        "binding": False,
    }
    block.update(over)
    return {k: v for k, v in block.items() if v is not None}


def _angle(**over) -> dict:
    block = {
        "id": "ciso-multi-framework-audit",
        "seat": "security",
        "premise": "multi-framework",
        "claim": "audit-signed",
        "proof": "regulator-note-2-1-2",
        "opener_kind": "account-event",
        "stakes": "an audit that cannot be reconstructed",
        "summary": "One chain of custody per agent action.",
        "status": "draft",
        "segments": ["enterprise"],
    }
    block.update(over)
    return {k: v for k, v in block.items() if v is not None}


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(json.dumps(v) for v in value) + "]"
    return json.dumps(value)


def _render(table: str, blocks: list[dict]) -> str:
    out = []
    for block in blocks:
        out.append(f"[[{table}]]")
        out.extend(f"{key} = {_toml_value(value)}" for key, value in block.items())
        out.append("")
    return "\n".join(out)


def _write(
    tmp_path: Path,
    name: str,
    *,
    claims: list[dict] | None = None,
    proof: list[dict] | None = None,
    angles: list[dict] | None = None,
    omit: tuple[str, ...] = (),
) -> Path:
    """Build a tmp ``profiles/`` tree and return its root.

    ``name`` keeps each tree at its own path so a test can hold a good and a bad tenant
    at once without either one's cached resolution answering for the other.
    """
    root = tmp_path / name
    knowledge = root / _PROFILE / "knowledge"
    knowledge.mkdir(parents=True)
    files = {
        "claims.toml": _render("claim", claims if claims is not None else [_claim()]),
        "proof.toml": _render("proof", proof if proof is not None else [_proof()]),
        "angles.toml": _render("angle", angles if angles is not None else [_angle()]),
        "role-vocabulary.toml": _VOCABULARY_TOML,
        "premise-vocab.toml": _PREMISE_TOML,
    }
    for filename, text in files.items():
        if filename in omit:
            continue
        (knowledge / filename).write_text(text, encoding="utf-8")
    return root


def _refusal(root: Path) -> str:
    with pytest.raises(registry.RegistryError) as exc:
        registry.load(_PROFILE, profiles_root=root)
    return str(exc.value)


# --- refusals -------------------------------------------------------------------------


def test_claim_verified_without_source_refuses(tmp_path):
    """A `verified` status is a promise a reader can check; with no source there is nothing to check."""
    good = _write(tmp_path, "good", claims=[_claim()])
    registry.load(_PROFILE, profiles_root=good)  # negative control: the same fixture loads

    bad = _write(tmp_path, "bad", claims=[_claim(source=None)])
    message = _refusal(bad)
    assert "claims.toml" in message
    assert "audit-signed" in message
    assert "source" in message
    assert len(message.splitlines()) == 1, "one line per defect, never a traceback"


@pytest.mark.parametrize(
    ("table", "field", "ok", "bad"),
    [
        ("claim", "status", "design-target", "probably"),
        ("proof", "kind", "stat", "vibe"),
        ("angle", "status", "live", "published"),
        ("angle", "opener_kind", "public-event", "cold"),
    ],
)
def test_status_outside_closed_set_refuses(tmp_path, table, field, ok, bad):
    """Four closed sets, four chances for a typo to become an unenumerable value."""
    builder = {"claim": _claim, "proof": _proof, "angle": _angle}[table]
    key = {"claim": "claims", "proof": "proof", "angle": "angles"}[table]

    good = _write(tmp_path, f"good-{table}-{field}", **{key: [builder(**{field: ok})]})
    registry.load(_PROFILE, profiles_root=good)  # negative control

    broken = _write(tmp_path, f"bad-{table}-{field}", **{key: [builder(**{field: bad})]})
    message = _refusal(broken)
    assert bad in message
    assert field in message


def test_figure_kind_outside_closed_set_refuses(tmp_path):
    """`figure_kind` is how a disputed number is kept out of a body; an unknown value is an unpoliced number."""
    good = _write(tmp_path, "good-figure", proof=[_proof(figure_kind="measured")])
    registry.load(_PROFILE, profiles_root=good)  # negative control

    bad = _write(tmp_path, "bad-figure", proof=[_proof(figure_kind="roughly")])
    message = _refusal(bad)
    assert "proof.toml" in message
    assert "regulator-note-2-1-2" in message
    assert "roughly" in message


@pytest.mark.parametrize("field", ["claim", "proof", "seat", "premise"])
def test_dangling_ids_refuse(tmp_path, field):
    """An angle is four references; one that resolves to nothing renders an empty slot."""
    good = _write(tmp_path, f"good-ref-{field}", angles=[_angle()])
    registry.load(_PROFILE, profiles_root=good)  # negative control

    bad = _write(tmp_path, f"bad-ref-{field}", angles=[_angle(**{field: "no-such-thing"})])
    message = _refusal(bad)
    assert "angles.toml" in message
    assert "ciso-multi-framework-audit" in message
    assert "no-such-thing" in message


def test_angle_segment_outside_the_declared_vocabulary_refuses(tmp_path):
    """An angle's `segments` is a fifth reference, and it had no home check.

    A SEAT's `segments` is refused when it names a segment `role-vocabulary.toml` does not
    declare. An angle's was neither lowercased nor checked against anything, so it was the
    one reference whose dangling value rendered an empty slot with nothing on screen to say
    so — the failure the other four checks exist to stop.
    """
    good = _write(tmp_path, "good-seg", angles=[_angle(segments=["enterprise"])])
    registry.load(_PROFILE, profiles_root=good)  # negative control: the same fixture loads

    bad = _write(tmp_path, "bad-seg", angles=[_angle(segments=["midmarket"])])
    message = _refusal(bad)
    assert "angles.toml" in message
    assert "ciso-multi-framework-audit" in message
    assert "midmarket" in message
    assert "role-vocabulary.toml" in message, "a defect names the file that owns the value"


def test_angle_segments_are_lowercased_like_a_seats(tmp_path):
    """`["Enterprise"]` is what the template taught, and it must resolve, not refuse.

    The negative control for the refusal above: a capitalised segment that IS declared is a
    casing question, not a dangling reference, and `_str_tuple` on the seat side already
    folds it. Two spellings of one segment would split a matrix grid in half.
    """
    root = _write(tmp_path, "cased-seg", angles=[_angle(segments=["Enterprise"])])
    reg = registry.load(_PROFILE, profiles_root=root)
    assert reg.angles["ciso-multi-framework-audit"].segments == ("enterprise",)


def test_duplicate_id_case_insensitive_refuses(tmp_path):
    """Two ids differing only in case are one id to every reader that lowercases — and all do."""
    good = _write(
        tmp_path,
        "good-dupe",
        claims=[_claim(), _claim(id="mtls", status="design-target", source=None)],
    )
    registry.load(_PROFILE, profiles_root=good)  # negative control

    bad = _write(tmp_path, "bad-dupe", claims=[_claim(), _claim(id="Audit-Signed")])
    message = _refusal(bad)
    assert "claims.toml" in message
    assert "audit-signed" in message
    assert "twice" in message or "duplicate" in message


def test_unknown_key_refuses(tmp_path):
    """A typo'd field is silently dropped by every TOML reader, so the check has to be the allowlist."""
    good = _write(tmp_path, "good-key", claims=[_claim()])
    registry.load(_PROFILE, profiles_root=good)  # negative control

    typo = _claim()
    typo["statment"] = typo.pop("statement")
    bad = _write(tmp_path, "bad-key", claims=[typo])
    message = _refusal(bad)
    assert "claims.toml" in message
    assert "statment" in message


@pytest.mark.parametrize("bad_id", ["../x", "a/b", "back\\slash", "nul\x00byte"])
def test_ids_pass_safe_segment(tmp_path, bad_id):
    """An id becomes a path segment downstream; traversal here is the highest-risk tenant error."""
    good = _write(
        tmp_path,
        f"good-id-{abs(hash(bad_id))}",
        claims=[_claim(), _claim(id="mtls", status="design-target", source=None)],
    )
    registry.load(_PROFILE, profiles_root=good)  # negative control

    bad = _write(
        tmp_path,
        f"bad-id-{abs(hash(bad_id))}",
        claims=[_claim(), _claim(id=bad_id, status="design-target", source=None)],
    )
    message = _refusal(bad)
    assert "claims.toml" in message
    assert "unsafe" in message


def test_load_is_all_or_nothing(tmp_path):
    """One bad angle refuses the tenant. A registry that drops what it cannot parse is a
    silently smaller personalised lane — the failure this loader exists to prevent."""
    sound = [_angle(), _angle(id="ciso-second-angle", status="live")]
    good = _write(tmp_path, "good-all", angles=sound)
    loaded = registry.load(_PROFILE, profiles_root=good)  # negative control
    assert loaded.angle_count == 2

    bad = _write(
        tmp_path,
        "bad-all",
        angles=[*sound, _angle(id="ciso-third-angle", claim="no-such-claim")],
    )
    message = _refusal(bad)
    assert "ciso-third-angle" in message
    assert "ciso-multi-framework-audit" not in message, "the sound angles are not the defect"


def test_missing_registry_file_refuses(tmp_path):
    """Fail closed: an absent table is not an empty one. A tenant onboarded without
    `angles.toml` would otherwise resolve zero angles and read as fully covered."""
    good = _write(tmp_path, "good-present")
    registry.load(_PROFILE, profiles_root=good)  # negative control

    bad = _write(tmp_path, "bad-missing", omit=("angles.toml",))
    message = _refusal(bad)
    assert message == "angles.toml: missing — every tenant declares this table, even empty", (
        "the operator reading this is editing TOML: an errno and an absolute path is not a defect line"
    )


def test_missing_required_key_refuses_rather_than_dropping_the_block(tmp_path):
    """A block short of a required key is REPORTED, never skipped.

    Skipping it is the same silent shrink as dropping a bad angle: the tenant reads a
    registry that loaded cleanly and is missing a claim it wrote.
    """
    good = _write(tmp_path, "good-required", claims=[_claim()])
    assert "audit-signed" in registry.load(_PROFILE, profiles_root=good).claims  # control

    short = _claim()
    del short["group"]
    bad = _write(tmp_path, "bad-required", claims=[short])
    message = _refusal(bad)
    assert "claims.toml" in message
    assert "audit-signed" in message
    assert "group" in message


# --- the loaded surface ---------------------------------------------------------------


def test_registry_exposes_claims_proof_angles_and_seats(tmp_path):
    """The four tables, keyed lowercase, plus the live view the matrix and resolver read."""
    root = _write(
        tmp_path,
        "surface",
        angles=[_angle(), _angle(id="CISO-Live-Angle", status="live")],
    )
    reg = registry.load(_PROFILE, profiles_root=root)

    assert reg.claims["audit-signed"].status == "verified"
    assert reg.proof["regulator-note-2-1-2"].kind == "anchor"
    assert set(reg.angles) == {"ciso-multi-framework-audit", "ciso-live-angle"}
    assert "security" in reg.seats
    assert reg.angle_count == 2
    assert [a.id for a in reg.live_angles()] == ["ciso-live-angle"]
