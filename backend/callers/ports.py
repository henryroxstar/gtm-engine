"""Structural ports for caller verification and admission (PRD §2.1).

Both ports share one shape: raise ``Refusal`` to refuse, return (``None``) to allow. There
is deliberately no "allow" VALUE — a bool-returning ``admit()`` invites the footgun PRD
§2.1 rule 2/3 calls out, where a stub registry's ``True`` could look like it overrides an
earlier registry's ``False``. With "only an exception refuses," there is nothing to
combine by mistake: the AND composition in dependency.admit_all is just "did anything
raise" (see its comment for the exact short-circuit-vs-poll-all choice).
"""

from __future__ import annotations

from typing import Any, Protocol

from .principal import Principal


class Refusal(Exception):
    """A caller/registry refusal. Carries the HTTP status + machine code that
    dependency.refusal_to_http adapts into a FastAPI HTTPException at a route boundary."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str = "",
        *,
        principal: Principal | None = None,
    ) -> None:
        super().__init__(message or code)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.principal = principal


class CallerVerifier(Protocol):
    """Establishes a Principal from a raw credential.

    PRD §2.1 rule 3: fail closed. Any error, timeout, or "unknown" answer must be surfaced
    as a raised ``Refusal`` (or another exception, which a caller such as verify_chain
    treats identically to a refusal — see its own docstring). Never return ``None`` for a
    refusal; a return value is always a real ``Principal``.
    """

    async def verify(self, raw_token: str, pool) -> Principal: ...


class AgentRegistry(Protocol):
    """Admits (or refuses) a Principal against a local or external registry.

    Returns ``None`` on ALLOW, raises ``Refusal`` to refuse.
    """

    async def admit(self, principal: Principal, conn) -> None: ...


class InletGuard(Protocol):
    """Guards caller-supplied text against declared constraints and envelopes it (PRD §2.2 / §3 G8).

    Verifies declared keys, bounds on size and item counts, and wraps rendered content
    in the §R5 untrusted-data envelope. Raises Refusal(422, 'input_rejected', ...) on violation.
    """

    def guard_context(
        self,
        context: dict[str, str],
        declared: tuple[Any, ...] | dict[str, Any],
    ) -> dict[str, str]: ...

    def render_context_envelope(self, name: str, value: str) -> str: ...
