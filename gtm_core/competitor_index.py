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

The rule now (2026-10-02, PRD ``2026-10-02-regulator-competitor-classifier``):

* **a domain is an explicit identity.** An entry's ``domains`` are indexed as exact hosts, and a
  row matches when its ``company_domain`` or its email's host IS a listed host or sits UNDER one
  (``mail.contoso.example`` is under ``contoso.example``). A sibling does not match, the parent of
  a listed subdomain does not match, and the same first label on another ending does not match.
  The first-label stem was the key until then, and one entry convicted every company that shared
  its first label (a hospital against a software vendor, a code host against a project listed
  under it, a legacy address against an AI vendor).
* **a stem is opt-in.** An entry that says ``match = "stem"`` also indexes each of its domains by
  registrable stem, for a competitor that operates on a different ending in every country.
* **a name is an exact token.** The entry's name and each alias are indexed as
  ``org_token("", <name>)`` — lower-cased, corporate suffixes dropped, non-alphanumerics removed
  — so ``Contoso Ltd`` equals ``Contoso`` by design and ``Contoso Freight Lines`` does not. A name
  is never matched against a domain's first label.
* **a path is not a host.** ``host/project`` is refused with a warning and never truncated to
  ``host``; truncation is how an unrelated company matched.
* **a free-mail domain is never an identity**, on either side.

Index keys: a name token is alphanumeric (``contoso``), a domain is dotted
(``contoso.example``), a stem is ``stem:contoso``. The three cannot collide.

``competitors.toml`` is tenant-maintained data (§R5): every field is type-checked, and a
malformed one is skipped with a logged warning rather than raising — one bad alias must not
take the whole competitor check down with a traceback, and must not pass silently either.
"""

from __future__ import annotations

import logging
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .merge_hygiene import _FREEMAIL, bare_host
from .paths import _safe_segment, resolve_profiles_root
from .prospects_consolidate import org_token

__all__ = [
    "DIRECT_TIER",
    "CompetitorEntry",
    "CompetitorHit",
    "competitor_entries",
    "competitor_lookup",
    "competitor_match",
    "domain_problem",
    "domain_stem",
    "host_chain",
    "load_competitors",
    "row_hosts",
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


# --- identity helpers (shared with gtm_core.account_relation) -----------------------

_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://")


def domain_problem(value: str) -> tuple[str, str]:
    """``(host, "")`` when ``value`` names one registrable host, else ``("", why)``.

    The strict reader for a domain a TENANT FILE lists. It does not do what
    :func:`~gtm_core.merge_hygiene.bare_host` does to a row's dirty ``company_domain`` — cut at
    the first ``/`` — because that is exactly the defect: an entry that wrote ``host/project``
    was indexed as ``host``, and everyone at ``host`` then matched. A listed value that is not a
    bare host is refused with the reason, so the file's author can fix it.
    """
    v = _SCHEME_RE.sub("", (value or "").strip().lower()).removeprefix("www.")
    if not v:
        return "", "is blank"
    head, slash, tail = v.partition("/")
    if slash and tail.strip("/"):
        return "", "carries a path"
    v = head.rstrip(".")
    if any(c in v for c in "?#@: "):
        return "", "is not a bare host"
    if not _HOST_RE.match(v):
        return "", "is not a domain name"
    if v in _FREEMAIL:
        return "", "is a free-mail host, which names a mail provider and not an employer"
    return v, ""


def host_chain(value: str) -> list[str]:
    """The host in ``value`` and each parent that still has two labels, longest first.

    ``a.b.contoso.example`` gives ``[a.b.contoso.example, b.contoso.example, contoso.example]``.
    A listed host matches when it is any member: the host itself or a parent of it. The bare
    ending (``example``) is never a member, so a one-label key cannot match everything on a TLD.
    A free-mail host, an address that was not split, and a blank give nothing.
    """
    host = bare_host(value)
    if not host or "@" in host or "." not in host or host in _FREEMAIL:
        return []
    labels = [x for x in host.split(".") if x]
    return [".".join(labels[i:]) for i in range(len(labels) - 1)]


def row_hosts(company_domain: str, email: str = "") -> list[str]:
    """The hosts a row presents — its ``company_domain`` and its email's host — each once.

    Free-mail hosts are dropped. A row's own address names its employer as well as its
    ``company_domain`` does, and it is the only identity on the rows whose domain column is blank.
    """
    email_host = email.rsplit("@", 1)[-1] if "@" in (email or "") else ""
    out: list[str] = []
    for raw in (company_domain, email_host):
        chain = host_chain(raw or "")
        if chain and chain[0] not in out:
            out.append(chain[0])
    return out


# --- competitor flag -----------------------------------------------------------

_COMPETITORS_FILE = "competitors.toml"

#: The one tier that is a hard stop. ``competitors.toml``'s own header defines it as
#: "products that overlap the core wedge" — there is no framing that rescues a cold
#: pitch to a company selling the thing you are selling, so this is not a judgement
#: the gate defers to a human. Every other tier (``adjacent``, ``nhi-native``,
#: ``si-channel``) genuinely can be reframed, and stays a WARN.
DIRECT_TIER = "direct"

#: ``match`` values an entry may carry. ``domain`` (the default) is the exact registrable domain;
#: ``stem`` also indexes the first label before the public suffix, for a competitor that operates
#: on a different ending in every country.
MATCH_MODES = ("domain", "stem")


@dataclass(frozen=True)
class CompetitorHit:
    """One ``competitors.toml`` entry, matched to an account.

    Carries the ``tier`` rather than only the rendered summary because the tier is
    what decides ERROR vs WARN, and re-parsing it back out of a display string is
    how a grading rule silently stops matching the file it grades.
    """

    tier: str
    summary: str
    #: The entry's name, so a report can say which entry matched without parsing ``summary``.
    entry: str = ""

    @property
    def direct(self) -> bool:
        return self.tier.strip().lower() == DIRECT_TIER


@dataclass(frozen=True)
class CompetitorEntry:
    """One parsed ``[[competitor]]``: what it declares and what the loader refused of it."""

    name: str
    tier: str
    aliases: tuple[str, ...]
    domains: tuple[str, ...]
    #: Listed domains that were refused (a path, a free-mail host, not a domain) — kept so the
    #: audit can report them instead of the warning being the only trace.
    rejected: tuple[str, ...]
    match: str
    summary: str


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


def _match_mode(entry: dict, name: str) -> str:
    raw = entry.get("match")
    if raw is None:
        return "domain"
    if isinstance(raw, str) and raw.strip().lower() in MATCH_MODES:
        return raw.strip().lower()
    _log.warning(
        "competitors.toml: %r has `match` = %r — read as the default (exact domain); the "
        "allowed values are %s",
        name,
        raw,
        ", ".join(MATCH_MODES),
    )
    return "domain"


def _parse_entry(entry: object) -> CompetitorEntry | None:
    if not isinstance(entry, dict):
        _log.warning("competitors.toml: a `competitor` entry is not a table — skipped")
        return None
    raw_name = entry.get("name")
    if raw_name is not None and not isinstance(raw_name, str):
        _log.warning("competitors.toml: non-string `name` (%r) — entry skipped", raw_name)
        return None
    name = (raw_name or "").strip()
    if not name:
        return None
    raw_tier = entry.get("tier") or "unknown"
    if not isinstance(raw_tier, str):
        _log.warning("competitors.toml: %r has a non-string `tier` — read as unknown", name)
        raw_tier = "unknown"
    tier = raw_tier.strip()
    domains: list[str] = []
    rejected: list[str] = []
    for raw in _strings(entry, "domains", name):
        host, problem = domain_problem(raw)
        if host:
            domains.append(host)
        else:
            rejected.append(raw)
            _log.warning(
                "competitors.toml: %r lists domain %r which %s — skipped, not truncated to "
                "its host; list the bare registrable domain",
                name,
                raw,
                problem,
            )
    return CompetitorEntry(
        name=name,
        tier=tier,
        aliases=tuple(_strings(entry, "aliases", name)),
        domains=tuple(domains),
        rejected=tuple(rejected),
        match=_match_mode(entry, name),
        summary=f"{name} ({tier}) — {entry.get('note', '')}".rstrip(" —"),
    )


def competitor_entries(profile: str, profiles_root: Path | None = None) -> list[CompetitorEntry]:
    """The profile's parsed ``[[competitor]]`` entries, or ``[]`` when it ships no file.

    The one parser: :func:`load_competitors` builds its index from this, and the classifier's
    audit reads it to report an entry that matches nothing or one the loader refused part of.
    """
    root = profiles_root or resolve_profiles_root()
    # `profile` arrives from a CLI flag; a segment guard keeps it from walking out of the tree.
    path = root / _safe_segment(profile, "profile") / "knowledge" / _COMPETITORS_FILE
    if not path.is_file():
        return []
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    entries = data.get("competitor", [])
    if not isinstance(entries, list):
        _log.warning("competitors.toml: `competitor` is not an array of tables — ignored")
        return []
    return [e for e in (_parse_entry(raw) for raw in entries) if e is not None]


def load_competitors(profile: str, profiles_root: Path | None = None) -> dict[str, CompetitorHit]:
    """Map identity key -> a one-line ``name (tier): note`` summary, sourced from the
    profile's own maintained ``knowledge/competitors.toml`` (schema=1, ``[[competitor]]``
    entries — see the file's header for tier definitions and provenance). Reuses that
    file rather than a second list: it is already reviewed and dated, and a prospected
    account is at least as often named by a product/alias as by the entity the
    watchlist was filed under, so every alias and domain indexes to the same account
    identity as the canonical name.

    Every identity is indexed on its own (see the module docstring): the name and each alias by
    ``org_token("", <name>)``, each domain as an exact host, and — only for ``match = "stem"``
    — each domain's stem as ``stem:<label>``.

    Returns an empty dict when the profile ships no such file — this check has
    nothing to compare against; the profile owns the list, this module never
    fabricates one.
    """
    out: dict[str, CompetitorHit] = {}
    for entry in competitor_entries(profile, profiles_root):
        hit = CompetitorHit(tier=entry.tier, summary=entry.summary, entry=entry.name)
        for n in (entry.name, *entry.aliases):
            _index(out, org_token("", n), hit)
        for d in entry.domains:
            _index(out, d, hit)
            if entry.match == "stem" and (stem := domain_stem(d)):
                _index(out, f"stem:{stem}", hit)
    return out


def competitor_lookup(
    company: str,
    company_domain: str,
    competitors: dict[str, CompetitorHit],
    *,
    email: str = "",
) -> tuple[CompetitorHit, str] | None:
    """``(entry, how it matched)`` for the strongest hit on this row, or ``None``.

    A row presents up to three identities — its ``company_domain``, its email's host, and its
    company-name token — and any one is enough. ``how`` is ``domain:<listed host>``,
    ``name:<token>`` or ``stem:<label>``, so a report can say what a hit rested on. A ``direct``
    hit outranks any other: one row matching an adjacent entry by name and a direct one by address
    is a direct competitor.
    """
    if not competitors:
        return None
    found: list[tuple[CompetitorHit, str]] = []
    for host in row_hosts(company_domain, email):
        for candidate in host_chain(host):
            if candidate in competitors:
                found.append((competitors[candidate], f"domain:{candidate}"))
                break  # the most specific listed host is the entry; its parents add nothing
        stem = domain_stem(host)
        if stem and f"stem:{stem}" in competitors:
            found.append((competitors[f"stem:{stem}"], f"stem:{stem}"))
    token = org_token("", company or "")
    if token and token in competitors:
        found.append((competitors[token], f"name:{token}"))
    return next((f for f in found if f[0].direct), found[0] if found else None)


def competitor_match(
    company: str,
    company_domain: str,
    competitors: dict[str, CompetitorHit],
    *,
    email: str = "",
) -> CompetitorHit | None:
    """Return the matching ``competitors.toml`` entry, or ``None`` if not on the list.

    See :func:`competitor_lookup` for what a row is matched on and how a tie is broken.
    """
    found = competitor_lookup(company, company_domain, competitors, email=email)
    return found[0] if found else None
