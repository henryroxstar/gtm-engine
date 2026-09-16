
# Account Dossier

## How it works

`account-dossier` produces a **friendly, ≤4-page Word briefing** that makes a non-technical seller
(AE, sales exec, partner) fluent in an account's world before a meeting — assuming **zero prior
context**. It exists because `solution-design`, `deck-research`, and `build-deck` are too detailed
and intimidating to forward to a seller. The dossier is the standalone layer on top: read it, walk
in prepared.

It is **research-first** — mine what's already known, verify anything time-sensitive against fresh
web sources, and only then build the document. It is **NOT** a deck and **NOT** a technical solution
design. Hard cap: 4 pages.

---

## Step 1 — Load context

Resolve the active company profile bundle (`profiles/<active>/`) and load brand + product context.
The dossier carries **zero hardcoded company strings** — everything brand-specific is read here.

- **`profiles/<active>/PROFILE.md`** — `deck_byline` / `brand_name` (the byline default),
  `output_folder`, `language`. **No palette lives here.**
- **Brand kit** — the single source for every colour and typeface:
  `uv run python -m gtm_core.brandkit --profile <active> [--product <slug>]`.
- **`profiles/<active>/knowledge/brand-notes.md`** — ownable taglines and the "don'ts" only;
  it declares no colours.
> **Knowledge resolution (product-aware).** Wherever this skill loads a per-product knowledge file —
> `icp-personas.md` or `market-scan-config.md` — resolve its path with
> `python -m gtm_core.resolve_knowledge <file> --profile <active> [--product <slug>]` and read whatever
> path it prints, instead of opening `knowledge/<file>` directly. The helper returns the product-level
> file (`products/<slug>/<file>`) when present and falls back to the profile-level `knowledge/<file>`
> otherwise. Pass `--product` when the run is bound to one product (the lead `default_product` from
> PROFILE.md, or a product the operator named); omit it for profile-wide work — a profile that keeps one
> shared knowledge pack always falls back to the profile level, so nothing changes for it.

- **`profiles/<active>/knowledge/product.md`** + **`company.md`** — what "our product" is and the
  gap we close (for §5 and the process-flow visual).
- **`profiles/<active>/knowledge/icp-personas.md`** — the buyer persona's worldview and what they're
  allergic to (for §8).

**Brand colours:** resolve from the brand kit — `palette.canvas` (background), `palette.primary`
(action/emphasis), `palette.accent` (highlight), `palette.ink` (text); use the `_light` variants for
a light-mode document. Bind `--product` when the dossier leads with one product, so a sub-brand
accent is picked up. Pass the resolved hexes to the visuals script in Step 6.

If **no** profile resolves, use a neutral achromatic fallback — `#0B0B0F` ground, `#FFFFFF` text,
`#9AA0A6` accent — and say in the output that the document is unbranded.
> Corrected 2026-09-01. This step used to name a hardcoded "on-brand default palette" of navy
> `#0A0E27` / blue `#3E7BFA` / purple `#B57BFF` / cyan `#22D3EE`. Those matched no tenant in this
> repo — it was a fifth invented palette described as on-brand. A fallback must look obviously
> unset, never like somebody's brand.

**Byline default = the company brand** (`deck_byline` / `brand_name`), never an individual's name.

---

## Step 2 — Gather inputs

- **Required:** the target **account** (company) and the target **buyer** (name + role/persona).
- **Optional:** any existing `solution-design`, `deck-research`, `call-prep`, deck, call notes, or
  CRM context to mine instead of starting cold.
- If the account or buyer is **missing or ambiguous**, ask **one** concise clarifying question
  before researching — don't research the wrong company or persona.

---

## Step 3 — Mine provided materials (research first)

Before any web search, extract what's already known from uploaded docs and prior skill outputs in
the account folder `content/<active>/accounts/<account-slug>/` (see CLAUDE.md "Per-account outputs"):

- Who the account is and what they do.
- The buyer's role / persona.
- The product or use case in play.
- Named systems and agents.
- Any internal "appendix" or persona notes worth reusing.

Prior outputs to look for: `solution-discovery-*`, `solution-design-*`, `deck-research-*`,
`call-prep-*`, `prospects-*`, `outreach-*`.

---

## Step 4 — Fresh web research to verify and fill gaps

Confirm anything time-sensitive against **current** sources. **Never trust dates from older internal
docs** — they go stale fast. Explicitly re-check:

- **Funding / valuation** (rounds close and re-price).
- **Leadership** (people move).
- **Product launches** (roadmaps slip).
- **ANY regulatory or compliance dates** — laws get delayed, amended, or phased. If a deadline has
  moved, that is itself the story (see §6 below).

**Capture the source URL for every external claim as you go** — they become §11 (Sources).

---

## Step 5 — Generate visuals (before the build — the document embeds them)

Derive the visual labels from the **active profile's `product.md`** (loaded in Step 1):

- **`--gap-title`** — a short headline for the three-card problem visual (e.g. "Where the gaps are today").
- **`--gap-cards`** — exactly 3 concise problem labels (≤5 words each), semicolon-separated, drawn
  from the **"The problem"** or **pain** section of `product.md`. Do NOT use defaults from prior runs.
- **`--flow-steps`** — 3–5 stage labels (≤3 words each), semicolon-separated, drawn from the
  product's core value chain or solution phases in `product.md`.
- **Optional product hero** — for a "what the solution looks like" visual, you may embed one real product
  screenshot from `profiles/<active>/knowledge/brand/product-screenshots/` (e.g. `ss-dashboard-overview.png`;
  see that folder's `INDEX.md`). It shows the *proposed* product, not the customer's systems — caption it as such.

Then run the bundled helper with those derived values plus the resolved brand colors:

```bash
python scripts/dossier_visuals.py --out <work_dir> --which both \
  --bg "<bg_hex>" --accent1 "<accent1_hex>" --accent2 "<accent2_hex>" --accent3 "<accent3_hex>" \
  --gap-title "<gap title from product.md>" \
  --gap-cards "<label 1>;<label 2>;<label 3>" \
  --flow-steps "<step 1>;<step 2>;<step 3>;<step 4>"
```

`--gap-cards` and `--flow-steps` are **required** — the script exits with an error if either is
omitted. Always derive them from the active profile; never copy labels from a previous dossier for a
different profile or product. Keep them readable at ~6in wide.

---

## Step 6 — Build the document

The `.docx` is rendered from a **declarative JSON spec** by a **committed builder** — you do not
hand-write and run an arbitrary program; the least-privilege policy only permits running *these
specific reviewed scripts* (exactly as `python` is gated to `dossier_visuals.py`). Your job is to
**compose the spec**; the script renders it deterministically.

1. Write `<work_dir>/dossier-spec.json` — a `{ "brand": {…}, "closingLine": "…", "blocks": [ … ] }`
   document. The **block types and the fixed 12-section order** are defined in
   [references/document-structure.md](references/document-structure.md): `banner`, `callout`,
   `heading`, `paragraph`, `facts_table`, `table`, `two_col`, `questions`, `image`, `sources`.
   - Put the **resolved brand hexes** from Step 1 in `"brand"` (`bg`/`accent1`/`accent2`/`accent3`).
   - Embed the Step-5 PNGs with `{"type":"image","path":"gap.png","widthIn":6}` (paths are resolved
     relative to the spec file).
   - Substitute `[buyer]`, `[account]`, `[agents/product]` placeholders with the real names.
2. Render it with **`scripts/render_dossier_pydocx.py`** (python-docx — always available, no
   external install needed):

```bash
uv run python scripts/render_dossier_pydocx.py <work_dir>/dossier-spec.json \
  <work_dir>/account-dossier-[account]-[YYYY-MM-DD].docx
```

This is the **primary/default renderer**. `scripts/build_dossier.js` (docx-js, Node) reads the
identical spec and is kept as an alternative for local dev when the `docx` npm package happens to be
installed — but `npm install`/`npx` are hard-denied by the agent's permission policy
(`agent/permissions.py:_DANGEROUS_PROGRAMS`), so `node scripts/build_dossier.js …` reliably fails
with `Cannot find module 'docx'` in this environment. Both renderers are committed and reviewed;
prefer the Python one unless you already know the Node module is present.

(`scripts/` is this skill's directory — run from here or prefix with the skill's absolute path.)

---

## Step 7 — Validate and deliver

1. **Validate** the docx with the docx skill's validator: `python <docx-skill>/scripts/office/validate.py <doc>.docx`.
2. **Convert to PDF** via the docx skill's headless LibreOffice wrapper, then rasterise to confirm
   it is **≤4 pages** and renders correctly (banner, tables, callouts, embedded PNGs, hyperlinks):

```bash
python <docx-skill>/scripts/office/soffice.py --headless --convert-to pdf <doc>.docx
pdftoppm -jpeg -r 150 <doc>.pdf page   # one page-NN.jpg per page → eyeball + count
```

   If it overflows 4 pages, tighten prose / trim table rows in the spec and re-run Step 6.
3. **Save** the final `.docx` to the **per-account folder** `content/<active>/accounts/<account-slug>/`
   (`<account-slug>` = `python -m gtm_core.slugify "<company name>"` — always run the CLI, never
   hand-kebab-case it, so every skill lands on the same folder for the same account; create the
   folder if needed — see CLAUDE.md "Per-account outputs"), named
   `account-dossier-[account]-[YYYY-MM-DD].docx`. Never save it to the repo root or the bare working
   folder.
4. **Present** a one-line summary of what's inside and append a `⟦FILE:…⟧` sentinel for each
   deliverable so the Telegram cockpit sends the files to the operator automatically:

```
⟦FILE:/absolute/path/to/account-dossier-[account]-[YYYY-MM-DD].docx⟧
⟦FILE:/absolute/path/to/account-dossier-[account]-[YYYY-MM-DD].pdf⟧
```

   Put these on their own lines at the very end of your response, after all prose.

---

## Brand & style rules

- **On-brand, but a proper Word doc — not a deck.** Navy banner; brand-blue **uppercase** section
  headings with a thin accent rule; light shaded fact tables; colored callout boxes.
- **Plain English throughout.** Define jargon inline — e.g. "DMS (dealer management system)". Write
  for a smart reader with no domain context.
- **Concise.** Every section earns its place. Short paragraphs for prose; tables for facts; bullets
  only where they aid scanning.
- **Candid about maturity.** Do NOT overclaim or invent urgency. Honest framing builds trust with
  technical and legal buyers.
- **Default byline = the company brand**, not an individual's name.

---

## Guardrails

- **Never fabricate.** Mark unknowns as unknown; don't invent funding, headcount, or dates.
- **Product-accuracy discipline on any capability claim** — if the dossier states what the active
  company's product does, tag it SHIPPED/CONDITIONAL/ROADMAP and never state conditional/roadmap as live;
  verify cited facts too: `docs/product-accuracy.md`.
- **Every external claim gets a source** (§11). Internal docs are referenced as plain text — never
  as `#` placeholder links (they break docx validation).
- **No fake urgency.** If a regulatory deadline moved, say so and turn it into an honest angle.
- **§9 questions follow the evidence-based method, not vibes.** Sequence and phrase them per
  `docs/sales-questions-by-deal-phase.md` (Phase 2 + Phase 4, and its anchor-first technique) and
  `references/document-structure.md` §9 for how that plays out in this document's three groups.
  Escalate to Implication and Need-payoff questions — don't stop at Situation.
- **Match §9's sequencing weight to deal complexity.** Run the full anchor → Problem → Implication →
  Need-payoff chain when the account reads as a considered, multi-stakeholder buy (most of this
  ICP — security/compliance/infra decisions pull in more than one approver regardless of company
  size). Compress toward Situation + Need-payoff only when research clearly shows a single
  decision-maker, low-consequence purchase — don't run major-sale-weight discovery on a self-serve
  buy, and don't skip it just because the account is small.
- **Fail closed on §9 if there's nothing to anchor on.** If §5/§6/§7 didn't surface a concrete
  friction point, say so in the anchor callout and ask for more detail rather than emitting nine
  confident questions on a thin research pass — this matters most on a **research pack** or
  **prospecting-brief** account, where Step 4's deep verification was deliberately skipped.
- **Hard cap 4 pages.** If it overflows, tighten prose — don't drop required sections.
- **Don't run environment checks before the real step.** No `which`, `command -v`, version flags,
  `python -c "import …"`, or `python3 - <<'PY'` heredocs. The container image pre-installs
  everything the dossier needs: LibreOffice (`soffice`), Node.js, `python-docx`, Pillow, `pdftoppm`.
  If a dependency is genuinely missing the script itself will exit with a clear `ModuleNotFoundError`
  or "command not found" — that's the real check. Just run the script directly.
- **Don't mix commands in a compound statement.** Don't join two commands with `;` or `&&` in a
  single Bash call. If one of the sub-commands would be blocked (e.g. `python3 -c`), the whole
  compound is denied — even if the other sub-command was fine. Run each command in its own Bash call.
- Close with the line: **"Internal briefing — not for customer distribution."**

---

## Exec one-pager variant

For a **principal who already knows how to run the call** (e.g. a CEO briefing another CEO before a
founder-to-founder or partnership conversation) — someone who needs substance and a judgment call,
not seller coaching — use the **hard 1-page exec variant** instead of the standard ≤4-page dossier:

- Start from **[references/exec-onepager-template.json](references/exec-onepager-template.json)** —
  copy its blocks into a fresh spec, fill every `[bracket]`, and render with the same
  `scripts/render_dossier_pydocx.py` pipeline (Steps 5–7 unchanged).
- **Drops** everything that's seller-coaching, not substance: the full company snapshot, DO/DON'T
  table, and questions-to-ask bank.
- **Keeps** only: a one-line framing callout, a **product brief** (Claim / Pain / Gain / How it
  works, quoted from the target's own materials where possible), a **credibility calibration**
  paragraph (blunt, evidence-based read on the founder/company — corrections stated plainly, never
  narrated as "corrected from first-pass research," since the principal never saw a first pass), and
  one closing callout.
- **The closing callout adapts to whether the meeting is already decided.** If the principal hasn't
  committed yet, it's a take/pass recommendation. If they've already agreed to take the call, replace
  it with **"What to get from the call"** — the 2–3 concrete unknowns that determine the outcome,
  framed as a decision tree (if X, pursue Y; if not, there's nothing to pursue) — not a redundant
  "should you take this meeting" recommendation.
- Same brand/validation/delivery discipline as the standard dossier: resolve brand hexes from
  PROFILE, validate + rasterize to confirm **exactly 1 page**, save to the account folder, close with
  an internal-only line, `⟦FILE:…⟧` sentinels.
- Origin case: an exec one-pager saved to
  `content/<active>/accounts/<account-slug>/<account>-ceo-onepager-<YYYY-MM-DD>.docx` (a CEO brief for
  the account's exec).

---

## Prospecting brief variant

For a **cold, scored-but-unmet Tier-A lead** — a rep with **zero context and no meeting booked
yet** — use the **hard 1-page prospecting-brief variant** instead of the standard ≤4-page dossier
or the exec one-pager. This is the artifact the `prospect` skill's Tier-A sweep generates
automatically for a newly-qualified account; it is deliberately the cheapest of the three variants
because it may run across many accounts in one pass, not just one.

- **Trigger:** invoked automatically for each account the `prospect` skill's
  `tier-a-needing-dossier` sweep surfaces, or manually ("prospecting brief for [account]", "cold
  brief on [account]").
- **Reader:** a rep who has never touched this account — no prior relationship, no scheduled call,
  possibly only a resolved name/title/email and nothing else.
- **Research — skip the deep pass.** Do **not** run Step 4's fresh web verification
  (funding/leadership/regulatory dates). Rely on what the pipeline already resolved for this
  account — pull `why_now`, `cohort`, and `intent_topics` for the account's row in
  `content/<active>/prospects/sequences/.pool/master-list.csv` — plus **one light company-overview
  lookup** (what they do, in one or two sentences) if that isn't already obvious from the company
  name/domain. This keeps per-account cost proportional to running the sweep across every Tier-A
  account, which is the entire reason generation is gated to Tier A in the first place.
- **Content** (format at your discretion, but the brief must give the rep all four of):
  1. **Who they are** — 2-3 sentences on the company and what it does.
  2. **Why they're qualified** — the ICP cohort and why-now signal that scored this account Tier A
     (from `cohort`/`why_now`/`intent_topics`), stated as the concrete reason this lead is worth a
     rep's time, not a generic ICP-fit claim.
  3. **Likely needs** — 2-3 needs/pain points a company like this, in this cohort, plausibly has —
     inferred, not fabricated as confirmed fact.
  4. **How [lead product] concretely helps** — one short paragraph grounding the fit in
     `profiles/<active>/knowledge/product.md`, with the same SHIPPED/CONDITIONAL/ROADMAP
     product-accuracy discipline as the standard dossier (`docs/product-accuracy.md`) — never state
     a conditional/roadmap capability as live.
  - Close with one line naming the recommended next step (a call to book, a specific angle to open
    with) — not a hard sell, an internal prep note.
- Start from
  **[references/prospecting-brief-template.json](references/prospecting-brief-template.json)** —
  copy its blocks into a fresh spec, fill every `[bracket]`, render with the same
  `scripts/render_dossier_pydocx.py` pipeline (Steps 6-7 unchanged), validate + rasterize to
  confirm **exactly 1 page**.
- **Output path:** `content/<active>/accounts/<account-slug>/prospecting-brief-<account-slug>-<YYYY-MM-DD>.docx`,
  where `<account-slug>` is computed via `python -m gtm_core.slugify "<company name>"` — the
  canonical slug, not a hand-picked one, so the pipeline's dossier-existence check
  (`tier-a-needing-dossier`) and the status dashboard can both find it reliably. The
  `prospecting-brief-` filename prefix is deliberate — distinct from `account-dossier-` and
  `*-onepager-` — so a filesystem check for "does this account already have a dossier of any kind"
  can't confuse artifact types, and so an account that later earns a full dossier or exec one-pager
  can carry both without a naming collision.
- Same brand/validation/delivery discipline as the other two variants: resolve brand hexes from
  PROFILE, close with the internal-only line, `⟦FILE:…⟧` sentinels.

---

## Research pack variant

For **bulk coverage across a large, mixed-tier account list** — a `prospect` skill dossier sweep
running across hundreds of accounts, most of them Tier-B or bulk-sourced, where even the prospecting
brief's one-page **docx** render (LibreOffice conversion, PDF rasterization, page-count validation) is
too much per-account overhead to run at that scale. This is the variant this skill's Step 8 broadened
sweep uses by default for every candidate that isn't Tier-A; Tier-A candidates still get the fuller
prospecting-brief docx above. Origin: 439 accounts dossiered this way in one pass on 2026-08-12, after
the Tier-A-only sweep left 439 of 496 sequenced contacts with zero research behind their Why Now
clause.

- **Trigger:** invoked automatically by the `prospect` skill's `accounts-needing-dossier` sweep for a
  non-Tier-A candidate, or manually ("research pack for [account]", "quick dossier for [account], no
  visuals").
- **Reader:** the same downstream consumer as the prospecting brief — a rep or a merge-sequence Why
  Now clause — not a person reading a formatted document. There is no requirement this be
  presentation-quality; it exists to give `gtm_core.merge_hygiene.signal_clause` and
  `draft-outreach`/`email-sequence` real research to draw from instead of a bare one-line `why_now`.
- **Research — same one light company-overview lookup as the prospecting brief, plus the account's
  `why_now`/`cohort`/`intent_topics`.** Do **not** run Step 4's fresh web verification
  (funding/leadership/regulatory dates) — same cost-containment tradeoff as the prospecting brief, for
  the same reason: this keeps per-account cost proportional to running across an entire non-Tier-A
  pool. This is why `gtm_core.account_integrity`'s `leadership-freshness` check flags every account
  whose only dossier is this variant (or the prospecting brief) — a departure, a death, or a reorg
  would not have been caught, and the operator should know that gap exists rather than assume it was
  checked.
- **Output — plain markdown, no docx/pdf/images.** Fixed section order:
  ```
  # <Company> — account dossier

  ## Contact
  ## What they run today
  ## Regulated or sensitive data in play
  ## Where agent identity bites
  ## Why now
  ## Buyer map
  ## Confidence
  ## Sources
  ```
  "Why now" bullets are dated and sourced, same discipline as the standard dossier's §6/§11 — mark
  unknowns as unknown, never invent a date or a source.
- **Output path:** `content/<active>/accounts/<account-slug>/dossier-<account-slug>-<YYYY-MM-DD>.md`,
  where `<account-slug>` is computed via `python -m gtm_core.slugify "<company name>"` — the canonical
  slug, so the pipeline's dossier-existence check (`accounts-needing-dossier`) and the status dashboard
  both find it. The `dossier-` prefix (vs. `prospecting-brief-`/`account-dossier-`/`*-onepager-`) keeps
  every variant distinguishable by filename alone.
- **No `⟦FILE:…⟧` sentinel** — this variant is written straight to the account folder as part of a
  batch sweep, not delivered as a standalone document to send to a seller.
- **No PROFILE/brand-hex resolution, no visuals script, no docx renderer, no page-count validation** —
  the entire reason this variant exists is to skip that pipeline. Skip Steps 5–7 of the standard flow
  entirely.

---

## Trigger examples

1. **"Make a dossier for Acme Motors and their VP of Digital."** → research Acme + the VP persona,
   build the ≤4-page briefing, deliver `.docx` + PDF.
2. **"Prep me on my meeting with Jane Doe, CISO at Globex."** → buyer-led: profile Globex, then build
   §8 around the CISO worldview and a DO/DON'T table.
3. **"One-pager on Initech and their Head of AI — I have the solution-design already."** → mine the
   provided `solution-design`, verify time-sensitive facts on the web, then build the dossier.
4. **"Prospecting brief for Umbrella Health — new Tier-A account, no meeting yet."** → no deep web
   research; pull `why_now`/`cohort`/`intent_topics` from `master-list.csv`, build the 1-page
   prospecting brief.
