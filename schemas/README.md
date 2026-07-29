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

> Note: `NewsItem` maps onto the prod `discovery_items` row — `COALESCE(item_name,name)→title`, plus `trending_score`, `is_active`, `is_stale`, `published_at`, `created_at`.
