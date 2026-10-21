# Seam Finding — public/private triage and the self-named gap

The research method behind a `product-partner-brief`. §1 is how to see what a product company
actually ships. §2 is the core technique. §3 is the honesty spine that keeps the result defensible.

---

## 1 — Public vs private triage

You need three different answers, and they are easy to conflate:

1. **What do they ship?**
2. **What is publicly visible?**
3. **What am I allowed to cite?**

### Registries expose metadata even when the source is private

A package published from a private repository still publishes **version history, licence, publish
dates, dependency lists, and often a README**. That is usually enough to establish maturity,
cadence, and architecture direction without seeing a line of source.

**Registry web pages are frequently JavaScript applications** that return an empty shell to a plain
HTTP read. When a page comes back with no content, do not conclude the package is empty — switch to
the JSON API:

| Ecosystem | JSON endpoint |
|---|---|
| Rust | `https://crates.io/api/v1/crates/<name>` |
| npm | `https://registry.npmjs.org/<name>` |
| Python | `https://pypi.org/pypi/<name>/json` |
| Go | `https://proxy.golang.org/<module>/@v/list` |

**Generated documentation sites** (docs.rs, pkg.go.dev, published TypeDoc/Javadoc) render the full
public API surface of a package whose repository is private. The type names alone are often where
the seam is found — see §2.

### A 404 is ambiguous — use a positive control

`404` on a repository means **private OR nonexistent OR your access is broken**. These lead to
opposite conclusions, and guessing wrong has burned real analyses.

Always pair the negative with a positive control:

```bash
gh api repos/<owner>/<repo>   # 404
gh api users/<owner>          # 200  → access works, so the repo is genuinely not public
```

If the control also fails, the problem is your access, not their visibility. Fix that before
concluding anything. **Every mistake looks like a denial** — require the positive control before
attributing a result to the other side.

### Record what constrains the integration

Build one table and carry it into the brief:

| Artifact | What it does | Public / private | Licence | Maturity signal |
|---|---|---|---|---|

**Licence is not a footnote.** A strong copyleft licence changes what an integration may look like,
and proposing an embedding that the licence forbids marks the author as careless in the first
technical read.

### The citation rule

> **Private material shared with you under a relationship is understanding, never evidence.**

If a partner gave you repository or document access during a conversation, you may use it to
understand their design. You may **not** cite it, quote its internals, or reveal that you enumerated
it. Every claim that reaches the partner-facing brief must independently resolve to a public source.

Reading someone's private code and quoting it back at them does not read as diligence. It reads as
surveillance, and it is the fastest way to end a partnership conversation.

---

## 2 — The self-named gap

> Find the capability the partner's own type system, docs, non-goals, or roadmap **already names**
> but cannot produce — and show that our product is the shape that fits the slot.

A gap they named is a roadmap conversation. A gap we assert is a pitch. The difference is the entire
value of the technique.

### Where the slot hides

Search their public artifacts in roughly this order — highest signal first:

**A README's "non-goals" or "out of scope" section.** The single highest-signal text in any
repository. It is a list, written by them, of capabilities they have decided not to build.

**Type systems.** An enum variant, trait, interface, or state that is declared but unimplemented —
or, strongest of all, one whose *construction requires an input the system cannot generate itself*.
A type that can only be built from an external signature, attestation, endorsement, or authority is
a slot with a shape.

**Error and status types.** `Unsupported`, `NotImplemented`, `Unverified`, `Untrusted`, `External*`,
`Pending*`. Each names a state their system can be in and cannot exit alone.

**Configuration surfaces.** A field taking an endpoint, issuer, key, or provider for something they
do not ship. They have already designed the integration point; the question is only who fills it.

**Documentation hedges.** "out of scope", "future work", "bring your own", "you must supply",
"assumed to be handled by", "left to the operator", "beyond the scope of this document".

**Roadmaps, changelogs, issue trackers.** A feature that keeps slipping release to release is a
capability they want and keep failing to prioritise. The most-upvoted unimplemented issue is a
customer-validated slot.

**Unreleased sibling projects.** Reserved package names, empty repositories, and named-but-unshipped
components tell you what they believe they still need — and, if one points at your territory, they
are also the **watch item** for the layer boundary.

### Tier the seam and act on the tier

| Tier | What you found | Strength | Action |
|---|---|---|---|
| **1** | Named in **their own vocabulary**, structurally unable to produce it | Strongest | Build the brief. Quote the artifact. |
| **2** | Named as a **non-goal / out of scope** | Strong | Build it. Frame as a deliberate layer boundary. |
| **3** | Their **customers' context** requires it; they have no answer | Workable | Build it only with independent evidence of the requirement. |
| **4** | **We** assert a gap they have never acknowledged | None | **Stop. Do not write the brief.** |

**The Tier 4 stop is a real gate.** When only an asserted gap exists, report:

- what artifacts you searched,
- what you found,
- why it does not clear the bar,
- and the honest alternatives — a customer conversation rather than a partnership one, a narrower
  integration, or waiting until their roadmap creates the slot.

An unfounded partnership brief costs a relationship that a truthful "not yet" preserves.

### Quote the evidence

Carry the **exact artifact and phrase** into the brief. *"Your `X` variant requires an external
issuer"* is a fact they can verify in ten seconds. *"You have a trust gap"* is an opinion they can
dismiss just as fast.

---

## 3 — The honesty spine

Before writing a line, state precisely what their mechanism **does** guarantee and what it does
**not**. Getting this sentence right is what makes the whole document defensible:

> *Their mechanism proves X. It does not prove Y, Z, or W — and it was never meant to.*

Useful decompositions when a mechanism is doing less than its marketing implies:

| Often conflated | Actually distinct |
|---|---|
| integrity | *that content did not change* |
| attribution | *who produced it* |
| authority | *whether they were permitted to* |
| portability | *whether anyone else can verify it* |
| freshness | *whether it is still true* |

A mechanism that delivers one of these is frequently described — by its own authors, in good faith —
as if it delivered all five. Separating them is generous, accurate, and almost always where the seam
is.

**Say the narrow version out loud in the brief.** Conceding what our own product does *not* do, in
the same paragraph, is the cheapest credibility available and makes the seam claim land as analysis
rather than as sales.
