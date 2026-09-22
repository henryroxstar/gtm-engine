"""The competitor index — which accounts the profile's own ``competitors.toml`` names.

Split out of :mod:`gtm_core.account_integrity` (which re-exports every public name here, so
existing importers are unaffected) when the matching rule grew from "one token per row" to
"every identity an entry declares, against every identity a row carries".

Why the rule grew. The first implementation indexed ``org_token(domain, name)`` for every
(name/alias x domain) pair, and :func:`gtm_core.prospects_consolidate.org_token` prefers the
DOMAIN's first label whenever a domain is given. So an entry that listed a name, two aliases
and one domain produced exactly one key — the domain's first label — and the name and the
aliases were never indexed at all. The row side had the mirror defect: one token, domain
first, and the first label of ``mail.contoso.example`` is ``mail``. Measured 2026-09-21: a
direct competitor's exact listed name missed on a blank domain, on a regional domain, and on
a mail subdomain, and hit only on the one apex domain the entry happened to list. A direct
competitor on any other host passed the gate in every lane.

The rule now:

* **an entry** indexes each of its domains by registrable stem, its name, and each alias —
  independently, so listing a domain can never un-index the name;
* **a row** is matched on its ``company_domain`` stem, its email's domain stem, and its
  company-name token — any one is enough, and a ``direct`` hit outranks any other;
* matching is **exact-token**, never substring: ``Contoso`` listed does not convict
  ``Contoso Freight Lines`` (tokens ``contoso`` vs ``contosofreightlines``). The name token
  is ``org_token("", name)``, which lower-cases, drops corporate suffixes (inc, corp, ltd,
  llc, plc, group, holdings, company, co, limited, pte) and removes every non-alphanumeric
  — so ``Contoso Ltd`` and ``Contoso Group`` DO equal ``Contoso``, by design;
* a **free-mail** domain is never an identity, on either side: ``gmail.com`` names a mail
  provider, not the company a founder works for.

Domain stems and name tokens deliberately share one key space. That is
``org_token``'s own contract ("``vertex.example`` and ``Vertex`` both collapse to
``vertex``"), and it is what lets a row carrying only a bare name match an entry that
listed only a domain.

``competitors.toml`` is tenant-maintained data (§R5): every field is type-checked, and a
malformed one is skipped with a logged warning rather than raising — one bad alias must not
take the whole competitor check down with a traceback, and must not pass silently either.
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .merge_hygiene import _FREEMAIL, bare_host
from .paths import _safe_segment, resolve_profiles_root
from .prospects_consolidate import org_token

__all__ = [
    "DIRECT_TIER",
    "CompetitorHit",
    "competitor_match",
    "domain_stem",
    "load_competitors",
]

_log = logging.getLogger(__name__)

#: Trailing labels that carry no organisational identity, so the registrable stem is the
#: first label left after they are stripped. Small and explicit rather than a public-suffix
#: dependency: this only has to separate `riverbend.edu` from `riverbend.org`, not parse the
#: whole DNS.
_PUBLIC_SUFFIX_PARTS = frozenset(
    {
        "edu",
        "com",
        "org",
        "net",
        "gov",
        "int",
        "ac",
        "co",
        "or",
        "ne",
        "go",
        "uk",
        "sg",
        "au",
        "nz",
        "za",
        "in",
        "jp",
        "hk",
        "my",
        "ca",
        "us",
        # RFC 2606's reserved documentation TLD, which every fixture in this repo uses.
        "example",
    }
)


def _registrable_stem(domain: str) -> str:
    """The identity-bearing label of ``domain`` — ``riverbend`` for both riverbend.edu and
    riverbend.org.

    Used to tell "this .edu IS the account's own domain on another TLD" from "this .edu
    belongs to somebody else entirely", which is the whole difference between a health
    system's real work address and the university-domain-on-a-corporate-row defect.
    """
    labels = [x for x in (domain or "").strip().lower().split(".") if x]
    while len(labels) > 1 and labels[-1] in _PUBLIC_SUFFIX_PARTS:
        labels.pop()
    return labels[-1] if labels else ""


def domain_stem(value: str) -> str:
    """The company-identifying label of a domain/URL/host, or ``""`` when it has none.

    ``contoso`` for ``contoso.example``, ``mail.contoso.example`` and
    ``https://www.contoso.example/sg`` alike — and for the same name under a two-part
    country suffix or a newer TLD: the label *before the public suffix*, never the first
    label (which is ``mail`` on a mail subdomain).

    Built on :func:`_registrable_stem`, with one addition it does not need for its own
    job: the final label of a multi-label host is ALWAYS a TLD, listed or not. Without
    that, every host on a TLD missing from the small explicit list would stem to the TLD
    itself, and every company on that TLD would share one key.

    A free-mail host returns ``""``: it identifies a mail provider, not an employer.
    """
    host = bare_host(value)
    if not host or host in _FREEMAIL:
        return ""
    labels = [x for x in host.split(".") if x]
    if len(labels) > 1 and labels[-1] not in _PUBLIC_SUFFIX_PARTS:
        labels.pop()
    return _registrable_stem(".".join(labels))


# --- competitor flag -----------------------------------------------------------

_COMPETITORS_FILE = "competitors.toml"

#: The one tier that is a hard stop. ``competitors.toml``'s own header defines it as
#: "products that overlap the core wedge" — there is no framing that rescues a cold
#: pitch to a company selling the thing you are selling, so this is not a judgement
#: the gate defers to a human. Every other tier (``adjacent``, ``nhi-native``,
#: ``si-channel``) genuinely can be reframed, and stays a WARN.
DIRECT_TIER = "direct"


@dataclass(frozen=True)
class CompetitorHit:
    """One ``competitors.toml`` entry, matched to an account.

    Carries the ``tier`` rather than only the rendered summary because the tier is
    what decides ERROR vs WARN, and re-parsing it back out of a display string is
    how a grading rule silently stops matching the file it grades.
    """

    tier: str
    summary: str

    @property
    def direct(self) -> bool:
        return self.tier.strip().lower() == DIRECT_TIER


def _strings(entry: dict, key: str, name: str) -> list[str]:
    """``entry[key]`` as a list of non-blank strings. Anything else is tenant data that
    failed its type check (§R5): warned about and skipped, never raised on."""
    value = entry.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        _log.warning(
            "competitors.toml: %r has a non-list `%s` (%s) — ignored; write it as a list",
            name,
            key,
            type(value).__name__,
        )
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            if item.strip():
                out.append(item.strip())
        else:
            _log.warning(
                "competitors.toml: %r has a non-string entry in `%s` (%r) — skipped",
                name,
                key,
                item,
            )
    return out


def _index(out: dict[str, CompetitorHit], key: str, hit: CompetitorHit) -> None:
    """First entry to claim a key keeps it — unless the newcomer is ``direct`` and the
    holder is not. Two entries sharing an identity must resolve to the hard stop."""
    if not key:
        return
    held = out.get(key)
    if held is None or (hit.direct and not held.direct):
        out[key] = hit


def load_competitors(profile: str, profiles_root: Path | None = None) -> dict[str, CompetitorHit]:
    """Map identity token -> a one-line ``name (tier): note`` summary, sourced from the
    profile's own maintained ``knowledge/competitors.toml`` (schema=1, ``[[competitor]]``
    entries — see the file's header for tier definitions and provenance). Reuses that
    file rather than a second list: it is already reviewed and dated, and a prospected
    account is at least as often named by a product/alias as by the entity the
    watchlist was filed under, so every alias and domain indexes to the same account
    identity as the canonical name.

    Every identity is indexed on its own (see the module docstring): each domain by
    :func:`domain_stem`, the name and each alias by ``org_token("", <name>)``.

    Returns an empty dict when the profile ships no such file — this check has
    nothing to compare against; the profile owns the list, this module never
    fabricates one.
    """
    root = profiles_root or resolve_profiles_root()
    # `profile` arrives from a CLI flag; a segment guard keeps it from walking out of the tree.
    path = root / _safe_segment(profile, "profile") / "knowledge" / _COMPETITORS_FILE
    out: dict[str, CompetitorHit] = {}
    if not path.is_file():
        return out
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    entries = data.get("competitor", [])
    if not isinstance(entries, list):
        _log.warning("competitors.toml: `competitor` is not an array of tables — ignored")
        return out
    for entry in entries:
        if not isinstance(entry, dict):
            _log.warning("competitors.toml: a `competitor` entry is not a table — skipped")
            continue
        raw_name = entry.get("name")
        if raw_name is not None and not isinstance(raw_name, str):
            _log.warning("competitors.toml: non-string `name` (%r) — entry skipped", raw_name)
            continue
        name = (raw_name or "").strip()
        if not name:
            continue
        raw_tier = entry.get("tier") or "unknown"
        if not isinstance(raw_tier, str):
            _log.warning("competitors.toml: %r has a non-string `tier` — read as unknown", name)
            raw_tier = "unknown"
        tier = raw_tier.strip()
        summary = f"{name} ({tier}) — {entry.get('note', '')}".rstrip(" —")
        hit = CompetitorHit(tier=tier, summary=summary)
        for n in (name, *_strings(entry, "aliases", name)):
            _index(out, org_token("", n), hit)
        for d in _strings(entry, "domains", name):
            _index(out, domain_stem(d), hit)
    return out


def competitor_match(
    company: str,
    company_domain: str,
    competitors: dict[str, CompetitorHit],
    *,
    email: str = "",
) -> CompetitorHit | None:
    """Return the matching ``competitors.toml`` entry, or ``None`` if not on the list.

    A row is matched on ANY identity it carries — its ``company_domain`` stem, its email's
    domain stem, its company-name token — because a competitor is as often reached through
    a regional domain or a mail subdomain as through the apex the watchlist recorded. A
    ``direct`` hit outranks any other: one row matching an adjacent entry by name and a
    direct one by address is a direct competitor.
    """
    if not competitors:
        return None
    email_host = email.rsplit("@", 1)[-1] if "@" in (email or "") else ""
    keys = (domain_stem(company_domain), domain_stem(email_host), org_token("", company or ""))
    hits = [competitors[k] for k in keys if k and k in competitors]
    return next((h for h in hits if h.direct), hits[0] if hits else None)
