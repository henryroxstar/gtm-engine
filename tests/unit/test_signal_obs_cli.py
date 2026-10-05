"""The `python -m gtm_core.signal_obs` commands: each one wired to the code under test, read-only unless told."""

from __future__ import annotations

import json

import pytest

from gtm_core.signal_obs import cli
from unit.conftest import SOURCE_URL, page

NW = ("Northwind Traders", "northwind.example.test")


@pytest.fixture
def wired(signal_world, monkeypatch):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(signal_world.content_root))
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(signal_world.profiles_root))
    monkeypatch.setenv("GTM_SIGNAL_SOURCES_ENABLED", "1")
    return signal_world


def test_check_due_and_extract_run_end_to_end(wired, capsys):
    wired.ledger(NW)
    wired.capture(page(NW[0], domains={NW[0]: NW[1]}), "2026-10-01T08:00:00+00:00")
    assert cli.main(["check", "--profile", "realshape", "--product", "alpha"]) == 0
    assert "North members list" in capsys.readouterr().out
    assert cli.main(["due", "--profile", "realshape", "--product", "alpha"]) == 0
    assert "signal-sources.toml" in capsys.readouterr().out
    code = cli.main(
        [
            "extract",
            "--profile",
            "realshape",
            "--product",
            "alpha",
            "--source",
            "north-directory",
            "--run-id",
            "r1",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0 and "first look" in out and "1 member" in out
    assert [o["account_key"] for o in wired.observations()] == [NW[1]]


def test_due_write_manifest_writes_the_allow_list(wired, capsys):
    code = cli.main(
        [
            "due",
            "--profile",
            "realshape",
            "--product",
            "alpha",
            "--write-manifest",
            "--run-id",
            "r9",
        ]
    )
    path = wired.sources_dir / "manifest-r9.json"
    assert code == 0 and json.loads(path.read_text())["urls"] == [SOURCE_URL]
    assert str(path) in capsys.readouterr().out


def test_extract_with_no_capture_exits_nonzero_and_says_why(wired, capsys):
    code = cli.main(
        ["extract", "--profile", "realshape", "--product", "alpha", "--source", "north-directory"]
    )
    assert code == 2 and "no capture" in capsys.readouterr().out


def test_a_product_the_company_does_not_have_is_refused_in_plain_words(wired, capsys):
    code = cli.main(["check", "--profile", "realshape", "--product", "gamma"])
    assert code == 2 and capsys.readouterr().out.strip()


def test_dropping_the_product_on_a_two_product_company_refuses(wired, capsys):
    assert cli.main(["check", "--profile", "realshape"]) == 2
    assert "product" in capsys.readouterr().out.lower()


def test_extract_reads_a_brain_list_file_and_refuses_a_malformed_one(wired, tmp_path, capsys):
    wired.registry(extractor="brain_list", args="{}")
    wired.ledger(NW)
    wired.capture(f"# Members\n\n{NW[0]} is a member.\n", "2026-10-01T08:00:00+00:00")
    bad = tmp_path / "bad.json"
    bad.write_text('{"not": "a list"}')
    base = [
        "extract",
        "--profile",
        "realshape",
        "--product",
        "alpha",
        "--source",
        "north-directory",
    ]
    assert cli.main([*base, "--brain-list", str(bad)]) == 2
    assert "brain list" in capsys.readouterr().out
    good = tmp_path / "good.json"
    good.write_text(json.dumps([{"name": NW[0], "domain": NW[1]}]))
    assert cli.main([*base, "--brain-list", str(good), "--candidates", str(good)]) == 2
    assert cli.main([*base, "--brain-list", str(good)]) == 0


def test_review_prints_a_sheet_and_plan_is_read_only(wired, tmp_path, capsys):
    wired.ledger()
    wired.capture(page(NW[0], domains={NW[0]: NW[1]}), "2026-10-01T08:00:00+00:00")
    cli.main(
        ["extract", "--profile", "realshape", "--product", "alpha", "--source", "north-directory"]
    )
    capsys.readouterr()
    assert cli.main(["review", "--profile", "realshape", "--product", "alpha"]) == 0
    sheet = capsys.readouterr().out
    assert "Which company is this?" in sheet
    filled = tmp_path / "sheet.csv"
    filled.write_text(
        sheet.replace(
            ",confirm | different | not-a-prospect | skip,,",
            ",confirm | different | not-a-prospect | skip,confirm,",
        )
    )
    assert (
        cli.main(["review", "--profile", "realshape", "--product", "alpha", "--plan", str(filled)])
        == 0
    )
    assert wired.observations() == []
    assert (
        cli.main(["review", "--profile", "realshape", "--product", "alpha", "--apply", str(filled)])
        == 0
    )
    assert [o["account_key"] for o in wired.observations()] == [NW[1]]


def test_repair_is_plan_first(wired, capsys):
    from gtm_core.signal_obs import observations as obs

    rec = obs.make_observation(
        kind="source_member",
        product="alpha",
        source_id="north-directory",
        source_url=SOURCE_URL,
        capture_sha256="c" * 64,
        account_key=NW[1],
        observed="2026-10-01",
        observed_basis="first_seen_in_capture",
        role="buyer",
        premise_at_write="",
        writer="amy.r1",
    )
    shard = obs.append(wired.obs_dir, "amy.r1-2026-10.jsonl", [rec])
    whole = shard.read_bytes()
    shard.write_bytes(whole + b'{"schema": 1, "ki')
    base = ["repair", "--profile", "realshape", "--product", "alpha", "--shard", shard.name]
    assert cli.main(base) == 0 and shard.read_bytes() != whole
    assert cli.main([*base, "--apply"]) == 0 and shard.read_bytes() == whole


def _shard(w):
    from gtm_core.signal_obs import observations as obs

    rec = obs.make_observation(
        kind="source_member", product="alpha", source_id="north-directory", source_url=SOURCE_URL,
        capture_sha256="c" * 64, account_key=NW[1], observed="2026-10-01",
        observed_basis="first_seen_in_capture", role="buyer", premise_at_write="", writer="amy.r1",
    )  # fmt: skip
    return obs.append(w.obs_dir, "amy.r1-2026-10.jsonl", [rec])


def test_repair_of_a_shard_that_does_not_exist_is_a_refusal_not_a_crash(wired, capsys):
    argv = [
        "repair",
        "--profile",
        "realshape",
        "--product",
        "alpha",
        "--shard",
        "nobody-2026-10.jsonl",
    ]
    assert cli.main(argv) == 2
    assert "Refused" in capsys.readouterr().out


def test_repair_refuses_a_shard_that_is_a_link_out_of_the_observation_folder(
    wired, tmp_path, capsys
):
    outside = tmp_path / "outside-2026-10.jsonl"
    outside.write_bytes(b'{"half')
    wired.obs_dir.mkdir(parents=True, exist_ok=True)
    (wired.obs_dir / "amy.r1-2026-10.jsonl").symlink_to(outside)
    argv = [
        "repair",
        "--profile",
        "realshape",
        "--product",
        "alpha",
        "--shard",
        "amy.r1-2026-10.jsonl",
        "--apply",
    ]
    assert cli.main(argv) == 2
    assert outside.read_bytes() == b'{"half'


def test_repair_goes_through_the_run_scope_so_a_dropped_product_is_refused(wired, capsys):
    _shard(wired)
    argv = ["repair", "--profile", "realshape", "--shard", "amy.r1-2026-10.jsonl"]
    assert cli.main(argv) == 2
    assert "Refused" in capsys.readouterr().out


def test_review_out_is_confined_to_the_content_root(wired, tmp_path, capsys):
    target = tmp_path.parent / "elsewhere-sheet.csv"
    argv = ["review", "--profile", "realshape", "--product", "alpha", "--out", str(target)]
    assert cli.main(argv) == 2 and not target.exists()


def test_each_extract_without_a_run_id_gets_its_own(wired, monkeypatch):
    seen = []
    from gtm_core.signal_obs import extract

    def fake(*a, **kw):
        seen.append(kw["run_id"])
        return extract.ExtractReport("failed", reason="x")

    monkeypatch.setattr(extract, "run_extract", fake)
    for _ in range(2):
        cli.main(
            [
                "extract",
                "--profile",
                "realshape",
                "--product",
                "alpha",
                "--source",
                "north-directory",
            ]
        )
    assert len(set(seen)) == 2 and "run" not in seen


def test_the_report_says_what_was_quietly_left_out(wired, capsys):
    wired.ledger(NW)
    wired.capture(
        f"# M\n\n- [UBS](https://ubs.example.test/)\n- [{NW[0]}](https://{NW[1]}/)\n",
        "2026-10-01T08:00:00+00:00",
    )
    assert (
        cli.main(
            [
                "extract",
                "--profile",
                "realshape",
                "--product",
                "alpha",
                "--source",
                "north-directory",
            ]
        )
        == 0
    )
    assert "Left out: 1 name too short" in capsys.readouterr().out


def test_review_out_stays_inside_this_profiles_observation_folder(wired, capsys):
    ledger = wired.content_root / "realshape" / "prospects" / "latest.json"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text('{"kind": "prospects"}', encoding="utf-8")
    other = wired.content_root / "othertenant" / "x.csv"
    other.parent.mkdir(parents=True, exist_ok=True)
    base = ["review", "--profile", "realshape", "--product", "alpha", "--out"]
    for target in (ledger, other):
        assert cli.main([*base, str(target)]) == 2
    assert ledger.read_text(encoding="utf-8") == '{"kind": "prospects"}' and not other.exists()
    inside = wired.obs_dir / "sheet.csv"
    assert cli.main([*base, str(inside)]) == 0 and inside.is_file()
    assert cli.main([*base, str(inside)]) == 2  # never over a file that is already there


@pytest.mark.parametrize(
    "argv",
    [
        ["check", "--profile", "../x"],
        [
            "due",
            "--profile",
            "realshape",
            "--product",
            "alpha",
            "--write-manifest",
            "--run-id",
            "../../evil",
        ],
        ["review", "--profile", "realshape", "--product", "alpha", "--plan", "/nonexistent.csv"],
    ],
)
def test_a_bad_argument_is_a_refusal_not_a_traceback(wired, capsys, argv):
    assert cli.main(argv) == 2
    assert "Refused" in capsys.readouterr().out


def test_two_manifests_written_in_the_same_second_do_not_overwrite_each_other(wired, capsys):
    wired.capture(page(NW[0]), "2026-01-01T08:00:00+00:00")
    argv = ["due", "--profile", "realshape", "--product", "alpha", "--write-manifest"]
    assert cli.main(argv) == 0 and cli.main(argv) == 0
    assert len(list(wired.sources_dir.glob("manifest-*.json"))) == 2


def test_a_cut_off_queue_can_be_repaired_like_a_shard(wired, capsys):
    wired.obs_dir.mkdir(parents=True)
    queue = wired.obs_dir / "unresolved.jsonl"
    whole = '{"name": "Northwind Traders", "source_id": "s", "product": "alpha"}\n'
    queue.write_text(whole + '{"name": "Blue Ha', encoding="utf-8")
    argv = ["repair", "--profile", "realshape", "--product", "alpha", "--shard", "unresolved.jsonl"]
    assert cli.main(argv) == 0 and queue.read_text(encoding="utf-8") != whole  # plan only
    assert cli.main([*argv, "--apply"]) == 0
    assert queue.read_text(encoding="utf-8") == whole


def test_check_previews_only_captures_that_extract_would_trust(wired, capsys):
    wired.capture(page(NW[0]), "2026-10-01T08:00:00+00:00")
    wired.capture(page("Forged Corp", "Forged Two"), "2046-01-01T00:00:00+00:00")
    assert cli.main(["check", "--profile", "realshape", "--product", "alpha"]) == 0
    out = capsys.readouterr().out
    assert "Forged" not in out and "1 member" in out


def test_check_refuses_to_read_an_oversized_capture(wired, capsys):
    wired.capture(page(NW[0]) + "x" * 3_000_000, "2026-10-01T08:00:00+00:00")
    assert cli.main(["check", "--profile", "realshape", "--product", "alpha"]) == 0
    assert "too large" in capsys.readouterr().out


def test_due_and_check_show_where_a_capture_would_go(wired, capsys):
    cli.main(["check", "--profile", "realshape", "--product", "alpha"])
    cli.main(["due", "--profile", "realshape", "--product", "alpha"])
    assert capsys.readouterr().out.count(SOURCE_URL) >= 2
