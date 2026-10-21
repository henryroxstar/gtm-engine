"""``regulators.toml`` is a safety list, so its loader refuses rather than coerces.

A body that is silently dropped is a regulator that gets emailed, and a typo such as ``domain``
for ``domains`` would drop exactly that. So every key set is closed, every kind is from a closed
list, an ``[[allow]]`` must carry its reason and its dates, and a file that is present but wrong
stops the run with the rule named. A file that is ABSENT is different: the built-in government
endings still apply, so a tenant with no file is no less protected than before this existed.

Fictional data only (§R9); see ``relation_fixtures`` for where the shapes come from.
"""

from __future__ import annotations

import datetime

import pytest

from gtm_core.account_relation import (
    BODY_KINDS,
    FLOOR_ENDINGS,
    RelationConfigError,
)
from gtm_core.account_relation_load import load_index, load_regulators, parse_regulators
from tests.unit.relation_fixtures import (
    PROFILE,
    REGULATORS_TOML,
    write_profile,
)

#: Nothing at all, empty, whitespace, and a word nobody anticipated.
ABSENT = ("", "   ", "wharrgarbl")


def _file(extra: str = "", head: str = 'schema = 1\nreviewed = "2026-10-02"\n') -> str:
    return head + extra


def _allow(**over) -> str:
    base = {
        "domain": "atlas.wideloop.example",
        "reason": "supplier to its innovation unit, contact agreed in writing",
        "decided": '"2026-10-02"',
        "expires": '"2026-12-31"',
    }
    base.update({k: v for k, v in over.items() if v is not None})
    lines = [
        f"{k} = {v!r}" if k in ("domain", "reason", "email") else f"{k} = {v}"
        for k, v in base.items()
    ]
    for k, v in over.items():
        if v is None:
            lines = [ln for ln in lines if not ln.startswith(f"{k} = ")]
    return "\n[[allow]]\n" + "\n".join(lines) + "\n"


# --- a valid file ---------------------------------------------------------------------


def test_a_valid_file_parses_to_bodies_endings_and_no_allows():
    regs = parse_regulators(REGULATORS_TOML)
    assert {b.kind for b in regs.bodies} == {
        "central-bank",
        "regulator",
        "clearing",
        "exchange",
        "standards",
        "self-regulatory",
        "public-health",
    }
    assert len(regs.bodies) == 8
    assert {e.ending for e in regs.endings} >= {".gov.example", ".mil.example", ".nhs.example"}
    assert regs.allows == ()


def test_every_documented_body_kind_is_accepted_and_the_set_is_closed():
    assert BODY_KINDS == (
        "central-bank",
        "regulator",
        "government",
        "exchange",
        "clearing",
        "standards",
        "self-regulatory",
        "public-health",
    )
    for kind in BODY_KINDS:
        text = _file(
            f'\n[[body]]\nname = "Examplia Test Body"\nkind = "{kind}"\ndomains = ["x.example"]\n'
        )
        assert parse_regulators(text).bodies[0].kind == kind


def test_the_floor_endings_survive_a_file_that_lists_none():
    regs = parse_regulators(_file())
    assert {e.ending for e in regs.endings} == set(FLOOR_ENDINGS)


def test_the_floor_cannot_be_removed_by_the_file():
    text = _file('\n[endings]\ngovernment = [".gov.example"]\n')
    got = {e.ending for e in parse_regulators(text).endings}
    assert set(FLOOR_ENDINGS) <= got and ".gov.example" in got


def test_an_ending_without_a_leading_dot_gets_one():
    text = _file('\n[endings]\ngovernment = ["gov.example"]\n')
    assert ".gov.example" in {e.ending for e in parse_regulators(text).endings}


# --- refusals --------------------------------------------------------------------------

_BODY = '\n[[body]]\nname = "Examplia Test Body"\nkind = "regulator"\n{extra}'

BAD = [
    pytest.param(_file("\nsurprise = 1\n"), "surprise", id="unknown-top-level-key"),
    pytest.param(
        _file(_BODY.format(extra='domains = ["x.example"]\ndomain = "y.example"\n')),
        "domain",
        id="typo-key-domain-for-domains",
    ),
    pytest.param(_file(_BODY.format(extra="")), "needs", id="body-with-no-identity-at-all"),
    pytest.param(
        _file(
            '\n[[body]]\nname = "Examplia Test Body"\nkind = "lobbyist"\ndomains = ["x.example"]\n'
        ),
        "kind",
        id="unknown-kind",
    ),
    pytest.param(
        _file('\n[[body]]\nname = "Examplia Test Body"\ndomains = ["x.example"]\n'),
        "kind",
        id="missing-kind",
    ),
    pytest.param(
        _file('\n[[body]]\nkind = "regulator"\ndomains = ["x.example"]\n'),
        "name",
        id="missing-name",
    ),
    pytest.param(
        _file(_BODY.format(extra='domains = "x.example"\n')), "list", id="domains-not-a-list"
    ),
    pytest.param(
        _file(_BODY.format(extra='domains = ["x.example/path"]\n')),
        "path",
        id="path-bearing-domain-is-refused-not-truncated",
    ),
    pytest.param(
        _file(_BODY.format(extra='domains = ["nodots"]\n')), "domain", id="domain-with-no-dot"
    ),
    pytest.param(
        _file(_BODY.format(extra='domains = ["gmail.com"]\n')),
        "free",
        id="a-free-mail-domain-is-never-an-identity",
    ),
    pytest.param(
        _file(_BODY.format(extra='domains = ["x.example"]\naliases = ["MAS"]\n')),
        "short",
        id="alias-shorter-than-four-characters",
    ),
    pytest.param(
        _file(_BODY.format(extra='domains = ["x.example"]\nverified = "yes"\n')),
        "verified",
        id="verified-must-be-a-bool",
    ),
    pytest.param(
        _file('\n[endings]\ngovernment = [".com"]\n'), "commercial", id="a-commercial-ending"
    ),
    pytest.param(
        _file('\n[endings]\ngovernment = [".co.uk"]\n'), "commercial", id="a-commercial-2-label"
    ),
    pytest.param(_file('\n[endings]\ngovernment = "x"\n'), "list", id="endings-not-a-list"),
    pytest.param(
        _file('\n[endings]\nlobbyist = [".gov.example"]\n'), "kind", id="endings-unknown-kind"
    ),
    pytest.param(
        _file('\n[endings]\ngovernment = [".gov example"]\n'), "ending", id="ending-with-a-space"
    ),
    pytest.param("schema = 2\n", "schema", id="wrong-schema"),
    pytest.param('reviewed = "2026-10-02"\n', "schema", id="schema-absent"),
    pytest.param("schema = 1\n[[body]\n", "TOML", id="not-toml"),
]


@pytest.mark.parametrize(("text", "needle"), BAD)
def test_a_present_but_wrong_file_is_refused_with_the_rule_named(text, needle):
    with pytest.raises(RelationConfigError) as exc:
        parse_regulators(text)
    assert needle.lower() in str(exc.value).lower()


# --- [[allow]] honesty --------------------------------------------------------------------


def test_a_complete_allow_loads():
    regs = parse_regulators(_file(_allow()))
    (allow,) = regs.allows
    assert allow.domain == "atlas.wideloop.example"
    assert allow.decided == datetime.date(2026, 10, 2)
    assert allow.expires == datetime.date(2026, 12, 31)


def test_an_allow_may_use_native_toml_dates():
    text = _file(
        '\n[[allow]]\ndomain = "atlas.wideloop.example"\n'
        'reason = "agreed in writing with its innovation unit"\n'
        "decided = 2026-10-02\nexpires = 2026-11-02\n"
    )
    assert parse_regulators(text).allows[0].expires == datetime.date(2026, 11, 2)


@pytest.mark.parametrize("missing", ["reason", "decided", "expires", "domain"])
def test_an_allow_missing_a_required_field_refuses_the_whole_load(missing):
    with pytest.raises(RelationConfigError) as exc:
        parse_regulators(_file(_allow(**{missing: None})))
    assert missing in str(exc.value)


@pytest.mark.parametrize("reason", ABSENT + ("ok", "because"))
def test_a_blank_or_token_reason_is_not_a_reason(reason):
    with pytest.raises(RelationConfigError, match="reason"):
        parse_regulators(_file(_allow(reason=reason)))


@pytest.mark.parametrize("field", ["decided", "expires"])
@pytest.mark.parametrize("value", ['""', '"   "', '"someday"', '"2026-13-45"', "5"])
def test_an_unparseable_date_is_refused(field, value):
    with pytest.raises(RelationConfigError, match=field):
        parse_regulators(_file(_allow(**{field: value})))


def test_an_allow_that_expires_before_it_was_decided_is_refused():
    with pytest.raises(RelationConfigError, match="after"):
        parse_regulators(_file(_allow(expires='"2026-09-01"')))


def test_an_allow_may_not_outlive_a_year():
    """An override that never ends is a permanent exemption, which is a different decision."""
    with pytest.raises(RelationConfigError, match="366"):
        parse_regulators(_file(_allow(expires='"2028-01-01"')))
    ok = parse_regulators(_file(_allow(expires='"2027-10-03"')))
    assert ok.allows  # 366 days after 2026-10-02 is the last admissible day


def test_an_allow_with_an_unknown_key_is_refused():
    text = _file(_allow().rstrip("\n") + "\nforever = true\n")
    with pytest.raises(RelationConfigError, match="forever"):
        parse_regulators(text)


def test_an_allow_email_must_sit_under_its_domain():
    bad = _file(_allow(email="someone@elsewhere.example"))
    with pytest.raises(RelationConfigError, match="email"):
        parse_regulators(bad)
    good = _file(_allow(email="someone@atlas.wideloop.example"))
    assert parse_regulators(good).allows[0].email == "someone@atlas.wideloop.example"


def test_an_expired_allow_loads_but_is_reported_and_stops_working():
    regs = parse_regulators(_file(_allow(expires='"2026-10-05"')))
    assert regs.allows and [a.domain for a in regs.expired_allows(datetime.date(2026, 10, 6))] == [
        "atlas.wideloop.example"
    ]


# --- absence ---------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ABSENT)
def test_absence_a_blank_or_unheard_of_kind_refuses(value):
    text = _file(
        f'\n[[body]]\nname = "Examplia Test Body"\nkind = "{value}"\ndomains = ["x.example"]\n'
    )
    with pytest.raises(RelationConfigError, match="kind"):
        parse_regulators(text)


@pytest.mark.parametrize("value", ABSENT[:2])
def test_absence_a_blank_name_refuses(value):
    text = _file(f'\n[[body]]\nname = "{value}"\nkind = "regulator"\ndomains = ["x.example"]\n')
    with pytest.raises(RelationConfigError, match="name"):
        parse_regulators(text)


@pytest.mark.parametrize("value", ABSENT)
def test_absence_a_blank_domain_entry_refuses(value):
    text = _file(
        f'\n[[body]]\nname = "Examplia Test Body"\nkind = "regulator"\ndomains = ["{value}"]\n'
    )
    with pytest.raises(RelationConfigError):
        parse_regulators(text)


# --- the file on disk ----------------------------------------------------------------------------


def test_no_file_means_the_built_in_endings_alone(tmp_path):
    root = write_profile(tmp_path, regulators=None)
    regs = load_regulators(PROFILE, root)
    assert regs.bodies == () and {e.ending for e in regs.endings} == set(FLOOR_ENDINGS)


def test_the_file_is_read_through_the_profile_knowledge_folder(tmp_path):
    root = write_profile(tmp_path)
    assert len(load_regulators(PROFILE, root).bodies) == 8


def test_a_corrupt_file_on_disk_refuses_and_names_the_file(tmp_path):
    root = write_profile(tmp_path, regulators="schema = 1\n[[body]\n")
    with pytest.raises(RelationConfigError, match="regulators.toml"):
        load_regulators(PROFILE, root)


def test_a_non_utf8_file_is_refused_not_skipped(tmp_path):
    root = write_profile(tmp_path)
    (root / PROFILE / "knowledge" / "regulators.toml").write_bytes(b"schema = 1\n\xff\xfe\x00")
    with pytest.raises(RelationConfigError):
        load_regulators(PROFILE, root)


def test_a_traversal_profile_is_refused_by_the_path_guard(tmp_path):
    root = write_profile(tmp_path)
    with pytest.raises(ValueError, match="profile"):
        load_regulators("../acme", root)


def test_the_index_pairs_the_two_files(tmp_path):
    root = write_profile(tmp_path)
    idx = load_index(PROFILE, root)
    assert idx.regulators.bodies and idx.competitors
