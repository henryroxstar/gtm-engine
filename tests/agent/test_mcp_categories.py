from __future__ import annotations

from agent.permissions import classify_tool, email_context

# We expect gtm_core.mcp_categories and agent.permissions to integrate categories


def test_category_email_sequencer_deny_rules(monkeypatch):
    monkeypatch.setattr(
        "gtm_core.mcp_categories.get_category_for_connector",
        lambda name, config=None: "~~email_sequencer" if name == "saleshandy" else None,
    )

    # By default, without context, it must be denied
    assert classify_tool("mcp__saleshandy__add_leads_to_sequence", {}) == "deny"

    with email_context():
        assert classify_tool("mcp__saleshandy__add_leads_to_sequence", {}) == "allow"


def test_category_fail_closed_default(monkeypatch):
    monkeypatch.setattr(
        "gtm_core.mcp_categories.get_category_for_connector",
        lambda name, config=None: "~~CRM" if name == "newcrm" else None,
    )

    # We must add ~~CRM to CATEGORY_RULES temporarily
    import gtm_core.mcp_categories

    monkeypatch.setitem(
        gtm_core.mcp_categories.CATEGORY_RULES, "~~CRM", {"denied_patterns": [], "context": ""}
    )

    assert classify_tool("mcp__newcrm__delete_account", {}) == "deny"
    assert classify_tool("mcp__newcrm__get_account", {}) == "allow"


def test_category_seo_intelligence(monkeypatch):
    monkeypatch.setattr(
        "gtm_core.mcp_categories.get_category_for_connector",
        lambda name, config=None: "~~seo_intelligence" if name == "openseo" else None,
    )

    # Read/query tools are allowed
    assert classify_tool("mcp__openseo__get_domain_overview", {}) == "allow"
    assert classify_tool("mcp__openseo__get_ranked_keywords", {}) == "allow"
    assert classify_tool("mcp__openseo__research_keywords", {}) == "allow"

    # Destructive/denied tools are denied
    assert classify_tool("mcp__openseo__delete_project", {}) == "deny"
    assert classify_tool("mcp__openseo__purge_cache", {}) == "deny"


def test_category_staging_verbs_allowed(monkeypatch):
    monkeypatch.setattr(
        "gtm_core.mcp_categories.get_category_for_connector",
        lambda name, config=None: "~~marketing_automation" if name == "customerio" else None,
    )
    # Staging/creating drafts that are not in denied_patterns are allowed
    assert classify_tool("mcp__customerio__create_campaign_draft", {}) == "allow"
    assert classify_tool("mcp__customerio__update_template", {}) == "allow"
    # While send/launch verbs in denied_patterns are denied
    assert classify_tool("mcp__customerio__send_blast", {}) == "deny"
