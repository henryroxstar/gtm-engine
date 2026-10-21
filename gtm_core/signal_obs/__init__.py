"""Signal observations: a public source list is evidence, and what it shows is derived.

This package reads the source registry, extracts members from captured pages, and appends
observations under ``content/<profile>/observations/``. It never writes ``history.jsonl`` and
never imports ``gtm_core.signals``: an observation is not a signal, and nothing that reads the
signal feed may see one (``tests/contracts/test_signal_obs_never_emits_signals.py``).

Every reader takes the run scope through ``run_scope.require``, so a Stream run can never read
a Gateway registry.
"""
