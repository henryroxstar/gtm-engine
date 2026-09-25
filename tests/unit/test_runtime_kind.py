from gtm_core.runtime_kind import is_desktop_session


def test_desktop_when_claude_code_marker_present(monkeypatch):
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    assert is_desktop_session() is True


def test_not_desktop_when_no_marker(monkeypatch):
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
    assert is_desktop_session() is False


def test_headless_agent_sdk_is_not_desktop(monkeypatch):
    # The VPS brain runs under the SDK with the same markers but an explicit override.
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "sdk-py")
    monkeypatch.setenv("GTM_RUNTIME", "headless")
    assert is_desktop_session() is False
