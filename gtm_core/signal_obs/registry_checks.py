"""Field-level checks for ``signal-sources.toml``, split out of ``registry`` to keep it readable.

Every check here raises :class:`RegistryError` and nothing else: a value the file can hold, however
odd, must become a what/why/fix sentence, never a traceback (the loader for a run promises it never
raises). Two rules are applied throughout. A ``bool`` is never a number or a piece of text, though
Python makes it an ``int``. And a pattern is matched whole (``fullmatch``, ``[0-9]``): ``$`` lets a
trailing newline through and ``\\d`` reads Arabic-Indic digits.
"""

from __future__ import annotations

import re
from pathlib import Path

#: extractor -> the only ``extractor_args`` keys it accepts.
EXTRACTORS: dict[str, frozenset[str]] = {
    "links": frozenset({"href_contains"}),
    "table_column": frozenset({"column"}),
    "heading_list": frozenset({"level"}),
    "brain_list": frozenset({"hint"}),
}
#: extractor_args key -> the type its value must have.
_ARG_TYPES: dict[str, type] = {"href_contains": str, "column": str, "hint": str, "level": int}

#: At most four digits each: a sample is a hand-read list, and ``int`` refuses 4,300+ digits.
PRECISION_RE = re.compile(r"([0-9]{1,4})/([0-9]{1,4})")
ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
MAX_URL = 300
_URL_CHARS = re.compile(r"[\x21-\x7e]+")
#: A DNS name of two or more labels whose last label starts with a letter. That alone refuses an
#: IP address in any spelling (dotted, decimal, hex, bracketed), ``localhost`` and a bare host.
_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]*", re.IGNORECASE)


class RegistryError(ValueError):
    """The registry file is refused whole. ``what``, ``why`` and ``fix`` are what a person reads."""

    def __init__(self, what: str, why: str, fix: str) -> None:
        super().__init__(f"{what} Why: {why} Fix: {fix}")
        self.what, self.why, self.fix = what, why, fix


def shown(path: Path) -> str:
    return f"{path.parent.name}/{path.name}"


def nonempty(val: object) -> bool:
    return isinstance(val, str) and bool(val.strip())


def bad(path: Path, sid: str, key: str, why: str, fix: str) -> RegistryError:
    return RegistryError(f"{shown(path)}: source {sid!r} has a bad {key}.", why, fix)


def check_enum(path, sid, raw, key, allowed):
    if raw.get(key) not in allowed:
        raise bad(
            path,
            sid,
            key,
            f"{key} must be one of {sorted(allowed)}, got {raw.get(key)!r}.",
            f"set {key} to one of {sorted(allowed)}",
        )


def check_id(path: Path, sid: str, value: str) -> None:
    if not ID_RE.fullmatch(value):
        raise bad(
            path,
            sid,
            "id",
            "an id is lowercase letters, digits and dashes, at most 64, and it names a state file.",
            "use a bare lowercase name such as north-directory",
        )


def check_unique_ids(path: Path, sources) -> None:
    seen: set[str] = set()
    for s in sources:
        key = s.id.casefold()
        if key in seen:
            raise RegistryError(
                f"{shown(path)} has a duplicate source id {s.id!r}.",
                "an id names one source, and ids that differ only by case share one state file "
                "on a case-insensitive disk.",
                "rename one of them",
            )
        seen.add(key)


def _url_problem(url: str) -> str | None:
    rest = url[8:]
    authority = rest.split("/", 1)[0]
    host, colon, port = authority.partition(":")
    tests = (
        (len(url) > MAX_URL, f"it is longer than {MAX_URL} characters."),
        (
            not _URL_CHARS.fullmatch(url),
            "it has a space, a control character or a non-ASCII character.",
        ),
        (not url.startswith("https://"), "it must be an https URL."),
        (any(c in rest for c in "?#\\"), "it has a query, a fragment or a backslash."),
        ("@" in authority, "it has a login (user@) before the host."),
        (bool(colon) and port != "443", "it names a port other than 443."),
        (
            not _HOST.fullmatch(host),
            "the host must be a DNS name with two or more labels, not an IP address or localhost.",
        ),
    )
    return next((why for hit, why in tests if hit), None)


def check_url(path: Path, sid: str, url: str) -> None:
    """The url is the capture's egress address, so it is read as strictly as one."""
    why = _url_problem(url)
    if why:
        raise bad(
            path,
            sid,
            "url",
            why,
            "use the plain https address of the list: no login, query, fragment or port",
        )


def check_args(path, sid, raw) -> dict:
    extractor = raw.get("extractor")
    args = raw.get("extractor_args", {})
    allowed = EXTRACTORS.get(extractor, frozenset())
    if not isinstance(args, dict):
        raise bad(path, sid, "extractor_args", "it must be a table.", "write extractor_args = {}")
    unknown = sorted(set(args) - allowed)
    if unknown:
        raise bad(
            path,
            sid,
            "extractor_args",
            f"{unknown[0]!r} is not an argument of the {extractor} extractor "
            f"(allowed: {sorted(allowed) or 'none'}).",
            f"remove {unknown[0]!r}",
        )
    if extractor == "table_column" and not nonempty(args.get("column")):
        raise bad(
            path,
            sid,
            "extractor_args",
            "a table_column source must name the column it reads, and none is given.",
            'set extractor_args = {column = "Member"} to the heading of the column',
        )
    for key, val in args.items():
        kind = _ARG_TYPES[key]
        ok = isinstance(val, kind) and not isinstance(val, bool)
        ok = ok and (val.strip() != "" if kind is str else 1 <= val <= 6)
        if not ok:
            want = "a whole number from 1 to 6" if kind is int else "non-empty text"
            raise bad(path, sid, "extractor_args", f"{key} must be {want}.", f"set {key} to {want}")
    return args


def check_precision(path, sid, raw) -> str:
    text = raw.get("precision", "")
    if text == "":
        return ""
    m = PRECISION_RE.fullmatch(text) if isinstance(text, str) else None
    if not m or int(m.group(2)) == 0 or int(m.group(1)) > int(m.group(2)):
        raise bad(
            path,
            sid,
            "precision",
            f"precision must be k/n from the hand-checked sample, got {text[:40]!r}.",
            "write it as e.g. 8/10 (up to four digits each), or leave it empty until the sample "
            "is read",
        )
    return text


ATTESTATIONS = frozenset({"agentic", "pool"})


def check_attestation(path, sid, raw) -> tuple[str, str]:
    """``(attestation, agentic_basis)``. Absent means ``pool``, the claim that shows nothing."""
    kind = raw.get("attestation", "pool")
    if kind not in ATTESTATIONS:
        raise bad(
            path,
            sid,
            "attestation",
            f"attestation must be 'agentic' or 'pool', got {str(kind)[:40]!r}.",
            "write 'agentic' only if the list's own criterion shows AI agents, otherwise 'pool'",
        )
    basis = str(raw.get("agentic_basis", "")).strip()
    if kind == "agentic" and not basis:
        raise bad(
            path,
            sid,
            "agentic_basis",
            "an agentic source must say what about the list's own criterion shows agents.",
            "add one sentence as agentic_basis, or set attestation to 'pool'",
        )
    if kind == "pool" and basis:
        raise bad(
            path,
            sid,
            "agentic_basis",
            "a pool source makes no claim that its members run agents.",
            "remove agentic_basis, or set attestation to 'agentic' if the list itself shows agents",
        )
    return kind, basis


def check_int(path, sid, raw, key, floor) -> int:
    val = raw.get(key)
    if isinstance(val, bool) or not isinstance(val, int) or val < floor:
        raise bad(
            path,
            sid,
            key,
            f"{key} must be a whole number of at least {floor}.",
            f"set {key} to {floor} or more",
        )
    return val


def load_premises(profile: str, root: Path, product: str | None) -> set[str]:
    """The premise ids ``premise-vocab.toml`` declares; a file that cannot be read refuses."""
    from ..hook_coverage.premise import load_premise_vocab

    try:
        return set(load_premise_vocab(profile, root, product=product))
    except (ValueError, OSError, AttributeError, TypeError, RecursionError) as exc:
        raise RegistryError(
            f"premise-vocab.toml for {profile} could not be read.",
            f"{type(exc).__name__}: {str(exc)[:200]}",
            "fix premise-vocab.toml, or restore it from the content repo",
        ) from None
