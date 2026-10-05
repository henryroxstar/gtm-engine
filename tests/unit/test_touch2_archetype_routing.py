import pytest

from gtm_core.signal_quality import touch2_archetype_for_fit


@pytest.mark.parametrize(
    "fit,expected_archetype",
    [
        (3, "Gift"),
        (2, "Friction Point"),
        (1, "Sanity Check"),
        (0, "None"),
        ("3", "Gift"),
        ("2", "Friction Point"),
        ("1", "Sanity Check"),
        ("0", "None"),
        ("", "None"),
        (None, "None"),
    ],
)
def test_touch2_archetype_for_fit(fit: object, expected_archetype: str) -> None:
    assert touch2_archetype_for_fit(fit) == expected_archetype  # type: ignore[arg-type]
