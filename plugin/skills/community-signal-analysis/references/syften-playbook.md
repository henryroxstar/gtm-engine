# Syften playbook — syntax, noise scoring, collisions

Authoritative syntax is whatever `mcp__syften__syften_get_filters` / the Syften dashboard accept; the
live reference is the connected connector's `get_filter_syntax` tool when present. This file is the
durable cheat-sheet the skill lints against.

## Filter syntax (the rules that trip people up)

- **Space = AND.** `okta agent` requires both terms, any order.
- **No `OR`. No parentheses / grouping.** Express alternatives as **separate filters** — one filter
  per alternative. `Portkey AND (AI OR LLM)` is **invalid** Syften syntax; split it into
  `Portkey AI` and `Portkey LLM` (or scope with a single disambiguator).
- **`NOT`** excludes a word/phrase/operator: `postgres NOT mysql`, `NOT site:reddit.com`.
- **Phrases**: `"agent gateway"`. **Wildcards** only at a word/phrase boundary: `secur*` (never mid-word).
- **Field operators**: `site:` (URL substring; repeated → OR), `title:` (repeated → AND), `type:post|comment|podcast`,
  `lang:en`, `author:`, `replyto:` (HN / Stack Exchange / Indie Hackers only).
- **Configured-filter-only directives** (valid in the dashboard; NOT content-search operators — never
  send them to a preview or a content query):
  - `$accept:"true if …; false otherwise"` — the AI accept/reject rule. Use a direct true/false instruction.
  - `$tag:name` — routing tag (Slack/email/RSS/API/webhook). Does not narrow content.
  - `$brand:"Name"` — brand hint for sentiment accuracy.
  - `` `literal` `` and `// comment` — exact-literal match and comments.

## Noise scoring (how "signal quality" is measured)

Signal quality is measured **in code** (`gtm_core.community_signal.score`) from Syften's own AI verdict:

- `analysis.accept == true` → **accepted** (on-topic).
- `analysis.accept == false` → **rejected** (noise).
- absent/`null` → **unscored** (AI filtering didn't run; counted as delivered/relevant).
- **noise% for a filter = rejected / total.** A filter with high noise% is a tuning target.

Do not eyeball noise from reading posts — read the `per_filter` table the scorer produced.

## Collision words → mechanical disambiguation

`$accept` alone is not enough for a high-collision brand name: put a **mechanical disambiguator term**
in front of the AI gate so obvious off-topic hits never reach it. Pattern:

```
<Brand> <disambiguator> $accept:"true when about <Brand> the <thing>; false for <the collision>, and false for job/recruitment posts." $brand:"<Brand>"
```

Common collision classes to watch: a brand that is also a common word, a band/musician, an animal, a
game/fantasy title, or a place name. Unique tokens (coined product names, unusual acronyms, a product
domain like `example.dev`) are self-disambiguating — a bare term is fine.

Job/recruitment spam is a recurring noise source; a trailing `false for job/recruitment posts.` in the
`$accept` rule suppresses it.

## Recommend-only

There is **no write tool**. The skill drafts corrected filters into `filter-report-<date>.md`; a human
pastes them into the Syften dashboard. Never claim a filter was applied.
