import csv
import tomllib
from pathlib import Path

from gtm_core.merge_hygiene.api import check_row
from gtm_core.signal_record import check_record


def test_no_gate_loosened():
    base = Path("tests/fixtures/r36")
    csv_path = base / "rejected.csv"
    toml_path = base / "allowed_flips.toml"

    assert csv_path.is_file(), f"Missing required regression fixture: {csv_path}"

    try:
        flips = tomllib.loads(toml_path.read_text(encoding="utf-8")).get("flips", {})
    except Exception:
        flips = {}

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            contact_id = row.get("contact_id")

            findings = check_row(row) + check_record(row)
            is_blocked = any(f.level == "block" for f in findings)

            if contact_id in flips:
                assert not is_blocked, f"{contact_id} is in allowed_flips but still fails!"
            else:
                assert is_blocked, (
                    f"Row {contact_id} passed but is not in allowed_flips.toml! No gates may be loosened."
                )
