"""Sequence -> campaign -> product, with no default (``gtm_core.campaign_products``).

Fictional fixture data only. The property under test is the one outcome attribution depends on:
a sequence the map cannot place is ``unknown`` and reported — it is never quietly counted as the
default product, which would credit the default product with replies it did not earn.
"""

from __future__ import annotations

import pytest

from gtm_core import campaign_products as cp

CELLS = """\
[[sequence]]
id = "seq-a"
csv = "a.csv"
spec = "a.md"
campaign = "alpha-wave"

[[sequence]]
id = "seq-b"
csv = "b.csv"
spec = "b.md"
campaign = "beta-wave"

[[sequence]]
id = "seq-loose"
csv = "c.csv"
spec = "c.md"
"""


@pytest.fixture
def env(one_product_profiles, tmp_path, monkeypatch):
    content = tmp_path / "content"
    seq = content / "realshape" / "prospects" / "sequences"
    seq.mkdir(parents=True)
    (seq / "cells.toml").write_text(CELLS)
    (content / "realshape" / "plans" / "campaigns").mkdir(parents=True)
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(one_product_profiles))
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content))
    return content / "realshape" / "plans"


def test_an_unplaced_sequence_is_unknown_never_the_default(env):
    att = cp.attribute("realshape")
    assert set(att.by_sequence.values()) == {cp.UNKNOWN}
    assert len(att.gaps) == 3
    assert "alpha" not in att.by_sequence.values()


def test_the_map_places_campaigns_and_loose_sequences_by_slug_or_display_name(env):
    (env / cp.MAP_FILE).write_text(
        '[campaigns]\nalpha-wave = "alpha"\nbeta-wave = "Beta Ledger"\n\n'
        '[sequences]\n"seq-loose" = "alpha"\n'
    )
    att = cp.attribute("realshape")
    assert att.by_sequence == {"seq-a": "alpha", "seq-b": "beta", "seq-loose": "alpha"}
    assert att.gaps == []


def test_a_manifest_product_is_read_and_the_map_wins_over_it(env):
    (env / "campaigns" / "beta-wave.campaign.toml").write_text(
        'slug = "beta-wave"\nproduct = "Beta Ledger"\n'
    )
    assert cp.attribute("realshape").by_sequence["seq-b"] == "beta"
    (env / cp.MAP_FILE).write_text('[campaigns]\nbeta-wave = "alpha"\n')
    assert cp.attribute("realshape").by_sequence["seq-b"] == "alpha"


def test_an_unknown_product_in_the_map_refuses(env):
    (env / cp.MAP_FILE).write_text('[campaigns]\nalpha-wave = "gamma"\n')
    with pytest.raises(ValueError, match="alpha-wave"):
        cp.attribute("realshape")


def test_an_unreadable_map_refuses_rather_than_reading_as_empty(env):
    (env / cp.MAP_FILE).write_text("[campaigns\n")
    with pytest.raises(ValueError, match="unreadable"):
        cp.attribute("realshape")


def test_cli_exit_codes(env, capsys):
    assert cp._cli(["check", "--profile", "realshape"]) == 1
    assert "gap: seq-loose" in capsys.readouterr().out
    (env / cp.MAP_FILE).write_text(
        '[campaigns]\nalpha-wave = "alpha"\nbeta-wave = "beta"\n[sequences]\n"seq-loose" = "alpha"\n'
    )
    assert cp._cli(["check", "--profile", "realshape"]) == 0
    (env / cp.MAP_FILE).write_text('[campaigns]\nalpha-wave = "../x"\n')
    assert cp._cli(["check", "--profile", "realshape"]) == 2


def test_a_sequence_registered_under_two_campaigns_of_different_products_is_a_stop(env):
    (env.parent.parent / "realshape" / "prospects" / "sequences" / "cells.toml").write_text(
        '[[sequence]]\nid = "seq-a"\ncsv = "a.csv"\nspec = "a.md"\ncampaign = "alpha-wave"\n\n'
        '[[sequence]]\nid = "seq-a"\ncsv = "a.csv"\nspec = "a.md"\ncampaign = "beta-wave"\n'
    )
    (env / cp.MAP_FILE).write_text('[campaigns]\nalpha-wave = "alpha"\nbeta-wave = "beta"\n')
    with pytest.raises(ValueError, match="seq-a"):
        cp.attribute("realshape")


def test_a_map_whose_tables_are_the_wrong_type_is_a_value_error(env):
    (env / cp.MAP_FILE).write_text('campaigns = "alpha"\n')
    with pytest.raises(ValueError, match="tables"):
        cp.attribute("realshape")
