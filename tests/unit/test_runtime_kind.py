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


def test_is_headless_brain_when_headless(monkeypatch):
    from gtm_core.runtime_kind import is_headless_brain

    monkeypatch.setenv("GTM_RUNTIME", "headless")
    assert is_headless_brain() is True


def test_is_headless_brain_false_on_vps_value(monkeypatch):
    from gtm_core.runtime_kind import is_headless_brain

    monkeypatch.setenv("GTM_RUNTIME", "vps")
    assert is_headless_brain() is False


def test_is_headless_brain_false_when_unset(monkeypatch):
    # A plain shell is neither desktop nor headless brain.
    from gtm_core.runtime_kind import is_headless_brain

    monkeypatch.delenv("GTM_RUNTIME", raising=False)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
    assert is_headless_brain() is False
    assert is_desktop_session() is False


def test_is_headless_brain_wins_over_desktop_markers(monkeypatch):
    from gtm_core.runtime_kind import is_headless_brain

    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "sdk-py")
    monkeypatch.setenv("GTM_RUNTIME", "headless")
    assert is_headless_brain() is True
    assert is_desktop_session() is False
