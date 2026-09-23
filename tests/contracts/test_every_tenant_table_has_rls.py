"""Every table in the schema must be RLS-armed, or explicitly exempted here.

``tests/backend/test_rls_live.py`` already asserts FORCE RLS — but against a
HARDCODED ``_TENANT_TABLES`` tuple, so a new table is invisible to it until
someone remembers to add it, and it is in the ``dbtest`` tier, which self-skips
wherever ``GTM_TEST_PG_ADMIN_DSN`` is unset (it reports ``16 skipped``, exit 0, on
a developer machine and in every CI job but one). A guard that cannot fail for the
case it exists to catch is not a guard.

This contract derives the table list FROM THE SCHEMA instead, and runs in the
ordinary tier with no database. A table added without RLS fails here by default —
the exemption has to be written down, with a reason, by the person adding it.

It found ``headless_signals`` (V041, 2026-09-20): keyed on ``profile_name TEXT``
rather than ``workspace_id UUID``, no RLS at all, and a ``SECURITY DEFINER``
``claim_next_signal()`` that claims across every profile.
"""

from __future__ import annotations

import pathlib
import re

import pytest

SCHEMA_DIR = pathlib.Path(__file__).resolve().parents[2] / "backend" / "schema"

_CREATE_RE = re.compile(
    r"create\s+table\s+(?:if\s+not\s+exists\s+)?([a-z_][a-z0-9_.]*)", re.IGNORECASE
)
# NB: the migrations write "FORCE  ROW LEVEL SECURITY" with two spaces in places,
# so the whitespace here must be \s+ and not a literal space.
_ENABLE_RE = re.compile(
    r"alter\s+table\s+([a-z_][a-z0-9_.]*)\s+enable\s+row\s+level\s+security", re.IGNORECASE
)
_FORCE_RE = re.compile(
    r"alter\s+table\s+([a-z_][a-z0-9_.]*)\s+force\s+row\s+level\s+security", re.IGNORECASE
)

#: Tables deliberately outside the workspace RLS spine, each with the reason it is
#: not workspace-scoped. Adding a name here is a tenancy decision, not a formality.
RLS_EXEMPT: dict[str, str] = {
    "users": (
        "global identity spine — a user exists before any workspace does, and is "
        "scoped to workspaces through workspace_members"
    ),
    "external_identities": (
        "global identity spine — (issuer, subject) -> user_id, append-only, "
        "deletion rides the users FK cascade (V016)"
    ),
    "schema_migrations": "migration bookkeeping, not tenant data",
}

#: Tables that SHOULD be RLS-armed and are not — an acknowledged defect, never an
#: exemption. Kept separate from ``RLS_EXEMPT`` on purpose: a blanket xfail on the
#: whole contract would let the NEXT unarmed table through silently, which is the
#: exact failure this file exists to stop. Each entry is asserted to still be
#: broken (:func:`test_known_gaps_are_still_gaps`), so fixing one turns this list
#: red and forces its removal.
#: Empty, and it should stay that way. ``headless_signals`` lived here until V043
#: armed it (workspace_id + ENABLE/FORCE RLS + tenant_isolation, and
#: ``workspace_scope`` on every statement in backend/services/signals/queue.py).
RLS_KNOWN_GAPS: dict[str, str] = {}


def _sql_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(SCHEMA_DIR.glob("V*.sql")))


def _parse(sql: str) -> tuple[set[str], set[str], set[str]]:
    created = {m.group(1).lower() for m in _CREATE_RE.finditer(sql)}
    enabled = {m.group(1).lower() for m in _ENABLE_RE.finditer(sql)}
    forced = {m.group(1).lower() for m in _FORCE_RE.finditer(sql)}
    return created, enabled, forced


def _tables() -> tuple[set[str], set[str], set[str]]:
    return _parse(_sql_text())


# --------------------------------------------------------------------------- #
# Self-test: prove the contract DISCRIMINATES before trusting it on real schema.
# A guard that cannot fail on a planted violation is decoration.
# --------------------------------------------------------------------------- #

_SYNTHETIC_ARMED = """
CREATE TABLE IF NOT EXISTS good_rows (id UUID PRIMARY KEY, workspace_id UUID NOT NULL);
ALTER TABLE good_rows ENABLE ROW LEVEL SECURITY;
ALTER TABLE good_rows FORCE  ROW LEVEL SECURITY;
"""

_SYNTHETIC_UNARMED = """
CREATE TABLE IF NOT EXISTS sneaky_rows (id UUID PRIMARY KEY, workspace_id UUID NOT NULL);
"""

_SYNTHETIC_ENABLE_ONLY = """
CREATE TABLE IF NOT EXISTS half_armed (id UUID PRIMARY KEY);
ALTER TABLE half_armed ENABLE ROW LEVEL SECURITY;
"""


def test_parser_accepts_a_properly_armed_table():
    created, enabled, forced = _parse(_SYNTHETIC_ARMED)
    assert created == {"good_rows"}
    assert not created - enabled
    assert not enabled - forced


def test_parser_detects_a_table_with_no_rls():
    """The planted violation a new migration would introduce."""
    created, enabled, _ = _parse(_SYNTHETIC_UNARMED)
    assert sorted(created - enabled) == ["sneaky_rows"]


def test_parser_detects_enable_without_force():
    _, enabled, forced = _parse(_SYNTHETIC_ENABLE_ONLY)
    assert sorted(enabled - forced) == ["half_armed"]


def test_parser_tolerates_the_two_space_force_spelling():
    """The migrations really do write ``FORCE  ROW LEVEL SECURITY``; a single-space
    regex silently reports every one of those tables as unforced."""
    _, _, forced = _parse("ALTER TABLE t FORCE  ROW LEVEL SECURITY;")
    assert forced == {"t"}


def test_the_schema_directory_is_readable():
    """Instrument check: an empty parse would make every assertion below vacuous."""
    created, enabled, forced = _tables()
    assert len(list(SCHEMA_DIR.glob("V*.sql"))) >= 40
    assert len(created) >= 30, f"only parsed {len(created)} CREATE TABLE statements"
    assert len(enabled) >= 25, f"only parsed {len(enabled)} ENABLE ROW LEVEL SECURITY"
    assert len(forced) >= 25, f"only parsed {len(forced)} FORCE ROW LEVEL SECURITY"


def test_every_created_table_has_rls_enabled_or_is_exempt():
    created, enabled, _ = _tables()
    missing = sorted(created - enabled - set(RLS_EXEMPT) - set(RLS_KNOWN_GAPS))
    assert not missing, (
        "tables created with no ENABLE ROW LEVEL SECURITY and no entry in "
        f"RLS_EXEMPT: {missing}. Either scope the table to workspace_id and arm "
        "RLS, or add it to RLS_EXEMPT with the reason it is not tenant data."
    )


def test_known_gaps_are_still_gaps():
    """A fixed gap must be deleted from the list, not left as a stale excuse.

    This is what keeps ``RLS_KNOWN_GAPS`` from decaying into a second exemption
    list: the moment a table here gains RLS, this fails and the entry has to go.
    """
    created, enabled, _ = _tables()
    fixed = sorted(n for n in RLS_KNOWN_GAPS if n in enabled)
    assert not fixed, f"these tables are now RLS-armed — remove them from RLS_KNOWN_GAPS: {fixed}"
    vanished = sorted(n for n in RLS_KNOWN_GAPS if n not in created)
    assert not vanished, f"RLS_KNOWN_GAPS names tables that no longer exist: {vanished}"


@pytest.mark.parametrize("name", sorted(RLS_KNOWN_GAPS))
def test_each_known_gap_carries_a_reason(name):
    assert RLS_KNOWN_GAPS[name].strip(), f"{name} is listed as a gap with no reason given"


def test_every_rls_enabled_table_is_also_forced():
    """ENABLE alone leaves the table OWNER exempt; V009 exists because of that."""
    _, enabled, forced = _tables()
    missing = sorted(enabled - forced)
    assert not missing, f"ENABLE ROW LEVEL SECURITY without FORCE: {missing}"


def test_exemptions_still_exist_and_are_not_stale():
    """An exemption for a table that no longer exists is a comment pretending to be a rule."""
    created, _, _ = _tables()
    stale = sorted(n for n in RLS_EXEMPT if n not in created and n != "schema_migrations")
    assert not stale, f"RLS_EXEMPT names tables that no longer exist: {stale}"


@pytest.mark.parametrize("name", sorted(RLS_EXEMPT))
def test_each_exemption_carries_a_reason(name):
    assert RLS_EXEMPT[name].strip(), f"{name} is exempted with no reason given"
