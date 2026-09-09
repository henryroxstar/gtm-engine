"""C11 — the element CLI (`python -m gtm_core.elements`).

PRD test ids C11-T5 (brand-kit sync writes exactly the registered handles), C11-T7 (a real-person
character needs a consent note), C11-T9 (delete is local, and refuses while referenced) and
C11-T10/T11 (the legacy import round-trips a sixteen-pose folder).

The CLI is the only writer of an element, so these are the tests that prove no skill needs an
`Edit` on a file under `brand/` — every write here is schema-checked and dated by one code path.
"""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

import pytest

from gtm_core.elements import store
from gtm_core.elements.cli import main
from gtm_core.elements.legacy_import import ratio_of

FIXTURE = Path(__file__).resolve().parent / "tripwire" / "elements" / "legacy-character"


def _run(tmp_path, *argv, profile="probe") -> int:
    return main(
        [
            "--profile",
            profile,
            "--content-root",
            str(tmp_path),
            "--profiles-root",
            str(tmp_path / "profiles"),
            *argv,
        ]
    )


def _out(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


# ── a PNG writer, so no image bytes are committed (§R9 + .gitignore) ──────────────────────────


def _png(path: Path, width: int, height: int) -> Path:
    """A minimal valid PNG. The mirror of the IHDR reader `legacy_import` uses to read a ratio."""

    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))

    raw = b"".join(b"\x00" + b"\x80\x80\x80" * width for _ in range(height))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return path


def test_the_fixtures_png_writer_and_the_importers_reader_agree(tmp_path):
    """The fixture materialiser and the code under test must not drift — check them against
    each other before any test leans on either."""
    from gtm_core.elements.legacy_import import read_png_size

    assert read_png_size(_png(tmp_path / "a.png", 108, 192)) == (108, 192)
    assert ratio_of(108, 192) == "9:16"
    assert ratio_of(200, 200) == "1:1"
    assert ratio_of(101, 197) == "", "an unnamed ratio must be blank, never a wrong nearby one"


# ── C11-T7: the consent route ─────────────────────────────────────────────────────────────────


def _kit(tmp_path, monkeypatch, *, consent: str | None) -> None:
    """Point brandkit at a throwaway profiles tree, with or without a consent note."""
    profiles = tmp_path / "profiles" / "probe" / "knowledge"
    profiles.mkdir(parents=True, exist_ok=True)
    body = "[identity]\n"
    if consent is not None:
        body += f'consent_note = "{consent}"\n'
    (profiles / "BRAND.toml").write_text(body, encoding="utf-8")


def test_a_real_person_character_is_refused_without_a_consent_note(tmp_path, monkeypatch, capsys):
    """A likeness the tenant cannot show consent for is not storable here."""
    _kit(tmp_path, monkeypatch, consent=None)
    code = _run(
        tmp_path,
        "define",
        "a-real-colleague",
        "--kind",
        "character",
        "--name",
        "A colleague",
        "--depicts",
        "real_person",
    )
    assert code == 2
    assert "identity-kit" in capsys.readouterr().out, "the refusal must name the route that works"
    assert not store.element_path("probe", "a-real-colleague", content_root=tmp_path).exists()


def test_a_real_person_character_is_accepted_once_consent_is_recorded(
    tmp_path, monkeypatch, capsys
):
    """Positive control, and the element cites WHERE the consent lives."""
    _kit(tmp_path, monkeypatch, consent="Filmed with written consent, 2026-08-01.")
    assert (
        _run(
            tmp_path,
            "define",
            "a-real-colleague",
            "--kind",
            "character",
            "--name",
            "A colleague",
            "--depicts",
            "real_person",
        )
        == 0
    )
    element = store.load("probe", "a-real-colleague", content_root=tmp_path)
    assert element.consent_ref.startswith("identity.consent_note@"), (
        "an accepted real-person element must record which consent it rests on, and when"
    )


def test_a_fictional_character_needs_no_consent_note(tmp_path, monkeypatch):
    """The consent duty attaches to a real person, not to every character."""
    _kit(tmp_path, monkeypatch, consent=None)
    assert (
        _run(
            tmp_path,
            "define",
            "pip",
            "--kind",
            "character",
            "--name",
            "Pip",
            "--depicts",
            "fictional",
        )
        == 0
    )
    assert store.load("probe", "pip", content_root=tmp_path).consent_ref == ""


# ── define / set / poses ──────────────────────────────────────────────────────────────────────


def _defined(tmp_path) -> None:
    assert _run(tmp_path, "define", "brass-dial", "--kind", "object", "--name", "Brass dial") == 0


def test_defining_the_same_slug_twice_is_refused(tmp_path, capsys):
    """`define` creates. A re-define that silently dropped a pose set would be unrecoverable."""
    _defined(tmp_path)
    capsys.readouterr()
    assert _run(tmp_path, "define", "brass-dial", "--kind", "object", "--name", "Other") == 2
    assert "already exists" in capsys.readouterr().out
    assert store.load("probe", "brass-dial", content_root=tmp_path).name == "Brass dial"


def test_set_changes_an_existing_element_and_leaves_the_rest_alone(tmp_path, capsys):
    """Positive control for the refusal above."""
    _defined(tmp_path)
    assert (
        _run(
            tmp_path,
            "add-pose",
            "brass-dial",
            "--pose",
            "wide",
            "--file",
            "01.png",
            "--use",
            "establish",
        )
        == 0
    )
    assert _run(tmp_path, "set", "brass-dial", "--constraints", "always on walnut") == 0
    element = store.load("probe", "brass-dial", content_root=tmp_path)
    assert element.constraints == ["always on walnut"]
    assert [p.name for p in element.poses] == ["wide"], "set dropped the pose set"


def test_adding_a_pose_twice_is_refused(tmp_path, capsys):
    _defined(tmp_path)
    _run(tmp_path, "add-pose", "brass-dial", "--pose", "wide", "--file", "01.png", "--use", "u")
    capsys.readouterr()
    assert _run(tmp_path, "add-pose", "brass-dial", "--pose", "wide", "--file", "02.png") == 2
    assert "already exists" in capsys.readouterr().out


# ── C11-T5: brand-kit sync ────────────────────────────────────────────────────────────────────


def test_sync_writes_exactly_the_registered_handles_and_skips_local_only(
    tmp_path, monkeypatch, capsys
):
    """A local-only element has nothing to register, and must not become an empty id."""
    _kit(tmp_path, monkeypatch, consent=None)
    _defined(tmp_path)
    assert _run(tmp_path, "set", "brass-dial", "--handle", "higgsfield_element_id=elem-dial") == 0
    assert (
        _run(tmp_path, "define", "walnut-bench", "--kind", "environment", "--name", "Walnut bench")
        == 0
    )
    capsys.readouterr()

    assert _run(tmp_path, "sync-brandkit") == 0
    result = _out(capsys)
    assert result["reference_element_ids"] == ["elem-dial"]
    assert result["skipped_local_only"] == ["walnut-bench"]

    from gtm_core.brandkit import load_brand_kit, lookup

    kit = load_brand_kit(tmp_path / "profiles", "probe")
    assert lookup(kit, "identity.reference_element_ids") == ["elem-dial"]
    assert "consent_note" not in kit["identity"], "the sync invented a key that was not its own"


def test_sync_refuses_to_clear_the_kit_when_nothing_is_registered(tmp_path, monkeypatch, capsys):
    """Clearing every id is almost never what "sync" means, so it takes an explicit flag."""
    _kit(tmp_path, monkeypatch, consent=None)
    _defined(tmp_path)
    capsys.readouterr()
    assert _run(tmp_path, "sync-brandkit") == 2
    assert "--allow-empty" in capsys.readouterr().out
    assert _run(tmp_path, "sync-brandkit", "--allow-empty") == 0


# ── C11-T9: deletion ──────────────────────────────────────────────────────────────────────────


def _shot_list_naming(tmp_path, slug: str) -> Path:
    path = tmp_path / "probe" / "scripts" / "2026-09-07-demo.shots.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"shots": [{"n": 1, "elements": [slug]}]}), encoding="utf-8")
    return path


def test_delete_refuses_while_a_shot_list_still_names_the_element(tmp_path, capsys):
    """Deleting turns a resolvable reference into a silent nothing at render time."""
    _defined(tmp_path)
    _shot_list_naming(tmp_path, "brass-dial")
    capsys.readouterr()
    assert _run(tmp_path, "delete", "brass-dial") == 2
    assert "still named by" in capsys.readouterr().out
    assert store.element_path("probe", "brass-dial", content_root=tmp_path).exists()


def test_a_forced_delete_needs_a_reason_and_records_it(tmp_path, capsys):
    _defined(tmp_path)
    _shot_list_naming(tmp_path, "brass-dial")
    capsys.readouterr()
    assert _run(tmp_path, "delete", "brass-dial", "--force") == 2
    assert "--reason" in capsys.readouterr().out

    assert (
        _run(
            tmp_path,
            "delete",
            "brass-dial",
            "--force",
            "--reason",
            "replaced by the milled version",
        )
        == 0
    )
    ledger = json.loads(
        (store.brand_root("probe", content_root=tmp_path) / "deleted.jsonl")
        .read_text()
        .splitlines()[-1]
    )
    assert ledger["reason"] == "replaced by the milled version"
    assert not store.element_dir("probe", "brass-dial", content_root=tmp_path).exists()


def test_deleting_an_unreferenced_element_needs_no_force(tmp_path):
    """Positive control: the refusal is about references, not about deletion being scary."""
    _defined(tmp_path)
    assert _run(tmp_path, "delete", "brass-dial") == 0


def test_delete_never_calls_a_provider(tmp_path, monkeypatch):
    """A registered id may be referenced by a render nobody in this tree knows about, so revoking
    it is the operator's call on the provider's surface — not a side effect of tidying a folder."""
    import httpx

    def _boom(*a, **k):  # pragma: no cover — the point is that it never runs
        raise AssertionError("delete made a network call")

    monkeypatch.setattr(httpx, "Client", _boom)
    monkeypatch.setattr(httpx, "AsyncClient", _boom)
    _defined(tmp_path)
    _run(tmp_path, "set", "brass-dial", "--handle", "higgsfield_element_id=elem-dial")
    assert _run(tmp_path, "delete", "brass-dial") == 0


# ── C11-T10 / C11-T11: the legacy import ──────────────────────────────────────────────────────


def _materialise_legacy(tmp_path) -> tuple[Path, list[dict]]:
    manifest = json.loads((FIXTURE / "poses.json").read_text())
    src = tmp_path / "legacy" / "pip-character"
    for row in manifest:
        w, h = (108, 192) if row["ratio"] == "9:16" else (200, 200)
        _png(src / "poses" / row["file"], w, h)
    (src / "README.md").write_text((FIXTURE / "README.md").read_text(), encoding="utf-8")
    return src, manifest


def test_a_sixteen_pose_legacy_folder_imports_in_numeric_order_as_a_draft(tmp_path, capsys):
    """C11-T10. Text ordering is not numeric ordering: `10-` sorts before `2-` as a string, and a
    pose set whose order silently changed is one whose "first reference" moved."""
    src, manifest = _materialise_legacy(tmp_path)
    assert (
        _run(
            tmp_path,
            "import-legacy",
            str(src),
            "--slug",
            "pip",
            "--kind",
            "character",
            "--name",
            "Pip",
            "--depicts",
            "fictional",
        )
        == 0
    )
    result = _out(capsys)
    assert result["poses"] == len(manifest) == 16
    assert result["draft"] is True

    element = store.load("probe", "pip", content_root=tmp_path)
    assert [p.file for p in element.poses] == [row["file"] for row in manifest], (
        "the import re-ordered the pose set"
    )
    assert [p.ratio for p in element.poses] == [row["ratio"] for row in manifest], (
        "a ratio was misread from the PNG header"
    )
    assert sum(p.animatable for p in element.poses) == 6, (
        "animatable is proposed from the 9:16 stills — six of sixteen in this set"
    )
    assert all(p.use == "TODO" for p in element.poses), (
        "the importer invented a `use`; a generated one is worse than a blank because it looks "
        "answered"
    )
    assert all(p.name.startswith("beat:") for p in element.poses)


def test_the_imported_draft_cannot_resolve_until_a_human_finishes_it(tmp_path, capsys):
    """C11-T11. The import lands a shape; a human lands the meaning."""
    src, manifest = _materialise_legacy(tmp_path)
    _run(
        tmp_path,
        "import-legacy",
        str(src),
        "--slug",
        "pip",
        "--kind",
        "character",
        "--name",
        "Pip",
        "--depicts",
        "fictional",
    )
    capsys.readouterr()

    assert _run(tmp_path, "resolve", "pip") == 2
    assert "still a draft" in capsys.readouterr().out

    for row in manifest:
        name = "beat:" + row["file"].split("-", 1)[1].rsplit(".", 1)[0]
        assert _run(tmp_path, "set-pose", "pip", "--pose", name, "--use", row["use"]) == 0
    assert (
        _run(
            tmp_path,
            "set",
            "pip",
            "--constraints",
            "the same jacket in every beat",
            "--clear-draft",
        )
        == 0
    )
    capsys.readouterr()

    assert _run(tmp_path, "resolve", "pip") == 0
    resolved = _out(capsys)["reference_images"]
    assert [Path(p).name for p in resolved] == [row["file"] for row in manifest]

    element = store.load("probe", "pip", content_root=tmp_path)
    assert [p.use for p in element.poses] == [row["use"] for row in manifest]

    assert _run(tmp_path, "render-readme", "pip") == 0
    readme = (store.element_dir("probe", "pip", content_root=tmp_path) / "README.md").read_text()
    assert "the same jacket in every beat" in readme
    for row in manifest:
        assert row["use"] in readme


def test_an_unnumbered_legacy_folder_is_refused_rather_than_ordered_arbitrarily(tmp_path, capsys):
    """No numeric prefix means no order to read, and a guessed order is a wrong one."""
    src = tmp_path / "legacy" / "unnumbered"
    for name in ("standing.png", "walking.png"):
        _png(src / "poses" / name, 108, 192)
    assert (
        _run(tmp_path, "import-legacy", str(src), "--slug", "x", "--kind", "object", "--name", "X")
        == 2
    )
    assert "numeric prefix" in capsys.readouterr().out


def test_importing_over_an_existing_slug_is_refused(tmp_path, capsys):
    src, _ = _materialise_legacy(tmp_path)
    _run(
        tmp_path,
        "import-legacy",
        str(src),
        "--slug",
        "pip",
        "--kind",
        "character",
        "--name",
        "Pip",
        "--depicts",
        "fictional",
    )
    capsys.readouterr()
    assert (
        _run(
            tmp_path,
            "import-legacy",
            str(src),
            "--slug",
            "pip",
            "--kind",
            "character",
            "--name",
            "Pip",
            "--depicts",
            "fictional",
        )
        == 2
    )
    assert "already exists" in capsys.readouterr().out


# ── the ratio reader, against dimensions a provider actually emits ────────────────────────────


@pytest.mark.parametrize(
    ("w", "h", "expected"),
    [
        # The regression. A real sixteen-pose set came back at 1536x2752 — aspect 0.5581 against
        # 9:16's 0.5625, 0.8% off. An exact-match lookup named NONE of them, so `animatable` was
        # proposed false for every vertical still in a set whose own README says six are 9:16.
        (1536, 2752, "9:16"),
        (2048, 2048, "1:1"),
        # Nominal dimensions still work — the tolerance widens the net, it does not move it.
        (1080, 1920, "9:16"),
        (1920, 1080, "16:9"),
        (1024, 1280, "4:5"),
        # Off-nominal but recognisable, on the tightest neighbouring pair in the table.
        (1024, 1366, "3:4"),
        # Genuinely odd crops stay UNNAMED. "Unnamed" and "a ratio it is not" are very different
        # answers to hand a render path, and the second is the one that ships a wrong reframe.
        (1000, 1500, ""),
        (100, 300, ""),
    ],
)
def test_a_ratio_is_named_when_a_provider_rounds_it_and_unnamed_when_it_is_odd(w, h, expected):
    assert ratio_of(w, h) == expected


def test_no_two_named_ratios_sit_within_the_tolerance_of_each_other():
    """The tolerance is only safe because the table is sparse — asserted, not assumed.

    If a future ratio lands close to an existing one, `ratio_of` starts returning whichever the
    dict happened to yield first, and the failure is a silently mislabelled still rather than an
    error. This test is what makes adding a ratio a decision instead of an accident.
    """
    from gtm_core.elements.legacy_import import _KNOWN_RATIOS, RATIO_TOLERANCE

    names = sorted(_KNOWN_RATIOS, key=lambda n: _KNOWN_RATIOS[n])
    for a, b in zip(names, names[1:], strict=False):
        gap = abs(_KNOWN_RATIOS[a] - _KNOWN_RATIOS[b]) / _KNOWN_RATIOS[b]
        assert gap > 2 * RATIO_TOLERANCE, (
            f"{a} and {b} are {gap:.1%} apart, within twice the {RATIO_TOLERANCE:.0%} tolerance — "
            "a file between them could be named either"
        )


# ── sync-brandkit does not rewrite a value that is already correct ────────────────────────────


def test_sync_leaves_an_already_correct_kit_byte_identical(tmp_path, capsys):
    """`set_identity_value` replaces the whole line, comment included, with its own dated stamp.

    So a sync that "changes nothing" was destroying a comment a human wrote. The live case that
    found this carried several sentences of provenance — where the Element was seeded from, why
    Element and not Soul, where the pose library lives — none of which the writer would have
    reproduced. Byte equality is the assertion, because a value-only check would have passed
    while the comment was being deleted.
    """
    profiles = tmp_path / "profiles" / "probe" / "knowledge"
    profiles.mkdir(parents=True)
    kit = profiles / "BRAND.toml"
    hand_written = (
        "[identity]\n"
        'reference_element_ids = ["elem-dial"]  # hand-written 2026-09-03: seeded from the '
        "canonical shipped mark, Element not Soul because the roster must stay open\n"
    )
    kit.write_text(hand_written, encoding="utf-8")
    before = kit.read_bytes()

    _defined(tmp_path)
    assert _run(tmp_path, "set", "brass-dial", "--handle", "higgsfield_element_id=elem-dial") == 0
    capsys.readouterr()

    assert _run(tmp_path, "sync-brandkit") == 0
    result = _out(capsys)
    assert result["wrote"] is False
    assert kit.read_bytes() == before, "a no-op sync rewrote the file and lost its comment"


def test_sync_does_write_when_the_value_actually_changes(tmp_path, capsys):
    """Positive control: the skip is about an unchanged VALUE, not about never writing."""
    profiles = tmp_path / "profiles" / "probe" / "knowledge"
    profiles.mkdir(parents=True)
    kit = profiles / "BRAND.toml"
    kit.write_text('[identity]\nreference_element_ids = ["stale-id"]\n', encoding="utf-8")

    _defined(tmp_path)
    _run(tmp_path, "set", "brass-dial", "--handle", "higgsfield_element_id=elem-dial")
    capsys.readouterr()

    assert _run(tmp_path, "sync-brandkit") == 0
    assert _out(capsys)["wrote"] is True

    from gtm_core.brandkit import load_brand_kit, lookup

    kit_doc = load_brand_kit(tmp_path / "profiles", "probe")
    assert lookup(kit_doc, "identity.reference_element_ids") == ["elem-dial"]


def test_sync_writes_into_a_product_kit_that_only_INHERITS_the_key(tmp_path, capsys):
    """The skip compares the TARGET FILE, never the merged kit.

    A product kit that inherits the right value from the company kit still needs its own override
    written — comparing the merged value would read "already correct" and leave the product file
    empty, which is the opposite of what a per-product sync is for.
    """
    root = tmp_path / "profiles" / "probe"
    (root / "knowledge").mkdir(parents=True)
    (root / "knowledge" / "BRAND.toml").write_text(
        '[identity]\nreference_element_ids = ["elem-dial"]\n', encoding="utf-8"
    )
    (root / "products" / "rhythm").mkdir(parents=True)
    (root / "products" / "rhythm" / "BRAND.toml").write_text("[identity]\n", encoding="utf-8")

    _defined(tmp_path)
    _run(tmp_path, "set", "brass-dial", "--handle", "higgsfield_element_id=elem-dial")
    capsys.readouterr()

    assert (
        main(
            [
                "--profile",
                "probe",
                "--product",
                "rhythm",
                "--content-root",
                str(tmp_path),
                "--profiles-root",
                str(tmp_path / "profiles"),
                "sync-brandkit",
            ]
        )
        == 0
    )
    assert _out(capsys)["wrote"] is True, (
        "the product override was skipped because the COMPANY kit already had the value"
    )
    assert "elem-dial" in (root / "products" / "rhythm" / "BRAND.toml").read_text()


def test_import_legacy_refuses_a_source_folder_outside_the_content_root(tmp_path, capsys):
    """Review finding: nothing passed content_root, so plan_import's confinement branch was dead
    and any file on disk could be copied into the tenant tree as a pose."""
    outside = tmp_path.parent / f"{tmp_path.name}-outside" / "legacy"
    _png(outside / "poses" / "01-a.png", 108, 192)
    code = _run(
        tmp_path, "import-legacy", str(outside), "--slug", "x", "--kind", "object", "--name", "X"
    )
    assert code == 2
    assert not store.element_path("probe", "x", content_root=tmp_path).exists()
    assert not store.poses_dir("probe", "x", content_root=tmp_path).exists()


def test_the_importer_names_every_ratio_the_model_accepts_and_no_other():
    """Review finding: two hand-kept tables of the same seven ratios. Now one derives from the
    other, so this is the assertion that keeps them from ever disagreeing again."""
    from gtm_core.elements.legacy_import import _KNOWN_RATIOS
    from gtm_core.elements.model import _RATIOS

    assert set(_KNOWN_RATIOS) == set(_RATIOS)
    for name, aspect in _KNOWN_RATIOS.items():
        w, h = (int(x) for x in name.split(":"))
        assert aspect == w / h
