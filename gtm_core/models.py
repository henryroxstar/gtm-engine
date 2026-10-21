"""Model registry resolver for gtm_core — stdlib-only, env-overridable.

The single source of truth for *which model* each logical role uses. Mirrors
:func:`gtm_core.paths.resolve_knowledge_file`: reference a model by logical role
(``brain_plan``, ``brain_radar``, ``worker_draft``, ``vision``, ...) and resolve
it through committed config (:mod:`gtm_core.models.toml`) instead of hardcoding
ids. Replaces the three hardcoded ids (brain default, DeepSeek worker, Haiku
vision) with one swap-by-config registry.

  GTM_MODELS_REGISTRY  — absolute path to the registry toml (default: co-located
                         gtm_core/models.toml)
  HERMES_MODEL         — break-glass override of the ``brain_plan`` model id

SECRETS (see PRD §5.4): the registry is NON-SECRET and committed. Providers store
``api_key_env`` — the NAME of the env var that holds the key — never the key. This
module RAISES on an inline ``api_key`` or a secret-shaped value so a pasted key
fails fast instead of running. Keys resolve at call time via ``os.getenv`` only.

``ModelSpec`` is the frozen return type (mirrors ``PathConfig``); skills/tools can
reach the resolver via ``python -m gtm_core.models <role>`` and Python callers
import :func:`resolve_model` directly.
"""

from __future__ import annotations

import os
import re
import sys
import time
import tomllib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from gtm_core.paths import _safe_segment  # single source of truth for segment safety

# A value that looks like a real key (e.g. ``sk-ant-...``). The registry is
# non-secret config, so any such value is a paste mistake we refuse to load.
_SECRET_SHAPE = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")
# An env-var NAME (what ``api_key_env`` must be), not a key: UPPER_SNAKE_CASE.
_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]*")


@dataclass(frozen=True)
class ModelSpec:
    """Resolved model spec for one role (mirrors :class:`gtm_core.paths.PathConfig`).

    ``fallbacks`` is the ordered chain (primary → cheaper-same-provider →
    cross-provider) resolved recursively; the executor (P5) walks it on
    429/5xx/404-deprecated/timeout. ``supports_*`` gate request params so a
    swapped-in model can never 400 on an unsupported field.
    """

    role: str
    provider: str
    model: str
    base_url: str
    api_key_env: str
    supports_effort: bool = False
    supports_adaptive_thinking: bool = False
    # Per-1k-token rates for the cost ledger (consumed by the worker in P3).
    input_usd_per_1k: float | None = None
    output_usd_per_1k: float | None = None
    fallbacks: tuple[ModelSpec, ...] = ()

    def api_key(self) -> str | None:
        """Resolve the key from the named env var at call time — never stored here.

        The key material lives with the process env (Doppler-injected), not on the
        spec, so a ``ModelSpec`` is safe to log, cache, or serialise.
        """
        return os.getenv(self.api_key_env)


def _resolve_registry_path(registry_path: Path | None = None) -> Path:
    """Return the registry toml path, honouring an arg / env override / co-located default."""
    if registry_path is not None:
        return Path(registry_path).expanduser().resolve()
    override = os.getenv("GTM_MODELS_REGISTRY")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parent / "models.toml"


def _validate_provider(name: str, table: dict) -> None:
    """Fail fast if a provider table carries secret material instead of an env-var name.

    Two guardrails (PRD §5.4): reject an inline ``api_key`` key, and reject any
    string value that looks like a real secret. Also assert ``api_key_env`` is a
    plausible env-var NAME, so a key pasted into that field is caught too.
    """
    if "api_key" in table:
        raise ValueError(
            f"provider {name!r}: the registry stores 'api_key_env' (an env var NAME), "
            "never 'api_key' (a secret). Put the key in Doppler and name the env var here."
        )
    for key, value in table.items():
        if isinstance(value, str) and _SECRET_SHAPE.search(value):
            raise ValueError(
                f"provider {name!r}: value for {key!r} looks like a secret (sk-…); the "
                "registry is non-secret config — store the env var NAME, not the key."
            )
    env_name = table.get("api_key_env")
    if not env_name:
        raise ValueError(f"provider {name!r}: missing required 'api_key_env' (an env var NAME)")
    if not _ENV_NAME.fullmatch(env_name):
        raise ValueError(
            f"provider {name!r}: api_key_env {env_name!r} is not a plausible env var name "
            "(expected UPPER_SNAKE_CASE — it must be a NAME, not a key)."
        )


def _load_registry(registry_path: Path | None = None) -> dict:
    """Read + validate the registry toml. Raises ValueError on a missing/secret-bearing file."""
    path = _resolve_registry_path(registry_path)
    if not path.is_file():
        raise ValueError(f"model registry not found: {path}")
    with path.open("rb") as f:
        registry = tomllib.load(f)
    for name, table in registry.get("providers", {}).items():
        _validate_provider(name, table)
    return registry


def _build_spec(role: str, registry: dict, seen: frozenset[str]) -> ModelSpec:
    """Build a :class:`ModelSpec` for ``role``, resolving its fallback chain (cycle-guarded)."""
    roles = registry.get("roles", {})
    if role not in roles:
        raise ValueError(f"unknown model role: {role!r}")
    rc = roles[role]

    provider_name = rc.get("provider")
    providers = registry.get("providers", {})
    if provider_name not in providers:
        raise ValueError(f"role {role!r}: unknown provider {provider_name!r}")
    prov = providers[provider_name]

    model = rc.get("model")
    if not model:
        raise ValueError(f"role {role!r}: missing 'model'")
    # Break-glass: HERMES_MODEL wins for brain_plan only (redeploy-free brain swap).
    if role == "brain_plan":
        model = os.getenv("HERMES_MODEL") or model

    # Resolve fallbacks; a role already on this branch is skipped so a cyclic
    # chain (self-reference or A→B→A) can never infinite-loop.
    seen = seen | {role}
    fallbacks = tuple(
        _build_spec(fb, registry, seen) for fb in rc.get("fallbacks", []) if fb not in seen
    )

    return ModelSpec(
        role=role,
        provider=provider_name,
        model=model,
        base_url=prov.get("base_url", ""),
        api_key_env=prov.get("api_key_env", ""),
        supports_effort=bool(rc.get("supports_effort", False)),
        supports_adaptive_thinking=bool(rc.get("supports_adaptive_thinking", False)),
        input_usd_per_1k=rc.get("input_usd_per_1k"),
        output_usd_per_1k=rc.get("output_usd_per_1k"),
        fallbacks=fallbacks,
    )


def resolve_model(
    role: str,
    profile: str | None = None,
    *,
    registry_path: Path | None = None,
) -> ModelSpec:
    """Resolve a logical model ``role`` to its :class:`ModelSpec`.

    Mirrors :func:`gtm_core.paths.resolve_knowledge_file`. ``role`` is the logical
    name (``brain_plan``, ``brain_radar``, ``worker_draft``, ``vision``, ...).
    ``profile`` is accepted for forward-compatibility (per-profile overrides are a
    v2 extension) but the registry is global in v1. ``registry_path`` overrides the
    config location for tests; production uses ``GTM_MODELS_REGISTRY`` / the
    co-located ``gtm_core/models.toml``.

    Raises ``ValueError`` on an unknown role, an unsafe role/profile segment, a
    missing registry, or a secret-bearing provider table.
    """
    role = _safe_segment(role, "role")
    if profile is not None:
        _safe_segment(profile, "profile")  # validated now for the future per-profile lookup
    registry = _load_registry(registry_path)
    return _build_spec(role, registry, frozenset())


# --- Self-healing: fallback chains + circuit breaker (P5) --------------------
#
# Layer 2 of the three self-heal layers (PRD §7): a model that fails mid-flight
# (deprecation 404, 429, 5xx, timeout) falls to the next spec in its chain instead
# of failing the run. A per-role circuit breaker stops hammering a model that is
# down: after `threshold` consecutive failures it OPENS for `cooldown_s`, during
# which callers skip the primary and go straight to the fallback. Pure-Python and
# time-injectable so the open/cooldown behaviour is unit-testable without sleeping.

T = TypeVar("T")

#: HTTP statuses worth retrying on a fallback. 404 is included because a retired
#: model id 404s post-deprecation — exactly the case the fallback chain exists for.
RETRYABLE_STATUS = frozenset({404, 408, 409, 425, 429, 500, 502, 503, 504})


def is_retryable_error(exc: BaseException) -> bool:
    """True if ``exc`` is a transient/deprecation failure worth a fallback attempt.

    Stdlib-only and duck-typed (this module must import without httpx/SDK): an
    exception carrying ``.response.status_code`` in :data:`RETRYABLE_STATUS` is
    retryable, as are timeout/connection/network-shaped errors (matched by class
    name). Everything else (e.g. a 400 bad-request, an auth 401/403) is NOT
    retryable — falling back would just repeat a deterministic error.
    """
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        return status in RETRYABLE_STATUS
    name = type(exc).__name__.lower()
    return any(tok in name for tok in ("timeout", "connect", "network", "transport", "pool"))


@dataclass
class CircuitBreaker:
    """Per-role breaker: OPEN after ``threshold`` consecutive fails, for ``cooldown_s``.

    ``now`` is injectable (defaults to :func:`time.monotonic`) so tests can drive the
    cooldown deterministically. A success resets the count; reaching the cooldown end
    half-opens (one trial allowed, state reset) so a recovered model is used again.
    """

    threshold: int = 5
    cooldown_s: float = 60.0
    now: Callable[[], float] = field(default=time.monotonic)
    _fails: int = field(default=0, init=False)
    _opened_at: float | None = field(default=None, init=False)

    def is_open(self) -> bool:
        """True while the breaker is open (primary should be skipped)."""
        if self._opened_at is None:
            return False
        if self.now() - self._opened_at >= self.cooldown_s:
            # Cooldown elapsed → half-open: reset and allow a trial of the primary.
            self._opened_at = None
            self._fails = 0
            return False
        return True

    def record_success(self) -> None:
        self._fails = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._fails += 1
        if self._fails >= self.threshold and self._opened_at is None:
            self._opened_at = self.now()


# Module-level breakers, one per role (shared across calls within a process).
_BREAKERS: dict[str, CircuitBreaker] = {}


def breaker_for(role: str) -> CircuitBreaker:
    """Return the process-shared :class:`CircuitBreaker` for ``role`` (created on first use)."""
    bp = _BREAKERS.get(role)
    if bp is None:
        bp = CircuitBreaker()
        _BREAKERS[role] = bp
    return bp


async def call_with_fallback(
    spec: ModelSpec,
    attempt: Callable[[ModelSpec], Awaitable[T]],
    *,
    breaker: CircuitBreaker | None = None,
) -> T:
    """Run ``attempt(spec)``, falling through ``spec.fallbacks`` on a retryable error.

    Walks ``[spec, *spec.fallbacks]`` in order: the first spec whose ``attempt``
    succeeds wins. A non-retryable error (e.g. a 400) is re-raised immediately — the
    chain is for transient/deprecation failures, not deterministic ones. If
    ``breaker`` is open, the primary is skipped and the chain starts at the first
    fallback. Raises the last retryable error if the whole chain is exhausted.
    """
    chain = [spec, *spec.fallbacks]
    last_exc: BaseException | None = None
    for s in chain:
        if breaker is not None and s is spec and breaker.is_open():
            continue  # breaker open → skip the primary, try the fallback straight away
        try:
            result = await attempt(s)
        except Exception as exc:  # noqa: BLE001 — classify, then fall back or re-raise
            if not is_retryable_error(exc):
                raise
            last_exc = exc
            if breaker is not None and s is spec:
                breaker.record_failure()
            continue
        if breaker is not None and s is spec:
            breaker.record_success()
        return result
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"no model in the fallback chain for role {spec.role!r} was attempted")


# --- CLI ---------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Print a role's resolved ``provider/model`` (or full spec with ``--json``).

    Exit codes mirror ``gtm_core.resolve_knowledge``: 0 success, 2 on a bad role /
    unsafe segment / secret-bearing registry.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.models",
        description="Resolve a model role to its provider/model via the registry.",
    )
    parser.add_argument("role", help="logical role, e.g. brain_plan / brain_radar / vision")
    parser.add_argument(
        "--profile", default=None, help="active profile slug (reserved; registry is global in v1)"
    )
    parser.add_argument(
        "--registry",
        default=None,
        help="override registry path (default: GTM_MODELS_REGISTRY / co-located models.toml)",
    )
    parser.add_argument("--json", action="store_true", help="print the full resolved spec as JSON")
    args = parser.parse_args(argv)

    try:
        spec = resolve_model(
            args.role,
            args.profile,
            registry_path=Path(args.registry) if args.registry else None,
        )
    except ValueError as exc:
        print(f"[resolve-model] {exc}", file=sys.stderr)
        return 2

    if args.json:
        import dataclasses
        import json

        print(json.dumps(dataclasses.asdict(spec), indent=2))
    else:
        print(f"{spec.provider}/{spec.model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
