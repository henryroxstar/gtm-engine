from __future__ import annotations

import json
from pathlib import Path

# Maps category names to their denied verb patterns and the required context gate.
CATEGORY_RULES = {
    "~~email_sequencer": {
        "denied_patterns": ["add_leads_", "import_prospects_", "activate_", "resume_"],
        "context": "email_context",
    },
    "~~social_publisher": {
        "denied_patterns": ["create_post", "publish_", "schedule_"],
        "context": "publish_context",
    },
    "~~marketing_automation": {
        "denied_patterns": ["send_blast", "activate_campaign", "launch_"],
        "context": "email_context",
    },
    "~~video_generator": {
        "denied_patterns": ["tiktok_publish", "publish_website"],
        "context": "publish_context",
    },
    "~~seo_intelligence": {
        "denied_patterns": ["delete_project", "purge_"],
        "context": None,
    },
    "~~CRM": {
        "denied_patterns": ["delete_", "purge_", "bulk_destroy_"],
        "context": None,
    },
    "~~user_feedback": {
        "denied_patterns": ["delete_", "purge_"],
        "context": None,
    },
    "~~product_analytics": {
        "denied_patterns": ["delete_", "drop_"],
        "context": None,
    },
    "~~marketing_analytics": {
        "denied_patterns": ["delete_", "purge_"],
        "context": None,
    },
}


# The active tenant config loading logic. We assume `.mcp.json` contains a mapping
# from categories to the configured MCP connector names.
def load_tenant_config(root: Path | None = None) -> dict[str, str]:
    if not root:
        root = Path.cwd()
    mcp_config = root / ".mcp.json"
    if mcp_config.exists():
        try:
            return json.loads(mcp_config.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def get_category_for_connector(
    connector_name: str, config: dict[str, str] | None = None
) -> str | None:
    if config is None:
        config = load_tenant_config()
    for cat, conn in config.items():
        if cat.startswith("~~") and conn == connector_name:
            return cat
    return None


def is_write_tool(tool_name: str) -> bool:
    # A simple heuristic for write/mutation tools based on verbs
    write_verbs = (
        "create",
        "add",
        "import",
        "activate",
        "resume",
        "send",
        "launch",
        "publish",
        "schedule",
        "delete",
        "remove",
        "purge",
        "drop",
        "edit",
        "update",
        "set",
        "write",
        "execute",
        "bulk_",
        "clear",
    )
    tool_lower = tool_name.lower()
    return any(tool_lower.startswith(verb) for verb in write_verbs)
