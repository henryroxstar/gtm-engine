"""``python -m gtm_core.role_vocabulary`` — see :func:`gtm_core.role_vocabulary.main`.

This file exists only because the module became a package on 2026-09-21 (§R10 split). A
package cannot be executed through its ``__init__``, and the CLI form is cited in
``profiles/_template/knowledge/role-vocabulary.toml`` and in the PRD — so the entry point
has to keep working under exactly the name those already name.
"""

from __future__ import annotations

from . import main

raise SystemExit(main())
