"""Tests for gtm_core.slugify — the one canonical account-folder slug."""

from __future__ import annotations

from gtm_core.slugify import main, slug


def test_ascii_name_with_spaces():
    assert slug("A Room for Rent") == "a-room-for-rent"


def test_ascii_name_no_spaces():
    assert slug("Skyloom") == "skyloom"


def test_dotted_name_strips_punctuation():
    assert slug("Quorum.AI") == "quorumai"


def test_uppercase_and_extra_whitespace_normalized():
    assert slug("  ORVA   Ltd  ") == "orva-ltd"


def test_pure_non_ascii_name_falls_back_to_stable_hash_token():
    result = slug("日本語会社")
    assert result.startswith("co-")
    assert len(result) == len("co-") + 8
    # deterministic — same input, same token, every call
    assert slug("日本語会社") == result


def test_empty_and_whitespace_only_name_returns_empty():
    assert slug("") == ""
    assert slug("   ") == ""


def test_punctuation_only_name_falls_back_to_hash_token():
    result = slug("!!!")
    assert result.startswith("co-")


def test_cli_prints_slug_and_exits_zero(capsys):
    code = main(["A Room for Rent"])
    assert code == 0
    assert capsys.readouterr().out.strip() == "a-room-for-rent"


def test_cli_wrong_arg_count_exits_nonzero(capsys):
    assert main([]) != 0
    assert main(["a", "b"]) != 0
