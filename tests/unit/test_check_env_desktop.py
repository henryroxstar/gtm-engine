import io

from gtm_core import check_env


def test_desktop_session_is_ready_without_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    monkeypatch.delenv("GTM_RUNTIME", raising=False)
    out = io.StringIO()
    assert check_env.render(stream=out) is True
    text = out.getvalue()
    assert "signed in through the Claude app" in text
    assert "ANTHROPIC_API_KEY" not in text.split("TIER 1")[0]


def test_headless_still_requires_the_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GTM_RUNTIME", "headless")
    out = io.StringIO()
    assert check_env.render(stream=out) is False
    assert "set ANTHROPIC_API_KEY" in out.getvalue()


def test_desktop_with_a_stale_key_warns_about_double_billing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-real")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    out = io.StringIO()
    check_env.render(stream=out)
    assert "bill you twice" in out.getvalue()
