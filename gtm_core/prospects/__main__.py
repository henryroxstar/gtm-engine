"""Entry point for ``python -m gtm_core.prospects``.

A shim on purpose: the dispatcher lives in ``__init__`` so importing the package gives
you :func:`main` directly, and so running it as a module does not import this file
twice (which is what a package importing its own ``__main__`` produces).
"""

from __future__ import annotations

import sys

from . import main

if __name__ == "__main__":
    sys.exit(main())
