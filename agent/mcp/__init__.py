"""Runtime MCP servers authored in-repo (as opposed to off-the-shelf npx servers).

Currently houses the DeepSeek ``worker`` MCP (:mod:`agent.mcp.worker`) — a thin
stdio server that exposes DeepSeek as a downstream generation tool. The brain
(Claude, via the Agent SDK) calls it for bulk first-draft / summary work and then
reviews the output. DeepSeek is NEVER the SDK brain model — see
:mod:`agent.mcp_config` for how this server is wired and gated on
``DEEPSEEK_API_KEY``.

Also holds :func:`nonneg_price_env`, shared by the metered workers so the two
price-parsing sites cannot drift apart, and :func:`positive_timeout_env` for the
same reason on HTTP ceilings.
"""

from __future__ import annotations

import os


def nonneg_price_env(env_var: str, default: str) -> float:
    """Read a USD price from ``env_var``, rejecting anything not a non-negative number.

    Prices feed the §R2 monthly cost cap. A negative value would make a metered call
    *reduce* recorded spend, so a run could stay under the cap while spending past it;
    a non-numeric one would crash at the first generation instead of at boot. Both fail
    fast here, at import, so a misconfigured deployment refuses to start rather than
    logging corrupt cost records. No upper bound is enforced: an over-stated price fails
    safe (it trips the cap early) and a ceiling would only reject legitimate repricing.
    """
    raw = os.getenv(env_var) or default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{env_var} must be a number, got {raw!r}") from None
    if value < 0:
        raise ValueError(f"{env_var} must be non-negative, got {value}")
    return value


def positive_timeout_env(env_var: str, default: str) -> float:
    """Read an HTTP timeout in seconds from ``env_var``, rejecting non-positive values.

    A zero or negative ceiling makes every request abort before it is sent, which
    surfaces as a provider outage rather than as the misconfiguration it is. Like
    :func:`nonneg_price_env` this fails at import, so a bad value refuses to start
    instead of failing at the first generation. No upper bound: a too-generous
    ceiling only costs patience, while a ceiling below the model's real render time
    makes the worker unusable — which is the failure this exists to prevent.
    """
    raw = os.getenv(env_var) or default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{env_var} must be a number, got {raw!r}") from None
    if value <= 0:
        raise ValueError(f"{env_var} must be positive, got {value}")
    return value
