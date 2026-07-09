# agent/ — the brain (Claude Code + Agent SDK)

The orchestrator app. Headless Claude Code via the **Python Claude Agent SDK**, loading the
de-branded plugin in `../plugin/` and the MCP servers. The SDK model is **always Claude
(Anthropic API)** — DeepSeek is only a *downstream worker reached as an MCP tool*
(`mcp_config.py`), never the Claude Code model itself.

## Responsibilities

- Resolve the active profile **per Telegram chat** (session-bound; no global mutable
  `ACTIVE_PROFILE`) and load `../profiles/<company>/` — `PROFILE.md` + `knowledge/` +
  `products/<slug>/`. Company/product/ICP/brand/voice facts are read from the profile,
  **never** from `plugin/`.
- Run skills; call tools **only via MCP** — never raw HTTP.
- Maintain a **session per Telegram chat** (`SessionStore` in `session.py`).
- Enforce the three gates (Plan, Podcast script/voice, Publish) — see `../cockpit/`.
- Write the run manifest + history/cost ledgers under `content/<profile>/`,
  via `ledgers.py`.

## Module map

| File | Role |
|---|---|
| `config.py` | `Config` dataclass + `Config.from_env()` — env → typed config (repo paths, tokens, DSNs, OAuth). |
| `profiles.py` | `list_profiles` / `profile_dir` / `load_products` / `system_prompt_for` — profile resolution + the per-profile system-prompt injection. |
| `ledgers.py` | `Ledgers` — append-only `history.jsonl` / `costs.jsonl`, per-run manifest JSON, monthly cost rollups + cap check. Pure stdlib. |
| `mcp_config.py` | `build_mcp_servers` — assembles the Agent SDK `mcp_servers` dict; each server is gated on its env being present (`news`, `google`, `worker`). |
| `session.py` | `AgentSession` (one persistent `ClaudeSDKClient`) + `SessionStore` (per-`chat_id` session, profile-bound). |
| `__main__.py` | argparse CLI for headless / cron runs (one-shot `query()`). |

## Run headless (one-shot — for cron skill runs)

The brain runs unattended for cadence skills (e.g. the weekly `market-scan`). It uses the
one-shot `claude_agent_sdk.query()` path with the *same* `ClaudeAgentOptions` an interactive
`AgentSession` builds:

```bash
# pick a profile explicitly; falls back to $ACTIVE_PROFILE, default "template"
python -m agent --profile acme "run the market-scan skill and summarize the top 3 stories"

# list the available company profiles (dirs under profiles/ containing PROFILE.md)
python -m agent --list-profiles
```

The CLI streams assistant text to stdout, which is what cron/systemd timers capture into logs.

## Secrets

All secrets come from the environment (Doppler injects them at runtime on the VPS). Nothing is
hardcoded; `Config.from_env()` is the single ingestion point. See `../.env.example` for the
full set and `../profiles/<company>/PROFILE.md` for **config only — never keys**.
