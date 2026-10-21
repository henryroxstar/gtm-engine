# schemas/ — data contracts (typed handoffs)

JSON Schema for each stage handoff (spec §12). Validated in CI (spec §13). Stages are decoupled — each writes a typed file the next reads.

Implement one schema file per contract:

- `news-item.schema.json` — `{id,title,url,source,published_at,summary,topics[],raw_excerpt}`
- `story-cluster.schema.json` — `{id,pillar,score,why_it_matters,angle_seeds[],platform_fit[],source_items[]}`
- `content-item.schema.json` — `{id,pillar,story_id,platform,format,slot,locale,status,research_ref,asset_refs[]}`
- `transcript.schema.json` — `{episode,topic,length_target_min,cast[],segments[]}`
- `episode-bundle.schema.json` — `{episode,video,audio,srt,slides[],clips[],transcript,show_notes,chapters[]}`
- `run-manifest.schema.json` — `{run_id,trigger,stages[]}`
- `metric-record.schema.json` — `{post_id,platform,published_at,impressions,engagements,saves,shares,follows,url}`

Client API contracts (the backend's client-facing surface; consumed by any app/client):

- `pack-descriptor.schema.json` — `{pack,variant,nodes[],inputs}` — item shape of the pack-listing API (`GET /v1/packs`); a read-only view of a pack graph variant, no server internals.
- `run-event.schema.json` — `{event,seq,data}` — the SSE run-progress stream vocabulary (`GET /v1/runs/{id}/stream`), protocol 0 (live today) + additive protocol 1 (node lifecycle, structured content blocks, gate resolution).
- `agent.schema.json` — `{agent_id,workspace_id,name,profile_name,packs[],language,monthly_budget_usd,daily_dispatch_cap,read_scope,status}` — one agent as a narrowing-only bundle of profile + packs + budget + daily dispatch cap within a workspace; `read_scope` (`own`/`workspace`) sets which runs an API key bound to it may read (never cancel).
- `run-artifact.schema.json` — `{artifact_id,run_id,name,rel_path,size_bytes,media_type,sha256,node_id,created_at}` — one file deliverable of a run; item shape of `GET /v1/runs/{id}/artifacts` (download by opaque `artifact_id` only — pointer semantics, never a client-supplied path).

> Note: `NewsItem` maps onto the prod `discovery_items` row — `COALESCE(item_name,name)→title`, plus `trending_score`, `is_active`, `is_stale`, `published_at`, `created_at`.
