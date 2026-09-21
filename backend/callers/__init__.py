"""Fleet executor contract — caller identity, verification, and admission (PRD §2.1).

Nothing here is wired into a FastAPI route yet; a later task adapts backend/deps.py to
use these ports. See docs/prds/ for the fleet executor contract PRD.
"""

from __future__ import annotations
