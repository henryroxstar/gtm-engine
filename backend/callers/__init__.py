"""Fleet executor contract — caller identity, verification, and admission (PRD §2.1).

The run, pack, event and ledger routes admit a caller through these ports
(``backend/callers/rest.py:require_principal``); every other authenticated user route still
uses backend/deps.py alone. See docs/prds/ for the fleet executor contract PRD.
"""

from __future__ import annotations
