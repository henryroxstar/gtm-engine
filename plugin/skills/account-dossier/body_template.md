
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

- **`profiles/<active>/PROFILE.md`** — `brand_palette` (hex list), `deck_byline` / `brand_name`
  (the byline default), `output_folder`, `language`.
- **`profiles/<active>/knowledge/brand-notes.md`** — palette, typography (geometric sans-serif,
  no serifs), ownable taglines, and the "don'ts".
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

**Brand colors:** use `brand_palette` from PROFILE / `brand-notes.md`. If no profile resolves, fall
back to the on-brand default palette: navy `#0A0E27` background, electric blue `#3E7BFA`, purple
`#B57BFF`, cyan `#22D3EE`, white text. Pass the resolved hexes to the visuals script in Step 6.

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

The `.docx` is rendered by the **committed builder** `scripts/build_dossier.js` (docx-js). You do
**not** hand-write and run a `node` program — the least-privilege policy only permits `node` to run
*this one reviewed script* (exactly as `python` is gated to `dossier_visuals.py`). Your job is to
**compose a declarative JSON spec**; the script renders it deterministically.

1. Write `<work_dir>/dossier-spec.json` — a `{ "brand": {…}, "closingLine": "…", "blocks": [ … ] }`
   document. The **block types and the fixed 12-section order** are defined in
   [references/document-structure.md](references/document-structure.md): `banner`, `callout`,
   `heading`, `paragraph`, `facts_table`, `table`, `two_col`, `questions`, `image`, `sources`.
   - Put the **resolved brand hexes** from Step 1 in `"brand"` (`bg`/`accent1`/`accent2`/`accent3`).
   - Embed the Step-5 PNGs with `{"type":"image","path":"gap.png","widthIn":6}` (paths are resolved
     relative to the spec file).
   - Substitute `[buyer]`, `[account]`, `[agents/product]` placeholders with the real names.
2. Render it:

```bash
node scripts/build_dossier.js <work_dir>/dossier-spec.json \
  <work_dir>/account-dossier-[account]-[YYYY-MM-DD].docx
```

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
   (`<account-slug>` = the target company, kebab-cased; create the folder if needed — see CLAUDE.md
   "Per-account outputs"), named `account-dossier-[account]-[YYYY-MM-DD].docx`. Never save it to the
   repo root or the bare working folder.
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
- **Every external claim gets a source** (§11). Internal docs are referenced as plain text — never
  as `#` placeholder links (they break docx validation).
- **No fake urgency.** If a regulatory deadline moved, say so and turn it into an honest angle.
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

## Trigger examples

1. **"Make a dossier for Acme Motors and their VP of Digital."** → research Acme + the VP persona,
   build the ≤4-page briefing, deliver `.docx` + PDF.
2. **"Prep me on my meeting with Jane Doe, CISO at Globex."** → buyer-led: profile Globex, then build
   §8 around the CISO worldview and a DO/DON'T table.
3. **"One-pager on Initech and their Head of AI — I have the solution-design already."** → mine the
   provided `solution-design`, verify time-sensitive facts on the web, then build the dossier.
