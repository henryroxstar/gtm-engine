from pathlib import Path


def test_env_example_covers_founder_keys() -> None:
    env_example = Path(__file__).resolve().parents[2] / ".env.example"
    text = env_example.read_text(encoding="utf-8")
    assert "GTM_CONTENT_ROOT" in text, ".env.example must document GTM_CONTENT_ROOT"
    assert "GTM_PROFILES_ROOT" in text, ".env.example must document GTM_PROFILES_ROOT"
    assert "INVERTED" not in text, ".env.example scorecard flag comment must not contain 'INVERTED'"
