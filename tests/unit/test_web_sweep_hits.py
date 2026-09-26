import pytest

from gtm_core.web_sweep_hits import _determine_agent_kind, get_ai_vocab_regex


def test_dynamic_regex_compilation(tmp_path, monkeypatch):
    profile = "test_profile"

    # Setup mock knowledge dir
    knowledge_dir = tmp_path / "profiles" / profile / "knowledge"
    knowledge_dir.mkdir(parents=True)

    web_sweep_toml = knowledge_dir / "web-sweep.toml"
    web_sweep_toml.write_text("""
ai_vocabulary = ["agentic finance", "x402", "third-party agents"]
""")

    role_vocab_toml = knowledge_dir / "role-vocabulary.toml"
    role_vocab_toml.write_text("""
[[persona]]
name = "ai-platform"
cues = ["VP of GenAI"]
""")

    # Mock resolve_knowledge_file
    def mock_resolve(profiles_root, p, filename, **kwargs):
        return knowledge_dir / filename

    import gtm_core.web_sweep_hits as module

    monkeypatch.setattr(module, "resolve_knowledge_file", mock_resolve)

    # Test _determine_agent_kind
    # "agentic finance" is in web-sweep.toml
    assert (
        _determine_agent_kind("We are building agentic finance solutions.", profile=profile) == "ai"
    )

    # "VP of GenAI" is in role-vocabulary.toml
    assert _determine_agent_kind("New VP of GenAI joins the team.", profile=profile) == "ai"

    # Backward compatibility tests
    assert _determine_agent_kind("Some third-party agents.", profile=profile) == "ai"
    assert _determine_agent_kind("Using x402 processor.", profile=profile) == "ai"


def test_missing_toml_fails_loudly(tmp_path, monkeypatch):
    profile = "test_profile"

    knowledge_dir = tmp_path / "profiles" / profile / "knowledge"
    knowledge_dir.mkdir(parents=True)

    # web-sweep.toml is missing
    role_vocab_toml = knowledge_dir / "role-vocabulary.toml"
    role_vocab_toml.write_text("")

    def mock_resolve(profiles_root, p, filename, **kwargs):
        return knowledge_dir / filename

    import gtm_core.web_sweep_hits as module

    monkeypatch.setattr(module, "resolve_knowledge_file", mock_resolve)

    with pytest.raises(FileNotFoundError):
        _determine_agent_kind("Test text", profile=profile)


def test_missing_ai_vocabulary_key_fails_loudly(tmp_path, monkeypatch):
    profile = "test_profile"

    knowledge_dir = tmp_path / "profiles" / profile / "knowledge"
    knowledge_dir.mkdir(parents=True)

    web_sweep_toml = knowledge_dir / "web-sweep.toml"
    web_sweep_toml.write_text("[queries]\nnewsroom = '...'")

    role_vocab_toml = knowledge_dir / "role-vocabulary.toml"
    role_vocab_toml.write_text("")

    def mock_resolve(profiles_root, p, filename, **kwargs):
        return knowledge_dir / filename

    import gtm_core.web_sweep_hits as module

    monkeypatch.setattr(module, "resolve_knowledge_file", mock_resolve)

    with pytest.raises(ValueError):
        _determine_agent_kind("Test text", profile=profile)


def test_approval_gateway_validation():
    from gtm_core.gateways import ApprovalGateway

    assert ApprovalGateway.validate_regex_token("valid word") is True
    # *bad* is not a valid regex token because it starts with *
    with pytest.raises(ValueError):
        ApprovalGateway.validate_regex_token("*bad*")


def test_mtime_caching(tmp_path, monkeypatch):
    import time

    profile = "test_profile"

    knowledge_dir = tmp_path / "profiles" / profile / "knowledge"
    knowledge_dir.mkdir(parents=True)

    web_sweep_toml = knowledge_dir / "web-sweep.toml"
    web_sweep_toml.write_text('ai_vocabulary = ["term1"]')

    role_vocab_toml = knowledge_dir / "role-vocabulary.toml"
    role_vocab_toml.write_text("")

    def mock_resolve(profiles_root, p, filename, **kwargs):
        return knowledge_dir / filename

    import gtm_core.web_sweep_hits as module

    monkeypatch.setattr(module, "resolve_knowledge_file", mock_resolve)

    # First call, should compile and cache
    regex1 = get_ai_vocab_regex(profile)
    assert regex1.search("term1")
    assert not regex1.search("term2")

    # Sleep to ensure mtime changes
    time.sleep(0.01)

    # Modify web-sweep.toml
    web_sweep_toml.write_text('ai_vocabulary = ["term1", "term2"]')

    # Second call, mtime changed, should recompile
    regex2 = get_ai_vocab_regex(profile)
    assert regex2.search("term2")
    assert regex1 is not regex2
