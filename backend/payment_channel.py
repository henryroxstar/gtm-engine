"""Which ONE way credits a workspace's wallet right now (MM7).

Two channels exist: the RevenueCat webhook (``backend/routers/webhooks.py``) that is live today, and
the billing service's top-up route (``backend/routers/wallet.py``) that replaces it when that service
ships. Two open at once is a double credit for one payment; two shut is a payment that credits nothing.
``BILLING_SERVICE_ENABLED`` chooses, and this is the only place it is read.

The rule that matters is the one a mistyped value could break: ANYTHING that is not an unambiguous "on"
leaves the webhook live and the route shut. There is no value that opens both, and no value that closes
both.
"""

from __future__ import annotations

import os

SETTING = "BILLING_SERVICE_ENABLED"
_ON = frozenset({"1", "true", "yes", "on"})


def billing_service_active() -> bool:
    """True when the billing service's route is the payment channel (default: False)."""
    return (os.environ.get(SETTING) or "").strip().lower() in _ON
