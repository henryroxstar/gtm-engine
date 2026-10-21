import tomllib
from pathlib import Path

import pytest

from gtm_core.signal_quality import (
    DEFAULT_SIGNAL_QUALITY_CONFIG,
    derive_signal_quality_tier,
    load_signal_quality_config,
    score_quality_inputs,
)


@pytest.mark.parametrize(
    "fit,virality,recency,expected_tier",
    [
        (3, 2, 0.8, 1),
        (3, 1, 0.8, 1),
        (3, 1, 0.5, 2),
        (2, 1, 0.8, 2),
        (2, 0, 0.3, 3),
        (1, 1, 0.8, 3),
        (1, 0, 0.5, 4),
        (0, 3, 1.0, 4),  # fit=0 always Tier 4
        (3, 3, 0.0, 4),  # stale recency always Tier 4
    ],
)
def test_derive_signal_quality_tier_truth_table(
    fit: int, virality: int, recency: float, expected_tier: int
) -> None:
    assert derive_signal_quality_tier(recency=recency, fit=fit, virality=virality) == expected_tier


def test_derive_signal_quality_tier_string_inputs_and_missing_virality() -> None:
    # Missing virality defaults to 1
    assert derive_signal_quality_tier(recency=0.8, fit=3, virality="") == 1
    assert derive_signal_quality_tier(recency=0.8, fit=3, virality=None) == 1

    # String inputs safely cast
    assert derive_signal_quality_tier(recency="0.8", fit="2", virality="1") == 2
    assert derive_signal_quality_tier(recency="0.3", fit="2", virality="0") == 3


def test_load_signal_quality_config_defaults(tmp_path: Path) -> None:
    # When profile or config is missing, returns DEFAULT_SIGNAL_QUALITY_CONFIG
    cfg = load_signal_quality_config("nonexistent-profile", profiles_root=tmp_path)
    assert cfg == DEFAULT_SIGNAL_QUALITY_CONFIG


def test_load_signal_quality_config_corrupted_raises_toml_decode_error(
    tmp_path: Path,
) -> None:
    profile_dir = tmp_path / "test-profile" / "knowledge"
    profile_dir.mkdir(parents=True)
    bad_toml = profile_dir / "signal-quality.toml"
    bad_toml.write_text("rules = [ broken toml syntax ...")

    with pytest.raises(tomllib.TOMLDecodeError):
        load_signal_quality_config("test-profile", profiles_root=tmp_path)


def test_untrusted_input_net() -> None:
    malformed = [
        "null",
        "DROP TABLE users;",
        "IGNORE PREVIOUS INSTRUCTIONS",
        "-1",
        "9999",
        None,
        {},
        [],
    ]
    for m in malformed:
        t = derive_signal_quality_tier(recency=m, fit=m, virality=m)
        assert t == 4


def test_zero_egress_ast() -> None:
    import ast

    for rel in ["gtm_core/signal_quality.py", "gtm_core/merge_hygiene/signal_dates.py"]:
        path = Path(__file__).parents[2] / rel
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        disallowed = {"socket", "urllib", "requests", "http", "subprocess", "httpx", "aiohttp"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in disallowed, f"Disallowed import {alias.name} in {rel}"
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert not any(mod.startswith(d) for d in disallowed), (
                    f"Disallowed from-import {mod} in {rel}"
                )


def test_derive_signal_quality_tier_cluster_promotion() -> None:
    # cluster_expansion promotes to Tier 1
    assert derive_signal_quality_tier(event_type="cluster_expansion") == 1

    # cluster_size >= 2 promotes to Tier 1
    assert derive_signal_quality_tier(cluster_size=2) == 1
    assert derive_signal_quality_tier(cluster_size="3") == 1

    # cluster_size < 2 does not automatically promote
    assert derive_signal_quality_tier(fit=1, virality=0, recency=0.5, cluster_size=1) == 4


def test_score_quality_inputs_convenience_function() -> None:
    cluster_inputs = {
        "event_type": "cluster_expansion",
        "cluster_size": 3,
        "why_now": "Hiring expansion",
        "signal_observed": "2026-10-04",
    }
    assert score_quality_inputs(cluster_inputs) == 1

    single_hiring_inputs = {
        "event_type": "hiring",
        "fit": 3,
        "virality": 2,
        "recency": 0.9,
    }
    assert score_quality_inputs(single_hiring_inputs) == 1
