# Quality gates

Eight gates. Each names its **witness** — an independent source the proposal's own text is checked
against (§R11: a gate that reads only the field it polices proves nothing) — what it reports when the
property holds and when it does not (§R18: a check with one possible output carries no information),
and the fix.

**Hard fail** blocks delivery of the external file. **Open** does not block a *draft*; tag it
**before sending** (keeps the external "Internal draft — not for distribution" line) or **before
signature** (does not), and list it under that heading in the internal brief's "Decisions needed".

Record every result in the internal brief's "Gate results" table. A gate not recorded was not run.

---

## Gate 1 — Not a contract

- **Witness:** the rendered `.docx` as well as the Markdown (a spec can differ from the source).
- **Passes when:** the status stamp and a validity date are present; there is no signature block, no
  acceptance field, no "agreed and accepted" or "accept by signing", and no statement that the proposal
  binds either party.
- **Hard fail when:** any of those appears, or the stamp is missing.
- **Do not match on the word "binding".** The required stamp says *non-binding*; a check for the bare
  word fails every correct proposal and so carries no information (§R18). Match acceptance and
  obligation *claims*, not vocabulary.
- **Fix:** remove the acceptance language; restore the stamp.

## Gate 2 — Term and total

- **Witness:** `python -m gtm_core.commercial quote … --json` output for the same key, units and term.
- **Passes when:** every monthly figure in the external proposal sits in the same section as the term,
  the minimum commitment, and a **year-1 total that equals the CLI's `year1_total`** (and the term total,
  if the term is not 12 months).
- **Hard fail when:** a monthly figure appears without term and year-1 total, or a total differs from
  the CLI.
- **Negative control:** before trusting a pass, confirm the gate would have caught a monthly figure
  stated alone — find one sentence where it would have.

## Gate 3 — Price provenance

- **Witness:** the tenant's `pricing.toml`, read through `python -m gtm_core.commercial keys` — **re-run
  now**, not the ledger's recorded output.
- **Passes when:** every currency figure in both files has a row in the figures ledger, and re-running
  each ledger command reproduces the figure.
- **Hard fail when:** a figure has no ledger row, or re-running gives a different number.
- **Open when:** a figure depends on an override whose reason cites an unresolved tenant open question
  (e.g. two papers disagree on list price).
- **Never:** do arithmetic in prose; never quote a "from" price as a firm figure.

## Gate 4 — Contract consistency

- **Witness:** the tenant's `knowledge/commercial/contract-baseline.md` and `key-terms.md` — the default
  agreement, **not** the rate card. Where a rate card and the agreement disagree, the agreement decides
  what may be promised.
- **Checks:**
  1. every asserted term (unit definition, term, exit, support boundary, service levels, data handling,
     liability, resale) matches a clause the baseline records;
  2. none of the banned words in `proposal-structure.md` is used as a commitment unless the baseline
     carries it (the required negations listed there are not hits);
  3. **the product named in the proposal is one the default agreement grants rights to.**
- **Hard fail when:** 1 or 2 fails.
- **When 3 fails** — the product is not on the paper — the proposal passes only if its key terms come
  from `key-terms.md` with every *per product* row set for this product (or omitted and listed). The
  signing instrument is then **open, before signature**, named in the internal brief.

## Gate 5 — Maturity

- **Witness:** the product's own `products/<slug>/PRODUCT.md` `status:` line, and the tenant's
  `knowledge/commercial/products.md`. **Not** the proposal's label, a deck, or a site badge.
- **Passes when:** every non-GA product has a maturity entry stating its status as the product record
  states it, that no GA date is committed, whether documentation is published, what the counterparty is
  committing to, and what happens if it does not mature in the term.
- **Hard fail when:** a non-GA product lacks that entry, or any release date, version or roadmap
  commitment appears.
- **Open when:** no exit or remedy for non-maturity exists in any tenant paper — the proposal states what
  is offered; the internal brief lists it for approval.
- **Capability claims** in a "How <product> would be used" section each trace to the product's own
  reference docs (record source and section in the internal brief). If the configuration has not run on
  a live instance, the proposal says it is illustrative and will be tested before use.
- **Also check** the owner file agrees with itself; if its header and body disagree, use the newer
  dated fact and record the contradiction as a finding.

## Gate 6 — Outbound value

- **Witness:** the CLI quote run with `--outbound` and `--outbound-label`, and the tenant deal desk's
  outbound-value policy.
- **Applies when:** anything flows from us to the counterparty (see `commercial-models.md` "Outbound
  value").
- **Passes when:** it sits in its own external section headed as separate from the commercial terms;
  conditions for payment tied to its purpose (including our product being in use), an observable start
  date, a witness for each condition, payment timing, longstop, repayment and instrument are stated; the internal brief's first line
  is the CLI's net position; an independence statement exists wherever it funds evidence about outcomes
  our product enables; a propriety review is listed where the end customer or pilot host is a public
  body.
- **Hard fail when:** it is phrased as a discount, rebate or credit; it appears inside the pricing
  table; the net position is missing or hand-computed; or funded evidence is described as independent.
- **Open when:** the tenant's approval route or policy for outbound value is undecided.

## Gate 7 — Precedent consistency

- **Witness:** the tenant deal desk's precedents log **and** a scan of
  `content/<active>/accounts/*/` for other `commercial-proposal-*` files and partnership plans.
- **Passes when:** comparable proposals (same shape or same product) are listed in the internal brief,
  and every material difference — price, unit, share, floor, outbound value — is marked deliberate with
  a reason, or raised as a decision.
- **Open when:** a difference exists with no recorded reason.
- **Never** silently match a precedent, and never silently differ from one.

## Gate 8 — Approved proof

- **Witness:** the approval pack in the named customer's account folder. A document title that says
  "ready for review" is not clearance.
- **Checks:** every customer name, logo, quote, metric or case study in the external proposal is cleared
  for external use in its approval pack; quotes match the pack verbatim; the speaker's name and title
  match the pack.
- **Also:** a third party named in the proposal — the counterparty's own customer, a pilot host — is
  named only if the counterparty has cleared it; otherwise describe it by shape ("a public hospital
  cluster").
- **Hard fail when:** uncleared proof appears in the external file.
- **Fix:** cut it, or describe by shape. Record the clearance ask in "Decisions needed".
