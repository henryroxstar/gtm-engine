"""Unit tests for the content linter (spec §13.1)."""

import importlib

import content_linter
import pytest
from content_linter import lint, lint_prose_quality, main, passes


def _rules(asset):
    return {v.rule for v in lint(asset) if v.severity == "error"}


@pytest.fixture
def synthetic_denylist(tmp_path, monkeypatch):
    """Inject a fake tenant denylist + reload, so the safe-to-share tests carry no
    real tenant token and pass in both the private repo and the public cut (where
    the real denylist file is excluded → empty)."""
    f = tmp_path / "denylist.txt"
    f.write_text("token: acmecorp\nhost: internal\\.example\\.test\n", encoding="utf-8")
    monkeypatch.setenv("GTM_SAFE_SHARE_DENYLIST_FILE", str(f))
    importlib.reload(content_linter)
    yield content_linter
    monkeypatch.delenv("GTM_SAFE_SHARE_DENYLIST_FILE", raising=False)
    importlib.reload(content_linter)


# ── CLI: --safe-to-share-file (file form of the safe-to-share check) ───────────
# Lets skills lint a raw .md/text article or podcast script via an approved command
# instead of inline `python -c`, which the least-privilege policy denies.


def test_safe_to_share_file_clean_passes(tmp_path):
    f = tmp_path / "article.md"
    f.write_text("We shipped the two-gate approval model on day 2.", encoding="utf-8")
    assert main(["--safe-to-share-file", str(f)]) == 0


def test_safe_to_share_file_tenant_name_fails(tmp_path, synthetic_denylist):
    f = tmp_path / "article.md"
    f.write_text("Acmecorp was the pilot customer for this build.", encoding="utf-8")
    assert synthetic_denylist.main(["--safe-to-share-file", str(f)]) == 1


def test_safe_to_share_file_credential_reference_fails(tmp_path):
    f = tmp_path / "script.md"
    f.write_text("Set BACKEND_JWT_SECRET in Doppler before deploying.", encoding="utf-8")
    assert main(["--safe-to-share-file", str(f)]) == 1


def test_safe_to_share_file_internal_hostname_fails(tmp_path, synthetic_denylist):
    f = tmp_path / "runbook.md"
    f.write_text("POST the payload to https://api.internal.example.test/v1.", encoding="utf-8")
    assert synthetic_denylist.main(["--safe-to-share-file", str(f)]) == 1


def test_safe_to_share_file_placeholder_host_passes(tmp_path):
    f = tmp_path / "runbook.md"
    f.write_text("POST the payload to https://api.example.com/v1.", encoding="utf-8")
    assert main(["--safe-to-share-file", str(f)]) == 0


def test_linkedin_carousel_clean_passes():
    a = {
        "platform": "linkedin",
        "format": "carousel",
        "hook": "Why agent identity is the missing layer",
        "body": "x" * 1500,
        "slides": ["s"] * 10,
        "hashtags": ["#ai", "#trust"],
    }
    assert passes(a)
    assert lint(a) == []


def test_linkedin_hook_too_long_errors():
    a = {"platform": "linkedin", "format": "text", "hook": "h" * 141, "body": "x" * 1500}
    assert not passes(a)
    assert "li.hook" in _rules(a)


def test_linkedin_body_url_errors():
    a = {
        "platform": "linkedin",
        "format": "text",
        "hook": "ok",
        "body": "read more at https://example.com today",
    }
    assert "li.body_url" in _rules(a)


def test_linkedin_too_many_hashtags_errors():
    a = {
        "platform": "linkedin",
        "format": "text",
        "hook": "ok",
        "body": "x" * 1500,
        "hashtags": ["#a", "#b", "#c"],
    }
    assert "li.hashtags" in _rules(a)


def test_linkedin_carousel_slide_count_errors():
    assert "li.carousel_slides" in _rules(
        {
            "platform": "linkedin",
            "format": "carousel",
            "hook": "ok",
            "body": "x" * 1500,
            "slides": ["s"] * 5,
        }
    )
    assert "li.carousel_slides" in _rules(
        {
            "platform": "linkedin",
            "format": "carousel",
            "hook": "ok",
            "body": "x" * 1500,
            "slides": ["s"] * 13,
        }
    )


def test_linkedin_body_len_is_warn_not_error():
    a = {"platform": "linkedin", "format": "text", "hook": "ok", "body": "short"}
    assert passes(a)  # body-length is advisory
    assert any(v.rule == "li.body_len" and v.severity == "warn" for v in lint(a))


def test_x_thread_clean_passes():
    a = {
        "platform": "x",
        "format": "thread",
        "tweets": ["1/ the hook stands alone", "2/ point", "3/ cta"],
    }
    assert passes(a)


def test_x_first_tweet_link_errors():
    a = {"platform": "x", "format": "thread", "tweets": ["see https://x.com/thread", "2/ more"]}
    assert "x.first_link" in _rules(a)


def test_x_tweet_over_280_errors():
    a = {"platform": "x", "format": "single", "tweets": ["z" * 281]}
    assert "x.tweet_len" in _rules(a)


def test_x_single_clean_passes():
    a = {"platform": "x", "format": "single", "tweets": ["A sharp standalone take on agent trust."]}
    assert passes(a)


def test_x_thread_provided_as_body_is_not_silently_clean():
    # content-studio MUST emit `tweets` (an array) for X. If a whole thread is shoved into
    # `body` instead, the linter's fallback treats it as one tweet — and an oversized blob is
    # still caught as a tweet-length error rather than passing silently.
    a = {"platform": "x", "format": "thread", "body": "z" * 400}
    assert not passes(a)
    assert "x.tweet_len" in _rules(a)


def test_instagram_reel_duration_bounds():
    assert passes({"platform": "instagram", "format": "reel", "caption": "c", "duration_s": 45})
    assert "ig.reel_dur" in _rules(
        {"platform": "instagram", "format": "reel", "caption": "c", "duration_s": 120}
    )
    assert "ig.reel_dur" in _rules(
        {"platform": "instagram", "format": "reel", "caption": "c", "duration_s": 10}
    )


def test_instagram_reel_missing_duration_errors():
    a = {"platform": "instagram", "format": "reel", "caption": "c"}  # duration_s absent
    assert not passes(a)
    assert "ig.reel_dur" in _rules(a)


def test_instagram_reel_clean_passes():
    a = {
        "platform": "instagram",
        "format": "reel",
        "hook": "The trust gap in 7 seconds",
        "caption": "Why agent identity is the real bottleneck #ai",
        "duration_s": 60,
        "body": "0-2s hook · 2-8s tension · 8-N payoff · CTA",
    }
    assert passes(a)


def test_instagram_carousel_clean_passes():
    a = {
        "platform": "instagram",
        "format": "carousel",
        "caption": "Agent identity, explained #ai #trust",
        "slides": ["s"] * 8,
    }
    assert passes(a)


def test_instagram_caption_required():
    assert "ig.caption" in _rules(
        {"platform": "instagram", "format": "carousel", "caption": "", "slides": ["s"] * 8}
    )


def test_instagram_carousel_slide_count():
    assert "ig.carousel_slides" in _rules(
        {"platform": "instagram", "format": "carousel", "caption": "c", "slides": ["s"] * 11}
    )


def test_unknown_platform_errors():
    assert not passes({"platform": "tiktok"})


def test_linkedin_infographic_clean_passes():
    a = {
        "platform": "linkedin",
        "format": "infographic",
        "hook": "70% of AI systems have more access than humans",
        "body": "x" * 800,
        "key_points": ["76% over-privileged", "2.2x incident rate for confident orgs"],
        "tone": "data-driven",
        "hashtags": ["#ai"],
    }
    assert passes(a)


def test_linkedin_infographic_hook_too_long_errors():
    a = {
        "platform": "linkedin",
        "format": "infographic",
        "hook": "h" * 141,
        "body": "x" * 800,
        "key_points": ["stat"],
        "hashtags": [],
    }
    assert not passes(a)
    assert "li.hook" in _rules(a)


def test_linkedin_infographic_handwritten_too_many_hashtags_errors():
    a = {
        "platform": "linkedin",
        "format": "infographic-handwritten",
        "hook": "The AI Governance P&L Formula",
        "body": "x" * 500,
        "key_points": ["R.C. = Regulatory Cost"],
        "hashtags": ["#a", "#b", "#c"],
    }
    assert not passes(a)
    assert "li.hashtags" in _rules(a)


def test_linkedin_infographic_missing_key_points_warns():
    a = {
        "platform": "linkedin",
        "format": "infographic",
        "hook": "ok",
        "body": "x" * 500,
        "hashtags": [],
    }
    assert passes(a)  # warn only, not error
    assert any(v.rule == "li.infographic_key_points" and v.severity == "warn" for v in lint(a))


# ── prose quality: acronym-unexpanded ───────────────────────────────────────


def test_acronym_unexpanded_warns():
    text = "A survey out of UIUC, Meta, and Stanford named the thing I built."
    assert any(v.rule == "prose.acronym_unexpanded" for v in lint_prose_quality(text))


def test_acronym_expanded_parenthetical_after_passes():
    text = "A survey out of the University of Illinois (UIUC) named the thing I built."
    assert not any(v.rule == "prose.acronym_unexpanded" for v in lint_prose_quality(text))


def test_acronym_expanded_parenthetical_before_passes():
    text = "A survey out of UIUC (University of Illinois) named the thing I built."
    assert not any(v.rule == "prose.acronym_unexpanded" for v in lint_prose_quality(text))


def test_acronym_allowlist_never_flagged():
    text = "The AI product ships an API and a CRM sync, run by the CEO and CTO."
    assert not any(v.rule == "prose.acronym_unexpanded" for v in lint_prose_quality(text))


def test_acronym_flagged_once_per_asset():
    text = "UIUC published this. UIUC is credible. UIUC led the study."
    hits = [v for v in lint_prose_quality(text) if v.rule == "prose.acronym_unexpanded"]
    assert len(hits) == 1
