"""Guards for gtm_core.fictionalize — the tool that makes §R9 the cheap path.

The point of this module is that a fixture author reaches for it INSTEAD of the real row,
so it has to be trustworthy on exactly two counts: the fake must carry no identity, and it
must still exercise whatever property the test was reaching for. A generator that quietly
drops the property is worse than no tool, because the fixture then passes while testing
nothing — so each property gets its own test rather than one "looks fictional" assertion.

Inputs here are invented, never a real name that leaked.
"""

from __future__ import annotations

import pytest

from gtm_core.fictionalize import (
    company,
    domain,
    email,
    fictionalize,
    infer_kind,
    person,
    phone,
)
from gtm_core.slugify import slug


def test_the_same_input_always_yields_the_same_fake():
    """Determinism is what lets a fixture be built across sessions and still join: the
    company column and the email column must agree without recording the mapping."""
    assert company("Zylophane Robotics") == company("Zylophane Robotics")
    assert email("a.b@zylophane.com") == email("a.b@zylophane.com")


def test_different_inputs_do_not_collide():
    names = {company(f"Zylophane{i} Robotics") for i in range(25)}
    assert len(names) > 15, "the word stock is too small to keep fixtures distinguishable"


# --- the properties a name-handling test actually asserts on -------------------------- #


@pytest.mark.parametrize(
    "real",
    ["Zylophane Systems", "Quorix", "Vantabrix Labs", "Morrows"],
)
def test_a_trailing_sibilant_survives(real):
    """The possessive a renderer writes depends on it ("Acme's" vs "Atlas'")."""
    assert company(real).lower().rstrip(".").endswith(("s", "x", "z", "ch", "sh"))


@pytest.mark.parametrize("real", ["Zylophane Robotic", "Vantabrix Health"])
def test_a_non_sibilant_ending_survives(real):
    assert not company(real).lower().rstrip(".").endswith(("s", "x", "z", "ch", "sh"))


def test_a_generated_name_is_always_words_not_mangled_stems():
    """An earlier draft forced the ending by editing the chosen word and produced
    "Logisticn" and "Healths". A fixture that reads as a typo gets "fixed" back to the
    real name by the next person."""
    for i in range(40):
        for word in company(f"Zylophane{i} Solutions").replace("&", " ").split():
            assert word.isalnum() or word.rstrip(".").isalnum()
            assert not word.endswith("cn")


def test_a_leading_article_survives():
    assert company("The Zylophane Group").lower().startswith("the ")


def test_an_ampersand_survives():
    assert "&" in company("Zylophane & Vantabrix")


def test_an_embedded_digit_survives():
    assert any(c.isdigit() for c in company("Z2 Risk Solutions"))


def test_a_corporate_suffix_is_preserved_verbatim():
    """ "Ltd" and "Ltd." are different strings to a parser, so the tool must not
    canonicalise one into the other."""
    assert company("Zylophane Ltd").endswith(" Ltd")
    assert company("Zylophane Ltd.").endswith(" Ltd.")
    assert company("Zylophane Pte Ltd").endswith(" Pte Ltd")


def test_casing_class_survives():
    assert company("ZYLOPHANE").isupper()
    assert company("zylophane").islower()


def test_a_cjk_name_stays_cjk():
    """gtm_core.slugify has a hash fallback for a name that strips to empty under ASCII
    normalisation; a fixture for it must still strip to empty."""
    out = company("株式会社図書")
    assert not out.isascii()
    assert slug(out), "the slug fallback must still produce something"


def test_word_count_survives():
    assert len(company("Zylophane Vantabrix Quorix").split()) == 3


# --- contact shapes -------------------------------------------------------------------- #


def test_an_email_lands_on_a_reserved_domain():
    assert email("chris@zylophane.com").endswith(".example")


@pytest.mark.parametrize(
    "real,marker",
    [("j.doe@zylophane.com", "."), ("j_doe@zylophane.com", "_")],
)
def test_the_local_part_separator_survives(real, marker):
    assert marker in email(real).split("@")[0]


def test_a_trailing_digit_in_the_local_part_survives():
    assert email("chris7@zylophane.com").split("@")[0].endswith("7")


def test_an_academic_domain_stays_academic():
    """gtm_core.account_integrity matches on a real .edu/.ac.xx suffix, so a fixture for
    that rule must keep one."""
    assert ".edu" in domain("quorix.edu")


def test_domain_label_count_survives():
    assert domain("share.zylophane.com").count(".") == domain("a.b.example").count(".")


def test_a_phone_lands_in_the_reserved_range():
    assert "5550" in phone("+81 90-4471-2288")
    assert "555-01" in phone("415-222-9876")


def test_e164_digit_count_survives():
    real = "+81 90-4471-2288"
    assert sum(c.isdigit() for c in phone(real)) == sum(c.isdigit() for c in real)


def test_a_person_keeps_word_count_and_case():
    assert len(person("Jane Smith").split()) == 2
    assert person("JANE SMITH").isupper()


# --- the inference the `auto` mode uses ------------------------------------------------ #


@pytest.mark.parametrize(
    "value,kind",
    [
        ("chris@zylophane.com", "email"),
        ("+65 8123 4567", "phone"),
        ("zylophane.com", "domain"),
        ("Jane Smith", "person"),
        ("Zylophane Systems Ltd", "company"),
    ],
)
def test_kind_inference(value, kind):
    assert infer_kind(value) == kind


def test_auto_mode_routes_through_inference():
    assert fictionalize("chris@zylophane.com", "auto").endswith(".example")


# --- the output must survive the gate it exists to satisfy ----------------------------- #


def test_generated_values_pass_the_pii_gate(tmp_path):
    """The whole point: a fixture built with this tool is committable. If a generator
    ever emits something the checker rejects, the tool is worse than useless — people
    would go back to typing the real value."""
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("pii_check", root / "tests/lint/pii_check.py")
    pii_check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pii_check)

    lines = [f'A = "{company(f"Zylophane{i} Solutions")}"' for i in range(20)]
    lines += [f'B = "{email(f"chris{i}@zylophane.com")}"' for i in range(20)]
    lines += [f'C = "{phone("+81 90-4471-2288")}"', f'D = "{person("Jane Smith")}"']
    lines += [f'E = "{domain("share.zylophane.com")}"', f'F = "{domain("quorix.edu")}"']
    probe = tmp_path / "fixture.py"
    probe.write_text("\n".join(lines), encoding="utf-8")
    assert pii_check.main([str(probe)]) == 0
