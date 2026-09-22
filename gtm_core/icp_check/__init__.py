"""ICP critique — a read-only pre-spend check on the ICP definition itself, never on its output.

Every gate elsewhere in this repo polices rows, lists, copy and internal consistency; the ICP
definition — the phrases and rubric in ``knowledge/icp-scoring.toml`` — was the one unguarded
input, reasoned about only in a hand-written comment an operator had to remember to re-run by
hand. This package turns that methodology into a command.

* :mod:`.keyword` — ``icp keyword``: hit-count a candidate phrase against the live backlog,
  using the SAME matcher :func:`gtm_core.prospects_backlog.score_account` uses to spend credits
  (:func:`gtm_core.prospects_backlog._cohort_pattern`). One implementation, never a second one.
* :mod:`.checks` — ``icp check``: three structural findings — ``criterion-unqueryable``,
  ``persona-unmapped``, ``rubric-undiscriminating`` — each reusing the module that already owns
  its question.
* :mod:`.propose` — ``icp propose``: add / amend / retire lines from a finding set, in the
  ``lanes suggest-rules`` shape. Prints only; never writes ``profiles/``.
* :mod:`.cli` — ``python -m gtm_core.prospects icp check|keyword|propose``.

Nothing here writes tenant knowledge. There is no ``--apply``/``--write`` flag anywhere in this
package, and no code path opens a file under ``profiles/`` for writing — the property is
structural (:mod:`tests.contracts.test_icp_check_is_read_only`), not a convention.
"""

from __future__ import annotations

from .cli import main  # noqa: F401
from .keyword import KeywordHits, keyword_hits  # noqa: F401

__all__ = [
    "KeywordHits",
    "keyword_hits",
    "main",
]
