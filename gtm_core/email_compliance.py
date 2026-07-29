"""Deterministic pre-load compliance preflight for outbound email.

Three things must be true before a single lead is enrolled in a sequencer, and none of them
live in the copy — so none of them are visible in a sequence spec, and all three default to
off or unset:

1. every attached sending mailbox carries a **physical postal address** (it rides in the
   mailbox signature, appended to every send);
2. the sequence carries a working **opt-out** (one-click ``List-Unsubscribe`` header on, plus a
   visible link or text);
3. every lead sits inside the profile's ``target_markets`` — jurisdictions differ materially
   (see ``docs/email-compliance.md``).

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
    "u.s.": "united states",
    "u.s.a.": "united states",
    "america": "united states",
    "united states of america": "united states",
    "sg": "singapore",
    "uk": "united kingdom",
    "u.k.": "united kingdom",
    "great britain": "united kingdom",
    "uae": "united arab emirates",
    "ae": "united arab emirates",
    "au": "australia",
    "ca": "canada",
}


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

    Raises FileNotFoundError if the profile has no PROFILE.md, ValueError if the key is absent —
    both are hard stops: an unbounded market list is exactly the thing this guards against.
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


def check_markets(rows: list[dict], target_markets: list[str], *, strict: bool = False) -> Result:
    """Every lead's country must sit inside ``target_markets``.

    A blank country is *unknown*, not *allowed*: it is reported as its own bucket and, under
    ``--strict-market``, fails the gate.

    A literal ``"global"`` entry (case-insensitive) is a wildcard — see ``MarketGate`` in
    ``gtm_core.prospects_consolidate`` for why — and disables the jurisdiction check entirely.
    """
    allowed = {normalize_market(m) for m in target_markets}
    wildcard = "global" in allowed
    out_of_market: list[str] = []
    unknown: list[str] = []

    for row in rows:
        country = (row.get("country") or row.get("Country") or "").strip()
        who = (row.get("email") or row.get("Email") or "?").strip()
        city = (row.get("city") or row.get("City") or "").strip()
        if not country:
            unknown.append(f"{who} (city: {city or 'none'})")
        elif not wildcard and normalize_market(country) not in allowed:
            out_of_market.append(f"{who} — {country}")

    detail = [f"markets in scope: {', '.join(target_markets)}", f"{len(rows)} lead(s) checked"]
    if out_of_market:
        detail.append(
            f"FAIL {len(out_of_market)} lead(s) outside target_markets — drop before loading:"
        )
        detail += [f"  - {r}" for r in out_of_market[:20]]
        if len(out_of_market) > 20:
            detail.append(f"  … and {len(out_of_market) - 20} more")
    if unknown:
        label = "FAIL" if strict else "WARN"
        detail.append(
            f"{label} {len(unknown)} lead(s) with no country — resolve or exclude; they are not 'allowed' by default"
        )
        detail += [f"  - {r}" for r in unknown[:10]]
        if len(unknown) > 10:
            detail.append(f"  … and {len(unknown) - 10} more")

    if out_of_market or (unknown and strict):
        status = "FAIL"
    elif unknown:
        status = "WARN"
    else:
        status = "PASS"
    return Result("markets", status, detail)


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


def _preflight(args) -> int:
    results: list[Result] = []

    if args.accounts_json:
        results.append(check_addresses(extract_signatures(_load_json(args.accounts_json))))
    if args.settings_json:
        results.append(check_optout(extract_settings(_load_json(args.settings_json))))
    if args.leads_csv:
        with open(args.leads_csv, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        markets = args.market or read_target_markets(args.profile)
        results.append(check_markets(rows, markets, strict=args.strict_market))

    if not results:
        print(
            "nothing to check — pass at least one of --accounts-json / --settings-json / --leads-csv",
            file=sys.stderr,
        )
        return 2

    print(render(results, markdown=args.markdown))
    failed = [r for r in results if r.failed]
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
        "--markdown", action="store_true", help="emit the markdown table for the sequence spec"
    )
    pf.set_defaults(func=_preflight)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
