# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **How this is maintained.** The public repo is published as a squashed orphan-root snapshot
> (`docs/PUBLISHING.md` §3), so it carries **no incremental commit or PR history** — this file
> plus the `v*` release tags are the *only* "what's new" surface for users. Curate `[Unreleased]`
> as public-facing work lands. At release, roll it into a stamped `## [x.y.z] - DATE` heading and
> bump `VERSION`. The export (`scripts/oss-export.sh`) **gates** on this: a full run refuses to
> proceed unless the changelog was curated the day of the cut, and writes a safe auto-draft of the
> changed public subsystems to start from. Keep entries user-facing (features, not commit noise);
> never name a tenant or private path.

## [Unreleased]

<!--
Template for each release — copy the block, set the version + date, keep only the
sections that apply:

## [x.y.z] - YYYY-MM-DD
### Added
### Changed
### Deprecated
### Removed
### Fixed
### Security
-->

## [0.4.0] - 2026-07-19

### Added
- **`docs/gtm-data-infra.md`** — a generic, deployment-agnostic writeup of how the engine
  stores, scopes, and retires data: the three data kinds (config / knowledge / working
  state), per-tenant content-root scoping, and the staging→promotion pattern for anything
  that becomes durable knowledge.

### Changed
- **`voice-of-customer`**: the source-coverage collector now reads a 7th source — LinkedIn
  reply drafts that quote a poster's original words verbatim. Social-listening tools with no
  native LinkedIn crawl were previously read as "zero LinkedIn signal"; the brief now
  surfaces the real count separately so that blind spot isn't mistaken for an absence of
  signal. The brief template also gained a standing Appendix F (data provenance) — every
  source is classified as public data, paid third-party data, or the company's own
  already-public material, computed fresh per profile rather than templated boilerplate.

### Fixed
- **`prospect`**: discovery was skipping straight to the web-search fallback on small or
  single-vertical runs instead of attempting the connected discovery source first. Discovery
  now always attempts the primary source when connected, and the run header must state the
  specific reason for any fallback rather than a generic "unavailable."

## [0.3.0] - 2026-07-19

### Added
- **Knowledge lifecycle tooling** — a source corpus + meta/status/staging/usage/refresh
  pipeline for keeping a profile's knowledge base current, backed by a scheduled
  `knowledge-refresh` skill.
- **`campaign-plan`** skill — turns market signal into an outbound-program plan (cohorts,
  sequencing, and a campaign brief) grounded in the active profile's knowledge base.
- **`voice-of-customer`** skill — a product/engineering intelligence brief that separates
  customer-voice signal from BD-framed input and surfaces demand vs. capability gaps.
- **`community-signal-analysis`** skill — scores and synthesizes community-sourced signal
  (forums, social listening) into a structured brief.
- **`outcomes-sync`** skill — closes the loop from published content back to a lightweight
  outcomes ledger.
- **`account-dossier`**: a `python-docx`-based renderer as the default builder, always
  available via `uv run` (the prior Node `docx`-based renderer stays as a local-dev
  alternative, since `npm install`/`npx` are denied by the agent's permission policy).
- **`prospect`**: numeric topic-intent heat scoring — a topic-intent score ≥ 75 on either
  feed scores full heat (+2), 60–74 is elevated but earns no points, and both feeds
  converging on one account (double-intent) adds +1 more — plus a re-score mode that
  refreshes heat/intent on an existing prospect list against current intent data without
  re-running discovery.
- **`carousel-visuals`**: a full-text-card mode that renders every carousel card's copy
  directly into the generated image, bypassing the Slidev/deck-renderer text-overlay step.

### Changed
- **`linkedin-reply`** (0.3.0 → 0.6.0): a brief gate runs before drafting (register,
  named/veiled, length cap, the one frontier point, facts to verify); a hard 1,250-character
  comment cap with auto-split instead of trim-thrash; third-person critique; a required
  hostile-expert rebuttal self-check; the contribution is now grounded in the profile's
  topical depth before drafting, with an anti-lecture guardrail against restating the
  author's own field back to them; disguised-compliment openers ("sharp take", "strong
  frame") are now treated the same as generic praise; the frontier point is pressure-tested
  against the company's actual moat before it's used; and each draft now records a
  structured customer-voice-vs-BD-focus capture block so a later `voice-of-customer` audit
  can attribute who said what.

### Fixed
- Regenerated a stale committed knowledge-usage doc that had drifted from the knowledge
  base it documents; hardened the content-QA URL-reachability check to explicitly reject
  non-http(s) schemes before use.

## [0.2.0] - 2026-07-17

### Added
- **Outreach pack linter** (`tests/linter/outreach_pack_linter.py`) — a deterministic,
  stdlib-only gate for 1:1 cold-email packs. It stamps a `Rules-Version` and fails closed on
  stale packs, then enforces per-email hygiene (subject shape, `Hi <First>,` greeting, bare
  sign-off, word-count band, no links / em-dash / spintax / placeholders / banned fluff), a
  template-share ceiling across the pack, same-company divergence, a required *hedged* gap, an
  offer-shaped CTA, and a specificity-anchor floor. The case-study-company-name and
  mail-merge-"stem" checks are **file-driven** (`--case-study-file` / `--stem-file`) and empty
  by default, so you supply your own lists per profile — nothing is baked in.

### Changed
- **`draft-outreach`** and **`email-sequence`** now stamp every pack with the current
  `Rules-Version` and must pass the outreach linter with zero errors before a pack is presented
  as sendable. Drafting also gained a product-grounded compose step and a formality register.

## [0.1.0] - 2026-07-16

### Added
- Initial public release of the GTM engine, extracted from the private monorepo.
- **`solution-scope-check`** skill — turns a solution design (or a discovery brief) into a
  2-page, buyer-facing "scope check": page 1 restates the solution in plain terms, page 2 asks
  scope-validation questions drawn from a generic, reusable bank, each tagged with the design
  decision it unlocks and the assumption it tests. Runs pre-design (from `solution-discovery`) or
  post-design (from `solution-design`).
- Generated **`docs/SKILLS.md`** skill index — a single, always-current inventory of every skill,
  produced by `codegen` and gated in CI, so the skill list can never silently drift out of the
  docs (adding a skill regenerates the index or the build fails).
- Onboarding now scaffolds a per-profile **"Scoring & gates"** block, so a new profile is created
  with the prospecting rubric ready to fill in.

### Changed
- **`solution-design`** gained a richer visual layer in its HTML companion — a before→after
  outcome band, a numbered control strip, V1/V2 phase cards, and on-brand diagram theming — and
  now hands off to `solution-scope-check`.
- Prospecting is now fully profile-driven: the skill's `gates-and-scoring` reference holds only
  generic scoring machinery (gate/rubric shapes, heat axis, tiering, distribution, default
  thresholds), and each profile supplies its own gates, rubric line-items, and thresholds. The
  template profile ships a worked example.
- The skill inventory is no longer guarded by a hardcoded count; it is a generated, CI-gated index.
