"""Fictional vocabulary shared by the regulator / competitor classifier tests.

§R9: nothing here is a real body. Names are the invented ``Examplia`` family; every domain is
RFC 2606 ``.example``. The multi-label shapes that stand for a ``.co.uk``, ``.org.uk``, ``.int`` or
``.gov.xx`` body are the output of ``python -m gtm_core.fictionalize domain <real shape>`` — the
tool keeps the label count and drops the identity — so the test of "a body on a three-label
domain" runs on a three-label domain without a real one appearing outside ``profiles/``.

======================  ================================  =========================
shape it stands for     domain used                       body
======================  ================================  =========================
two-label (.org)        cascade.example                   Examplia Reserve System
three-label (.co.uk)    atlas.wideloop.example            Examplia Central Bank
three-label (.org.uk)   meridians.summitline.example      Examplia Markets Authority
two-label (.int)        marlowe.example                   Examplia Settlements Union
gov ending (.gov.xx)    <any>.gov.example                 any government body
======================  ================================  =========================
"""

from __future__ import annotations

import datetime
from pathlib import Path

PROFILE = "acme"
TODAY = datetime.date(2026, 10, 2)

REGULATORS_TOML = """
schema = 1
reviewed = "2026-10-02"

[endings]
government = [".gov.example", ".mil.example"]
public-health = [".nhs.example"]

[[body]]
name = "Examplia Central Bank"
kind = "central-bank"
domains = ["atlas.wideloop.example"]
aliases = ["Bank of Examplia"]
note = "supervises banks in Examplia"
verified = true

[[body]]
name = "Examplia Markets Authority"
kind = "regulator"
domains = ["meridians.summitline.example"]

[[body]]
name = "Examplia Reserve System"
kind = "central-bank"
domains = ["cascade.example"]

[[body]]
name = "Examplia Settlements Union"
kind = "clearing"
domains = ["marlowe.example"]

[[body]]
name = "Examplia Securities Exchange"
kind = "exchange"
domains = ["examplia-exchange.example"]
aliases = ["Examplia Bourse"]

[[body]]
name = "Examplia Standards Institute"
kind = "standards"
domains = ["examplia-standards.example"]

[[body]]
name = "Examplia Brokers Association"
kind = "self-regulatory"
domains = ["examplia-brokers.example"]

[[body]]
name = "Examplia Health Service"
kind = "public-health"
domains = ["examplia-health.example"]
"""

COMPETITORS_TOML = """
schema = 1
reviewed = "2026-10-02"

[[competitor]]
name = "Contoso Agent Broker"
tier = "direct"
aliases = ["Contoso Broker", "CAB Gateway"]
domains = ["contoso.example"]
note = "Overlaps the core wedge."

[[competitor]]
name = "Northwind Robotics"
tier = "adjacent"
aliases = []
domains = ["northwind.example"]
note = "Neighbouring product."

[[competitor]]
name = "Fabrikam Identity"
tier = "si-channel"
domains = ["fabrikam.example"]
note = "Channel."
"""


def write_profile(
    tmp_path: Path,
    *,
    regulators: str | None = REGULATORS_TOML,
    competitors: str | None = COMPETITORS_TOML,
    profile: str = PROFILE,
) -> Path:
    """A ``profiles/`` root under ``tmp_path`` holding the two lists; ``None`` omits a file."""
    root = tmp_path / "profiles"
    kdir = root / profile / "knowledge"
    kdir.mkdir(parents=True, exist_ok=True)
    for name, body in (("regulators.toml", regulators), ("competitors.toml", competitors)):
        if body is not None:
            (kdir / name).write_text(body, encoding="utf-8")
    return root


def row(**kw) -> dict:
    """A pool row. Defaults are a clean prospect at an unlisted company."""
    base = {
        "first": "Jordan",
        "last": "Vance",
        "email": "jordan.vance@vertex.example",
        "title": "Chief Information Security Officer",
        "company": "Vertex Systems",
        "company_domain": "vertex.example",
        "category_relation": "prospect",
        "verdict": "send",
    }
    base.update(kw)
    return base
