# `products/<slug>/`

One folder per product a company sells. A folder holding only `PRODUCT.md`, `BRAND.toml`, references
and how-tos is documentation: it never changes how a prospecting run behaves.

**The default product owns the profile level.** Its targeting and messaging files are the profile's
own `knowledge/` files, so it needs nothing here. A *second product* is a product in `PROFILE.md`
other than the default whose folder holds **every** one of the required files below. A folder with
some but not all is being set up: it is not offered, and `--product <it>` stops with
`product-not-ready`, naming what is missing. A company with
no second product behaves exactly as if this manifest did not exist.

A run for a second product (`--product <slug>`, or the answer to the one question the run asks) is
checked against a **closed manifest** (`gtm_core/run_scope.py`). A file on none of these lists is
refused, so a new knowledge file must be classified before any product can carry it.

| Class | Files | What a second product does |
|---|---|---|
| **Required** | `angles.toml`, `claims.toml`, `proof.toml`, `premise-vocab.toml`, `web-sweep.toml` | Must exist here. Missing → the run stops; it never falls back to the default product's |
| **Derived** | `hook-matrix.md` | Generate it with `python -m gtm_core.messaging matrix --profile <p> --product <slug>`. Readers refuse to fall back to another product's |
| **Shared by default** | `icp-scoring.toml`, `scorecard.toml`, `icp-personas.md`, `role-vocabulary.toml`, `case-studies.md`, `hooks.toml`, `market-scan-config.md` | A copy here wins; otherwise the company file is used and the run header says so in one sentence |
| **Company-wide (refused here)** | `competitors.toml`, `domain-aliases.toml`, `voice.md`, the ban lists, `lane-policy.toml`, `funnel-yields.toml`, suppression | A copy here **stops the run**. Company facts and safety rules are not a product's to override |
| **Own only** | `signal-sources.toml` | This product's own registry, or none. Never the company's |

Nothing an operator does not ask for is implied: the product is always an argument, never inferred
from a row, a file name or an earlier run.
