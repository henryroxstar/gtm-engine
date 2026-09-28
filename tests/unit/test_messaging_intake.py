"""Unit tests for gtm_core.messaging_intake: parsing, validation, export, backup, and staging."""

from __future__ import annotations

from pathlib import Path

from gtm_core.messaging_intake import (
    AngleIntake,
    ClaimIntake,
    IntakeDocument,
    ProofIntake,
    create_intake_backup,
    export_profile_to_markdown,
    parse_intake_markdown,
    stage_intake,
    validate_intake_document,
)

SAMPLE_INTAKE_MD = """\
# Messaging & Hook Intake

## 1. Target Seats & Pains

### Seat: security
- **Title / Persona:** CISO, Head of Security
- **Lead Pain:** Uncontrolled agent actions cross trust boundaries.
- **Gain:** Complete chain of custody for every automated agent interaction.
- **Forbidden Pains:** Developer onboarding latency, cloud margin compression
- **Register:** risk and regulatory

### Seat: cto
- **Title / Persona:** CTO, VP Engineering
- **Lead Pain:** Multi-framework identity fragmentation.
- **Gain:** One identity and policy layer across agent frameworks.
- **Forbidden Pains:** Annual compliance audit fatigue
- **Register:** technical

---

## 2. Capabilities & Claims

### Claim: audit-signed
- **Group:** observability
- **Status:** verified
- **Statement:** Each audit entry is cryptographically attested in a signed Verifiable Presentation.
- **Source:** products/agent-gateway/references/gateway-reference.md:187
- **Do Not Say:** tamper-proof, immutable, unforgeable
- **Boundary:** false
- **Notes:** Verified capability.

### Claim: new-roadmap-feature
- **Group:** deployment
- **Status:** design-target
- **Statement:** Automated cross-region replication is configured in one click.
- **Source:**
- **Do Not Say:** instant, zero latency
- **Boundary:** false

---

## 3. Proof Points & Benchmarks

### Proof: sg-framework-anchor
- **Kind:** anchor
- **Market:** singapore
- **Figure Kind:** none
- **Statement:** Published guidance expects an accountable owner for each automated process.
- **Source:** references/guidance.md:12
- **Binding:** false

### Proof: reconciliation-stat
- **Kind:** stat
- **Market:** global
- **Figure Kind:** illustrative
- **Statement:** Finance teams spend one week per quarter on manual reconciliation.
- **Source:**
- **Binding:** false

---

## 4. Narrative Angles & Hooks

### Angle: security-audit-custody
- **Seat:** security
- **Premise:** multi-framework
- **Claim:** audit-signed
- **Proof:** sg-framework-anchor
- **Opener Kind:** account-event
- **Summary:** One chain of custody across every framework.
- **Status:** draft
- **Give-First Gift CTA:** 1-page agent security architecture teardown
"""


def test_parse_intake_markdown_basic() -> None:
    doc = parse_intake_markdown(SAMPLE_INTAKE_MD)
    assert len(doc.seats) == 2
    assert len(doc.claims) == 2
    assert len(doc.proofs) == 2
    assert len(doc.angles) == 1

    # Verify Seat
    sec = doc.seats[0]
    assert sec.name == "security"
    assert "ciso" in [p.lower() for p in sec.personas]
    assert "Uncontrolled agent actions" in sec.lead_pain
    assert "Developer onboarding latency" in sec.forbidden_pains

    # Verify Claim
    claim = doc.claims[0]
    assert claim.id == "audit-signed"
    assert claim.status == "verified"
    assert "tamper-proof" in claim.do_not_say
    assert claim.source == "products/agent-gateway/references/gateway-reference.md:187"

    # Verify Proof
    proof = doc.proofs[0]
    assert proof.id == "sg-framework-anchor"
    assert proof.kind == "anchor"
    assert proof.market == "singapore"

    # Verify Angle
    angle = doc.angles[0]
    assert angle.id == "security-audit-custody"
    assert angle.seat == "security"
    assert angle.claim == "audit-signed"
    assert angle.proof == "sg-framework-anchor"


def test_validate_intake_document_valid() -> None:
    doc = parse_intake_markdown(SAMPLE_INTAKE_MD)
    errors = validate_intake_document(doc)
    assert not errors, f"Expected zero errors, got: {errors}"


def test_validate_intake_document_errors() -> None:
    bad_doc = IntakeDocument(
        claims=[
            ClaimIntake(id="bad-claim-1", status="invalid-status", statement="something"),
            ClaimIntake(
                id="bad-claim-2", status="verified", source="", statement="something"
            ),  # missing source
        ],
        proofs=[
            ProofIntake(id="bad-proof-1", kind="invalid-kind", statement="something"),
            ProofIntake(id="bad-proof-2", figure_kind="invalid-fig", statement="something"),
        ],
        angles=[
            AngleIntake(
                id="bad-angle-1", opener_kind="invalid-opener", seat="s", claim="c", proof="p"
            ),
            AngleIntake(id="bad-angle-2", status="invalid-status", seat="s", claim="c", proof="p"),
        ],
    )
    errors = validate_intake_document(bad_doc)
    assert any("invalid status" in e for e in errors)
    assert any("missing 'source' line" in e for e in errors)
    assert any("invalid kind" in e for e in errors)
    assert any("invalid figure_kind" in e for e in errors)
    assert any("invalid opener_kind" in e for e in errors)


def test_export_profile_to_markdown() -> None:
    md = export_profile_to_markdown("_template")
    assert "## 1. Target Seats & Pains" in md
    assert "## 2. Capabilities & Claims" in md
    assert "## 3. Proof Points & Benchmarks" in md
    assert "## 4. Narrative Angles & Hooks" in md

    # Round trip parse
    doc = parse_intake_markdown(md)
    assert len(doc.claims) >= 1
    assert len(doc.proofs) >= 1


def test_create_intake_backup(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    backup = create_intake_backup("_template", content_root=content_root)
    assert backup.is_file()
    assert "messaging-intake-backup-" in backup.name
    text = backup.read_text(encoding="utf-8")
    assert "# Messaging & Hook Intake" in text


def test_stage_intake_round_trip(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    profiles_root = Path(__file__).resolve().parents[2] / "profiles"

    intake_file = tmp_path / "test-intake.md"
    intake_file.write_text(SAMPLE_INTAKE_MD, encoding="utf-8")

    staged_files, backup = stage_intake(
        "_template",
        intake_file,
        content_root=content_root,
        profiles_root=profiles_root,
        auto_backup=True,
    )

    assert backup is not None
    assert backup.is_file()
    assert "claims.toml" in staged_files
    assert "proof.toml" in staged_files
    assert "angles.toml" in staged_files

    # Verify staged files exist on disk
    for f in staged_files.values():
        assert f.is_file()
        content = f.read_text(encoding="utf-8")
        assert len(content) > 0
