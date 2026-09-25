from gtm_core import capabilities


def test_cli_prints_plain_words_for_each_tool(monkeypatch, capsys):
    monkeypatch.delenv("VIBE_PROSPECTING_CONNECTED", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    capabilities.main([])
    out = capsys.readouterr().out
    assert "Finds companies that fit" in out  # Vibe, in the founder's words
    assert "not connected" in out
    assert "VIBE_PROSPECTING_CONNECTED" not in out  # never the env var name
