# Bespoke Solution Design Scaffold

A **company-agnostic method** for synthesising a custom architecture when there is no off-the-shelf
product to map the use case onto (Mode B). This document defines the starting scaffold, the
feasibility rubric, the V1/V2 discipline, and the honesty rule. All are **method, not product fact**
— safe to live in `plugin/`.

---

## Generic architecture layers

Start here. Adapt freely — add, split, or rename layers to match the build. The names below are
defaults; use whatever makes the customer's domain legible.

| Layer | What goes here |
|---|---|
| **Interface** | How the customer or operator interacts (Telegram bot, web app, API, CLI, webhook) |
| **Agents / Logic** | The agent(s) or orchestrated workflow steps that run the business logic |
| **Intelligence / Data sources** | External data the agents consume (price feeds, news APIs, search, internal DBs) |
| **Integration / Egress** | Third-party platforms the system writes to or triggers (marketplaces, CRMs, notification channels) |
| **Data / State** | What the system owns and persists (inventory, transaction history, user preferences, audit log) |

**Rule:** Draw a box for a layer only when the design has a concrete, named component to put in it.
An empty layer placeholder is worse than omitting the layer — it signals a capability that doesn't
exist yet and creates false confidence.

---

## Feasibility rubric

Run this per proposed capability before finalising V1 scope. One row per capability in the feature
wishlist.

| Capability | Buildable now? | Data / API source | Hard parts / unknowns | Confidence | V1 or V2 |
|---|---|---|---|---|---|
| _e.g. Auto-reprice listings_ | Yes | TCGplayer API (public), Carousell (unofficial) | Carousell has no official API — scraping only; rate-limit risk | M | V1 |
| _e.g. Buy-side card evaluation_ | Yes | Price history + grading data (public) | Grading sources vary by set; normalisation needed | H | V1 |
| _e.g. Wishlist price alerts_ | Yes | Same price feeds | Threshold logic is simple | H | V1 |
| _e.g. Cross-platform arbitrage_ | Partial | Cardmarket access needed | No SG Cardmarket presence; USD→SGD fx layer needed | L | V2 |

**Confidence key:**
- **H** — high: data source confirmed, approach clear, no material unknowns.
- **M** — medium: approach clear, one dependency not yet confirmed (e.g. unofficial API).
- **L** — low: approach unclear or data source unconfirmed. **Force to V2 or flag as a risk.**

---

## V1 / V2 discipline

Every feature gets an explicit cut. A **"What we are NOT building (V1)"** list is **mandatory** and
non-optional — it signals scope discipline and prevents a first delivery from sinking under ambition.

### V1 — ships first
- Only capabilities with confidence **H or M** and a confirmed (even unofficial) data source.
- The minimum set that delivers the core value proposition.
- Aim for a vertical slice the customer can show a stakeholder in 4–6 weeks.

### V2 — next iteration
- Capabilities requiring a missing or unconfirmed data source.
- Nice-to-have automation of a step that V1 already makes significantly faster.
- Any **L-confidence** capability, regardless of strategic priority.

### What we are NOT building (V1)
List each excluded feature explicitly. This section is non-optional. It covers:
- Features deferred because data is unavailable or unconfirmed.
- Features deferred because complexity exceeds V1 scope.
- Explicit surfaces the system does NOT expose (e.g. public storefront, external API, mobile app).

---

## Honesty rule

> **Don't scope a capability that has no real data source or can't be built now. Mark it V2 or flag
> it as a risk — never scope optimistically.**

This is the bespoke equivalent of the gateway honesty rule ("don't draw a box for a feature the
product doesn't have"). Apply it at every feasibility row:

- Required API doesn't exist or is unofficial/rate-limited → confidence M or L; state the risk explicitly; do NOT assume it works.
- Integration requires significant unknown engineering → confidence L; push to V2.
- Customer expects a capability but the data source isn't there → flag it as an open question and put it in V2, not V1 scope.

The feasibility table is the evidence trail for this rule. If a V1 capability has no row in the
table, it hasn't been assessed — assess it before scoping it.

---

## Worked example — Humble Marketplace

*The sample card-pricing build (`content/template/accounts/sample-account/`) is the
canonical Mode B reference. The architecture below was produced from that scoping session.*

**System architecture:**
```
Interface:     Telegram (Hermes) ←→ Web app
Agents:        Reprice · Buy-side eval · Source discovery · News digest · Wishlist hunter
Intelligence:  Price sources (managed + auto-discovered) · Market news · Wishlist price tracking
Data:          Inventory (in hand → listed → sold) · P&L + Transactions · Wishlist
```

**V1 scope decided:** Auto-reprice + Buy-side eval + Source discovery agents.
Data sources confirmed: TCGplayer API (public), Carousell (unofficial — risk noted).

**What was NOT built (V1):** Automated purchasing (requires payment integration), public storefront
(separate surface), cross-platform arbitrage (no official Cardmarket API for SG market).
