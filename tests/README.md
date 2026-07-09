# tests/ — testing strategy

Build in priority order:

1. **Content linter (build FIRST).** Enforces the playbooks automatically so every asset passes before review:
   - LinkedIn: hook ≤140 chars, no URLs in body, hashtag count, char-range 1,300–2,500, carousel 8–12 slides.
   - X: tweet 1/ hook stands alone, no body links, thread length sane.
   - Instagram: Reel 30–90s, carousel 7–10 slides, caption SEO present.
   - De-brand lint: `bash lint/debrand_check.sh` — no company token may appear in `../plugin/skills/`.
2. **Contract/schema tests.** Validate stage outputs against `../schemas/*.json`.
3. **Fixture/golden-path tests.** Canned `discovery_items` rows → `content-radar` → assert digest shape + scoring.
4. **Mocked integrations + dry-run publish.** Mock the MCPs/webhooks; `content-publish` dry-run logs payloads instead of POSTing — run the whole pipeline with zero spend.
5. **Staged live test.** Draft-only week → test account → real accounts.

**CI (GitHub Actions):** linter + schema tests + de-brand lint + a mocked smoke run on every PR.
