"""Deprecated alias. The implementation moved into ``tests/linter/outreach/`` on 2026-09-24.

Kept for the two importers FR3 Task 3.1 was told not to edit — ``gtm_core/build_eval_sheet.py``
and ``agent/mcp/judge/render.py``, which another task owns. Delete both aliases (this file and
``merge_render_linter.py``) when those two move to ``from outreach import ...``.
"""

import outreach as _pkg

globals().update({_k: _v for _k, _v in vars(_pkg).items() if not _k.startswith("__")})
