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

## [0.9.2] - 2026-07-29

### Fixed
- **Remaining real-world data removed from fixtures.** A real company's switchboard phone
  number and identity in an API-response fixture, a real company used to test non-ASCII name
  handling, and a real media company in a person fixture have been replaced with fictional
  equivalents that preserve the property each was testing.

### Added
- **A documented rule and four enforcement layers for third-party PII.** Real people and real
  companies now belong only in tenant data directories; code, tests, config, docs, fixtures and
  docstrings must be fictional. One checker enforces it at commit time, in CI, as a unit test,
  and over the release cut — so the release gate cannot disagree with the commit gate. It
  detects email addresses on non-reserved domains, personal profile URLs, and phone numbers
  outside the reserved fictional ranges.

## [0.9.1] - 2026-07-29

### Fixed
- **Test fixtures no longer contain real people.** Several suites used real names and work
  email addresses as fixture data, because those were the genuine messy-data cases the
  merge-hygiene and prospect-consolidation code was written to handle. They have been replaced
  with fictional people on RFC-2606 reserved (`.example`) domains, preserving the exact string
  shapes the tests assert on. Real company names used as prospect fixtures were genericized in
  the same pass.
- The export now **gates on third-party PII**: every email address in a release must sit on a
  domain that cannot belong to a real person, and binary documents are text-extracted and swept
  rather than skipped.

## [0.9.0] - 2026-07-29

### Added
- **`market-intelligence`** skill (rescoped from `voice-of-customer` v0.1.1 → v0.2.0) — widens
  demand-signal synthesis from one organic-market source to ten, tracked across five distinct
  speakers (customer-voice, bd-focus, standards-voice, vendor-voice, and internal), each carrying
  its own provenance so a caller can no longer mark a changelog or a roadmap post as demand
  evidence by mistake. Breadth now counts distinct (source, entity) pairs — ten quotes from one
  filing read as one source, not consensus — and every claim is tagged `verified` or
  `unverified`: a full-text search hit means the phrase appears, not that anyone said it. Adds a
  standards & spec watch (adopted vs. proposed) and a coverage-honesty model that distinguishes a
  source that ran but came back stale from one that never ran at all. Pre-rename briefs under the
  old skill name are read but never rewritten.
- **`market-harvest`** skill (v0.1.0) — the metered capture half split out from
  `market-intelligence`'s free, read-only synthesis, so a synthesis re-run never spends a credit.
  Adds per-lane pull watermarks that only advance on a fully-covered window (a failed or partial
  pull never silently claims coverage it doesn't have) and a filer-roster importer that treats a
  search hit as a worklist item, never as evidence, until a human verifies it.
- **`account-plan`** — DOCX rendering scripts (`md_to_docx_spec.py`,
  `render_account_plan_pydocx.py`) for turning a generated account plan into a Word document
  (v0.4.0).

### Fixed
- **`market-intelligence`** — a filer whose passage had already been verified-read under one
  claim no longer reappears in the unread backlog under a different claim; only a verified record
  now counts as "read".
- Onboarding docs (`END-USER-ONBOARDING.md`, `docs/onboarding/SALES-FAQ.md` +
  `END-USER-ONBOARDING.pdf`) are now correctly included in the public export — a prior cut shipped
  without them despite the guide already existing and being written for a public-repo reader.

### Changed
- README and `CLAUDE.md` now describe the pipeline runner as a workflow graph (a DAG scheduler)
  rather than a linear five-stage pipeline — the five default stages are one instance of that
  shape, not the shape itself — and the README gained a technical-evaluator section covering the
  control-flow and capability-boundary design decisions.

## [0.8.0] - 2026-07-28

### Added
- **Apollo prospecting source**: a new data source in the `prospect` skill's waterfall
  (RocketReach → Vibe → Apollo → web) plus a third buying-intent feed alongside
  Bombora/Intentsify, with least-privilege permission scoping for Apollo's send-capable
  hosted OAuth connector.
- **`case-study`**: a new skill that drafts a customer case study from account materials.
- **`product-partner-brief`** / **`consulting-partner-brief`**: two new partnership-brief
  skills — a peer-vendor motion (a self-named capability gap, with a hard stop below a
  confidence tier) and a systems-integrator motion (gated capability mapping) — sharing a
  merge-hygiene module and a linter that both briefs run through before being shown.
- **`email-sequence`**: a compliance module with new deliverability and compliance docs
  (deliverability rules, Do-Not-Contact cross-checks), and a signal-led vs. generic split
  for the ready-to-load list — rows whose research carries a safe, verbatim "why now" clause
  open on it; everything else falls back to the generic copy. Fail-closed: nothing is ever
  reworded to manufacture a clause.
- **`prospect`**: an enrichment backlog with a scheduled daily-enrich job, and matching
  consolidation/import/dashboard updates.
- **`reply-send`**: a staged reply gate across the sequencer connector, `inbound-triage`,
  and `draft-outreach` — mirrors the publish gate; nothing auto-sends.
- **`profile-onboard`**: buyer-journey extraction and an expanded profile-draft schema.
- **Campaigns portfolio dashboard** (`gtm_core.campaigns_dashboard`): joins `campaign-plan`
  manifests against staged-sequence ledger events and live sequence stats into a single
  promised-vs-actual view, auto-refreshed on every `prospect` consolidation run;
  `campaign-plan` now emits a machine-readable manifest for it.
- **Knowledge base**: an advisory duplicate-fact report and an expanded knowledge
  index/metadata pass, surfacing likely-duplicate facts across a profile's knowledge corpus
  without blocking anything.
- **Security**: an audit-ledger integrity checker (`gtm_core.ledger_verify`), a
  prompt-injection chokepoint test suite, Dependabot + pip-audit CI wiring, and an
  incident-response runbook.
- A canonical account-slug helper (`gtm_core.slugify`) so every skill that creates a
  per-account folder resolves the same company name to the same slug.

### Fixed
- **Campaigns dashboard**: a manifest-listed sequence with live stats but no
  staged-ledger event is now synthesized into the view instead of staying invisible.

## [0.7.0] - 2026-07-23

### Added
- **Prospect consolidation + status dashboard** (`gtm_core.prospects_consolidate`,
  `gtm_core.prospects_dashboard`), wired into the `prospect` skill. Every run now sweeps this
  run's (and any interrupted prior run's) emailed contacts into a single sequencer-ready
  `sequences/ready-to-load.csv` — deduped by email across all prior exports, person-unique (the
  same human under two address formats is collapsed), Do-Not-Contact-clean including cross-address,
  and gated by deliverability confidence (only verified / A–A- contacts land in the load file;
  weaker signal goes to a hidden `needs-verification.csv` hold queue). Each sweep also renders a
  `status.html` funnel page (account backlog → email funnel → ready / verifying / blocked, plus
  cost and next steps), so "how many are ready to send" is always one computed number instead of a
  hand-built list.
- **`email-sequence`**: bulk enrollment now sources from the consolidated pool by default rather
  than a hand-built list, refreshes the DNC cache before pulling leads, and can drain the
  verification hold queue on a single operator approval instead of asking them to manage CSV files.
- **`gtm_core.profile` CLI** — report the active tenant profile and list the available ones.
- **Syften filter schema** (`schemas/syften-filters.schema.json`) with a `_template` scaffold;
  `community-signal-analysis` scoring can now apply per-tenant filters and a strict mode, with
  path guards that refuse to read outside the active profile's content root.
- **`docs/sales-questions-by-deal-phase.md`** — a company-agnostic, evidence-graded method doc for
  what to ask across each deal phase (opening, discovery, qualification, value case, objections,
  advancing/closing), every claim cited to a primary source and graded by evidence strength. Wired
  into `call-prep`, `solution-discovery`, `account-plan`, and `inbound-triage` for method while
  question *content* stays sourced from the active profile's knowledge base.
- **`account-dossier`**: an exec one-pager variant — a hard single-page principal brief (product
  brief + credibility read) for a decision-maker who already knows how to run the call.

### Changed
- **`carousel-visuals`**: the vision accuracy check is now a blocking gate; product-capability
  claims baked into rendered card copy get the same SHIPPED/CONDITIONAL/ROADMAP discipline as
  prose before the first paid render; PDF assembly asserts a uniform pixel size across the batch
  (so a partial rerender can't silently land at a different resolution); and no-Telegram file
  handoffs confirm actual filenames instead of assuming a naming pattern.
- **`community-signal-analysis`**: expanded scoring and rendering, driven by the new Syften filter
  config.

### Fixed
- **Content linter**: verbatim double-quoted spans are now exempt from the word- and
  character-level prose rules (ban list, em dash) — a source quote is reproduced exactly or cut,
  never reworded to satisfy a linter. The same word outside quotes is still checked.

## [0.6.0] - 2026-07-21

### Added
- **Post-reply lifecycle**: booking-link insertion at reply-stage outreach, a read-only
  inbound-visibility adapter over the connected sequencer (source-agnostic, no send tool), and a
  new confidence-gated `inbound-triage` skill with its own reply gate — mirrors the publish gate,
  nothing auto-sends. Optional Calendly polling turns meeting-booked/no-show outcomes into signals
  that feed back into the pipeline.
- **`market-scan`**: a demand-driven focus lens — the skill now auto-discovers your own GTM plan,
  email sequence, account plan, or campaign and focuses the weekly sweep on the industries,
  use-case clusters, personas, and geos where direct sales is actually running, tagging every
  signal On-focus/Adjacent/Off-focus and flagging any priority market that comes back empty.

### Fixed
- **`rocketreach`**: several integration bugs found via an audit against RocketReach's own
  OpenAPI spec — stale tool names in the `prospect` skill's guidance, a malformed bulk-status
  request parameter, and an inaccurate cost-cap claim in the security doc (RocketReach billing is
  flat-subscription, not metered per call).
- **`prospect`**: the account-merge identity key no longer strips corporate suffixes and
  non-ASCII characters before comparing companies — that normalization was silently colliding
  distinct accounts and could drop an existing account's operator-edited status on merge.
- Non-ASCII slug fallback hashing switched from SHA1 to SHA256.

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

## [0.5.0] - 2026-07-20

### Added
- **`setup`/onboarding**: rewritten as a staged front door (extract → gaps → review → promote →
  proof, v0.5.0) with resume support, a status report that surfaces staleness, and automatic
  pruning of drafts abandoned for more than 14 days. The profile draft schema gained an optional
  `settings` block, used to derive `PROFILE.md` config instead of shipping hardcoded placeholders.
- **`prospect`**: a bulk mode for target-driven large discovery runs — firmographic + why-now
  filtering moves in-query (budget-gated, size-estimated before any metered call) instead of the
  standard mode's per-row web sweep, for targets standard mode's ~30-row intake can't reach.
- **`email-sequence`**: an enrollment hygiene gate — every batch is checked against email
  verification status and the Do-Not-Contact list (plus any manual outreach pack covering the same
  accounts) before enrollment, with each exclusion reason itemized in the ledger.
- HubSpot CSV export gained intent/firmographic columns (raw intent score, intent topics,
  industry, revenue range, and a qualification-path flag for runs where a gate was relaxed), so a
  rep working straight from HubSpot sees the same signal the scoring run saw.

### Changed
- **`setup`/onboarding**: ICP scoring is now derived from the profile draft's own data instead of
  shipping placeholder criteria (CI-guarded against regressions), and the generated peer-quality
  content bundle renders in full, with case studies sourced from a prompt rather than fabricated.
- **`carousel-pdf`**: captions must not restate the carousel's own content (no itemized recap of
  on-card stats or questions) — the caption's job is to earn the swipe, not pre-summarize the
  payoff.

### Fixed
- **`setup`/onboarding**: resume now works even without the original draft temp file (draft JSON
  is persisted at stage time); restored the `#`-header convention in generated `voice-bans.txt`.

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
