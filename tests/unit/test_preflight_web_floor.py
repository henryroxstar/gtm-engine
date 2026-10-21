from gtm_core.preflight import adjudicate, render


def test_web_floor_offer_when_contacts_falls_back_to_web():
    # With no connectors connected and needed={"contacts"}
    observed = {"vibe": "absent", "rocketreach": "absent", "apollo": "absent"}
    verdict = adjudicate(observed, ["contacts"])
    assert verdict["proceed"] is False
    assert verdict.get("web_floor_offer") is True

    rendered = render(verdict)
    assert "You can still run on free web search" in rendered
    assert "companies only, no verified contacts, nothing to load into a sender" in rendered
    assert "I stopped before spending" in rendered
    assert (
        "the step you asked for needs verified contacts and no contact tool is connected"
        in rendered
    )
    assert "Connect Vibe or RocketReach in Settings → Connectors" in rendered
    assert "say 'run it on web search' to get companies only" in rendered


def test_workspace_mode_auto_proceeds_on_web_discovery_with_byok_contacts():
    """Issue #337: in workspace mode, web discovery + BYOK contacts auto-proceeds without blocking."""
    observed = {"vibe": "absent", "rocketreach": "ok", "apollo": "absent"}
    verdict = adjudicate(observed, ["discovery", "contacts"], workspace_mode=True)
    assert verdict["proceed"] is True
    assert verdict["web_floor_offer"] is False
    assert "discovery" in verdict["degraded"]
    assert "discovery" not in verdict["blocking"]
    assert verdict["capabilities"]["discovery"]["via"] == "web"
    assert verdict["capabilities"]["contacts"]["via"] == "rocketreach"


def test_workspace_mode_env_auto_detection(monkeypatch):
    """WORKSPACE_ID in environment auto-enables workspace mode."""
    monkeypatch.setenv("WORKSPACE_ID", "00000000-0000-0000-0000-000000000001")
    observed = {"vibe": "absent", "rocketreach": "ok", "apollo": "absent"}
    verdict = adjudicate(observed, ["discovery", "contacts"])
    assert verdict["workspace_mode"] is True
    assert verdict["proceed"] is True
    assert "discovery" in verdict["degraded"]
    assert "discovery" not in verdict["blocking"]


def test_workspace_mode_refuses_when_contacts_falls_back_to_web():
    """In workspace mode, missing contact tools still blocks when contacts are needed."""
    observed = {"vibe": "absent", "rocketreach": "absent", "apollo": "absent"}
    verdict = adjudicate(observed, ["discovery", "contacts"], workspace_mode=True)
    assert verdict["proceed"] is False
    assert verdict["web_floor_offer"] is True
    assert "contacts" in verdict["blocking"]

    rendered = render(verdict)
    assert "Connect RocketReach or Apollo in Settings → Integrations" in rendered


def test_preflight_cli_workspace_flag(capsys):
    """The --workspace CLI flag passes workspace_mode to adjudicate."""
    import json

    from gtm_core.preflight import main

    code = main(
        [
            "--profile",
            "fixtureco",
            "--observed",
            json.dumps({"vibe": "absent", "rocketreach": "ok"}),
            "--need",
            "discovery,contacts",
            "--workspace",
        ]
    )
    assert code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["proceed"] is True
    assert payload["workspace_mode"] is True
