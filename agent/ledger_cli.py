"""Re-export shim — implementation lives in gtm_core.ledger_cli.

``python -m agent.ledger_cli`` continues to work; skills can also call
``python -m gtm_core.ledger_cli`` (preferred going forward).
"""

from gtm_core.ledger_cli import main  # noqa: F401

if __name__ == "__main__":
    raise SystemExit(main())
