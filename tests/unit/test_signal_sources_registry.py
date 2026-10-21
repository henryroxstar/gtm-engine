"""R1.1-R1.2: the source registry is read from one file, refused whole, and never borrowed.

Runs on the fictional ``realshape`` tenant: ``alpha`` is the default product (registry at profile
level), ``beta`` the second product (registry under ``products/beta`` or none at all).
"""

from __future__ import annotations

import datetime
import json

import pytest

from gtm_core.signal_obs import registry as reg

TODAY = datetime.date(2026, 10, 1)
PREMISES = {"multi-cloud"}

_GOOD = {
    "id": "north-directory",
    "title": "North members list",
    "url": "https://members.example.test/list",
    "kind": "member_directory",
    "premise": "multi-cloud",
    "claim_gap": "Membership shows the firm runs agents across clouds.",
    "precision": "8/10",
    "member_role": "buyer",
    "timing": "none",
    "cadence_days": 30,
    "extractor": "links",
    "extractor_args": {},
    "max_members": 200,
    "expires_on": "2026-12-01",
    "owner": "ops",
    "notes": "",
}


def _toml_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)  # a valid TOML basic string, newline included
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k} = {_toml_value(x)}" for k, x in v.items()) + "}"
    return str(v)


def _file(*sources, top=""):
    out = top
    for s in sources:
        out += "\n[[source]]\n" + "".join(f"{k} = {_toml_value(v)}\n" for k, v in s.items())
    return out


def _src(**over):
    s = {**_GOOD, **over}
    return {k: v for k, v in s.items() if v is not None}


def _write(root, profile, rel, text):
    p = root / profile / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _load(root, product="alpha", profile="realshape", **kw):
    return reg.load_registry(
        profile, product, profiles_root=root, today=TODAY, premises=PREMISES, **kw
    )


def test_default_product_reads_the_profile_file_and_says_so(one_product_profiles):
    path = _write(one_product_profiles, "realshape", "knowledge/signal-sources.toml", _file(_src()))
    got = _load(one_product_profiles)
    assert got.path == path and got.product == "alpha"
    assert [s.id for s in got.sources] == ["north-directory"]
    assert got.origin == "profile file"


def test_no_file_means_no_sources_not_an_error(one_product_profiles):
    got = _load(one_product_profiles)
    assert got.sources == () and got.path is None


@pytest.mark.parametrize(
    ("over", "needle"),
    [
        ({"kind": "rss_feed"}, "kind"),
        ({"extractor": "xpath"}, "extractor"),
        ({"extractor_args": {"selector": "a"}}, "selector"),
        ({"premise": "no-such-premise"}, "premise"),
        ({"expires_on": None}, "expires_on"),
        ({"expires_on": "2027-06-01"}, "180"),
        ({"expires_on": "next spring"}, "expires_on"),
        ({"title": None}, "title"),
        ({"claim_gap": None}, "claim_gap"),
        ({"owner": None}, "owner"),
        ({"url": "http://members.example.test/list"}, "https"),
        ({"id": "a/b"}, "id"),
        ({"id": ".."}, "id"),
        ({"cadence_days": 3}, "cadence_days"),
        ({"member_role": "friend"}, "member_role"),
        ({"timing": "whenever"}, "timing"),
        ({"max_members": 0}, "max_members"),
        ({"precision": "most"}, "precision"),
        ({"surprise": "x"}, "surprise"),
    ],
)
def test_one_bad_source_refuses_the_whole_file_with_what_why_fix(
    one_product_profiles, over, needle
):
    _write(
        one_product_profiles,
        "realshape",
        "knowledge/signal-sources.toml",
        _file(_src(id="fine-one"), _src(**{"id": "second-one", **over})),
    )
    with pytest.raises(reg.RegistryError) as exc:
        _load(one_product_profiles)
    err = exc.value
    assert needle in str(err)
    assert err.what and err.why and err.fix
    assert "signal-sources.toml" in str(err)


def test_duplicate_ids_and_unknown_top_level_keys_refuse_the_file(one_product_profiles):
    _write(
        one_product_profiles,
        "realshape",
        "knowledge/signal-sources.toml",
        _file(_src(), _src()),
    )
    with pytest.raises(reg.RegistryError, match="duplicate"):
        _load(one_product_profiles)
    _write(
        one_product_profiles,
        "realshape",
        "knowledge/signal-sources.toml",
        _file(_src(), top='flavour = "x"\n'),
    )
    with pytest.raises(reg.RegistryError, match="flavour"):
        _load(one_product_profiles)


def test_an_unreadable_file_is_refused_whole(one_product_profiles):
    _write(one_product_profiles, "realshape", "knowledge/signal-sources.toml", "[[source\n")
    with pytest.raises(reg.RegistryError, match="TOML"):
        _load(one_product_profiles)


@pytest.mark.parametrize(("precision", "active"), [("7/10", True), ("6/10", False), ("", False)])
def test_precision_at_the_bar_is_active_below_it_or_unsampled_is_inert(
    one_product_profiles, precision, active
):
    _write(
        one_product_profiles,
        "realshape",
        "knowledge/signal-sources.toml",
        _file(_src(precision=precision)),
    )
    got = _load(one_product_profiles)
    assert bool(got.active) is active
    if not active:
        assert reg.inert_reason(got.sources[0], TODAY).startswith("precision")


def test_an_expired_source_is_inert_but_the_file_still_loads(one_product_profiles):
    _write(
        one_product_profiles,
        "realshape",
        "knowledge/signal-sources.toml",
        _file(_src(expires_on="2026-09-01")),
    )
    got = _load(one_product_profiles)
    assert got.active == ()
    assert reg.inert_reason(got.sources[0], TODAY) == "expired on 2026-09-01"


# --- second product: its own file or nothing ---------------------------------------------------


def test_a_second_product_never_borrows_the_profile_registry(one_product_profiles):
    _write(one_product_profiles, "realshape", "knowledge/signal-sources.toml", _file(_src()))
    got = _load(one_product_profiles, product="beta")
    assert got.sources == () and got.path is None and got.product == "beta"


def test_a_second_product_reads_its_own_file_and_names_the_one_it_shadows(one_product_profiles):
    _write(one_product_profiles, "realshape", "knowledge/signal-sources.toml", _file(_src()))
    own = _write(
        one_product_profiles,
        "realshape",
        "products/beta/signal-sources.toml",
        _file(_src(id="beta-only")),
    )
    got = _load(one_product_profiles, product="beta")
    assert got.path == own and got.origin == "product file"
    assert [s.id for s in got.sources] == ["beta-only"]
    assert got.shadows is not None and got.shadows.name == "signal-sources.toml"
    assert got.shadows.parent.name == "knowledge"


def test_an_invalid_product_file_means_no_sources_never_the_profiles(one_product_profiles):
    _write(one_product_profiles, "realshape", "knowledge/signal-sources.toml", _file(_src()))
    bad = _write(
        one_product_profiles,
        "realshape",
        "products/beta/signal-sources.toml",
        _file(_src(kind="rss_feed")),
    )
    loaded, sentence = reg.load_for_run(
        "realshape",
        "beta",
        profiles_root=one_product_profiles,
        today=TODAY,
        premises=PREMISES,
    )
    assert loaded is None
    assert "signal-sources.toml" in sentence and str(bad.parent.name) in sentence
    assert "without sources" in sentence and "\n" not in sentence


def test_a_run_with_an_invalid_registry_continues_and_says_one_sentence(one_product_profiles):
    _write(one_product_profiles, "realshape", "knowledge/signal-sources.toml", "[[source\n")
    loaded, sentence = reg.load_for_run(
        "realshape", "alpha", profiles_root=one_product_profiles, today=TODAY, premises=PREMISES
    )
    assert loaded is None and sentence.count(".") >= 1
    assert "knowledge" in sentence and "Fix" in sentence


def test_a_product_the_profile_does_not_have_is_refused_not_guessed(one_product_profiles):
    from gtm_core import run_scope

    with pytest.raises(run_scope.ScopeError):
        _load(one_product_profiles, product="gamma")


def test_omitting_the_product_on_a_two_product_company_refuses(one_product_profiles):
    from gtm_core import run_scope

    with pytest.raises(run_scope.ProductRequired):
        _load(one_product_profiles, product=None)


# --- check -------------------------------------------------------------------------------------


def test_check_prints_the_file_a_summary_and_never_writes(one_product_profiles, capsys):
    path = _write(one_product_profiles, "realshape", "knowledge/signal-sources.toml", _file(_src()))
    before = sorted(p for p in one_product_profiles.rglob("*") if p.is_file())
    code = reg.check(
        "realshape",
        "alpha",
        profiles_root=one_product_profiles,
        today=TODAY,
        premises=PREMISES,
        sources_dir=None,
    )
    out = capsys.readouterr().out
    assert code == 0 and str(path) in out and "north-directory" in out
    assert "North members list" in out
    assert sorted(p for p in one_product_profiles.rglob("*") if p.is_file()) == before


def test_check_on_an_invalid_file_refuses_with_what_why_fix(one_product_profiles, capsys):
    _write(
        one_product_profiles,
        "realshape",
        "knowledge/signal-sources.toml",
        _file(_src(expires_on=None)),
    )
    code = reg.check(
        "realshape",
        "alpha",
        profiles_root=one_product_profiles,
        today=TODAY,
        premises=PREMISES,
        sources_dir=None,
    )
    out = capsys.readouterr().out
    assert code == 2
    assert "What:" in out and "Why:" in out and "Fix:" in out


def test_check_previews_members_from_the_latest_capture_and_writes_nothing(signal_world, capsys):
    from gtm_core.signal_obs import registry as r
    from unit.conftest import page

    signal_world.capture(
        page("Northwind Traders", "Blue Harbour Partners"), "2026-10-01T08:00:00+00:00"
    )
    before = sorted(p.name for p in signal_world.content_root.rglob("*") if p.is_file())
    code = r.check(
        "realshape",
        "alpha",
        profiles_root=signal_world.profiles_root,
        today=TODAY,
        premises=PREMISES,
        sources_dir=signal_world.sources_dir,
    )
    out = capsys.readouterr().out
    assert code == 0 and "Northwind Traders" in out and "Blue Harbour Partners" in out
    assert "2026-10-01" in out and "2 members" in out
    assert sorted(p.name for p in signal_world.content_root.rglob("*") if p.is_file()) == before


def test_check_says_so_when_there_is_no_capture_to_preview(signal_world, capsys):
    from gtm_core.signal_obs import registry as r

    r.check(
        "realshape",
        "alpha",
        profiles_root=signal_world.profiles_root,
        today=TODAY,
        premises=PREMISES,
        sources_dir=signal_world.sources_dir,
    )
    assert "no capture yet" in capsys.readouterr().out


@pytest.mark.parametrize(
    "field",
    [
        "id",
        "title",
        "url",
        "kind",
        "claim_gap",
        "member_role",
        "timing",
        "extractor",
        "owner",
        "premise",
        "precision",
        "notes",
    ],
)
@pytest.mark.parametrize("bad", [["a"], 5, {"x": 1}])
def test_a_field_of_the_wrong_type_is_a_named_refusal_never_a_crash(
    one_product_profiles, field, bad
):
    _write(
        one_product_profiles,
        "realshape",
        "knowledge/signal-sources.toml",
        _file(_src(**{field: bad})),
    )
    with pytest.raises(reg.RegistryError, match=field):
        _load(one_product_profiles)


@pytest.mark.parametrize(
    ("extractor", "args"),
    [
        ("links", {"href_contains": 5}),
        ("links", {"href_contains": ""}),
        ("table_column", {"column": ["a"]}),
        ("heading_list", {"level": "2"}),
        ("heading_list", {"level": 0}),
        ("heading_list", {"level": 7}),
        ("brain_list", {"hint": 3}),
    ],
)
def test_extractor_arguments_must_have_the_type_the_extractor_reads(
    one_product_profiles, extractor, args
):
    _write(
        one_product_profiles, "realshape", "knowledge/signal-sources.toml",
        _file(_src(extractor=extractor, extractor_args=args)),
    )  # fmt: skip
    with pytest.raises(reg.RegistryError, match="extractor_args"):
        _load(one_product_profiles)


@pytest.mark.parametrize(
    ("precision", "active"),
    [("10/10", True), ("7/10", True), ("9/9", False), ("3/3", False), ("7/9", False)],
)
def test_a_precision_from_a_sample_under_ten_is_not_enough_to_go_live(
    one_product_profiles, precision, active
):
    _write(
        one_product_profiles,
        "realshape",
        "knowledge/signal-sources.toml",
        _file(_src(precision=precision)),
    )
    got = _load(one_product_profiles)
    assert bool(got.active) is active
    if not active:
        assert "fewer than 10" in reg.inert_reason(got.sources[0], TODAY)


def test_a_run_reads_a_registry_with_a_wrong_type_as_no_sources_and_one_sentence(
    one_product_profiles,
):
    _write(
        one_product_profiles, "realshape", "knowledge/signal-sources.toml", _file(_src(id=["x"]))
    )
    got, sentence = reg.load_for_run(
        "realshape", "alpha", profiles_root=one_product_profiles, today=TODAY, premises=PREMISES
    )
    assert got is None and sentence.startswith("Sources are off for this run")


# --- hardening: what the registry's url, id, and numbers may be ---------------------------------


def _refused(root, *sources, top="", match=None):
    _write(root, "realshape", "knowledge/signal-sources.toml", _file(*sources, top=top))
    with pytest.raises(reg.RegistryError, match=match) as exc:
        _load(root)
    return exc.value


def _accepted(root, *sources, top=""):
    _write(root, "realshape", "knowledge/signal-sources.toml", _file(*sources, top=top))
    return _load(root)


def _u(*parts: str) -> str:
    """A URL assembled from pieces, so the source never carries a literal odd host."""
    return "https://" + "".join(parts)


_BAD_URLS = [
    "https://user@members.example.test/list",
    "https://user:pw@members.example.test/list",
    "https://members.example.test@evil.example.test/list",
    "https://members.example.test/list?page=2",
    "https://members.example.test/list#members",
    "https://members.example.test/li st",
    "https://members.example.test/list\n",
    "https://members.example.test/\x07list",
    "https://members.example.test/\tlist",
    _u("members", ".", "ex\u00e4mple", ".", "test", "/list"),
    "https://members.example.test/lïst",
    "https://members.example.test\\@evil.example.test/list",
    "https://127.0.0.1/list",
    _u("10", ".", "0", ".", "0", ".", "1:443/list"),
    "https://[::1]/list",
    "https://2130706433/list",
    "https://localhost/list",
    "https://intranet/list",
    "https://members.example.test:8443/list",
    "https://members.example.test:80/list",
    "https://members.example.test:/list",
    "https://members.example.test:0443/list",
    "https:///list",
    "https://",
    "https://.example.test/list",
    "https://members..example.test/list",
    "https://members.example.test./list",
    _u("members", ".", "example", ".", "123", "/list"),
    "https://members.example.test/" + "a" * 300,
]


@pytest.mark.parametrize("url", _BAD_URLS)
def test_a_url_that_could_send_the_capture_anywhere_but_the_named_list_is_refused(
    one_product_profiles, url
):
    err = _refused(one_product_profiles, _src(id="fine-one"), _src(id="second-one", url=url))
    assert "second-one" in str(err) and "url" in str(err)


@pytest.mark.parametrize(
    "url",
    [
        "https://members.example.test/list",
        "https://members.example.test",
        "https://members.example.test:443/list",
        "https://a.b.c.example.test/register/members.html",
        "https://members.example.test/list%20of/members_2026",
        "https://xn--mller-kva.example.test/list",
        "https://members.example.test/" + "a" * (300 - len("https://members.example.test/")),
    ],
)
def test_an_ordinary_https_list_address_is_accepted(one_product_profiles, url):
    got = _accepted(one_product_profiles, _src(url=url))
    assert got.sources[0].url == url


def test_check_prints_each_sources_url_so_the_operator_sees_where_a_capture_goes(
    one_product_profiles, capsys
):
    _write(one_product_profiles, "realshape", "knowledge/signal-sources.toml", _file(_src()))
    reg.check(
        "realshape", "alpha", profiles_root=one_product_profiles, today=TODAY, premises=PREMISES
    )
    assert "https://members.example.test/list" in capsys.readouterr().out


@pytest.mark.parametrize(
    "sid", ["North", "NORTH", "north\n", "-north", "north_list", "n" * 65, "é"]
)
def test_a_source_id_is_lowercase_letters_digits_and_dashes_only(one_product_profiles, sid):
    err = _refused(one_product_profiles, _src(id=sid))
    assert "id" in str(err)


def test_two_ids_that_differ_only_by_case_are_refused(one_product_profiles):
    _refused(one_product_profiles, _src(id="North"), _src(id="north"), match="id")


def test_the_duplicate_check_is_case_insensitive_on_its_own():
    from pathlib import Path
    from types import SimpleNamespace as NS

    from gtm_core.signal_obs import registry_checks as rc

    path = Path("knowledge/signal-sources.toml")
    with pytest.raises(reg.RegistryError, match="duplicate"):
        rc.check_unique_ids(path, (NS(id="North"), NS(id="north")))
    rc.check_unique_ids(path, (NS(id="north"), NS(id="south")))


def test_a_source_id_of_64_characters_is_accepted(one_product_profiles):
    got = _accepted(one_product_profiles, _src(id="n" * 64))
    assert got.sources[0].id == "n" * 64


@pytest.mark.parametrize(
    "precision",
    [
        "8/10\n",
        "\n8/10",
        "8/10 ",
        "٨/١٠",
        "8/١٠",
        "1" * 5000 + "/" + "1" * 5001,
        "12345/12345",
        "8/0",
    ],
)
def test_a_precision_that_is_not_plain_ascii_k_over_n_is_refused_not_a_crash(
    one_product_profiles, precision
):
    err = _refused(one_product_profiles, _src(precision=precision))
    assert "precision" in str(err)


def test_a_precision_of_four_digits_each_is_the_most_that_is_read(one_product_profiles):
    got = _accepted(one_product_profiles, _src(precision="7000/9999"))
    assert got.sources[0].precision == "7000/9999"


@pytest.mark.parametrize(
    "top", ["source = 5\n", 'source = "x"\n', "source = true\n", "source = {id = 'x'}\n"]
)
def test_a_source_key_that_is_not_a_list_of_tables_is_refused_not_a_crash(
    one_product_profiles, top
):
    _write(one_product_profiles, "realshape", "knowledge/signal-sources.toml", top)
    with pytest.raises(reg.RegistryError, match="source"):
        _load(one_product_profiles)
    got, sentence = reg.load_for_run(
        "realshape", "alpha", profiles_root=one_product_profiles, today=TODAY, premises=PREMISES
    )
    assert got is None and sentence.startswith("Sources are off for this run")


_NESTED = {
    "arrays": "x = " + "[" * 200_000 + "]" * 200_000 + "\n",
    "tables": "x = " + "{a = " * 50_000 + "1" + "}" * 50_000 + "\n",
}


@pytest.mark.parametrize("shape", sorted(_NESTED))
def test_a_file_nested_past_the_parser_limit_is_refused_and_a_run_goes_on(
    one_product_profiles, shape
):
    _write(one_product_profiles, "realshape", "knowledge/signal-sources.toml", _NESTED[shape])
    with pytest.raises(reg.RegistryError, match="TOML"):
        _load(one_product_profiles)
    got, sentence = reg.load_for_run(
        "realshape", "alpha", profiles_root=one_product_profiles, today=TODAY, premises=PREMISES
    )
    assert got is None and "without sources" in sentence


def _break_premise_vocab(root):
    _write(root, "realshape", "knowledge/signal-sources.toml", _file(_src()))
    _write(root, "realshape", "knowledge/premise-vocab.toml", "[premise.multi-cloud\n")


def test_a_broken_premise_vocabulary_is_a_registry_refusal_naming_the_file(one_product_profiles):
    _break_premise_vocab(one_product_profiles)
    with pytest.raises(reg.RegistryError, match="premise-vocab.toml"):
        reg.load_registry("realshape", "alpha", profiles_root=one_product_profiles, today=TODAY)


def test_check_reports_a_broken_premise_vocabulary_instead_of_crashing(
    one_product_profiles, capsys
):
    _break_premise_vocab(one_product_profiles)
    code = reg.check("realshape", "alpha", profiles_root=one_product_profiles, today=TODAY)
    out = capsys.readouterr().out
    assert code == 2 and "premise-vocab.toml" in out and "Fix:" in out


def test_a_run_with_a_broken_premise_vocabulary_goes_on_without_sources(one_product_profiles):
    _break_premise_vocab(one_product_profiles)
    got, sentence = reg.load_for_run(
        "realshape", "alpha", profiles_root=one_product_profiles, today=TODAY
    )
    assert got is None and "premise-vocab.toml" in sentence and "without sources" in sentence


@pytest.mark.parametrize("schema", ["true", "false", "1.0", '"1"', "2", "0"])
def test_schema_must_be_the_whole_number_one_not_a_bool_a_float_or_another_version(
    one_product_profiles, schema
):
    err = _refused(one_product_profiles, _src(), top=f"schema = {schema}\n", match="schema")
    assert "schema" in str(err)


def test_schema_one_written_out_is_accepted(one_product_profiles):
    assert _accepted(one_product_profiles, _src(), top="schema = 1\n").sources


@pytest.mark.parametrize("key", ["cadence_days", "max_members"])
def test_a_bool_is_not_a_whole_number(one_product_profiles, key):
    _refused(one_product_profiles, _src(**{key: True}), match=key)


def test_a_bool_is_not_a_heading_level_or_text(one_product_profiles):
    _refused(
        one_product_profiles,
        _src(extractor="heading_list", extractor_args={"level": True}),
        match="extractor_args",
    )
    _refused(one_product_profiles, _src(title=True), match="title")


@pytest.mark.parametrize("args", [None, {}, {"column": ""}, {"column": "  "}])
def test_a_table_column_source_must_name_the_column_it_reads(one_product_profiles, args):
    err = _refused(
        one_product_profiles, _src(extractor="table_column", extractor_args=args), match="column"
    )
    assert "extractor_args" in str(err)


def test_a_table_column_source_with_a_column_is_accepted(one_product_profiles):
    got = _accepted(
        one_product_profiles, _src(extractor="table_column", extractor_args={"column": "Member"})
    )
    assert got.sources[0].extractor_args == {"column": "Member"}


# --- boundaries the mutation pass found unpinned ------------------------------------------------


def test_a_cadence_of_seven_days_is_the_shortest_accepted(one_product_profiles):
    assert reg.MIN_CADENCE_DAYS == 7
    assert _accepted(one_product_profiles, _src(cadence_days=7)).sources[0].cadence_days == 7
    _refused(one_product_profiles, _src(cadence_days=6), match="cadence_days")


def test_a_source_may_run_180_days_from_today_and_not_a_day_longer(one_product_profiles):
    assert reg.MAX_EXPIRY_DAYS == 180
    last = (TODAY + datetime.timedelta(days=180)).isoformat()
    over = (TODAY + datetime.timedelta(days=181)).isoformat()
    assert _accepted(one_product_profiles, _src(expires_on=last)).active
    _refused(one_product_profiles, _src(expires_on=over), match="180")


def test_a_source_is_live_on_its_expiry_day_and_inert_the_day_after(one_product_profiles):
    same = _accepted(one_product_profiles, _src(expires_on=TODAY.isoformat()))
    assert len(same.active) == 1
    assert reg.inert_reason(same.sources[0], TODAY) is None
    assert reg.inert_reason(same.sources[0], TODAY + datetime.timedelta(days=1)) == (
        f"expired on {TODAY.isoformat()}"
    )
    yesterday = (TODAY - datetime.timedelta(days=1)).isoformat()
    lapsed = _accepted(one_product_profiles, _src(expires_on=yesterday))
    assert lapsed.active == () and len(lapsed.sources) == 1


def test_a_native_toml_date_is_read_like_a_quoted_one(one_product_profiles):
    got = _accepted(one_product_profiles, _src(expires_on=datetime.date(2026, 12, 1)))
    assert got.sources[0].expires_on == datetime.date(2026, 12, 1)


def test_a_schema_two_registry_is_refused_whole(one_product_profiles):
    err = _refused(one_product_profiles, _src(), top="schema = 2\n", match="schema")
    assert "2" in str(err)


@pytest.mark.parametrize("top", ['sources = "x"\n', "schemas = 1\n", "Source = []\n"])
def test_a_misspelled_top_level_key_is_refused_not_ignored(one_product_profiles, top):
    err = _refused(one_product_profiles, _src(), top=top, match="unknown key")
    assert top.split(" ")[0] in str(err)
