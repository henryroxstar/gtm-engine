"""gtm_core.content_quality.register_findings — the "too polished" check (2026-08-19).

The 2026-08-18 script was accurate, cleared Part A at 11/14 and claims at 8/8, and the operator's
verdict was still "too polished, doesn't feel authentic". Every existing gate passed. This check
mechanises three of the four countable tells that produced that reaction, so the failure is
visible rather than silent.

Advisory on purpose: register is a judgement, and a linter that blocked on it would be wrong more
often than the writer. The out-loud read in ``video-script``'s Register section is the real check.
"""

from __future__ import annotations

import pytest

from gtm_core.content_quality import extract_spoken, register_findings


def _findings(lines: list[str]) -> tuple[list[str], dict]:
    return register_findings(lines)


# --- the real defect -----------------------------------------------------------------------

#: The six [SPOKEN] lines of the presenter script the operator rejected on 2026-08-18.
SHIPPED = [
    "A guy asked his AI assistant to get him into a full gym class.",
    "It found a way. It cancelled someone else's spot.",
    "Nobody had locked that door. Every member could open it. He was just the first to try.",
    "This wasn't a hack. Nobody broke in. It used a door that was already open.",
    "The website could check who you were. It never asked what was acting on your behalf.",
    "That's not a gym problem. That's every system your AI touches. "
    "Verify who's acting, not just who's signed in.",
]


def test_the_shipped_script_is_flagged():
    findings, stats = _findings(SHIPPED)
    assert findings, "the script the operator rejected as 'too polished' must not read as clean"
    assert stats["connective_share"] == 0.0


def test_it_catches_the_exact_parallel_construction_the_operator_quoted():
    findings, stats = _findings(SHIPPED)
    assert stats["parallel_openings"] == 1
    hit = [f for f in findings if "parallel construction" in f]
    assert hit
    assert "That's not a gym problem." in hit[0]


def test_it_catches_the_missing_connective_tissue():
    findings, _ = _findings(SHIPPED)
    assert any("open with a connective" in f for f in findings)


# --- the fix reads clean -------------------------------------------------------------------


def test_a_spoken_register_rewrite_clears_the_check():
    rewritten = [
        "A guy asked his AI assistant to get him into a gym class that was already full.",
        "And it worked. It just cancelled someone else's spot to do it.",
        "But here's the part that should bother you — nobody had locked that door. "
        "Any member could have done the same thing. He was just first.",
        "So this wasn't a hack. Nobody broke in. The door was already open.",
        "Because the site could check who you were, and it never once asked "
        "what was acting on your behalf.",
        "And it's not just the gym. It's every app your agent can reach.",
    ]
    findings, stats = _findings(rewritten)
    assert findings == [], findings
    assert stats["connective_share"] >= 0.25
    assert stats["parallel_openings"] == 0


# --- individual rules ----------------------------------------------------------------------


def test_repeated_pronoun_openers_are_not_flagged_as_parallel():
    """ "It found a way. It cancelled someone else's spot." is ordinary speech, not a cadence."""
    _, stats = _findings(
        [
            "It found a way. It cancelled someone else's spot. And that is the whole story here.",
            "So nobody noticed for a while, which is the part that matters most of all.",
            "But the fix is small, honestly.",
        ]
    )
    assert stats["parallel_openings"] == 0


def test_a_demonstrative_mirror_is_flagged():
    findings, _ = _findings(
        [
            "That's not a small bug. That's the whole trust model failing at once.",
            "And it happens quietly, which is why nobody catches it until much later on.",
            "So you end up finding out from a customer instead.",
            "But the fix is small.",
        ]
    )
    assert any("parallel construction" in f for f in findings)


def test_metronomic_sentence_length_is_flagged():
    findings, stats = _findings(
        [
            "And the door was open. So nobody had locked it. But every member could see.",
            "And he was just first. So nothing else was hit. But it still counted.",
        ]
    )
    assert stats["sentence_len_stdev"] < 2.5
    assert any("metronomic" in f for f in findings)


def test_a_short_script_skips_the_statistical_tells():
    """Below five sentences the numbers are noise — reporting them would be false precision."""
    findings, stats = _findings(["One line only.", "And a second."])
    assert findings == []
    assert "skipped" in stats


# --- extraction ----------------------------------------------------------------------------


def test_extract_spoken_reads_the_authored_script_format():
    text = (
        '- **[SPOKEN]** "A guy asked his AI assistant to get him in."\n'
        "- **[VISUAL]** office at dusk\n"
        '- **[SPOKEN]** "It found a way."\n'
    )
    assert extract_spoken(text) == [
        "A guy asked his AI assistant to get him in.",
        "It found a way.",
    ]


def test_extract_spoken_handles_a_line_wrapped_across_rows():
    """Authored scripts wrap long spoken lines — the extractor must not truncate at the newline."""
    text = '- **[SPOKEN]** "Nobody had locked that door. Every member\ncould open it."\n'
    assert extract_spoken(text) == ["Nobody had locked that door. Every member could open it."]


def test_extract_spoken_handles_typographic_quotes():
    text = "- **[SPOKEN]** “It found a way.”\n"
    assert extract_spoken(text) == ["It found a way."]


@pytest.mark.parametrize("word", ["and", "so", "but", "because", "look", "now"])
def test_the_connective_set_covers_the_words_people_actually_start_with(word):
    from gtm_core.content_quality import _CONNECTIVES

    assert word in _CONNECTIVES


# --- K3: captions extraction, pronoun-lead tell, and register_check on captions-only scripts ---


def test_extract_captions_reads_varied_formats_and_skips_placeholders():
    from gtm_core.content_quality import extract_captions

    text = "\n".join(
        [
            "- `[CAPTION]` **Before sunrise, he shipped a month of work.**",
            '- [CAPTION] "So he asked the tool everyone uses."',
            "- **[CAPTION]** **It sounded like everyone.**",
            "- `[CAPTION]` — none · no caption by design",
            "- `[CAPTION]` — silent by design",
            "- `[CAPTION]` **In your voice. Someone's waiting to hear it.**",
        ]
    )
    assert extract_captions(text) == [
        "Before sunrise, he shipped a month of work.",
        "So he asked the tool everyone uses.",
        "It sounded like everyone.",
        "In your voice. Someone's waiting to hear it.",
    ]


def test_pronoun_lead_runs_detects_runs_of_three_or_more():
    from gtm_core.content_quality.register import pronoun_lead_runs

    lines_with_run = [
        "Before sunrise, he shipped a month of work.",
        "So he asked the tool everyone uses.",
        "It sounded like everyone.",
        "He deleted it and went to work.",
        "Someone went looking for him.",
        "In your voice. Someone's waiting to hear it.",
    ]
    runs = pronoun_lead_runs(lines_with_run)
    assert len(runs) == 1
    assert len(runs[0]) == 4  # So he, It, He, Someone

    lines_without_run = [
        "Before sunrise, he shipped a month of work.",
        "The prompt asked for his voice.",
        "Every phrase sounded borrowed.",
        "He deleted it and went to work.",
        "Six months with nothing published.",
        "This one sounded like him.",
    ]
    assert pronoun_lead_runs(lines_without_run) == []


def test_register_check_reads_captions_on_captions_only_script(tmp_path):
    from gtm_core.content_quality import register_check

    content_root = tmp_path / "content"
    scripts_dir = content_root / "example" / "scripts"
    scripts_dir.mkdir(parents=True)

    # 1. Captions-only script with a run of 4 pronoun-lead captions
    captions_script = "\n".join(
        [
            "source_item: ci-captions",
            "claims_verified: 0/0 · log: none: no external claims",
            "",
            "- `[CAPTION]` **Before sunrise, he shipped a month of work.**",
            "- `[CAPTION]` **So he asked the tool everyone uses.**",
            "- `[CAPTION]` **It sounded like everyone.**",
            "- `[CAPTION]` **He deleted it and went to work.**",
            "- `[CAPTION]` **Someone went looking for him.**",
            "- `[CAPTION]` **In your voice. Someone's waiting to hear it.**",
        ]
    )
    (scripts_dir / "2026-09-07-captions.md").write_text(captions_script, encoding="utf-8")

    result = register_check("example", "ci-captions", content_root=content_root)
    assert result["proceed"] is True
    assert result["checks"]["spoken_lines"] == 0
    assert result["checks"]["captions"] == 6
    assert result["checks"]["pronoun_lead_runs"] == 1
    assert any("consecutive captions open on a pronoun subject" in w for w in result["warnings"])

    # 2. Script with [SPOKEN] lines reads ONLY spoken lines
    mixed_script = "\n".join(
        [
            "source_item: ci-mixed",
            "claims_verified: 0/0 · log: none: no external claims",
            "",
            '- **[SPOKEN]** "A guy asked his assistant to get him in."',
            '- **[SPOKEN]** "And it worked right away."',
            '- **[SPOKEN]** "So nobody noticed for a week."',
            '- **[SPOKEN]** "Because the door was wide open."',
            '- **[SPOKEN]** "And that is every system your AI touches."',
            "- `[CAPTION]` **He went there.**",
            "- `[CAPTION]` **It did that.**",
            "- `[CAPTION]` **She said this.**",
        ]
    )
    (scripts_dir / "2026-09-08-mixed.md").write_text(mixed_script, encoding="utf-8")

    result_mixed = register_check("example", "ci-mixed", content_root=content_root)
    assert result_mixed["proceed"] is True
    assert result_mixed["checks"]["spoken_lines"] == 5
    assert "captions" not in result_mixed["checks"]
