"""``python -m gtm_core.messaging`` — see :func:`gtm_core.messaging.cli.main`.

A package cannot be executed through its ``__init__``, and the CLI form is the one the plan
and the skills cite — so the entry point has to keep working under exactly that name.
"""

from __future__ import annotations

from .cli import main

raise SystemExit(main())
