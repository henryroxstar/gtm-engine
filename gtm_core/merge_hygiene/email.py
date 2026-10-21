from __future__ import annotations

import re

# --- row-level -----------------------------------------------------------

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_ROLE_LOCAL_RE = re.compile(
    r"^(?:info|sales|support|contact|hello|admin|team|office|enquiries|inquiries|"
    r"help|billing|careers|jobs|press|marketing|noreply|no-reply|postmaster|webmaster)$",
    re.IGNORECASE,
)
_FREEMAIL = frozenset(
    {
        "gmail.com",
        "yahoo.com",
        "hotmail.com",
        "outlook.com",
        "aol.com",
        "icloud.com",
        "protonmail.com",
        "proton.me",
        "gmx.com",
        "mail.com",
        "yandex.com",
        "qq.com",
        "163.com",
    }
)
# Values that mean enrichment failed, not that the company has an odd domain.
_JUNK_DOMAIN_TOKENS = (
    "azurewebsites.net",
    "geo-blocked",
    "linkedin.com",
    "example.com",
    "localhost",
    "notfound",
    # A landing-page URL parked in a domain column. Providers do this routinely
    # ("acme.example/sg", "globex.example/us", "initech.example/en"): the host is right, the
    # field is
    # wrong. Kept as junk so the data defect stays visible even though
    # :func:`bare_host` now stops it inventing a domain mismatch.
    "/",
)

_URLISH_RE = re.compile(r"^\s*[a-z][a-z0-9+.-]*://", re.IGNORECASE)


def bare_host(value: str) -> str:
    """A ``company_domain`` field reduced to the host it actually names.

    Strips a scheme, ``www.``, any path/query/fragment, a port, and a trailing dot —
    so ``"https://www.acme.example/sg"`` and ``"acme.example"`` compare equal.

    Why this exists: providers park a landing-page URL in the domain column, and the
    comparison it feeds (``email-domain-mismatch``) then reports
    ``acme.example != acme.example/sg``
    — the host matches, only the field is dirty. Measured 2026-08-20 on the live lists:
    **every** non-bare-host value produced a false mismatch, 10 of 52 mismatch findings
    in total. A gate that cries wolf on a fifth of its own findings trains a reader to
    skim all of them.

    Normalising for comparison must not hide the defect, so the raw value still trips
    ``company-domain-junk`` via the ``"/"`` token above: right finding, right name.
    """
    v = (value or "").strip().lower()
    v = _URLISH_RE.sub("", v)
    v = v.removeprefix("www.")
    # Cut at the first path/query/fragment character, then drop any :port.
    v = re.split(r"[/?#]", v, maxsplit=1)[0]
    v = v.split(":", 1)[0]
    return v.strip().rstrip(".")
