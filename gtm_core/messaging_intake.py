"""Deterministic parser, validator, exporter and stager for messaging intake documents.

Bridges the 3-Tier Seam:
  Tier 1: Human-facing markdown intake (MESSAGING-INTAKE.md)
  Tier 2: Parser, validator, backup generator, and staging into content/<profile>/knowledge-staging/
  Tier 3: Machine-checked fact registry (claims.toml, proof.toml, angles.toml, role-vocabulary.toml)

Stdlib-only, deterministic, zero egress (§R6). Only writes under content/<profile>/
(knowledge-staging/ and knowledge-backups/), preserving the read-only profiles/ boundary (§R1).
Promotion to profiles/<profile>/knowledge/ remains strictly with gtm_core.knowledge_staging.promote.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .fsio import atomic_write_text, utc_stamp
from .messaging.registry import (
    ANGLE_STATUSES,
    ANGLES_FILE,
    CLAIM_STATUSES,
    CLAIMS_FILE,
    FIGURE_KINDS,
    OPENER_KINDS,
    PROOF_FILE,
    PROOF_KINDS,
)
from .paths import _safe_segment, resolve_content_root, resolve_profiles_root
from .slugify import slug as slugify

BACKUPS_DIRNAME = "knowledge-backups"
STAGING_DIRNAME = "knowledge-staging"
ROLE_VOCABULARY_FILE = "role-vocabulary.toml"


@dataclass
class SeatIntake:
    name: str
    personas: list[str] = field(default_factory=list)
    lead_pain: str = ""
    gain: str = ""
    forbidden_pains: list[str] = field(default_factory=list)
    register: str = "standard"
    notes: str = ""


@dataclass
class ClaimIntake:
    id: str
    group: str = ""
    status: str = "design-target"
    statement: str = ""
    source: str = ""
    do_not_say: list[str] = field(default_factory=list)
    boundary: bool = False
    notes: str = ""


@dataclass
class ProofIntake:
    id: str
    kind: str = "stat"
    market: str = ""
    figure_kind: str = "none"
    statement: str = ""
    source: str = ""
    binding: bool = False
    notes: str = ""


@dataclass
class AngleIntake:
    id: str
    seat: str = ""
    premise: str = ""
    claim: str = ""
    proof: str = ""
    opener_kind: str = "account-event"
    summary: str = ""
    status: str = "draft"
    cta: str = ""
    notes: str = ""


@dataclass
class IntakeDocument:
    seats: list[SeatIntake] = field(default_factory=list)
    claims: list[ClaimIntake] = field(default_factory=list)
    proofs: list[ProofIntake] = field(default_factory=list)
    angles: list[AngleIntake] = field(default_factory=list)


# --- parsing ------------------------------------------------------------------


def _clean_val(val: str) -> str:
    val = val.strip()
    # Strip any bold or italic markdown markers
    while val.startswith("**") and val.endswith("**") and len(val) >= 4:
        val = val[2:-2].strip()
    if val.startswith("**"):
        val = val[2:].strip()
    if val.endswith("**"):
        val = val[:-2].strip()
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        val = val[1:-1].strip()
    return val


def _split_list(val: str) -> list[str]:
    val = _clean_val(val)
    if not val:
        return []
    # If formatted as JSON / TOML array: ["a", "b"]
    if val.startswith("[") and val.endswith("]"):
        try:
            parsed = tomllib.loads(f"k = {val}").get("k", [])
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except Exception:
            val = val[1:-1]
    return [p.strip().strip('"').strip("'") for p in val.split(",") if p.strip()]


def _parse_bullet_kv(lines: list[str]) -> dict[str, str]:
    kv: dict[str, str] = {}
    current_key: str | None = None
    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue
        m_bullet = re.match(r"^[-*+]\s+(.*)$", line_clean)
        if m_bullet:
            content = m_bullet.group(1).strip()
            if ":" in content:
                k_part, _, v_part = content.partition(":")
                k_clean = (
                    k_part.replace("*", "").strip().lower().replace(" ", "_").replace("/", "_")
                )
                v_clean = _clean_val(v_part)
                if k_clean:
                    kv[k_clean] = v_clean
                    current_key = k_clean
                    continue
        if current_key and (
            line.startswith("  ")
            or line.startswith("\t")
            or not line_clean.startswith(("-", "*", "+", "#"))
        ):
            kv[current_key] += " " + _clean_val(line_clean)
    return kv


def parse_intake_markdown(text: str) -> IntakeDocument:
    """Parse an intake markdown document into structured dataclasses."""
    doc = IntakeDocument()

    # Split by level 3 headers: ### (Seat|Claim|Proof|Angle): <name>
    # or iterate line by line
    lines = text.splitlines()
    block_type: str | None = None
    block_id: str = ""
    block_lines: list[str] = []

    def flush_block() -> None:
        if not block_type or not block_id:
            return
        kv = _parse_bullet_kv(block_lines)
        b_type = block_type.lower()
        if b_type == "seat":
            doc.seats.append(
                SeatIntake(
                    name=slugify(block_id),
                    personas=_split_list(
                        kv.get("title___persona") or kv.get("persona") or kv.get("title", "")
                    ),
                    lead_pain=kv.get("lead_pain", ""),
                    gain=kv.get("gain", ""),
                    forbidden_pains=_split_list(kv.get("forbidden_pains", "")),
                    register=kv.get("register", "standard"),
                    notes=kv.get("notes", ""),
                )
            )
        elif b_type == "claim":
            status = kv.get("status", "design-target").lower()
            boundary_str = kv.get("boundary", "false").lower()
            doc.claims.append(
                ClaimIntake(
                    id=slugify(block_id),
                    group=slugify(kv.get("group", "general")),
                    status=status,
                    statement=kv.get("statement", ""),
                    source=kv.get("source", ""),
                    do_not_say=_split_list(kv.get("do_not_say", "")),
                    boundary=boundary_str in ("true", "yes", "1"),
                    notes=kv.get("notes", ""),
                )
            )
        elif b_type == "proof":
            binding_str = kv.get("binding", "false").lower()
            doc.proofs.append(
                ProofIntake(
                    id=slugify(block_id),
                    kind=kv.get("kind", "stat").lower(),
                    market=kv.get("market", "global").lower(),
                    figure_kind=kv.get("figure_kind", "none").lower(),
                    statement=kv.get("statement", ""),
                    source=kv.get("source", ""),
                    binding=binding_str in ("true", "yes", "1"),
                    notes=kv.get("notes", ""),
                )
            )
        elif b_type == "angle":
            doc.angles.append(
                AngleIntake(
                    id=slugify(block_id),
                    seat=slugify(kv.get("seat", "")),
                    premise=slugify(kv.get("premise", "")),
                    claim=slugify(kv.get("claim", "")),
                    proof=slugify(kv.get("proof", "")),
                    opener_kind=kv.get("opener_kind", "account-event").lower(),
                    summary=kv.get("summary", ""),
                    status=kv.get("status", "draft").lower(),
                    cta=kv.get("give_first_gift_cta") or kv.get("cta", ""),
                    notes=kv.get("notes", ""),
                )
            )

    for line in lines:
        line_s = line.strip()

        # Subsections: ### (Seat|Claim|Proof|Angle): <name>
        m_head = re.match(r"^###\s+(Seat|Claim|Proof|Angle):\s*(.+)$", line_s, re.I)
        if m_head:
            flush_block()
            block_type = m_head.group(1).lower()
            block_id = m_head.group(2).strip()
            block_lines = []
            continue

        if block_type:
            block_lines.append(line)

    flush_block()
    return doc


# --- validation ---------------------------------------------------------------


def _validate_claims(claims: list[ClaimIntake]) -> list[str]:
    errors: list[str] = []
    for claim in claims:
        if not claim.id:
            errors.append("claim missing id")
        if claim.status not in CLAIM_STATUSES:
            errors.append(
                f"claim '{claim.id}': invalid status {claim.status!r} (must be one of {sorted(CLAIM_STATUSES)})"
            )
        if claim.status == "verified" and not claim.source:
            errors.append(
                f"claim '{claim.id}': marked 'verified' but missing 'source' line (required by product-accuracy)"
            )
        if not claim.statement:
            errors.append(f"claim '{claim.id}': missing statement")
    return errors


def _validate_proofs(proofs: list[ProofIntake]) -> list[str]:
    errors: list[str] = []
    for proof in proofs:
        if not proof.id:
            errors.append("proof missing id")
        if proof.kind not in PROOF_KINDS:
            errors.append(
                f"proof '{proof.id}': invalid kind {proof.kind!r} (must be one of {sorted(PROOF_KINDS)})"
            )
        if proof.figure_kind not in FIGURE_KINDS:
            errors.append(
                f"proof '{proof.id}': invalid figure_kind {proof.figure_kind!r} (must be one of {sorted(FIGURE_KINDS)})"
            )
        if not proof.statement:
            errors.append(f"proof '{proof.id}': missing statement")
    return errors


def _validate_angles(angles: list[AngleIntake]) -> list[str]:
    errors: list[str] = []
    for angle in angles:
        if not angle.id:
            errors.append("angle missing id")
        if angle.opener_kind not in OPENER_KINDS:
            errors.append(
                f"angle '{angle.id}': invalid opener_kind {angle.opener_kind!r} (must be one of {sorted(OPENER_KINDS)})"
            )
        if angle.status not in ANGLE_STATUSES:
            errors.append(
                f"angle '{angle.id}': invalid status {angle.status!r} (must be one of {sorted(ANGLE_STATUSES)})"
            )
        if not angle.seat:
            errors.append(f"angle '{angle.id}': missing seat reference")
        if not angle.claim:
            errors.append(f"angle '{angle.id}': missing claim reference")
        if not angle.proof:
            errors.append(f"angle '{angle.id}': missing proof reference")
    return errors


def validate_intake_document(
    doc: IntakeDocument,
    *,
    profiles_root: Path | None = None,
    profile: str | None = None,
) -> list[str]:
    """Validate intake records against registry invariants. Returns list of defect messages."""
    return (
        _validate_claims(doc.claims) + _validate_proofs(doc.proofs) + _validate_angles(doc.angles)
    )


# --- export to markdown -------------------------------------------------------


def _load_toml_items(file: Path, key: str) -> list[dict]:
    if not file.is_file():
        return []
    try:
        data = tomllib.loads(file.read_text(encoding="utf-8"))
        return data.get(key, [])
    except Exception:
        return []


def _format_seat_export(s: dict) -> list[str]:
    s_name = s.get("name", "unknown")
    personas = ", ".join(s.get("personas", []))
    lead_pain = s.get("lead_pain", "")
    gain = s.get("gain", "")
    forbidden = ", ".join(s.get("forbidden_pains", []))
    register = s.get("register", "standard")
    return [
        f"### Seat: {s_name}",
        f"- **Title / Persona:** {personas}",
        f"- **Lead Pain:** {lead_pain}",
        f"- **Gain:** {gain}",
        f"- **Forbidden Pains:** {forbidden}",
        f"- **Register:** {register}",
        "",
    ]


def _format_claim_export(c: dict) -> list[str]:
    cid = c.get("id", "unknown")
    group = c.get("group", "general")
    status = c.get("status", "design-target")
    stmt = c.get("statement", "")
    src = c.get("source", "")
    dns = ", ".join(c.get("do_not_say", []))
    boundary = str(c.get("boundary", False)).lower()
    notes = c.get("notes", "")
    res = [
        f"### Claim: {cid}",
        f"- **Group:** {group}",
        f"- **Status:** {status}",
        f"- **Statement:** {stmt}",
        f"- **Source:** {src}",
        f"- **Do Not Say:** {dns}",
        f"- **Boundary:** {boundary}",
    ]
    if notes:
        res.append(f"- **Notes:** {notes}")
    res.append("")
    return res


def _format_proof_export(p: dict) -> list[str]:
    pid = p.get("id", "unknown")
    kind = p.get("kind", "stat")
    market = p.get("market", "")
    fig_kind = p.get("figure_kind", "none")
    stmt = p.get("statement", "")
    src = p.get("source", "")
    binding = str(p.get("binding", False)).lower()
    notes = p.get("notes", "")
    res = [
        f"### Proof: {pid}",
        f"- **Kind:** {kind}",
        f"- **Market:** {market}",
        f"- **Figure Kind:** {fig_kind}",
        f"- **Statement:** {stmt}",
        f"- **Source:** {src}",
        f"- **Binding:** {binding}",
    ]
    if notes:
        res.append(f"- **Notes:** {notes}")
    res.append("")
    return res


def _format_angle_export(a: dict) -> list[str]:
    aid = a.get("id", "unknown")
    seat = a.get("seat", "")
    premise = a.get("premise", "")
    claim = a.get("claim", "")
    proof = a.get("proof", "")
    opener = a.get("opener_kind", "account-event")
    summary = a.get("summary", "")
    status = a.get("status", "draft")
    notes = a.get("notes", "")
    res = [
        f"### Angle: {aid}",
        f"- **Seat:** {seat}",
        f"- **Premise:** {premise}",
        f"- **Claim:** {claim}",
        f"- **Proof:** {proof}",
        f"- **Opener Kind:** {opener}",
        f"- **Summary:** {summary}",
        f"- **Status:** {status}",
    ]
    if notes:
        res.append(f"- **Notes:** {notes}")
    res.append("")
    return res


def export_profile_to_markdown(profile: str, profiles_root: Path | None = None) -> str:
    """Export a profile's live knowledge registry to MESSAGING-INTAKE.md format."""
    roots = profiles_root or resolve_profiles_root()
    profile_safe = _safe_segment(profile, "profile")
    prof_dir = roots / profile_safe / "knowledge"

    seats = _load_toml_items(prof_dir / ROLE_VOCABULARY_FILE, "seat")
    claims = _load_toml_items(prof_dir / CLAIMS_FILE, "claim")
    proofs = _load_toml_items(prof_dir / PROOF_FILE, "proof")
    angles = _load_toml_items(prof_dir / ANGLES_FILE, "angle")

    out: list[str] = [
        f"# Messaging & Hook Intake — {profile_safe}",
        "",
        f"Consolidated human-readable export of live messaging facts for `{profile_safe}`.",
        f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%SZ')}",
        "",
        "---",
        "",
        "## 1. Target Seats & Pains",
        "",
    ]

    if seats:
        for s in seats:
            out.extend(_format_seat_export(s))
    else:
        out.extend(
            ["*(No seat overrides declared in profile; uses built-in default vocabulary)*", ""]
        )

    out.extend(["---", "", "## 2. Capabilities & Claims", ""])
    if claims:
        for c in claims:
            out.extend(_format_claim_export(c))
    else:
        out.extend(["*(No claims declared)*", ""])

    out.extend(["---", "", "## 3. Proof Points & Benchmarks", ""])
    if proofs:
        for p in proofs:
            out.extend(_format_proof_export(p))
    else:
        out.extend(["*(No proof points declared)*", ""])

    out.extend(["---", "", "## 4. Narrative Angles & Hooks", ""])
    if angles:
        for a in angles:
            out.extend(_format_angle_export(a))
    else:
        out.extend(["*(No angles declared)*", ""])

    return "\n".join(out)


# --- backups & staging --------------------------------------------------------


def create_intake_backup(
    profile: str,
    content_root: Path | None = None,
    profiles_root: Path | None = None,
) -> Path:
    """Create a dated markdown backup of current live messaging in content/<profile>/knowledge-backups/."""
    c_root = content_root or resolve_content_root()
    p_root = profiles_root or resolve_profiles_root()
    profile_safe = _safe_segment(profile, "profile")

    backup_dir = c_root / profile_safe / BACKUPS_DIRNAME
    backup_dir.mkdir(parents=True, exist_ok=True)

    stamp = utc_stamp()
    dest = backup_dir / f"messaging-intake-backup-{stamp}.md"

    md_content = export_profile_to_markdown(profile_safe, profiles_root=p_root)
    atomic_write_text(dest, md_content)
    return dest


def _serialize_toml_table(table_name: str, records: list[dict], existing_header: str = "") -> str:
    lines: list[str] = []
    if existing_header:
        lines.append(existing_header.strip())
        lines.append("")

    for rec in records:
        lines.append(f"[[{table_name}]]")
        for k, v in rec.items():
            if isinstance(v, bool):
                lines.append(f"{k} = {str(v).lower()}")
            elif isinstance(v, list):
                if not v:
                    lines.append(f"{k} = []")
                else:
                    items_str = ", ".join(f'"{item}"' for item in v)
                    lines.append(f"{k} = [{items_str}]")
            else:
                s_val = str(v).replace('"', '\\"')
                lines.append(f'{k} = "{s_val}"')
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _extract_header(toml_text: str, first_key: str) -> str:
    """Extract comment/header lines before the first block."""
    header_lines: list[str] = []
    for line in toml_text.splitlines():
        if line.strip().startswith(f"[[{first_key}]]") or (
            first_key == "seat" and line.strip().startswith("default_persona")
        ):
            break
        header_lines.append(line)
    return "\n".join(header_lines)


def _stage_claims(claims: list[ClaimIntake], prof_knowledge: Path, staging_dir: Path) -> Path:
    live_claims_file = prof_knowledge / CLAIMS_FILE
    live_claims: list[dict] = []
    header = "# Outbound claim registry"
    if live_claims_file.is_file():
        raw_text = live_claims_file.read_text(encoding="utf-8")
        header = _extract_header(raw_text, "claim")
        try:
            live_claims = tomllib.loads(raw_text).get("claim", [])
        except Exception:
            live_claims = []

    claims_map = {c["id"].lower(): c for c in live_claims if "id" in c}
    for c in claims:
        cid = c.id.lower()
        rec = {
            "id": c.id,
            "group": c.group,
            "status": c.status,
            "statement": c.statement,
            "source": c.source,
            "do_not_say": c.do_not_say,
            "boundary": c.boundary,
        }
        if c.notes:
            rec["notes"] = c.notes
        claims_map[cid] = rec

    staged_claims_text = _serialize_toml_table("claim", list(claims_map.values()), header)
    dest = staging_dir / CLAIMS_FILE
    atomic_write_text(dest, staged_claims_text)
    return dest


def _stage_proofs(proofs: list[ProofIntake], prof_knowledge: Path, staging_dir: Path) -> Path:
    live_proof_file = prof_knowledge / PROOF_FILE
    live_proofs: list[dict] = []
    header = "# Outbound proof registry"
    if live_proof_file.is_file():
        raw_text = live_proof_file.read_text(encoding="utf-8")
        header = _extract_header(raw_text, "proof")
        try:
            live_proofs = tomllib.loads(raw_text).get("proof", [])
        except Exception:
            live_proofs = []

    proof_map = {p["id"].lower(): p for p in live_proofs if "id" in p}
    for p in proofs:
        pid = p.id.lower()
        rec = {
            "id": p.id,
            "kind": p.kind,
            "market": p.market,
            "figure_kind": p.figure_kind,
            "statement": p.statement,
            "source": p.source,
            "binding": p.binding,
        }
        if p.notes:
            rec["notes"] = p.notes
        proof_map[pid] = rec

    staged_proof_text = _serialize_toml_table("proof", list(proof_map.values()), header)
    dest = staging_dir / PROOF_FILE
    atomic_write_text(dest, staged_proof_text)
    return dest


def _stage_angles(angles: list[AngleIntake], prof_knowledge: Path, staging_dir: Path) -> Path:
    live_angles_file = prof_knowledge / ANGLES_FILE
    live_angles: list[dict] = []
    header = "# Outbound angle registry"
    if live_angles_file.is_file():
        raw_text = live_angles_file.read_text(encoding="utf-8")
        header = _extract_header(raw_text, "angle")
        try:
            live_angles = tomllib.loads(raw_text).get("angle", [])
        except Exception:
            live_angles = []

    angles_map = {a["id"].lower(): a for a in live_angles if "id" in a}
    for a in angles:
        aid = a.id.lower()
        rec = {
            "id": a.id,
            "seat": a.seat,
            "premise": a.premise,
            "claim": a.claim,
            "proof": a.proof,
            "opener_kind": a.opener_kind,
            "summary": a.summary,
            "status": a.status,
        }
        if a.notes:
            rec["notes"] = a.notes
        angles_map[aid] = rec

    staged_angles_text = _serialize_toml_table("angle", list(angles_map.values()), header)
    dest = staging_dir / ANGLES_FILE
    atomic_write_text(dest, staged_angles_text)
    return dest


def _serialize_seat_entry(s: dict) -> list[str]:
    lines = ["[[seat]]", f'name = "{s.get("name", "")}"']
    for k in ("personas", "forbidden_pains", "segments", "stakes"):
        if k in s:
            lines.append(f"{k} = {tomllib_list_repr(s[k])}")
    for k in ("lead_pain", "gain", "register"):
        if k in s:
            lines.append(f'{k} = "{s[k]}"')
    lines.append("")
    return lines


def _stage_seats(seats: list[SeatIntake], prof_knowledge: Path, staging_dir: Path) -> Path | None:
    live_rv_file = prof_knowledge / ROLE_VOCABULARY_FILE
    if not live_rv_file.is_file():
        return None
    raw_text = live_rv_file.read_text(encoding="utf-8")
    try:
        parsed_rv = tomllib.loads(raw_text)
    except Exception:
        parsed_rv = {}
    live_seats = parsed_rv.get("seat", [])
    seats_map = {s["name"].lower(): s for s in live_seats if "name" in s}

    for s in seats:
        sname = s.name.lower()
        existing = seats_map.get(sname, {"name": s.name})
        if s.lead_pain:
            existing["lead_pain"] = s.lead_pain
        if s.gain:
            existing["gain"] = s.gain
        if s.forbidden_pains:
            existing["forbidden_pains"] = s.forbidden_pains
        if s.register:
            existing["register"] = s.register
        seats_map[sname] = existing

    parsed_rv["seat"] = list(seats_map.values())
    rv_lines: list[str] = [
        f'default_persona = "{parsed_rv.get("default_persona", "founder-operator")}"',
        f"segments = {tomllib_list_repr(parsed_rv.get('segments', ['enterprise', 'startup', 'unspecified']))}",
        "",
    ]
    if "persona" in parsed_rv:
        for p in parsed_rv["persona"]:
            rv_lines.append("[[persona]]")
            rv_lines.append(f'name = "{p.get("name", "")}"')
            rv_lines.append(f"cues = {tomllib_list_repr(p.get('cues', []))}")
            rv_lines.append("")

    for s in parsed_rv.get("seat", []):
        rv_lines.extend(_serialize_seat_entry(s))

    dest = staging_dir / ROLE_VOCABULARY_FILE
    atomic_write_text(dest, "\n".join(rv_lines).strip() + "\n")
    return dest


def stage_intake(
    profile: str,
    intake_path: Path,
    content_root: Path | None = None,
    profiles_root: Path | None = None,
    *,
    auto_backup: bool = True,
) -> tuple[dict[str, Path], Path | None]:
    """Parse, validate, backup, and stage candidate files in content/<profile>/knowledge-staging/."""
    c_root = content_root or resolve_content_root()
    p_root = profiles_root or resolve_profiles_root()
    profile_safe = _safe_segment(profile, "profile")
    prof_knowledge = p_root / profile_safe / "knowledge"

    text = intake_path.read_text(encoding="utf-8")
    doc = parse_intake_markdown(text)
    errors = validate_intake_document(doc, profiles_root=p_root, profile=profile_safe)
    if errors:
        raise ValueError("Intake document validation failed:\n  - " + "\n  - ".join(errors))

    backup_path: Path | None = None
    if auto_backup:
        backup_path = create_intake_backup(profile_safe, content_root=c_root, profiles_root=p_root)

    staging_dir = c_root / profile_safe / STAGING_DIRNAME
    staging_dir.mkdir(parents=True, exist_ok=True)
    staged_files: dict[str, Path] = {}

    if doc.claims:
        staged_files[CLAIMS_FILE] = _stage_claims(doc.claims, prof_knowledge, staging_dir)

    if doc.proofs:
        staged_files[PROOF_FILE] = _stage_proofs(doc.proofs, prof_knowledge, staging_dir)

    if doc.angles:
        staged_files[ANGLES_FILE] = _stage_angles(doc.angles, prof_knowledge, staging_dir)

    if doc.seats:
        seat_dest = _stage_seats(doc.seats, prof_knowledge, staging_dir)
        if seat_dest:
            staged_files[ROLE_VOCABULARY_FILE] = seat_dest

    return staged_files, backup_path


def tomllib_list_repr(items: list[str]) -> str:
    return "[" + ", ".join(f'"{i}"' for i in items) + "]"


# --- CLI ----------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Messaging Intake: parse, export, or stage MESSAGING-INTAKE.md"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    p_parse = subparsers.add_parser("parse", help="Parse and validate an intake markdown file")
    p_parse.add_argument("--file", required=True, type=Path, help="Path to markdown intake file")
    p_export = subparsers.add_parser(
        "export", help="Export live profile messaging facts to intake markdown"
    )
    p_export.add_argument("--profile", required=True, help="Active profile slug")
    p_export.add_argument(
        "--output", type=Path, help="Destination markdown path (defaults to stdout)"
    )
    p_stage = subparsers.add_parser(
        "stage", help="Stage intake changes and create automatic backup"
    )
    p_stage.add_argument("--profile", required=True, help="Active profile slug")
    p_stage.add_argument("--file", required=True, type=Path, help="Path to markdown intake file")
    p_stage.add_argument(
        "--no-backup", action="store_true", help="Skip creating automated markdown backup"
    )
    args = parser.parse_args()

    if args.command == "parse":
        text = args.file.read_text(encoding="utf-8")
        doc = parse_intake_markdown(text)
        errors = validate_intake_document(doc)
        print(
            f"Parsed {len(doc.seats)} seat(s), {len(doc.claims)} claim(s), {len(doc.proofs)} proof point(s), {len(doc.angles)} angle(s)."
        )
        if errors:
            print(f"\nValidation failed with {len(errors)} error(s):", file=sys.stderr)
            for err in errors:
                print(f"  ✗ {err}", file=sys.stderr)
            sys.exit(2)
        print("✓ All intake records validated cleanly.")

    elif args.command == "export":
        md = export_profile_to_markdown(args.profile)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(md, encoding="utf-8")
            print(f"✓ Exported {args.profile} live messaging to {args.output}")
        else:
            print(md)

    elif args.command == "stage":
        try:
            staged, backup = stage_intake(args.profile, args.file, auto_backup=not args.no_backup)
        except Exception as exc:
            print(f"Error staging intake: {exc}", file=sys.stderr)
            sys.exit(2)

        if backup:
            print(f"✓ Automated backup created: {backup}")
        print(
            f"✓ Staged {len(staged)} candidate file(s) under content/{args.profile}/knowledge-staging/:"
        )
        for topic, path in staged.items():
            print(f"  • {topic} -> {path}")
        print("\nNext step: review diffs and promote:")
        for topic in staged:
            print(
                f"  uv run python -m gtm_core.knowledge_staging diff --profile {args.profile} --topic {topic}"
            )
            print(
                f"  uv run python -m gtm_core.knowledge_staging promote --profile {args.profile} --topic {topic}"
            )


if __name__ == "__main__":
    main()
