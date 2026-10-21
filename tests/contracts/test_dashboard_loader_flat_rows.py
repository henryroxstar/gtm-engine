"""Round-2 loose ends at the page boundary: what a hand-edited or older file must not break.

Each test is the regression for one finding that sat in a file no fix agent owned:

* a refused figure is a dash on the Results tab too, never a ``0`` (the Emails table already did);
* a campaign manifest that is not UTF-8, or names a sequence by number, must not abort the page;
* ``row_id`` is total — an integer past the interpreter's digit limit is "no id", not a raise;
* a payload row with no ``prospects`` list still has its ``delivered`` count, and its name;
* a refused inventory write (a symlinked sidecar) must not undo a finished consolidation.
"""

from __future__ import annotations

from gtm_core import email_campaign_dashboard as gd
from gtm_core.campaigns_dashboard import _campaigns_dir, _id_set, _load_manifests
from gtm_core.email_campaign_dashboard.views_results import _fig, _sequence_results_table
from gtm_core.page_inputs import write_inventory_or_warn
from gtm_core.page_inputs_io import inventory_path
from gtm_core.prospects_dashboard import _normalize_seq
from gtm_core.sequence_snapshot_format import row_id
from tests.test_email_campaign_dashboard import _seed


def test_a_refused_figure_is_a_dash_on_the_results_tab_never_a_zero():
    assert "—" in _fig({"refused": ["sent"]}, "sent", 0)
    assert _fig({"refused": ["replied"]}, "sent", 1234) == "1,234"
    assert _fig({}, "sent", 1234) == "1,234"
    html = _sequence_results_table(
        {"sequences": [{"title": "Alpha", "live": {"sent": 0, "replied": 3, "refused": ["sent"]}}]}
    )
    first_cells = html.split("<strong>Alpha</strong></td>", 1)[1].split("</td>")[:2]
    assert "—" in first_cells[0], "the refused count must not read as 0"
    assert ">3<" in first_cells[1] + "<", "a usable neighbour is still a number"


def test_a_manifest_that_is_not_utf8_is_skipped_like_a_malformed_one(tmp_path):
    profile = _seed(tmp_path)
    folder = _campaigns_dir(profile, tmp_path)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "broken.campaign.toml").write_bytes(b"slug = '\xff\xfe'\n")
    (folder / "good.campaign.toml").write_text('slug = "good"\n', encoding="utf-8")
    slugs = [m["slug"] for m in _load_manifests(profile, tmp_path)]
    assert "good" in slugs and "broken" not in slugs


def test_manifest_sequence_ids_are_text_and_a_bare_string_is_not_its_characters():
    assert _id_set([7, "S1", " ", None, 2.5, ["x"]]) == {"7", "S1"}
    assert _id_set("abc") == set()
    assert _id_set(None) == set()


def test_a_numeric_sequence_in_a_manifest_does_not_abort_the_page(tmp_path):
    profile = _seed(tmp_path)
    folder = _campaigns_dir(profile, tmp_path)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "nums.campaign.toml").write_text(
        'slug = "nums"\nsequences = [7, "S-text"]\n', encoding="utf-8"
    )
    page = gd.render_html(gd.build_model(profile, tmp_path))
    assert "nums" in page


def test_row_id_is_total_for_an_integer_past_the_digit_limit():
    huge = 10**5000
    assert row_id({"sequenceId": huge}) == ""
    assert row_id({"sequenceId": 7}) == "7"


def test_a_row_with_no_prospects_list_keeps_delivered_and_its_name():
    row = _normalize_seq(
        {
            "sequenceId": "S1",
            "sequenceName": "Alpha",
            "emails": {"status": {"delivered": 40}},
        }
    )
    assert row["delivered"] == 40 and row["name"] == "Alpha"
    # An explicit top-level figure still wins over the nested block.
    flat = _normalize_seq({"id": "S2", "delivered": 5, "emails": {"status": {"delivered": 40}}})
    assert flat["delivered"] == 5


def test_a_refused_inventory_write_is_reported_and_leaves_the_link_target_alone(tmp_path, capsys):
    page = tmp_path / "master-list.csv"
    page.write_text("a,b\n")
    victim = tmp_path / "victim.json"
    victim.write_text("keep me")
    sidecar = inventory_path(page)
    sidecar.symlink_to(victim)
    out = write_inventory_or_warn(page, (tmp_path, ["*.nothing"]), scope="x")
    assert out is None
    assert victim.read_text() == "keep me"
    assert "inventory not written" in capsys.readouterr().err
