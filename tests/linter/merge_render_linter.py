"""Deprecated alias. The implementation moved into ``tests/linter/outreach/`` on 2026-09-24.

See ``outreach_pack_linter.py`` beside this file for why the two aliases still exist and when
they go. ``RULES_VERSION`` here is now the single merged value, not this module's old one.
"""

import outreach as _pkg

globals().update({_k: _v for _k, _v in vars(_pkg).items() if not _k.startswith("__")})
