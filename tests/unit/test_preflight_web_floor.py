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
