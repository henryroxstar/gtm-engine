"""CLI + shared function: the one canonical account-folder slug.

Skills are markdown executed by the brain via tools; they cannot import Python.
Every skill that creates ``content/<profile>/accounts/<account-slug>/`` (account-
dossier, draft-outreach, solution-design, call-prep, ...) needs the *same* slug for
the *same* company, or the same account silently ends up with two folders. Before
this module existed, each skill free-text kebab-cased the company name by hand —
confirmed real drift: "Abacus.AI" was hand-slugged to ``abacus-ai`` in one run, but
this function (unchanged from the original ``prospects_import._slug``) produces
``abacusai``. That's an accepted, documented gap for the ~800 folders that predate
this module (see ``docs/prds/`` — no retroactive rename); it is not accepted for
anything created going forward, hence one shared function everyone calls.

VPS invocation:   python -m gtm_core.slugify "<company name>"
Local invocation: python "$CLAUDE_PLUGIN_ROOT/lib/gtm_core/slugify.py" "<company name>"

Prints the slug to stdout and exits 0.
"""

from __future__ import annotations

import hashlib
import re
import sys


def slug(name: str) -> str:
    """Kebab-case a company name into a filesystem-safe, stable account slug.

    A pure non-ASCII name (e.g. a CJK company) strips to empty under the normal
    rule; falls back to a stable, non-empty, collision-resistant token so the row
    keeps a usable id instead of "" (which would collide with every other such row).
    """
    n = re.sub(r"[^\x00-\x7f]", "", name.lower())
    n = re.sub(r"\s+", "-", n.strip())
    n = re.sub(r"[^a-z0-9\-]", "", n)
    n = n.strip("-")
    if n:
        return n
    stripped = name.strip()
    if not stripped:
        return ""
    return "co-" + hashlib.sha256(stripped.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print('usage: python -m gtm_core.slugify "<company name>"', file=sys.stderr)
        return 2
    print(slug(argv[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
