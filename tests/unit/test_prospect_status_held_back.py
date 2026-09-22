"""People the build holds out for an address check must not vanish from the status block (UX-03)."""

from __future__ import annotations

from gtm_core import prospect_status_cli as cli


def test_held_back_contacts_get_a_line_only_when_there_are_some() -> None:
    assert "Held back for a check" not in cli._format_report({"ready_to_send": 2}, 0, 0)
    block = cli._format_report({"ready_to_send": 2}, 0, 0, held_back_count=2)
    (line,) = [ln for ln in block.splitlines() if ln.startswith("Held back for a check")]
    assert line.split()[5] == "2"


def test_held_back_count_reads_the_builds_own_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    assert cli._held_back_contacts("qa-sandbox") == 0
    from gtm_core.prospects_consolidate.paths import needs_verification_path

    path = needs_verification_path("qa-sandbox")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "email,company\navery.quill@northwind.example,Northwind Robotics\n"
        "jules.marsh@tailspinhealth.example,Tailspin Health\n",
        encoding="utf-8",
    )
    assert cli._held_back_contacts("qa-sandbox") == 2
