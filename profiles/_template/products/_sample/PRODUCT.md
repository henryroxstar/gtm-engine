# Product: <slug>

Replace this file with per-product config when your profile has multiple distinct products
with different ICPs, scan configs, or knowledge packs.

```
name:        <Product Name>
slug:        <url-safe-slug>
description: <one-line value proposition>
# Which product-bound skills this product unlocks. A skill declaring
# `requires_capability: [x]` runs only when some product here provides `x`.
# Valid values are the slugs in gtm_core/capability_registry.py KNOWN_CAPABILITIES.
# Trim this list to what your product actually does — the sample declares the
# three that the shipped SA-chain skills need, so a fresh clone runs green.
capabilities:  [gateway, solution-architecture, technical-discovery]
```

If all your products share one knowledge pack, you don't need per-product folders —
skills resolve to profiles/<company>/knowledge/ by default.
