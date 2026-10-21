"""The coverage report's own counters resolve titles against the TENANT's role vocabulary.

Found 2026-09-24. FR2 (`aadc3a84`) re-axed the matrix and threaded the profile through the
JOIN — `Matrix.recipient_key` / `Matrix.row_key` both pass `self.profile` — and left the two
DISPLAY counters behind: `audit_campaign` called the module-level `seat_of(title)` and
`persona_of(title)` with no profile, so `cov.seats`, `cov.personas`, and the `cov.unresolved`
list that falls out of them were computed against the BUILT-IN default vocabulary while the
join beside them used the tenant's.

Why it stayed invisible: Phase 0b (2026-09-21) recorded the then-only tenant's vocabulary as
byte-identical to the defaults, so passing the profile changed nothing. Once a tenant added
cues of its own the two halves silently disagreed. Measured on a live profile the day this was
written: **4 of 33** unresolved rows resolved fine under the tenant's own vocabulary — and the
one report whose entire job is finding missing cues was sending the operator to add cues that
were already there.

Fictional tenant and titles throughout (R9).
"""

from __future__ import annotations

from pathlib import Path

from gtm_core.hook_coverage import audit_campaign

PROFILE = "acme"

#: A persona and seat the built-in default vocabulary does not have, so "resolved" can only
#: mean "this tenant's file was read".
_VOCAB = """\
default_persona = "owner-operator"
segments = ["enterprise", "startup", "builder", "unspecified"]
security_only = []
non_buyer_cues = ["intern"]
ceo_title_cues = ["chief executive"]

[[persona]]
name = "kiln-warden"
cues = ["kiln warden", "warden of the kiln"]

[[persona]]
name = "owner-operator"
cues = ["owner", "founder"]

[[seat]]
name = "operations"
personas = ["kiln-warden"]
stakes = ["throughput", "downtime"]

[[seat]]
name = "exec"
personas = ["owner-operator"]
stakes = ["margin", "revenue"]
"""

_MATRIX = """\
# Hook matrix

## Enterprise

| id | Persona | Signal to open on | Hook angle |
|---|---|---|---|
| h1 | Kiln Warden | Kiln log arrived | Answering in the log beats answering from memory. |
"""


def _tenant(tmp_path: Path, *, titles: list[str], vocabulary: str | None = _VOCAB):
    knowledge = tmp_path / "profiles" / PROFILE / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "hook-matrix.md").write_text(_MATRIX, encoding="utf-8")
    if vocabulary is not None:
        (knowledge / "role-vocabulary.toml").write_text(vocabulary, encoding="utf-8")

    seq = tmp_path / "content" / PROFILE / "prospects" / "sequences"
    seq.mkdir(parents=True)
    (seq / "spec-a.md").write_text(
        "```\nCampaign: q4\nhook_cell: Kiln Warden × Kiln log arrived\nargument_id: arg-0\n```\n"
        "\nThe firing schedule nobody signed leaves the glaze on somebody's word.\n",
        encoding="utf-8",
    )
    lines = ["first,last,email,title,company,company_domain,suppression"]
    for n, title in enumerate(titles):
        lines.append(f"Ada,Okonkwo,ada{n}@halden.example,{title},Halden Systems,halden.example,")
    (seq / "list-0.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (seq / "cells.toml").write_text(
        '[[sequence]]\nid = "seq0"\ncsv = "list-0.csv"\nspec = "spec-a.md"\ncampaign = "q4"\n',
        encoding="utf-8",
    )
    return tmp_path / "profiles", tmp_path / "content"


def _audit(tmp_path, monkeypatch, titles, **kw):
    profiles, content = _tenant(tmp_path, titles=titles, **kw)
    # The vocabulary loader resolves its own root rather than taking the one passed to
    # `audit_campaign` — in production the two are the same tree, in a fixture they are not.
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(profiles))
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(content))
    return audit_campaign(PROFILE, "q4", content_root=content, profiles_root=profiles)


def test_a_cue_only_the_tenant_declares_still_resolves(tmp_path, monkeypatch):
    """The defect, stated as the operator meets it: a title the tenant's own file resolves
    is reported as an unresolved title to go and add a cue for."""
    cov = _audit(tmp_path, monkeypatch, ["Kiln Warden"])

    assert cov.personas.get("kiln-warden") == 1, (
        f"the tenant's persona cue was not read — personas={dict(cov.personas)}, "
        f"unresolved={dict(cov.unresolved)}"
    )
    assert cov.seats.get("operations") == 1, f"seats={dict(cov.seats)}"
    assert not cov.unresolved, (
        f"a title the tenant's vocabulary resolves is still booked unresolved: "
        f"{dict(cov.unresolved)}"
    )


def test_a_title_no_vocabulary_knows_is_still_unresolved(tmp_path, monkeypatch):
    """The positive control. The fix must read the tenant's file, not empty the bucket:
    an unknown title still has to report, or the counter stops doing its one job."""
    cov = _audit(tmp_path, monkeypatch, ["Assistant to the Regional Glazier"])

    assert dict(cov.unresolved) == {"Assistant to the Regional Glazier": 1}
    assert not cov.personas
    assert dict(cov.seats) == {"unresolved": 1}  # the seat counter books it explicitly


def test_the_two_counters_stay_on_their_own_axes(tmp_path, monkeypatch):
    """`cov.seats` and `cov.personas` are DISPLAY facts on fixed axes — the report prints
    them as two captioned blocks. Resolving them through `Matrix.recipient_key` would put
    both on the matrix's axis and print the seat twice, so the fix is the profile, not the
    matrix."""
    cov = _audit(tmp_path, monkeypatch, ["Kiln Warden"])

    assert dict(cov.personas) == {"kiln-warden": 1}
    assert dict(cov.seats) == {"operations": 1}
