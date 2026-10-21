"""Read ``regulators.toml`` — a safety list, so it refuses rather than coerces.

A body silently dropped is a regulator that gets emailed, and the cheapest way to drop one is a
typo (``domain`` for ``domains``) that a lenient reader accepts. So every key set here is closed,
every ``kind`` is from a closed list, an ``[[allow]]`` must carry its reason and both dates, and a
file that is PRESENT but wrong stops the run with the rule named. A file that is ABSENT is a
different fact: the built-in government endings (:data:`~gtm_core.account_relation.FLOOR_ENDINGS`)
still apply, so a tenant with no file is no less protected than before it existed.

Shape (see ``profiles/_template/knowledge/regulators.toml``)::

    schema = 1
    reviewed = "2026-10-02"

    [endings]                    # dot-boundary endings, by kind
    government = [".gov", ".gov.xx"]

    [[body]]                     # a named body: a listed domain matches itself and what is under it
    name    = "Examplia Central Bank"
    kind    = "central-bank"     # one of account_relation.BODY_KINDS
    domains = ["examplia-bank.example"]
    aliases = ["Bank of Examplia"]          # optional exact names, four characters or more
    verified = true                          # optional: the domain was read off the body's own site

    [[allow]]                    # lets the GATE pass one body; the router still holds
    domain  = "examplia-bank.example"
    reason  = "supplier to its innovation unit; contact agreed in writing"
    decided = "2026-10-02"
    expires = "2026-12-31"       # after decided, at most 366 days later
    email   = "..."              # optional: narrow to one address

The file lives in the profile's own ``knowledge/`` and is tenant-wide: a copy under
``products/<slug>/`` refuses the run (``product_manifest.TENANT_WIDE``), and an experiment overlay
cannot swap it (``experiments.REFUSED``).
"""

from __future__ import annotations

import datetime as _dt
import re
import tomllib
from collections.abc import Iterable
from pathlib import Path

from .account_relation import (
    BODY_KINDS,
    FLOOR_ENDINGS,
    Allow,
    Body,
    Ending,
    Regulators,
    RelationConfigError,
    RelationIndex,
)
from .competitor_index import domain_problem, load_competitors
from .paths import resolve_knowledge_file, resolve_profiles_root
from .prospects_consolidate import org_token

__all__ = ["load_index", "load_regulators", "normalise_ending", "parse_regulators"]

FILE = "regulators.toml"
SCHEMA = 1
#: An alias or name shorter than this is not an identity: a three-letter acronym is some other
#: company's name too. List the full name, or the domain.
MIN_TOKEN = 4
#: How long a dated override may last. An override that never ends is a permanent exemption,
#: which is a different decision from "we may write to this body this quarter".
MAX_ALLOW_DAYS = 366
#: A reason is a sentence, not a word. "approved" says nobody thought about it.
MIN_REASON_WORDS = 3

_TOP = frozenset({"schema", "reviewed", "endings", "body", "allow"})
_BODY = frozenset({"name", "kind", "domains", "aliases", "note", "verified"})
_ALLOW = frozenset({"domain", "email", "reason", "decided", "expires"})
_ENDING_RE = re.compile(r"^\.[a-z0-9-]+(\.[a-z0-9-]+)*$")
#: Endings that name a whole commercial namespace. Listed under ``[endings]`` they would hold or
#: refuse every company on the TLD, so they are refused at load. (A government body that lives on
#: one of these is listed by domain.)
COMMERCIAL_ENDINGS = frozenset(
    {
        ".com", ".org", ".net", ".io", ".ai", ".co", ".info", ".biz", ".edu", ".app", ".dev",
        ".co.uk", ".org.uk", ".ac.uk", ".com.au", ".org.au", ".com.sg", ".org.sg", ".co.in",
        ".org.in", ".com.hk", ".org.hk", ".co.jp", ".or.jp", ".com.cn", ".org.cn", ".com.my",
        ".co.nz", ".co.za", ".com.br", ".co.ae", ".com.ae",
    }
)  # fmt: skip


def normalise_ending(value: str, *, where: str) -> str:
    """``gov.xx`` -> ``.gov.xx``. The leading dot is what makes the match a dot boundary: without
    it ``endswith("gov.xx")`` also matches ``notgov.xx``."""
    v = (value or "").strip().lower()
    if v and not v.startswith("."):
        v = "." + v
    if not _ENDING_RE.match(v):
        raise RelationConfigError(
            f"{where}: ending {value!r} is not a dotted ending such as '.gov.xx' "
            "(letters, digits and hyphens between dots)"
        )
    if v in COMMERCIAL_ENDINGS:
        raise RelationConfigError(
            f"{where}: ending {value!r} is a commercial ending that names whole countries' "
            "companies, not a government namespace — list the body by its domain instead"
        )
    return v


def _text(obj: dict, key: str, where: str, *, required: bool = True) -> str:
    raw = obj.get(key)
    if raw is None:
        if required:
            raise RelationConfigError(f"{where}: `{key}` is required")
        return ""
    if not isinstance(raw, str) or not raw.strip():
        raise RelationConfigError(f"{where}: `{key}` must be a non-blank string")
    return raw.strip()


def _strings(obj: dict, key: str, where: str) -> list[str]:
    raw = obj.get(key)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise RelationConfigError(f"{where}: `{key}` must be a list of strings")
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise RelationConfigError(
                f"{where}: `{key}` holds a blank or non-string entry: {item!r}"
            )
    return [i.strip() for i in raw]


def _closed(obj: object, allowed: frozenset[str], where: str) -> dict:
    if not isinstance(obj, dict):
        raise RelationConfigError(f"{where}: must be a table")
    unknown = sorted(set(obj) - allowed)
    if unknown:
        raise RelationConfigError(
            f"{where}: unknown key(s) {', '.join(f'`{k}`' for k in unknown)} — "
            f"allowed: {', '.join(sorted(allowed))}"
        )
    return obj


def _domain(value: str, where: str) -> str:
    host, problem = domain_problem(value)
    if not host:
        raise RelationConfigError(f"{where}: domain {value!r} {problem}")
    return host


def _token_ok(name: str, where: str, what: str) -> None:
    tok = org_token("", name)
    if len(tok) < MIN_TOKEN:
        raise RelationConfigError(
            f"{where}: {what} {name!r} is too short to be an identity ({len(tok)} characters; "
            f"at least {MIN_TOKEN}) — list the full name, or rely on the domain"
        )


def _body(raw: object, n: int) -> Body:
    where = f"regulators.toml [[body]] #{n}"
    obj = _closed(raw, _BODY, where)
    name = _text(obj, "name", where)
    where = f"regulators.toml body {name!r}"
    kind = str(obj.get("kind") or "").strip()
    if kind not in BODY_KINDS:
        raise RelationConfigError(f"{where}: kind {kind!r} is not one of {', '.join(BODY_KINDS)}")
    domains = tuple(_domain(d, where) for d in _strings(obj, "domains", where))
    if not domains:
        raise RelationConfigError(
            f"{where}: needs at least one domain — a name alone is too weak an identity "
            "to refuse a body on"
        )
    aliases = tuple(_strings(obj, "aliases", where))
    for label, value in (("name", name), *(("alias", a) for a in aliases)):
        _token_ok(value, where, label)
    verified = obj.get("verified")
    if verified is not None and not isinstance(verified, bool):
        raise RelationConfigError(f"{where}: `verified` must be true or false, got {verified!r}")
    note = obj.get("note")
    if note is not None and not isinstance(note, str):
        raise RelationConfigError(f"{where}: `note` must be a string")
    return Body(name, kind, domains, aliases, (note or "").strip(), verified)


def _date(obj: dict, key: str, where: str) -> _dt.date:
    raw = obj.get(key)
    if isinstance(raw, _dt.datetime):
        return raw.date()
    if isinstance(raw, _dt.date):
        return raw
    if raw is None:
        raise RelationConfigError(f"{where}: `{key}` is required (a date, YYYY-MM-DD)")
    if isinstance(raw, str) and raw.strip():
        try:
            return _dt.date.fromisoformat(raw.strip())
        except ValueError:
            pass
    raise RelationConfigError(f"{where}: `{key}` {raw!r} is not a date (YYYY-MM-DD)")


def _allow(raw: object, n: int) -> Allow:
    where = f"regulators.toml [[allow]] #{n}"
    obj = _closed(raw, _ALLOW, where)
    domain = _domain(_text(obj, "domain", where), where)
    reason = _text(obj, "reason", where)
    if len(reason.split()) < MIN_REASON_WORDS:
        raise RelationConfigError(
            f"{where}: `reason` must say why in at least {MIN_REASON_WORDS} words — "
            f"{reason!r} is not a reason someone can check later"
        )
    decided, expires = _date(obj, "decided", where), _date(obj, "expires", where)
    if expires <= decided:
        raise RelationConfigError(f"{where}: `expires` must be after `decided`")
    if (expires - decided).days > MAX_ALLOW_DAYS:
        raise RelationConfigError(
            f"{where}: an override lasts at most {MAX_ALLOW_DAYS} days; this one is "
            f"{(expires - decided).days}. Renew it with a new decision"
        )
    email = _text(obj, "email", where, required=False).lower()
    if email and not (email.count("@") == 1 and email.rsplit("@", 1)[1].endswith(domain)):
        raise RelationConfigError(f"{where}: `email` {email!r} must be an address under {domain}")
    return Allow(domain, reason, decided, expires, email)


def parse_regulators(text: str) -> Regulators:
    """Parse a ``regulators.toml`` body. The built-in endings are always included."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise RelationConfigError(f"regulators.toml: not valid TOML — {exc}") from exc
    _closed(data, _TOP, "regulators.toml")
    if data.get("schema") != SCHEMA:
        raise RelationConfigError(
            f"regulators.toml: `schema` must be {SCHEMA}, got {data.get('schema')!r}"
        )
    reviewed = data.get("reviewed", "")
    if not isinstance(reviewed, (str, _dt.date)):
        raise RelationConfigError("regulators.toml: `reviewed` must be a date")
    endings: list[Ending] = []
    table = data.get("endings", {})
    if not isinstance(table, dict):
        raise RelationConfigError("regulators.toml: `endings` must be a table of lists, by kind")
    for kind, values in table.items():
        where = f"regulators.toml [endings] {kind}"
        if kind not in BODY_KINDS:
            raise RelationConfigError(
                f"{where}: kind {kind!r} is not one of {', '.join(BODY_KINDS)}"
            )
        if not isinstance(values, list):
            raise RelationConfigError(f"{where}: must be a list of endings")
        endings.extend(Ending(normalise_ending(str(v), where=where), kind) for v in values)
    bodies = tuple(_body(raw, i) for i, raw in enumerate(_listed(data, "body"), 1))
    allows = tuple(_allow(raw, i) for i, raw in enumerate(_listed(data, "allow"), 1))
    return Regulators(bodies, tuple(endings), allows, str(reviewed)).with_endings(FLOOR_ENDINGS)


def _listed(data: dict, key: str) -> list:
    raw = data.get(key, [])
    if not isinstance(raw, list):
        raise RelationConfigError(f"regulators.toml: `{key}` must be an array of tables")
    return raw


def load_regulators(
    profile: str,
    profiles_root: Path | None = None,
    *,
    extra_endings: Iterable[str] = (),
) -> Regulators:
    """The profile's regulators, or just the built-in endings when it ships no file.

    ``extra_endings`` is where the deprecated ``lane-policy.toml [hold] regulated_domains``
    arrives, so it gets the same dot boundary as every other ending.
    """
    root = profiles_root or resolve_profiles_root()
    # No product and no overlay: a regulator is a regulator for every product, and an overlay
    # must never be able to swap a safety list (`experiments.REFUSED`).
    path = resolve_knowledge_file(root, profile, FILE)
    if not path.is_file():
        return Regulators().with_endings(FLOOR_ENDINGS).with_endings(extra_endings)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RelationConfigError(
            f"{path.name}: cannot be read ({type(exc).__name__}): {exc}"
        ) from exc
    return parse_regulators(text).with_endings(extra_endings)


def load_index(profile: str, profiles_root: Path | None = None) -> RelationIndex:
    """Regulators and competitors together: the one object every caller classifies with."""
    return RelationIndex(
        load_regulators(profile, profiles_root), load_competitors(profile, profiles_root)
    )
