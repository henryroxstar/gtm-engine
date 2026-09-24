"""Deterministic pre-load compliance preflight for outbound email.

Three things must be true before a single lead is enrolled in a sequencer, and none of them
live in the copy — so none of them are visible in a sequence spec, and all three default to
off or unset:

1. every attached sending mailbox carries a **physical postal address** (it rides in the
   mailbox signature, appended to every send);
2. the sequence carries a working **opt-out** (one-click ``List-Unsubscribe`` header on, plus a
   visible link or text);
3. every lead sits inside the profile's ``target_markets`` — jurisdictions differ materially
   (see ``docs/email-compliance.md``). The lead's ``country`` is **cross-examined against its
   ``city``**, because that column is enrichment-supplied and has twice been wrong in the
   direction that widens the send; a gate that reads only the field it polices is not a gate.

The provider payloads are fetched by the agent over MCP and piped in here as JSON; the judging
is done in code so it is the same every run and cannot be talked out of a FAIL. Exit status is
the gate: ``0`` = safe to load, ``1`` = do not load.

This checks *mechanics*, not legality. It is not legal advice, and a PASS is not a lawyer's
sign-off — the operator still confirms. See ``docs/email-compliance.md``.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .paths import resolve_profiles_root

# Saleshandy sequence-setting codes (see plugin/skills/email-sequence/references/providers/saleshandy.md)
CODE_UNSUB_LINK = 1
CODE_UNSUB_TEXT = 2
CODE_UNSUB_HEADER = 13

# Market aliases → canonical name. Kept deliberately small: a market not listed here is compared
# on its normalized string, so an unknown-but-matching value still passes.
_MARKET_ALIASES = {
    "us": "united states",
    "usa": "united states",
    # NOTE 2026-09-23: `normalize_market` strips a TRAILING period before this lookup, so a key
    # written with one ("u.s.") can never match — "U.S." arrives here as "u.s". The dotless
    # variants below are the ones that actually fire; the dotted keys are kept only so a reader
    # grepping for "u.s." still finds this note. Same applies to u.k / g.b / u.a.e.
    "u.s.": "united states",
    "u.s": "united states",
    "u.s.a.": "united states",
    "u.s.a": "united states",
    "america": "united states",
    "united states of america": "united states",
    "sg": "singapore",
    "uk": "united kingdom",
    "u.k.": "united kingdom",
    "u.k": "united kingdom",
    "great britain": "united kingdom",
    # Added 2026-09-23 with the UK re-admission. `gb` is the ISO 3166 alpha-2 code and is what
    # several enrichment providers return; the constituent-country names show up in
    # hand-maintained sheets. Without these a row declaring "GB" or "England" normalises to a
    # string absent from target_markets and is dropped as OUT of market — the same silent,
    # lead-losing failure documented for "Hong Kong SAR" below.
    "gb": "united kingdom",
    "g.b.": "united kingdom",
    "g.b": "united kingdom",
    "britain": "united kingdom",
    "england": "united kingdom",
    "scotland": "united kingdom",
    "wales": "united kingdom",
    "northern ireland": "united kingdom",
    "united kingdom of great britain and northern ireland": "united kingdom",
    "uae": "united arab emirates",
    "ae": "united arab emirates",
    "u.a.e.": "united arab emirates",
    "u.a.e": "united arab emirates",
    "au": "australia",
    "ca": "canada",
    # Hong Kong (in-market since 2026-08-26). Enrichment providers return the SAR suffix far more
    # often than the bare name, and `normalize_market` only strips (parenthesised) annotations —
    # so without these a row declaring "Hong Kong SAR" normalises to a string that is absent from
    # target_markets and is dropped as OUT of market. That failure is silent and loses real leads.
    "hk": "hong kong",
    "hong kong sar": "hong kong",
    "hong kong s.a.r": "hong kong",
    "hong kong sar china": "hong kong",
    "hong kong sar, china": "hong kong",
    "hksar": "hong kong",
    # Mainland China is OUT of market and must canonicalise to one value, so the city↔country
    # cross-check reports a Shenzhen-on-a-Hong-Kong-row conflict against `china` rather than
    # against three different spellings of it.
    "cn": "china",
    "prc": "china",
    "mainland china": "china",
    "china mainland": "china",
    "people's republic of china": "china",
}

# Region/continent shorthand that is not a single jurisdiction. Email law differs per country
# within a region (see the Hong Kong/India additions above — each was its own operator decision,
# cleared with counsel), so a region name in `target_markets` must never be silently expanded
# into "every country in it": that would open several jurisdictions at once with none of the
# per-country review the gate exists to force. `read_target_markets` rejects these outright.
#
# Confirmed 2026-09-14: a tenant's PROFILE.md declared `target_markets: [..., Southeast Asia,
# ...]`, which this gate compared literally against each lead's `country` — so a real Singapore
# or Malaysia lead FAILED as "out of market" (silently narrowing, the opposite failure from the
# HK-SAR case above, but the same root cause: a value in `target_markets` that no lead's country
# column can ever literally equal). The profile's own knowledge docs glossed "Southeast Asia" as
# meaning one specific country — evidence that the fix is a wording correction to PROFILE.md,
# not a region-to-countries expansion in code.
_REGION_NAMES = {
    "southeast asia",
    "south east asia",
    "sea",
    "asia",
    "asia pacific",
    "apac",
    "europe",
    "emea",
    "north america",
    "south america",
    "latin america",
    "latam",
    "middle east",
    "gcc",
    "africa",
    "sub-saharan africa",
    "oceania",
    "anz",
    "nordics",
    "benelux",
    "dach",
}


# --------------------------------------------------------------------------- city ↔ country
#
# The `country` column is enrichment-supplied and has been WRONG twice, both times in the
# direction that silently widens the send:
#
#   2026-08-11  1 lead    country="United States", company HQ in Spain    → gate passed 8/8
#   2026-08-11  44 leads  country="United States"/"Singapore", city Bengaluru / Mumbai / Hyderabad
#                         / Pune / Chennai / Dubai / Sydney / Toronto / Shanghai / Ghent / …
#
# A jurisdiction gate that reads only the field it is trying to police is not a gate. The city
# is an independent witness, so we cross-examine it. Purely additive: a city we do not know
# says nothing, and never turns a passing row into a failing one.
#
# Two tiers, and the ORDER MATTERS. `_CITY_COUNTRY` is matched on the whole normalized string
# first; only then do we fall back to whole-token containment (so "bengaluru south" resolves to
# India and "jakarta jakarta" to Indonesia). Exact-first is what keeps "new london" in
# Connecticut instead of deporting it to the UK — which is exactly what a substring match does.
_CITY_COUNTRY = {
    # -- United States: only the ones that collide with a foreign city of the same name.
    # Everything else US is left unlisted; unknown is silent, and silence is the safe default.
    "new london": "united states",
    "new brunswick": "united states",
    "new berlin": "united states",
    # -- India (in-market since 2026-08-11)
    "bengaluru": "india",
    "bangalore": "india",
    "mumbai": "india",
    "bombay": "india",
    "hyderabad": "india",
    "pune": "india",
    "chennai": "india",
    "madras": "india",
    "delhi": "india",
    "new delhi": "india",
    "gurgaon": "india",
    "gurugram": "india",
    "noida": "india",
    "kolkata": "india",
    "ahmedabad": "india",
    "jaipur": "india",
    "mangaluru": "india",
    "mangalore": "india",
    "kochi": "india",
    "coimbatore": "india",
    "indore": "india",
    "nagpur": "india",
    "chandigarh": "india",
    "vadodara": "india",
    "surat": "india",
    "bhubaneswar": "india",
    "mysuru": "india",
    "mysore": "india",
    "visakhapatnam": "india",
    "lucknow": "india",
    "thiruvananthapuram": "india",
    # -- Singapore
    "singapore": "singapore",
    # -- everything below is OUT of every current target market
    "amsterdam": "netherlands",
    "rotterdam": "netherlands",
    "the hague": "netherlands",
    "utrecht": "netherlands",
    "eindhoven": "netherlands",
    "hardinxveld-giessendam": "netherlands",
    "bangkok": "thailand",
    "chiang mai": "thailand",
    "cairo": "egypt",
    "alexandria governorate": "egypt",
    "calgary": "canada",
    "toronto": "canada",
    "vancouver": "canada",
    "montreal": "canada",
    "montréal": "canada",
    "ottawa": "canada",
    "edmonton": "canada",
    "mississauga": "canada",
    "waterloo": "canada",
    "québec": "canada",
    "colombo": "sri lanka",
    "dubai": "united arab emirates",
    "abu dhabi": "united arab emirates",
    "sharjah": "united arab emirates",
    "frankfurt": "germany",
    "karlsruhe": "germany",
    "berlin": "germany",
    "munich": "germany",
    "münchen": "germany",
    "hamburg": "germany",
    "stuttgart": "germany",
    "cologne": "germany",
    "köln": "germany",
    "düsseldorf": "germany",
    "leipzig": "germany",
    "gent": "belgium",
    "ghent": "belgium",
    "brussels": "belgium",
    "antwerp": "belgium",
    "jakarta": "indonesia",
    "bandung": "indonesia",
    "surabaya": "indonesia",
    "karachi": "pakistan",
    "lahore": "pakistan",
    "islamabad": "pakistan",
    "kuala lumpur": "malaysia",
    "penang": "malaysia",
    "johor bahru": "malaysia",
    "petaling jaya": "malaysia",
    "cyberjaya": "malaysia",
    "madrid": "spain",
    "barcelona": "spain",
    "seville": "spain",
    "bilbao": "spain",
    "málaga": "spain",
    "malaga": "spain",
    "shanghai": "china",
    "beijing": "china",
    "shenzhen": "china",
    "guangzhou": "china",
    "hangzhou": "china",
    "chengdu": "china",
    "suzhou": "china",
    # Added 2026-08-26 with the Hong Kong market. The HK↔mainland boundary is precisely what the
    # cross-border pilot cohort sells across, so a mainland operating address arriving on a row
    # that declares `country: Hong Kong` corrupts the list rather than merely widening the send.
    # Weighted toward the port, customs and manufacturing cities that cohort actually surfaces.
    # Deliberately omitted: names that are a district in more than one jurisdiction (Zhongshan is
    # a district of both Dalian and Taipei; Nanshan and Central are generic) — a confident wrong
    # answer from this gazetteer is worse than the silence an unlisted city already gives.
    "dongguan": "china",
    "foshan": "china",
    "zhuhai": "china",
    "xiamen": "china",
    "qingdao": "china",
    "tianjin": "china",
    "ningbo": "china",
    "nanjing": "china",
    "wuhan": "china",
    "chongqing": "china",
    "dalian": "china",
    "xi'an": "china",
    "xian": "china",
    "jinan": "china",
    "changsha": "china",
    "zhengzhou": "china",
    "hefei": "china",
    "fuzhou": "china",
    "wuxi": "china",
    "yiwu": "china",
    "shantou": "china",
    "huizhou": "china",
    "kunshan": "china",
    "taicang": "china",
    "nantong": "china",
    "shaoxing": "china",
    "jiaxing": "china",
    "lianyungang": "china",
    "yantai": "china",
    "weihai": "china",
    "rizhao": "china",
    "shenyang": "china",
    "harbin": "china",
    "changchun": "china",
    "kunming": "china",
    "guiyang": "china",
    "nanning": "china",
    "haikou": "china",
    "sanya": "china",
    "urumqi": "china",
    "lanzhou": "china",
    # Port/free-trade zones that appear as the operating address on trade and logistics rows.
    "shekou": "china",
    "nansha": "china",
    "qianhai": "china",
    "yangshan": "china",
    "sydney": "australia",
    "melbourne": "australia",
    "brisbane": "australia",
    "canberra": "australia",
    "adelaide": "australia",
    "tokyo": "japan",
    "osaka": "japan",
    "kyoto": "japan",
    "yokohama": "japan",
    "seoul": "south korea",
    "busan": "south korea",
    "hong kong": "hong kong",
    "hong kong sar": "hong kong",
    "hksar": "hong kong",
    "kowloon": "hong kong",
    # HK districts that show up as the enrichment `city` instead of the bare SAR name.
    # Omitted on purpose: "central", "admiralty", "north point" and "aberdeen" — each is either a
    # common noun or a city elsewhere, and resolving one confidently would deport a real lead.
    "sha tin": "hong kong",
    "shatin": "hong kong",
    "tsuen wan": "hong kong",
    "kwun tong": "hong kong",
    "tsim sha tsui": "hong kong",
    "wan chai": "hong kong",
    "causeway bay": "hong kong",
    "sheung wan": "hong kong",
    "quarry bay": "hong kong",
    "kwai chung": "hong kong",
    "kwai fong": "hong kong",
    "tuen mun": "hong kong",
    "yuen long": "hong kong",
    "tai po": "hong kong",
    "sai kung": "hong kong",
    "mong kok": "hong kong",
    "sham shui po": "hong kong",
    "cheung sha wan": "hong kong",
    "hung hom": "hong kong",
    "tseung kwan o": "hong kong",
    "wong tai sin": "hong kong",
    "ma on shan": "hong kong",
    "fo tan": "hong kong",
    "lantau": "hong kong",
    "discovery bay": "hong kong",
    # Adjacent SAR, out of market — listed so it resolves rather than passing as unknown.
    "macau": "macau",
    "macao": "macau",
    "taipei": "taiwan",
    "hsinchu": "taiwan",
    "manila": "philippines",
    "makati": "philippines",
    "cebu": "philippines",
    "quezon city": "philippines",
    "taguig": "philippines",
    "ho chi minh city": "vietnam",
    "ho chi minh": "vietnam",
    "hanoi": "vietnam",
    "da nang": "vietnam",
    "tel aviv": "israel",
    "tel aviv-yafo": "israel",
    "jerusalem": "israel",
    "haifa": "israel",
    "herzliya": "israel",
    "paris": "france",
    "lyon": "france",
    "toulouse": "france",
    "marseille": "france",
    "nantes": "france",
    "bordeaux": "france",
    "milan": "italy",
    "milano": "italy",
    "turin": "italy",
    "bologna": "italy",
    "stockholm": "sweden",
    "gothenburg": "sweden",
    "oslo": "norway",
    "copenhagen": "denmark",
    "helsinki": "finland",
    "zurich": "switzerland",
    "zürich": "switzerland",
    "geneva": "switzerland",
    "basel": "switzerland",
    "lausanne": "switzerland",
    "warsaw": "poland",
    "krakow": "poland",
    "kraków": "poland",
    "wroclaw": "poland",
    "prague": "czechia",
    "brno": "czechia",
    "budapest": "hungary",
    "bucharest": "romania",
    "sofia": "bulgaria",
    "lisbon": "portugal",
    "porto": "portugal",
    "istanbul": "türkiye",
    "ankara": "türkiye",
    "izmir": "türkiye",
    "moscow": "russia",
    "saint petersburg": "russia",
    "kyiv": "ukraine",
    "kiev": "ukraine",
    "lviv": "ukraine",
    "sao paulo": "brazil",
    "são paulo": "brazil",
    "rio de janeiro": "brazil",
    "brasilia": "brazil",
    "belo horizonte": "brazil",
    "buenos aires": "argentina",
    "bogota": "colombia",
    "bogotá": "colombia",
    "medellin": "colombia",
    "medellín": "colombia",
    "mexico city": "mexico",
    "ciudad de méxico": "mexico",
    "guadalajara": "mexico",
    "monterrey": "mexico",
    "nairobi": "kenya",
    "lagos": "nigeria",
    "abuja": "nigeria",
    "cape town": "south africa",
    "johannesburg": "south africa",
    "pretoria": "south africa",
    "east london": "south africa",
    "durban": "south africa",
    "accra": "ghana",
    "auckland": "new zealand",
    "riyadh": "saudi arabia",
    "jeddah": "saudi arabia",
    "doha": "qatar",
    "kuwait city": "kuwait",
    "manama": "bahrain",
    "muscat": "oman",
    "amman": "jordan",
    "beirut": "lebanon",
    "dhaka": "bangladesh",
    "kathmandu": "nepal",
    "edinburgh": "united kingdom",
    "glasgow": "united kingdom",
    "leeds": "united kingdom",
    "liverpool": "united kingdom",
    "sheffield": "united kingdom",
    "nottingham": "united kingdom",
    "belfast": "united kingdom",
    "cardiff": "united kingdom",
    "dublin 2": "ireland",
    "cork": "ireland",
}

# City names that exist in a target market AND outside one, where neither reading dominates
# enough to assert. We refuse to guess: an ambiguous city is treated exactly like a blank
# country — WARN normally, FAIL under --strict-market. "Cannot prove the jurisdiction" and
# "no jurisdiction recorded" are the same risk, so they get the same verdict.
_AMBIGUOUS_CITIES = frozenset(
    {
        "london",
        "birmingham",
        "manchester",
        "cambridge",
        "oxford",
        "reading",
        "york",
        "dublin",
        "vienna",
        "athens",
        "rome",
        "lima",
        "bristol",
        "wellington",
        "hamilton",
        "windsor",
        "richmond",
        "kingston",
        "newcastle",
        "perth",
        "victoria",
        "santiago",
        "valencia",
        "naples",
        "syracuse",
        "st petersburg",
        "saint john",
        "columbia",
        "santa cruz",
    }
)

# Trailing state/province/region qualifier: "Henderson, NV" → "henderson".
_CITY_QUALIFIER_RE = re.compile(r"\s*,\s*[a-z .]{2,20}$")
# Descriptive suffixes enrichment tacks on: "San Francisco Bay Area", "South Delhi Campus".
_CITY_NOISE_RE = re.compile(r"\b(bay area|metropolitan area|metro area|campus|greater)\b")


def normalize_city(value: str) -> str:
    """Fold a raw city string to its comparable form: no diacritics, no state suffix, no noise."""
    v = unicodedata.normalize("NFKD", value or "")
    v = "".join(ch for ch in v if not unicodedata.combining(ch))
    v = v.strip().lower()
    v = _CITY_QUALIFIER_RE.sub("", v)
    v = _CITY_NOISE_RE.sub(" ", v)
    return re.sub(r"[\s,]+", " ", v).strip()


#: The gazetteer keyed by its own folded form, built once at import.
#: Diacritics are already folded on the lookup side, so the comparison needs a folded
#: view of the keys — rebuilding it per row made every call re-fold the whole table.
_CITY_COUNTRY_FOLDED = {normalize_city(k): v for k, v in _CITY_COUNTRY.items()}


def city_country(city: str) -> tuple[str | None, bool]:
    """Resolve a city to ``(country, ambiguous)``.

    ``(None, False)`` means "no opinion" — an unlisted city never contradicts anything.
    ``(None, True)`` means the name is real but shared across jurisdictions.

    Exact match wins outright; only an unmatched string falls through to whole-token
    containment, and even then the longest gazetteer entry wins so "new delhi" beats "delhi".
    """
    norm = normalize_city(city)
    if not norm:
        return None, False
    if norm in _AMBIGUOUS_CITIES:
        return None, True
    exact = _CITY_COUNTRY_FOLDED
    if norm in exact:
        return exact[norm], False
    tokens = norm.split()
    best: tuple[int, str] | None = None
    for key, country in exact.items():
        parts = key.split()
        n = len(parts)
        if n <= len(tokens) and any(tokens[i : i + n] == parts for i in range(len(tokens) - n + 1)):
            if best is None or n > best[0]:
                best = (n, country)
    if best:
        return best[1], False
    # Ambiguity is judged on the WHOLE name only. "New York" contains the token "york" but is
    # not itself ambiguous, and token-level matching flagged all 14 New York rows on the first
    # run — noise that trains the operator to skim past this section.
    return None, False


def _strip_quotes(value: str) -> str:
    """Strip one layer of matching '...' or "..." quoting, e.g. from a YAML-flow-style list item."""
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1].strip()
    return v


def normalize_market(value: str) -> str:
    """Normalize a market/country string: lowercase, strip any ``(primary)``-style annotation."""
    v = re.sub(r"\(.*?\)", "", value or "").strip().lower().rstrip(".")
    v = re.sub(r"\s+", " ", v)
    return _MARKET_ALIASES.get(v, v)


@dataclass
class Result:
    """One named check with a verdict and human-readable detail lines."""

    name: str
    status: str  # PASS | FAIL | WARN
    detail: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return self.status == "FAIL"


# --------------------------------------------------------------------------- profile


def read_target_markets(profile: str, profiles_root: Path | None = None) -> list[str]:
    """Parse ``target_markets: [a, b]`` out of a profile's PROFILE.md.

    Raises FileNotFoundError if the profile has no PROFILE.md, ValueError if the key is absent
    or names a region rather than a country (see ``_REGION_NAMES``) — all three are hard stops:
    an unbounded or region-shaped market list is exactly the thing this guards against.
    """
    root = profiles_root or resolve_profiles_root()
    path = root / profile / "PROFILE.md"
    if not path.is_file():
        raise FileNotFoundError(f"no PROFILE.md for profile {profile!r} at {path}")
    text = path.read_text(encoding="utf-8")
    # Only the assignment line counts — prose mentioning the key (e.g. the reminder table) is
    # skipped by requiring the line to start with the key.
    for line in text.splitlines():
        if not line.startswith("target_markets:"):
            continue
        raw = line.split(":", 1)[1].split("#", 1)[0].strip()
        inner = raw[1:-1] if raw.startswith("[") and raw.endswith("]") else raw
        markets = [_strip_quotes(m.strip()) for m in inner.split(",") if m.strip()]
        markets = [m for m in markets if m]
        if markets:
            regions = [m for m in markets if normalize_market(m) in _REGION_NAMES]
            if regions:
                raise ValueError(
                    f"{path} `target_markets` names a region, not a country: {regions!r}. "
                    "Email law differs per country within a region, and a lead's `country` "
                    "column is never literally the region's name, so this gate would either "
                    "silently drop every real lead in it (compared literally, nothing matches) "
                    "or — if expanded in code — silently open several jurisdictions at once "
                    "with none of the per-country legal review the gate exists to force. List "
                    "the actual countries explicitly instead (operator decision, ideally "
                    "cleared with counsel — see an existing profile's PROFILE.md for the "
                    "pattern)."
                )
            return markets
    raise ValueError(f"{path} has no `target_markets:` assignment — cannot bound the send")


# --------------------------------------------------------------------------- payload readers


def _load_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def extract_signatures(accounts_payload) -> dict[str, str]:
    """Map ``fromEmail -> signature`` from a raw ``list_email_accounts`` payload.

    Accepts the full response, its ``payload``, or a bare list of accounts, so the agent can pipe
    the tool result through unreshaped.
    """
    node = accounts_payload
    if isinstance(node, dict):
        node = node.get("payload", node)
    if isinstance(node, dict):
        node = node.get("emails", node)
    if not isinstance(node, list):
        raise ValueError("accounts payload: expected a list of email accounts")

    out: dict[str, str] = {}
    for acct in node:
        email = (acct.get("fromEmail") or acct.get("email") or "").strip()
        sig = ""
        for setting in acct.get("settings") or []:
            if str(setting.get("code")) == "signature":
                sig = setting.get("value") or ""
                break
        out[email] = sig
    return out


def extract_settings(settings_payload) -> dict[int, str]:
    """Map ``code -> value`` from a raw ``get_sequence_settings`` payload."""
    node = settings_payload
    if isinstance(node, dict):
        node = node.get("payload", node)
    if isinstance(node, dict):
        node = node.get("settings", node)
    if not isinstance(node, list):
        raise ValueError("settings payload: expected a list of sequence settings")
    return {int(s["code"]): (s.get("value") or "") for s in node if s.get("code") is not None}


_TAG_RE = re.compile(r"<[^>]+>")


def _plain(html: str) -> str:
    """Flatten a signature's HTML to text — tags to spaces, the few entities that show up decoded."""
    text = _TAG_RE.sub(" ", html or "")
    for entity, char in (("&amp;", "&"), ("&nbsp;", " "), ("&#39;", "'"), ("&quot;", '"')):
        text = text.replace(entity, char)
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------- checks


def check_addresses(signatures: dict[str, str]) -> Result:
    """Every sending mailbox must carry an address-shaped signature.

    Address *shape* is all code can judge — a street number or postcode's worth of digits, and
    enough text to be an identity block. Whether the address is real and current is the operator's
    confirm, which is why every signature is echoed in the detail lines.
    """
    detail: list[str] = []
    bad = False
    if not signatures:
        return Result("postal address", "FAIL", ["no sending mailboxes in the payload"])
    for email, sig in sorted(signatures.items()):
        text = _plain(sig)
        if not text:
            detail.append(f"FAIL {email}: signature is empty — sends would carry no postal address")
            bad = True
        elif not re.search(r"\d", text):
            detail.append(f"FAIL {email}: signature has no digits — no street number or postcode")
            bad = True
        elif len(text.split()) < 6:
            detail.append(f"FAIL {email}: signature too short to be an identity block — {text!r}")
            bad = True
        else:
            detail.append(f"pass {email}: {text}")
    return Result("postal address", "FAIL" if bad else "PASS", detail)


def check_optout(settings: dict[int, str]) -> Result:
    """One-click header must be on, and a visible link or text must exist."""
    detail: list[str] = []
    bad = False

    header = settings.get(CODE_UNSUB_HEADER, "")
    if header == "1":
        detail.append("pass one-click List-Unsubscribe header (code 13) is ON")
    else:
        detail.append(
            f"FAIL one-click List-Unsubscribe header (code 13) is {header or 'unset'!r} — turn it on"
        )
        bad = True

    link = _plain(settings.get(CODE_UNSUB_LINK, ""))
    text = _plain(settings.get(CODE_UNSUB_TEXT, ""))
    if link:
        detail.append(f"pass unsubscribe link (code 1): {link}")
    if text:
        detail.append(f"pass unsubscribe text (code 2): {text}")
    if not link and not text:
        detail.append("FAIL no visible opt-out — set an unsubscribe link (code 1) or text (code 2)")
        bad = True

    return Result("opt-out", "FAIL" if bad else "PASS", detail)


def check_markets(
    rows: list[dict],
    target_markets: list[str],
    *,
    strict: bool = False,
    suppressed: set[str] | None = None,
) -> Result:
    """Every lead's country must sit inside ``target_markets``.

    A blank country is *unknown*, not *allowed*: it is reported as its own bucket and, under
    ``--strict-market``, fails the gate.

    A literal ``"global"`` entry (case-insensitive) is a wildcard — see ``MarketGate`` in
    ``gtm_core.prospects_consolidate`` for why — and disables the jurisdiction check entirely.

    ``suppressed`` is the set of lower-cased emails carried by the **suppression ledger**
    (``gtm_core.suppression``); those rows are not being loaded, so they are excluded from the
    verdict and reported separately. Trust the ledger, never the file's own ``suppression``
    column: that column is written onto a build output that the next ``prospects_consolidate``
    run regenerates and silently discards, so honouring it here would let a rebuilt file pass a
    gate it no longer satisfies. Without this parameter a correctly-suppressed list can never go
    green — and a gate that always fails is a gate people stop running, which is how 32
    out-of-market leads reached a live sequence on 2026-08-12.
    """
    allowed = {normalize_market(m) for m in target_markets}
    wildcard = "global" in allowed
    suppressed = suppressed or set()
    out_of_market: list[str] = []
    unknown: list[str] = []
    conflicts: list[str] = []
    ambiguous: list[str] = []
    excluded = 0

    for row in rows:
        country = (row.get("country") or row.get("Country") or "").strip()
        who = (row.get("email") or row.get("Email") or "?").strip()
        city = (row.get("city") or row.get("City") or "").strip()
        if who.lower() in suppressed:
            excluded += 1
            continue
        if not country:
            unknown.append(f"{who} (city: {city or 'none'})")
        elif not wildcard and normalize_market(country) not in allowed:
            out_of_market.append(f"{who} — {country}")
        elif not wildcard:
            # The declared country is in-market. Before accepting it, ask the city.
            implied, is_ambiguous = city_country(city)
            if implied and implied != normalize_market(country):
                verdict = "OUT of market" if implied not in allowed else "in market"
                conflicts.append(
                    f"{who} — declared {country!r}, city {city!r} implies "
                    f"{implied.title()} ({verdict})"
                )
            elif is_ambiguous:
                ambiguous.append(f"{who} — declared {country!r}, city {city!r} is not unique")

    detail = [
        f"markets in scope: {', '.join(target_markets)}",
        f"{len(rows) - excluded} lead(s) checked",
    ]
    if excluded:
        detail.append(f"{excluded} row(s) excluded by the suppression ledger — not being loaded")
    if out_of_market:
        detail.append(
            f"FAIL {len(out_of_market)} lead(s) outside target_markets — drop before loading:"
        )
        detail += [f"  - {r}" for r in out_of_market[:20]]
        if len(out_of_market) > 20:
            detail.append(f"  … and {len(out_of_market) - 20} more")
    if conflicts:
        detail.append(
            f"FAIL {len(conflicts)} lead(s) whose city contradicts the declared country — "
            "the country column is enrichment-supplied and cannot be trusted alone; "
            "resolve each before loading:"
        )
        detail += [f"  - {r}" for r in conflicts[:20]]
        if len(conflicts) > 20:
            detail.append(f"  … and {len(conflicts) - 20} more")
    if ambiguous:
        label = "FAIL" if strict else "WARN"
        detail.append(
            f"{label} {len(ambiguous)} lead(s) whose city exists in more than one country — "
            "jurisdiction unproven, same risk as a blank country:"
        )
        detail += [f"  - {r}" for r in ambiguous[:10]]
        if len(ambiguous) > 10:
            detail.append(f"  … and {len(ambiguous) - 10} more")
    if unknown:
        label = "FAIL" if strict else "WARN"
        detail.append(
            f"{label} {len(unknown)} lead(s) with no country — resolve or exclude; they are not 'allowed' by default"
        )
        detail += [f"  - {r}" for r in unknown[:10]]
        if len(unknown) > 10:
            detail.append(f"  … and {len(unknown) - 10} more")

    if out_of_market or conflicts or ((unknown or ambiguous) and strict):
        status = "FAIL"
    elif unknown or ambiguous:
        status = "WARN"
    else:
        status = "PASS"
    return Result("markets", status, detail)


# --------------------------------------------------------------------------- capabilities
#
# SC2/SC4 live in `gtm_core.capability_preflight`: judging what the PROVIDER does and what
# this workspace switched on is a different question from judging what this SEQUENCE
# carries. That module imports `Result`/`extract_settings` from HERE, so this one imports
# it lazily inside the CLI paths that need it — one direction at import time, no cycle.
# The capability Result still joins `_preflight`'s single list and single exit status: a
# second gate is a gate somebody runs separately, which is to say occasionally.

# --------------------------------------------------------------------------- report


def render(results: list[Result], *, markdown: bool = False) -> str:
    if markdown:
        lines = ["| Check | Verdict | Detail |", "|---|---|---|"]
        for r in results:
            first = r.detail[0] if r.detail else ""
            lines.append(f"| {r.name} | **{r.status}** | {first} |")
            for extra in r.detail[1:]:
                lines.append(f"| | | {extra} |")
        return "\n".join(lines)
    lines = []
    for r in results:
        lines.append(f"[{r.status}] {r.name}")
        lines += [f"    {d}" for d in r.detail]
    return "\n".join(lines)


def _resolve_provider(args) -> str | None:
    """The sequencer to assert against: ``--provider``, else the profile's ``email_tool``.

    Returns None when neither is available (no provider named, nothing to assert) and for the
    ``manual`` tool, which is a human with an inbox — it has no capabilities to read.
    """
    from .capability_preflight import read_email_tool

    provider = getattr(args, "provider", None)
    if not provider and getattr(args, "profile", None):
        try:
            provider = read_email_tool(args.profile)
        except (FileNotFoundError, ValueError):
            return None
    if not provider or provider == "manual":
        return None
    return provider


def _record_autoset(args) -> int:
    """Record an auto-set that has already been applied, AFTER verifying the re-read shows it.

    The verification is the point: an auto-set the provider accepted but did not apply is the
    2026-08-11 shape (`modifiedAt` moved, the value did not). A `capability_autoset` row is
    written only when the after-payload actually shows the flipped value, and it carries both
    values so the row alone is enough to reverse the change (test plan §3.E).
    """
    from .capability_preflight import _SETTING_ON, AUTOSET_ALLOWLIST

    before = extract_settings(_load_json(args.before_json))
    after = extract_settings(_load_json(args.after_json))
    was = before.get(args.code)
    now = after.get(args.code)
    if now not in _SETTING_ON:
        print(
            f"refusing to record: setting code {args.code} reads {now!r} after the write, not on "
            f"— the provider accepted the call without applying it, or the wrong code was sent",
            file=sys.stderr,
        )
        return 1
    if args.code not in AUTOSET_ALLOWLIST:
        print(
            f"refusing to record: setting code {args.code} is not on AUTOSET_ALLOWLIST "
            f"({sorted(AUTOSET_ALLOWLIST)}) — an auto-set outside the allowlist is a manual "
            "change and must be recorded as one",
            file=sys.stderr,
        )
        return 1
    from gtm_core.capability_ledger import record_autoset

    record_autoset(
        args.profile,
        provider=args.provider,
        capability=args.capability,
        setting_code=args.code,
        value_before=was,
        value_after=now,
    )
    print(f"recorded capability_autoset: {args.provider}/{args.capability} code {args.code}")
    return 0


def _preflight(args) -> int:
    results: list[Result] = []

    if args.accounts_json:
        results.append(check_addresses(extract_signatures(_load_json(args.accounts_json))))
    if args.settings_json:
        results.append(check_optout(extract_settings(_load_json(args.settings_json))))

    provider = _resolve_provider(args)
    capability_result: Result | None = None
    if provider:
        from .capability_preflight import autoset_enabled, check_capabilities

        settings_payload = _load_json(args.settings_json) if args.settings_json else None
        capability_result = check_capabilities(
            provider,
            settings_payload,
            attested=frozenset(args.attest or ()),
            autoset_enabled=autoset_enabled(),
        )
        results.append(capability_result)

    if args.leads_csv:
        with open(args.leads_csv, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        markets = args.market or read_target_markets(args.profile)
        suppressed = None
        if args.suppression_ledger:
            from gtm_core import suppression as _suppression

            suppressed = set(_suppression.load(args.suppression_ledger))
        results.append(
            check_markets(rows, markets, strict=args.strict_market, suppressed=suppressed)
        )

    if not results:
        print(
            "nothing to check — pass at least one of --accounts-json / --settings-json / --leads-csv",
            file=sys.stderr,
        )
        return 2

    print(render(results, markdown=args.markdown))
    failed = [r for r in results if r.failed]

    # "Was this sequence checked, and when?" must be answerable from the ledger with no provider
    # call (test plan §3.E). Written for a FAIL too: a refused staging attempt is exactly the
    # event somebody will later want to find.
    if getattr(args, "sequence_id", None) and getattr(args, "profile", None) and capability_result:
        from gtm_core.capability_ledger import record_asserted

        record_asserted(
            args.profile,
            provider=provider,
            sequence_id=args.sequence_id,
            status=capability_result.status,
            attested=list(args.attest or ()),
            detail=capability_result.detail,
        )

    print()
    if failed:
        print(f"DO NOT LOAD — {len(failed)} check(s) failed: {', '.join(r.name for r in failed)}")
        return 1
    print(
        "Mechanics pass. This is not legal advice — the operator still confirms (docs/email-compliance.md)."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gtm_core.email_compliance",
        description="Pre-load compliance preflight: postal address, opt-out, market. Exit 1 = do not load.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    pf = sub.add_parser("preflight", help="run the checks and gate on the result")
    pf.add_argument("--profile", help="active profile (for target_markets)")
    pf.add_argument("--accounts-json", type=Path, help="raw list_email_accounts payload")
    pf.add_argument("--settings-json", type=Path, help="raw get_sequence_settings payload")
    pf.add_argument("--leads-csv", type=Path, help="the lead list about to be loaded")
    pf.add_argument("--market", action="append", help="override target_markets (repeatable)")
    pf.add_argument(
        "--strict-market", action="store_true", help="treat an unknown country as a failure"
    )
    pf.add_argument(
        "--suppression-ledger",
        type=Path,
        help="suppression ledger (.pool/suppression.csv); its rows are excluded from the "
        "market check because they are not being loaded",
    )
    pf.add_argument(
        "--markdown", action="store_true", help="emit the markdown table for the sequence spec"
    )
    pf.add_argument(
        "--provider",
        help="sequencer to assert capabilities against (default: the profile's email_tool)",
    )
    pf.add_argument(
        "--attest",
        action="append",
        default=[],
        help="capability the operator confirmed in the provider UI THIS RUN (repeatable). "
        "Accepted only where the registry says the setting cannot be read back; it is never "
        "carried into a later run.",
    )
    pf.add_argument(
        "--sequence-id",
        help="record a `capability_asserted` history row for this sequence (needs --profile)",
    )
    pf.set_defaults(func=_preflight)

    ra = sub.add_parser(
        "record-autoset",
        help="record an auto-set that has ALREADY been applied and verified in the provider",
    )
    ra.add_argument("--profile", required=True, help="active profile (the ledger to write)")
    ra.add_argument("--provider", required=True, help="sequencer the setting belongs to")
    ra.add_argument("--capability", required=True, help="capability the setting implements")
    ra.add_argument("--code", required=True, type=int, help="provider setting code that changed")
    ra.add_argument(
        "--before-json", required=True, type=Path, help="settings payload read BEFORE the write"
    )
    ra.add_argument(
        "--after-json", required=True, type=Path, help="settings payload re-read AFTER the write"
    )
    ra.set_defaults(func=_record_autoset)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
