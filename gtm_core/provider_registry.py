"""Provider unit prices — what one unit of a metered provider costs the caller's wallet.

The committed, non-secret registry is ``gtm_core/provider_costs.toml``, the same shape as
:mod:`gtm_core.models` / ``models.toml``. This module is its loader and the one place a worker
asks "what does this call cost?":

* :func:`unit_price` returns a price or raises :class:`UnpricedProviderError`. A provider that is
  absent, or whose entry is zero, negative, non-finite, non-numeric or the wrong unit, is
  UNPRICED. There is no default price and no environment override, because the previous design
  (``os.getenv("..._USD_PER_...", "0")``) billed every call at $0.00 whenever nobody set it.
* :func:`usd_per_unit_for` adds the one waiver: a tenant on its OWN vendor account
  (``key_source == "byok"``) is not billed for the vendor's unit, so no price is needed. Only the
  exact string ``"byok"`` waives; ``None`` (the variable unset, i.e. the single-tenant VPS) and
  ``"house"`` are priced from the registry.

* :class:`Pricing` is what a worker holds: its provider and unit, the ``<PROVIDER>_KEY_SOURCE``
  variable it reads, and ``guard(tool)`` — the refusal (recorded in ``denials.jsonl``) every
  METERED tool returns before any HTTP request when the call cannot be priced.

Stdlib and sibling ``gtm_core`` modules only: the stdio workers import this, and it sits under
the §R6 egress scan, so no HTTP client may ever arrive here. The only environment it reads is a
worker's key-source variable and ``GTM_PROFILE``/``GTM_CONTENT_ROOT`` (to find the denial
ledger); it never reads a secret.
"""

from __future__ import annotations

import math
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

_DEFAULT_REGISTRY = Path(__file__).resolve().parent / "provider_costs.toml"

#: ``data`` providers may be pooled on a house account; ``identity`` providers are BYOK-mandatory
#: (a shared sending identity shares a reputation). Absent ⇒ ``identity``: the restrictive side.
CLASSES = ("data", "identity")
DEFAULT_CLASS = "identity"
STATUSES = ("estimate", "reconciled")

#: The class of every key-bearing server that has no metered unit, so no entry in ``provider_costs.toml``
#: (a priced provider declares its class beside its price, and a name is never in both — one home).
#: ``data`` here means only "may be pooled one day"; none of these has a quota to divide, and
#: :func:`pooled_providers` refuses a switch for any of them. Firecrawl, DeepSeek and the rest are ALREADY
#: one house key for every run (plan F13): they are classed explicitly, not reclassified. ``identity`` is a
#: tenant's own account or nothing — a shared sending identity shares a reputation.
UNPRICED_SERVER_CLASSES = {
    "news": "data",
    "worker": "data",
    "vision": "data",
    "judge": "data",
    "gemini_image": "data",
    "higgsfield_video": "data",
    "tts": "data",
    "deck": "data",
    "firecrawl": "data",
    "saleshandy": "identity",
    "syften": "identity",
    "google": "identity",
}

#: Prefix of the per-provider pooling switch (``GTM_POOL_ROCKETREACH``). Default off; exact yes only.
POOL_SWITCH_PREFIX = "GTM_POOL_"
_YES = frozenset({"1", "true", "yes", "on"})

#: Where the wallet's lookups run on the tenant's own vendor account, or on the house's.
KEY_SOURCES = ("house", "byok")

_REQUIRED = ("unit", "usd_per_unit", "status", "source")
_ALLOWED = frozenset(_REQUIRED) | {"class", "default_share"}

#: The workspace entitlements a default share may be keyed by (``backend/schema`` CHECK constraint).
ENTITLEMENTS = ("free", "pro", "pro_plus")


class ProviderRegistryError(ValueError):
    """The registry (or a key-source value) cannot be trusted. ``code`` is the refusal reason."""

    code = "provider_registry_invalid"


class UnpricedProviderError(ProviderRegistryError):
    """No honest price exists for this provider and unit. The caller must refuse, not bill $0."""

    code = "provider_unpriced"


class PoolingRefused(ProviderRegistryError):
    """A pooling switch for a provider that may not, or cannot, be pooled on the house account."""

    code = "provider_pooling_refused"


class KeySourceError(ProviderRegistryError):
    """A key-source value that is neither ``house`` nor ``byok``."""

    code = "provider_key_source_invalid"


@dataclass(frozen=True)
class ProviderCost:
    """One validated registry entry."""

    provider: str
    unit: str
    usd_per_unit: float
    status: str
    source: str
    provider_class: str = DEFAULT_CLASS
    #: Default monthly share of the pooled key, in provider units, per entitlement (MM4). An
    #: entitlement not named here — and a provider with no table at all — has a share of ZERO.
    default_share: dict = field(default_factory=dict)


def _validate_entry(name: str, entry: object) -> ProviderCost:
    if not isinstance(entry, dict):
        raise ProviderRegistryError(f"provider {name!r}: expected a table, got {entry!r}")
    unknown = sorted(set(entry) - _ALLOWED)
    if unknown:
        raise ProviderRegistryError(f"provider {name!r}: unknown key(s) {unknown}")
    missing = [key for key in _REQUIRED if key not in entry]
    if missing:
        raise ProviderRegistryError(f"provider {name!r}: missing required key(s) {missing}")

    unit = entry["unit"]
    if not isinstance(unit, str) or not unit.strip():
        raise ProviderRegistryError(f"provider {name!r}: 'unit' must be a non-empty string")

    price = entry["usd_per_unit"]
    # bool is an int in Python: `usd_per_unit = true` must not read as a price of 1.
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        raise ProviderRegistryError(f"provider {name!r}: 'usd_per_unit' must be a number")
    if not math.isfinite(price) or price <= 0:
        raise ProviderRegistryError(
            f"provider {name!r}: 'usd_per_unit' must be a finite number > 0, got {price!r}"
        )

    status = entry["status"]
    if status not in STATUSES:
        raise ProviderRegistryError(f"provider {name!r}: 'status' must be one of {STATUSES}")

    source = entry["source"]
    if not isinstance(source, str) or not source.strip():
        raise ProviderRegistryError(f"provider {name!r}: 'source' must say where the price is from")

    provider_class = entry.get("class", DEFAULT_CLASS)
    if provider_class not in CLASSES:
        raise ProviderRegistryError(f"provider {name!r}: 'class' must be one of {CLASSES}")

    default_share = _validate_default_share(name, entry.get("default_share", {}))
    if provider_class == "identity" and default_share:
        # A share is a grant out of a pooled key. An identity provider has no pooled key to grant from.
        raise ProviderRegistryError(
            f"provider {name!r}: an identity provider cannot carry a default_share (BYOK only)"
        )

    return ProviderCost(
        provider=name,
        unit=unit.strip(),
        usd_per_unit=float(price),
        status=status,
        source=source.strip(),
        provider_class=provider_class,
        default_share=default_share,
    )


def _validate_default_share(name: str, raw: object) -> dict[str, int]:
    if not isinstance(raw, dict):
        raise ProviderRegistryError(f"provider {name!r}: 'default_share' must be a table")
    out: dict[str, int] = {}
    for entitlement, units in raw.items():
        if entitlement not in ENTITLEMENTS:
            raise ProviderRegistryError(
                f"provider {name!r}: default_share key {entitlement!r} is not one of {ENTITLEMENTS}"
            )
        # bool is an int in Python: `pro = true` must not read as a share of 1.
        if isinstance(units, bool) or not isinstance(units, int) or units < 0:
            raise ProviderRegistryError(
                f"provider {name!r}: default_share.{entitlement} must be a whole number >= 0"
            )
        out[entitlement] = units
    return out


def load_provider_costs(registry_path: Path | None = None) -> dict[str, ProviderCost]:
    """Read and validate the registry. Raises :class:`ProviderRegistryError` on ANY defect.

    The whole file is validated, not just the entry a caller asks about: a registry with one
    broken entry is a broken deploy, and pricing from it would trust the rest on faith.
    """
    path = Path(registry_path) if registry_path is not None else _DEFAULT_REGISTRY
    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except OSError as exc:
        raise ProviderRegistryError(f"provider cost registry unreadable: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ProviderRegistryError(f"provider cost registry is not valid TOML: {exc}") from exc

    unknown = sorted(set(raw) - {"provider"})
    if unknown:
        raise ProviderRegistryError(f"unknown top-level table(s) {unknown}")
    providers = raw.get("provider", {})
    if not isinstance(providers, dict):
        raise ProviderRegistryError("'provider' must be a table of provider tables")
    return {name: _validate_entry(name, entry) for name, entry in providers.items()}


def unit_price(provider: str, unit: str, *, registry_path: Path | None = None) -> float:
    """USD per ``unit`` of ``provider``, or :class:`UnpricedProviderError`. Never returns <= 0."""
    try:
        costs = load_provider_costs(registry_path)
    except ProviderRegistryError as exc:
        raise UnpricedProviderError(f"{provider}: no usable price registry ({exc})") from exc
    entry = costs.get(provider)
    if entry is None:
        raise UnpricedProviderError(f"{provider}: not in the provider cost registry")
    if entry.unit != unit:
        raise UnpricedProviderError(
            f"{provider}: priced per {entry.unit!r}, not per {unit!r} — refusing to guess"
        )
    return entry.usd_per_unit


def server_class(name: str, *, registry_path: Path | None = None) -> str:
    """``data`` or ``identity`` for a provider/server name. Anything nobody classified is ``identity``."""
    entry = load_provider_costs(registry_path).get(name)
    if entry is not None:
        return entry.provider_class
    return UNPRICED_SERVER_CLASSES.get(name, DEFAULT_CLASS)


def known_servers(*, registry_path: Path | None = None) -> frozenset[str]:
    """Every server the platform starts for a run, by the name it is registered under.

    The priced providers and the closed table of the rest — the one set of names with a declared class.
    A registry that cannot be read leaves only the unpriced ones (a priced worker is not callable
    without its price anyway), never an open set.
    """
    try:
        priced = frozenset(load_provider_costs(registry_path))
    except ProviderRegistryError:
        priced = frozenset()
    return priced | frozenset(UNPRICED_SERVER_CLASSES)


def pooled_providers(
    environ: Mapping[str, str] | None = None, *, registry_path: Path | None = None
) -> frozenset[str]:
    """The providers the operator has switched on for house pooling (``GTM_POOL_<PROVIDER>``).

    Off unless the switch is an exact yes (``1/true/yes/on``): a blank, a typo, ``maybe`` all leave the
    provider unpooled. A switch that is ON must name a ``data`` provider that is priced — the only kind
    with a share to hand out — or the whole call raises :class:`PoolingRefused`: an identity provider
    must never be pooled, a ``data`` provider with no quota (Firecrawl) has nothing to share, and an
    unknown name is a typo. Raising here is the "rejected at load": a config that pools Saleshandy never
    produces a tenant run.
    """
    env = os.environ if environ is None else environ
    costs = load_provider_costs(registry_path)
    out: set[str] = set()
    for key, raw in env.items():
        if not key.startswith(POOL_SWITCH_PREFIX) or (raw or "").strip().lower() not in _YES:
            continue
        name = key[len(POOL_SWITCH_PREFIX) :].lower()
        if server_class(name, registry_path=registry_path) != "data":
            raise PoolingRefused(f"{key}: {name} is an identity provider — BYOK only, never pooled")
        if name not in costs:
            raise PoolingRefused(f"{key}: {name} has no priced pool to share (not in the registry)")
        out.add(name)
    return frozenset(out)


def default_share(
    provider: str, entitlement: str | None, *, registry_path: Path | None = None
) -> int:
    """The default monthly share of ``provider``'s pooled key for a workspace on ``entitlement``.

    Zero for anything not named: an unknown provider, a provider with no ``default_share`` table, an
    entitlement the table omits or that does not match EXACTLY (``"PRO"`` is not ``"pro"``). Absence
    refuses — a share nobody wrote down is not one a workspace may spend.
    """
    entry = load_provider_costs(registry_path).get(provider)
    if entry is None or not isinstance(entitlement, str):
        return 0
    return int(entry.default_share.get(entitlement, 0))


def resolve_key_source(raw: str | None) -> str | None:
    """``None`` for an unset/blank variable (the single-tenant VPS), else ``house`` or ``byok``.

    Exact match only: ``byok`` waives the price, so ``BYOK``, `` byok`` and ``true`` must refuse
    rather than be read as the tenant's own account.
    """
    if raw is None or not raw.strip():
        return None
    if raw not in KEY_SOURCES:
        raise KeySourceError(f"key source must be one of {KEY_SOURCES}, got {raw!r}")
    return raw


def usd_per_unit_for(
    provider: str, unit: str, key_source: str | None, *, registry_path: Path | None = None
) -> float:
    """What one ``unit`` costs the caller's wallet: ``0.0`` on the tenant's own key, else priced.

    ``key_source`` is the raw ``<PROVIDER>_KEY_SOURCE`` value. ``byok`` needs no price (the tenant
    pays the vendor directly, so billing the wallet too would charge them twice); every other
    legal value is priced from the registry and raises :class:`UnpricedProviderError` if it can't be.
    """
    if resolve_key_source(key_source) == "byok":
        return 0.0
    return unit_price(provider, unit, registry_path=registry_path)


def _record_refusal(provider: str, tool: str, reason: str) -> None:
    """Append one refusal to the owning profile's ``denials.jsonl`` (``Ledgers.append_denial`` —
    the existing denial ledger, the same record shape as ``agent.denial_log``). Best-effort:
    observability never changes the refusal, and with no ``GTM_PROFILE`` there is no ledger."""
    profile = (os.getenv("GTM_PROFILE") or "").strip()
    if not profile:
        return
    try:
        from gtm_core.ledgers import Ledgers
        from gtm_core.paths import resolve_content_root

        Ledgers(SimpleNamespace(content_root=resolve_content_root()), profile).append_denial(
            {
                "tool": f"mcp__{provider}__{tool}",
                "decision": "deny",
                "outcome": "denied",
                "detail": reason,
                "source": "provider-worker",
            }
        )
    except Exception:  # noqa: BLE001 — a ledger failure must not turn a refusal into a crash
        return


#: Providers whose POOLED worker failed to record a spend in this process (a worker lives for one run).
#: A pooled worker that cannot write its cost record stops taking paid calls: the record is the only
#: thing that bills the wallet and counts against the workspace's share, so carrying on would spend the
#: operator's key unmetered. Process memory is the right lifetime — a restarted worker is a new attempt.
_METERING_BROKEN: set[str] = set()


def metering_failed(provider: str) -> bool:
    """True once ``provider``'s pooled worker has failed to record a spend in this process."""
    return provider in _METERING_BROKEN


def reset_metering_failures() -> None:
    """Forget every recorded failure (tests; a worker never calls this)."""
    _METERING_BROKEN.clear()


@dataclass(frozen=True)
class Pricing:
    """One worker's pricing contract: which provider and unit it bills, where its key source is
    read from, and how its refusals are spelled (``[<worker>-error]``, the worker's own prefix)."""

    provider: str
    unit: str
    key_source_env: str
    error_prefix: str

    def usd_per_unit(self) -> float:
        """What one unit costs the caller's wallet (0.0 on the tenant's own key)."""
        return usd_per_unit_for(self.provider, self.unit, os.getenv(self.key_source_env))

    def guard(self, tool: str) -> str | None:
        """Refuse a METERED call before any HTTP request when it cannot be priced.

        Returns ``"<error_prefix> <code>: <provider>"`` — and records the refusal in
        ``denials.jsonl`` — or ``None`` to proceed. A tenant on its own key (``byok``) needs no
        price; every other call must resolve to a registry price or it is refused rather than
        recorded at $0.00. Credit-free tools never call this.
        """
        try:
            self.usd_per_unit()
        except ProviderRegistryError as exc:
            reason = f"{exc.code}: {self.provider}"
            _record_refusal(self.provider, tool, reason)
            return f"{self.error_prefix} {reason}"
        if metering_failed(self.provider) and self._tenant_pooled():
            reason = f"provider_metering_unavailable: {self.provider}"
            _record_refusal(self.provider, tool, reason)
            return f"{self.error_prefix} {reason}"
        return None

    def record_meter_failure(self, tool: str) -> None:
        """A worker could not write the cost record for a call that already spent.

        On the pooled house key this is fatal to further spend: the failure is recorded and every later
        metered call is refused (:meth:`guard`). The VPS and a tenant's own vendor account keep the
        legacy reading — their spend is not what the wallet is billed for, so a metering hiccup must
        not take a lookup away. The call that failed to record stays unbilled (it has already been
        paid for; the data is not withheld) — the exposure is bounded to the one in-flight call or batch.
        """
        if not self._tenant_pooled():
            return
        _METERING_BROKEN.add(self.provider)
        _record_refusal(self.provider, tool, f"provider_metering_failed: {self.provider}")

    def _tenant_pooled(self) -> bool:
        """True when this process spends the platform's POOLED key: its quota may not be absent.

        ``house`` is the pooled key; so is any value we cannot read (a mangled marker must land on
        the strict side, never on the VPS's legacy side). ``byok`` (the tenant's own vendor account)
        and an unset variable (the single-tenant VPS) are the legacy postures.
        """
        raw = os.getenv(self.key_source_env)
        try:
            return resolve_key_source(raw) == "house"
        except KeySourceError:
            return True

    def allowance_remaining(self, *, cap_env: str, tool: str, unit_key: str) -> int | None:
        """Units left this month under the allowance in ``cap_env``; ``None`` means uncapped.

        Legacy (VPS, ``byok``): an unset/zero/unparseable cap is uncapped, no profile or a ledger
        read error stays uncapped, and the count is this profile's ledger — byte-for-byte what the
        workers always did. Pooled (``house``): the cap is the workspace's allocated share (MM4), so
        an unset, zero, negative or unparseable cap is a share of ZERO, no profile or an unreadable
        ledger is "nothing remaining", and the count is the whole workspace's — every profile
        ledger under the content root. A returned value ``<= 0`` is a refusal.
        """
        pooled = self._tenant_pooled()
        none = 0 if pooled else None
        raw = (os.getenv(cap_env) or "").strip()
        try:
            cap = int(raw)
        except ValueError:
            cap = None
        if cap is not None and cap <= 0:
            cap = None
        if cap is None:
            return none
        profile = (os.getenv("GTM_PROFILE") or "").strip()
        if not profile:
            return none
        try:
            from gtm_core.ledgers import Ledgers, content_root_unit_total
            from gtm_core.paths import resolve_content_root

            root = resolve_content_root()
            if pooled:
                used = content_root_unit_total(root, tool, unit_key, strict=True)
            else:
                used = Ledgers(SimpleNamespace(content_root=root), profile).month_unit_total(
                    tool, unit_key
                )
        except Exception:  # noqa: BLE001 — legacy fails open; a pooled tenant fails closed
            return none
        return int(cap - used)

    def record_quota_denial(self, tool: str, limit: int | None) -> None:
        """Record a quota refusal in the workspace's ``denials.jsonl``: provider, limit and tree.

        Only a POOLED tenant's refusal is a quota event worth a row of its own; the VPS keeps the
        legacy message only. The workspace is named by its content tree — the only identity a
        worker has (it carries no workspace id).
        """
        if not self._tenant_pooled():
            return
        root = (os.getenv("GTM_CONTENT_ROOT") or "").strip()
        _record_refusal(
            self.provider, tool, f"provider_quota_exhausted: limit={limit or 0} root={root}"
        )
