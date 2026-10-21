import json

from gtm_core import firmographics
from gtm_core import prospects_state as ps


def _write_latest(root, profile, items):
    p = ps.latest_path(profile, content_root=root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"kind": "prospects", "profile": profile, "items": items}), encoding="utf-8"
    )
    return p


def test_queue_lists_accounts_with_blank_target_fields(tmp_path, capsys):
    _write_latest(
        tmp_path,
        "acme",
        [
            {
                "id": "1",
                "company": "Complete",
                "industry": "A",
                "country": "B",
                "city": "C",
                "employees_range": "D",
                "description": "E",
            },
            {
                "id": "2",
                "company": "MissingInd",
                "industry": "",
                "country": "B",
                "city": "C",
                "employees_range": "D",
                "description": "E",
            },
            {
                "id": "3",
                "company": "MissingCity",
                "industry": "A",
                "country": "B",
                "city": "",
                "employees_range": "D",
                "description": "E",
            },
            {
                "id": "4",
                "company": "SpaceDesc",
                "industry": "A",
                "country": "B",
                "city": "C",
                "employees_range": "D",
                "description": "   ",
            },
        ],
    )

    firmographics.queue_cmd("acme", limit=None, content_root=tmp_path)

    out, _ = capsys.readouterr()
    lines = [json.loads(line) for line in out.strip().split("\n") if line.strip()]
    assert len(lines) == 3
    assert {item["id"] for item in lines} == {"2", "3", "4"}


def test_apply_clean_rows(tmp_path):
    _write_latest(tmp_path, "acme", [{"id": "1", "company": "Target"}])
    payload = json.dumps([{"id": "1", "company": "Target", "country": "US"}])

    firmographics.apply_cmd("acme", payload, content_root=tmp_path)

    data = ps.load_latest("acme", content_root=tmp_path)
    assert data["items"][0]["country"] == "United States"
    assert data["items"][0]["firmo_source"] == "apply"


def test_apply_from_file_path(tmp_path):
    _write_latest(tmp_path, "acme", [{"id": "1", "company": "Target"}])
    payload_file = tmp_path / "payload.json"
    payload_file.write_text(
        json.dumps([{"id": "1", "company": "Target", "country": "US"}]), encoding="utf-8"
    )

    firmographics.apply_cmd("acme", str(payload_file), content_root=tmp_path)

    data = ps.load_latest("acme", content_root=tmp_path)
    assert data["items"][0]["country"] == "United States"
    assert data["items"][0]["firmo_source"] == "apply"


def test_apply_refused_rows_and_conflicts_to_csv(tmp_path):
    _write_latest(
        tmp_path,
        "acme",
        [
            {"id": "1", "company": "Target", "industry": "Old"},  # conflict
            {"id": "2", "domain": "dup.example"},
            {"id": "3", "domain": "dup.example"},
        ],
    )

    payload = json.dumps(
        [
            {"id": "1", "company": "Target", "industry": "New", "country": "us"},
            {"domain": "dup.example", "company": "Dup", "industry": "New"},  # refused
        ]
    )

    firmographics.apply_cmd("acme", payload, content_root=tmp_path)

    # Check CSV
    review_files = list((tmp_path / "acme/prospects").glob("firmographics-review-*.csv"))
    assert len(review_files) == 1

    with review_files[0].open() as f:
        content = f.read()
        assert "conflict" in content
        assert "refused" in content
        assert "Old" in content
        assert "New" in content
        assert "dup.example" in content


def test_apply_malformed_payload(capsys):
    assert firmographics.apply_cmd("acme", '{"not": "array"}') == 2
    out, err = capsys.readouterr()
    assert "Schema validation failed" in err


def test_apply_dry_run_leaves_bytes_unchanged(tmp_path):
    p = _write_latest(tmp_path, "acme", [{"id": "1"}])
    orig_bytes = p.read_bytes()

    payload = json.dumps([{"id": "1", "industry": "New"}])
    firmographics.apply_cmd("acme", payload, dry_run=True, content_root=tmp_path)

    assert p.read_bytes() == orig_bytes


def test_accept_overwrites_only_yes_rows(tmp_path):
    _write_latest(
        tmp_path,
        "acme",
        [
            {"id": "1", "company": "A", "industry": "Old"},
            {"id": "2", "company": "B", "industry": "Old2"},
        ],
    )

    # Create a review CSV
    csv_path = tmp_path / "review.csv"
    with csv_path.open("w", newline="") as f:
        f.write("accept,type,domain,id,company,field,existing,incoming,reason\n")
        f.write("yes,conflict,,1,A,industry,Old,New,\n")
        f.write("no,conflict,,2,B,industry,Old2,New2,\n")

    firmographics.accept_cmd("acme", str(csv_path), content_root=tmp_path)

    data = ps.load_latest("acme", content_root=tmp_path)
    item1 = next(i for i in data["items"] if i["id"] == "1")
    item2 = next(i for i in data["items"] if i["id"] == "2")

    assert item1["industry"] == "New"
    assert item1["firmo_source"] == "accept"
    assert item2["industry"] == "Old2"


def test_apply_hq_country_conflicts_first(tmp_path):
    _write_latest(
        tmp_path,
        "acme",
        [
            {"id": "1", "industry": "OldInd", "country": "OldC"},
        ],
    )

    payload = json.dumps(
        [
            {"id": "1", "industry": "NewInd", "country": "us"},
        ]
    )

    firmographics.apply_cmd("acme", payload, content_root=tmp_path)

    review_files = list((tmp_path / "acme/prospects").glob("firmographics-review-*.csv"))
    with review_files[0].open() as f:
        lines = f.readlines()
        assert "country" in lines[1]
        assert "industry" in lines[2]


def test_queue_bytes_unchanged(tmp_path):
    p = _write_latest(
        tmp_path,
        "acme",
        [
            {"id": "1", "industry": ""},
        ],
    )
    orig = p.read_bytes()
    firmographics.queue_cmd("acme", limit=None, content_root=tmp_path)
    assert p.read_bytes() == orig
