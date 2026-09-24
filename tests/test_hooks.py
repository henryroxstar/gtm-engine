"""Tests for gtm_core.hooks — Hook Library engine."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gtm_core import hooks as hk
from gtm_core import hooks_lint as hl
from gtm_core import outcomes as oc


@pytest.fixture
def tmp_profile(tmp_path: Path) -> tuple[Path, Path, str]:
    """Return (profiles_root, content_root, profile_slug) for a temp tenant."""
    profiles_root = tmp_path / "profiles"
    content_root = tmp_path / "content"
    profile = "testco"
    (profiles_root / profile / "knowledge").mkdir(parents=True)
    (content_root / profile).mkdir(parents=True)
    return profiles_root, content_root, profile


def _write_hooks_toml(profiles_root: Path, profile: str, text: str) -> None:
    path = profiles_root / profile / "knowledge" / "hooks.toml"
    path.write_text(text, encoding="utf-8")


def test_load_save_round_trip(tmp_profile: tuple[Path, Path, str]) -> None:
    profiles_root, content_root, profile = tmp_profile
    bank = hk.HookBank(
        hooks=[
            hk.Hook(
                id="acme-augmentation",
                angle="AI that replaces judgment is a bad trade",
                payoff_promise="AI that hands you back your time",
                formats=["linkedin-text", "reel"],
                opening_beats=[
                    hk.OpeningBeat(format="linkedin-text", variant="A", text="Your niche..."),
                    hk.OpeningBeat(format="reel", variant="A", video="cold open..."),
                ],
            )
        ],
        banned=hk.BannedConfig(stems=["just use AI"]),
    )
    hk.save_hooks(profiles_root, profile, bank)
    loaded = hk.load_hooks(profiles_root, profile, content_root=content_root)
    assert loaded.authoritative == "hooks.toml"
    assert len(loaded.hooks) == 1
    assert loaded.hooks[0].id == "acme-augmentation"
    assert loaded.hooks[0].formats == ["linkedin-text", "reel"]
    assert loaded.banned.stems == ["just use AI"]


def test_pattern_id_survives_file_round_trip(tmp_profile: tuple[Path, Path, str]) -> None:
    """`OpeningBeat.pattern_id` must survive save_hooks() -> disk -> load_hooks() unchanged.

    Regression net for _render_toml (hooks.py) hardcoding its own field list independently of
    OpeningBeat.to_raw() — a field added to the dataclass but not the writer round-trips through
    to_raw() in a unit test while being silently stripped on the very next save_hooks() call
    (promote/demote/revive/add-candidate all trigger one). This test goes through the file, not
    to_raw(), so it would have caught that class of bug.
    """
    profiles_root, content_root, profile = tmp_profile
    bank = hk.HookBank(
        hooks=[
            hk.Hook(
                id="acme-pattern-beat",
                angle="An audit trail you can't query is a backup",
                payoff_promise="Why queryability, not existence, is the bar",
                formats=["single", "thread"],
                opening_beats=[
                    hk.OpeningBeat(
                        format="single", variant="A", text="...", pattern_id="harsh-truth"
                    ),
                    hk.OpeningBeat(format="thread", variant="A", text="1/ ..."),  # no pattern_id
                ],
            )
        ],
    )
    hk.save_hooks(profiles_root, profile, bank)
    loaded = hk.load_hooks(profiles_root, profile, content_root=content_root)
    beats = {b.format: b for b in loaded.hooks[0].opening_beats}
    assert beats["single"].pattern_id == "harsh-truth"
    assert beats["thread"].pattern_id is None


def test_render_toml_is_idempotent(tmp_profile: tuple[Path, Path, str]) -> None:
    """save -> load -> save must be byte-identical (writer must not reorder/duplicate fields,
    pattern_id included) — the risk a hand-rolled writer with its own field list carries on
    every edit to that list."""
    profiles_root, content_root, profile = tmp_profile
    bank = hk.HookBank(
        hooks=[
            hk.Hook(
                id="acme-idempotent",
                angle="Angle text",
                payoff_promise="Payoff text",
                formats=["single"],
                opening_beats=[
                    hk.OpeningBeat(format="single", variant="A", text="...", pattern_id="list"),
                ],
            )
        ],
    )
    hk.save_hooks(profiles_root, profile, bank)
    path = profiles_root / profile / "knowledge" / "hooks.toml"
    first_write = path.read_text(encoding="utf-8")

    reloaded = hk.load_hooks(profiles_root, profile, content_root=content_root)
    hk.save_hooks(profiles_root, profile, reloaded)
    second_write = path.read_text(encoding="utf-8")

    assert first_write == second_write


def test_list_hooks_excludes_candidates_by_default(
    tmp_profile: tuple[Path, Path, str],
) -> None:
    profiles_root, content_root, profile = tmp_profile
    bank = hk.HookBank(
        hooks=[
            hk.Hook(id="proven-hook", angle="a", payoff_promise="b", status="proven"),
            hk.Hook(id="candidate-hook", angle="c", payoff_promise="d", status="candidate"),
        ]
    )
    hk.save_hooks(profiles_root, profile, bank)
    loaded = hk.load_hooks(profiles_root, profile, content_root=content_root)
    assert [h.id for h in hk.list_hooks(loaded)] == ["proven-hook"]
    assert [h.id for h in hk.list_hooks(loaded, include_candidates=True)] == [
        "candidate-hook",
        "proven-hook",
    ]


def test_promote_hook_appends_history(
    tmp_profile: tuple[Path, Path, str],
) -> None:
    profiles_root, content_root, profile = tmp_profile
    bank = hk.HookBank(
        hooks=[hk.Hook(id="test-hook", angle="a", payoff_promise="b", status="test")]
    )
    hk.save_hooks(profiles_root, profile, bank)
    hk.promote_hook(profiles_root, content_root, profile, "test-hook", "beat baseline")
    loaded = hk.load_hooks(profiles_root, profile, content_root=content_root)
    assert loaded.by_id("test-hook").status == "proven"
    assert loaded.by_id("test-hook").history[-1].event == "promoted"


def test_demote_hook_refuses_when_outcome_references_it(
    tmp_profile: tuple[Path, Path, str],
) -> None:
    profiles_root, content_root, profile = tmp_profile
    bank = hk.HookBank(
        hooks=[hk.Hook(id="used-hook", angle="a", payoff_promise="b", status="proven")]
    )
    hk.save_hooks(profiles_root, profile, bank)
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "impressions",
            "value": 100,
            "tags": ["hook:used-hook"],
        },
    )
    with pytest.raises(ValueError, match="outcome rows"):
        hk.demote_hook(profiles_root, content_root, profile, "used-hook", "underperformed")


def test_is_fatigued_respects_window_and_max(
    tmp_profile: tuple[Path, Path, str],
) -> None:
    profiles_root, content_root, profile = tmp_profile
    now = datetime(2026, 8, 16, 12, 0, 0, tzinfo=UTC)
    bank = hk.HookBank(
        hooks=[
            hk.Hook(
                id="tired-hook",
                angle="a",
                payoff_promise="b",
                max_impressions=50,
                fatigue_window_days=30,
            )
        ]
    )
    hk.save_hooks(profiles_root, profile, bank)

    # Old impressions outside the window do not count.
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "impressions",
            "value": 100,
            "tags": ["hook:tired-hook"],
            "ts": "2026-06-01T00:00:00Z",
        },
    )
    bank = hk.load_hooks(profiles_root, profile, content_root=content_root)
    assert not hk.is_fatigued(content_root, profile, "tired-hook", bank=bank, now=now)

    # Recent impressions inside the window cross the threshold.
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "impressions",
            "value": 60,
            "tags": ["hook:tired-hook"],
            "ts": "2026-08-15T00:00:00Z",
        },
    )
    bank = hk.load_hooks(profiles_root, profile, content_root=content_root)
    assert hk.is_fatigued(content_root, profile, "tired-hook", bank=bank, now=now)


def test_add_candidate_cli_appends_candidate(
    tmp_profile: tuple[Path, Path, str],
) -> None:
    profiles_root, content_root, profile = tmp_profile
    bank = hk.HookBank(hooks=[hk.Hook(id="existing", angle="a", payoff_promise="b")])
    hk.save_hooks(profiles_root, profile, bank)
    payload = json.dumps(
        {
            "id": "new-candidate",
            "angle": "A fresh angle from radar",
            "payoff_promise": "placeholder payoff",
            "formats": ["linkedin-text"],
            "pillar": "trust",
            "source_signal": "radar: signal cluster",
            "status": "candidate",
        }
    )
    assert (
        hk.main(
            [
                "add-candidate",
                "--profile",
                profile,
                "--profiles-root",
                str(profiles_root),
                "--content-root",
                str(content_root),
                "--json",
                payload,
            ]
        )
        == 0
    )
    loaded = hk.load_hooks(profiles_root, profile, content_root=content_root)
    candidate = loaded.by_id("new-candidate")
    assert candidate is not None
    assert candidate.status == "candidate"
    assert candidate.history[-1].event == "candidate_added"


def test_migrate_from_matrix(tmp_profile: tuple[Path, Path, str]) -> None:
    profiles_root, content_root, profile = tmp_profile
    matrix = profiles_root / profile / "knowledge" / "hook-matrix.md"
    matrix.write_text(
        "---\nsource: manual\n---\n\n"
        "# Hook matrix\n\n"
        "| id | Persona | Hook angle |\n"
        "|---|---|---|\n"
        '| acme-augmentation | Builder | "AI that replaces judgment is a bad trade" |\n',
        encoding="utf-8",
    )
    path = hk.migrate_from_matrix(profiles_root, profile)
    assert path.name == "hooks.toml"
    loaded = hk.load_hooks(profiles_root, profile, content_root=content_root)
    assert len(loaded.hooks) == 1
    assert loaded.hooks[0].id == "acme-augmentation"
    assert "bad trade" in loaded.hooks[0].angle
    assert "Builder" in (loaded.hooks[0].source_signal or "")


def test_generated_matrix_refuses_rather_than_yielding_an_empty_bank(
    tmp_profile: tuple[Path, Path, str],
) -> None:
    """A `hook-matrix.md` generated from `angles.toml` is not a hook bank, and must say so.

    `_parse_hook_matrix` looks for a table with an `| id |` header column. The seat-axis grid
    `gtm_core.messaging.matrix_view` writes has no id column at all, so the parser matched
    nothing and returned `HookBank(hooks=[])` — zero hooks, no defect, and every downstream
    gate passing by finding nothing. The tenant with a `hooks.toml` is unaffected; the next
    tenant to adopt the generated matrix WITHOUT one is the one this refusal is for.
    """
    from gtm_core.messaging import matrix_view

    profiles_root, content_root, profile = tmp_profile
    matrix = profiles_root / profile / "knowledge" / "hook-matrix.md"
    matrix.write_text(
        matrix_view.BANNER + "\n\n"
        "# Outreach hook matrix\n\n"
        "| Signal → / Seat ↓ | multi-framework × account-event |\n"
        "|---|---|\n"
        "| security | One chain of custody per agent action. |\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="hook-matrix.md"):
        hk.load_hooks(profiles_root, profile, content_root=content_root)

    # NEGATIVE CONTROL 1 — the legacy persona × signal matrix, same directory, same call, is
    # still parsed. Without it, "load_hooks raises" would also be satisfied by a fallback
    # parser that had simply been switched off.
    matrix.write_text(
        "# Hook matrix\n\n"
        "| id | Persona | Hook angle |\n"
        "|---|---|---|\n"
        '| acme-augmentation | Builder | "AI that replaces judgment is a bad trade" |\n',
        encoding="utf-8",
    )
    legacy = hk.load_hooks(profiles_root, profile, content_root=content_root)
    assert [h.id for h in legacy.hooks] == ["acme-augmentation"]

    # NEGATIVE CONTROL 2 — a tenant that HAS a hooks.toml never reaches the fallback, so the
    # generated matrix beside it is not a refusal for them. (Every live profile today.)
    matrix.write_text(matrix_view.BANNER + "\n\n# Outreach hook matrix\n", encoding="utf-8")
    hk.save_hooks(
        profiles_root,
        profile,
        hk.HookBank(hooks=[hk.Hook(id="acme-augmentation", angle="a", payoff_promise="p")]),
    )
    both = hk.load_hooks(profiles_root, profile, content_root=content_root)
    assert [h.id for h in both.hooks] == ["acme-augmentation"]
    assert both.authoritative == "hooks.toml"


def test_migrate_from_a_generated_matrix_is_refused(
    tmp_profile: tuple[Path, Path, str],
) -> None:
    """The migration path reads the same file through the same parser, and inherits the same
    refusal — migrating a generated grid would mint a `hooks.toml` of zero hooks and stamp it
    with the matrix's SHA, which reads downstream as a completed migration."""
    from gtm_core.messaging import matrix_view

    profiles_root, _content_root, profile = tmp_profile
    matrix = profiles_root / profile / "knowledge" / "hook-matrix.md"
    matrix.write_text(matrix_view.BANNER + "\n\n# Outreach hook matrix\n", encoding="utf-8")

    with pytest.raises(ValueError, match="hook-matrix.md"):
        hk.migrate_from_matrix(profiles_root, profile)
    assert not (profiles_root / profile / "knowledge" / "hooks.toml").exists()


def test_cross_profile_read_rejected_by_safe_segment(
    tmp_profile: tuple[Path, Path, str],
) -> None:
    profiles_root, content_root, profile = tmp_profile
    with pytest.raises(ValueError, match="unsafe profile"):
        hk.load_hooks(profiles_root, "../other", content_root=content_root)


def test_lint_bank_catches_missing_payoff(
    tmp_profile: tuple[Path, Path, str],
) -> None:
    profiles_root, content_root, profile = tmp_profile
    bank = hk.HookBank(hooks=[hk.Hook(id="bad-hook", angle="a", payoff_promise="")])
    errors = hl.lint_bank(bank)
    assert any("payoff_promise" in e for e in errors)


def test_revive_hook_resets_fatigue_window(
    tmp_profile: tuple[Path, Path, str],
) -> None:
    profiles_root, content_root, profile = tmp_profile
    now = datetime(2026, 8, 16, 12, 0, 0, tzinfo=UTC)
    bank = hk.HookBank(
        hooks=[
            hk.Hook(
                id="tired-hook",
                angle="a",
                payoff_promise="b",
                max_impressions=50,
                fatigue_window_days=30,
            )
        ]
    )
    hk.save_hooks(profiles_root, profile, bank)

    # Push the hook over its fatigue limit.
    oc.append_outcome(
        content_root,
        profile,
        {
            "channel": "linkedin",
            "outcome": "impressions",
            "value": 60,
            "tags": ["hook:tired-hook"],
            "ts": "2026-08-15T00:00:00Z",
        },
    )
    bank = hk.load_hooks(profiles_root, profile, content_root=content_root)
    assert hk.is_fatigued(content_root, profile, "tired-hook", bank=bank, now=now)

    hk.revive_hook(profiles_root, content_root, profile, "tired-hook", "operator revival")
    bank = hk.load_hooks(profiles_root, profile, content_root=content_root)
    assert not hk.is_fatigued(content_root, profile, "tired-hook", bank=bank, now=now)
    assert bank.by_id("tired-hook").history[-1].event == "revived"


def test_promote_demote_revive_cli(
    tmp_profile: tuple[Path, Path, str],
) -> None:
    profiles_root, content_root, profile = tmp_profile
    bank = hk.HookBank(
        hooks=[
            hk.Hook(id="promote-me", angle="a", payoff_promise="b", status="test"),
            hk.Hook(id="demote-me", angle="c", payoff_promise="d", status="proven"),
            hk.Hook(
                id="revive-me",
                angle="e",
                payoff_promise="f",
                max_impressions=10,
                fatigue_window_days=30,
            ),
        ]
    )
    hk.save_hooks(profiles_root, profile, bank)

    assert (
        hk.main(
            [
                "promote",
                "--profile",
                profile,
                "--profiles-root",
                str(profiles_root),
                "--content-root",
                str(content_root),
                "--hook",
                "promote-me",
                "--evidence",
                "beat baseline",
            ]
        )
        == 0
    )
    assert (
        hk.main(
            [
                "demote",
                "--profile",
                profile,
                "--profiles-root",
                str(profiles_root),
                "--content-root",
                str(content_root),
                "--hook",
                "demote-me",
                "--evidence",
                "underperformed",
            ]
        )
        == 0
    )
    assert (
        hk.main(
            [
                "revive",
                "--profile",
                profile,
                "--profiles-root",
                str(profiles_root),
                "--content-root",
                str(content_root),
                "--hook",
                "revive-me",
            ]
        )
        == 0
    )

    loaded = hk.load_hooks(profiles_root, profile, content_root=content_root)
    assert loaded.by_id("promote-me").status == "proven"
    assert loaded.by_id("demote-me").status == "test"
    assert loaded.by_id("revive-me").history[-1].event == "revived"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.mark.private_tree  # carve ships profiles/_template only; the >=3 census is private
def test_committed_banks_survive_a_save_cycle(tmp_path: Path) -> None:
    """Every real profile's hooks.toml, run through load -> save -> load in a scratch copy, must
    come out with the same hook count, the same id set, and the same pattern_id count.

    This is the rehearsal for what a Telegram /hooks promote/demote/revive button press does to
    the LIVE banks (cockpit/hooks.py calls save_hooks(), which rewrites the whole file via the
    hand-rolled _render_toml writer) — run here against a copy so a writer regression is caught
    before it reaches an operator's first button press after a backfill lands.
    """
    real_profiles_root = _repo_root() / "profiles"
    checked = 0
    for hooks_toml in sorted(real_profiles_root.glob("*/knowledge/hooks.toml")):
        profile = hooks_toml.parent.parent.name
        tmp_profiles_root = tmp_path / profile / "profiles"
        target = tmp_profiles_root / profile / "knowledge" / "hooks.toml"
        target.parent.mkdir(parents=True)
        target.write_text(hooks_toml.read_text(encoding="utf-8"), encoding="utf-8")

        before = hk.load_hooks(tmp_profiles_root, profile)
        before_ids = {h.id for h in before.hooks}
        before_pattern_count = sum(
            1 for h in before.hooks for b in h.opening_beats if b.pattern_id is not None
        )

        hk.save_hooks(tmp_profiles_root, profile, before)
        after = hk.load_hooks(tmp_profiles_root, profile)
        after_ids = {h.id for h in after.hooks}
        after_pattern_count = sum(
            1 for h in after.hooks for b in h.opening_beats if b.pattern_id is not None
        )

        assert after_ids == before_ids, f"{profile}: hook id set changed across a save cycle"
        assert len(after.hooks) == len(before.hooks), f"{profile}: hook count changed"
        assert after_pattern_count == before_pattern_count, (
            f"{profile}: pattern_id count changed across a save cycle "
            f"({before_pattern_count} -> {after_pattern_count})"
        )
        checked += 1

    assert checked >= 3, "expected to find hooks.toml under at least 3 committed profiles"
