"""The product manifest: what a second product must provide, may share, and may never override.

One closed table, one home. ``gtm_core.run_scope`` enforces it; ``tests/unit/test_product_manifest.py``
fails the day a tenant gains a knowledge file nobody classified. Split out of ``run_scope`` so the
table can be read on its own, without the resolver around it.

The split underneath the table: a company-level knowledge file is either a **fact about the company**
(competitors, aliases, voice, the ban lists, lane policy) — the same whichever product asks, so no
product may override it — or **one product's argument** (claims, proof, angles, premises, search
terms), which a second product must bring for itself and never borrow.
"""

from __future__ import annotations

#: A second product must provide every one of these. Missing → refuse, never fall back: a
#: fallback here is how a Stream run would quietly argue Gateway's case.
PRODUCT_REQUIRED = frozenset(
    {
        "angles.toml",
        "claims.toml",
        "proof.toml",
        "premise-vocab.toml",
        "hook-matrix.md",
        "web-sweep.toml",
    }
)

#: Derived from another required file, so its absence is a state to report, not a missing input:
#: ``hook-matrix.md`` is generated from the product's ``angles.toml`` by ``messaging matrix``, and
#: a run scope that refused until it existed could never be used to generate it. The readers of a
#: derived file refuse instead (``run_scope.product_file``), which is where a fallback would bite.
PRODUCT_DERIVED = frozenset({"hook-matrix.md"})

#: A product copy wins if present, else the profile file is used **and the run header says so**.
SHARED_BY_DEFAULT: dict[str, str] = {
    "icp-scoring.toml": "targeting rules",
    "scorecard.toml": "scorecard",
    "icp-personas.md": "buyer personas",
    "role-vocabulary.toml": "seat pains",
    "case-studies.md": "case studies",
    "hooks.toml": "hook library",
    "market-scan-config.md": "market scan settings",
    "kinetic-chains.toml": "kinetic chain rules",
}

#: Facts about the company, or safety and voice rules, that no product may override. A copy under
#: ``products/<slug>/`` refuses the run.
TENANT_WIDE = frozenset(
    {
        "competitors.toml",
        "competitors.md",
        "regulators.toml",
        "send-windows.toml",
        "domain-aliases.toml",
        "voice.md",
        "voice-bans.txt",
        "voice-rules.toml",
        "outreach-banned-stems.txt",
        "outreach-case-studies.txt",
        "shared-phrases.txt",
        "gift-artifacts.txt",
        "lane-policy.toml",
        "strategic-accounts.toml",
        "funnel-yields.toml",
        "outbound-program-defaults.md",
        "suppression.csv",
        "PROFILE.md",
        "packs.toml",
    }
)

#: A registry a second product owns or does without. Never the profile's: a Gateway registry
#: would hand a Stream run Gateway's premise attestations. Optional, so not PRODUCT_REQUIRED.
PRODUCT_OWN_ONLY = frozenset({"signal-sources.toml"})

#: Config-format files that are legitimately at either level and are not a scoped fact.
NOT_PRODUCT_FILE = frozenset({"BRAND.toml", "syften-filters.json"})

_CONFIG_SUFFIXES = (".toml", ".txt", ".json")


def classify(filename: str) -> str | None:
    """The manifest class of a knowledge filename, or ``None`` when it is on no list."""
    if filename in PRODUCT_REQUIRED:
        return "product-required"
    if filename in SHARED_BY_DEFAULT:
        return "shared-by-default"
    if filename in TENANT_WIDE:
        return "tenant-wide"
    if filename in PRODUCT_OWN_ONLY:
        return "product-own-only"
    if filename in NOT_PRODUCT_FILE:
        return "not-product-file"
    return None
