"""Tests for the sequencer capability registry resolver (gtm_core.sequencers).

Covers the contract SC2/SC4 (and every later step) rely on:
  - the shipped gtm_core/sequencers.toml is internally valid and resolves as expected
  - the granting test is a closed list of exactly one value: the literal boolean True
  - a capability row missing `supported`/`source`/`verified_on`, a non-https source, an
    unparseable date, or a secret-shaped value fails the WHOLE registry to load
  - an unknown provider or an unverified/absent capability REFUSES, never raises
  - the registry path is refused when it resolves under profiles/ or content/
  - a tenant override can only tighten (true -> false), never widen, and only a known
    capability name may be overridden
  - render_summary agrees across fmt="text"/"html" and html-escapes untrusted-shaped text
  - the CLI's exit codes and JSON output

SDK-INDEPENDENT: gtm_core.sequencers is pure stdlib (tomllib), like gtm_core.models.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core import sequencers
from gtm_core.sequencers import (
    Capability,
    SequencerOverrideError,
    _grants,
    apply_tenant_override,
    render_summary,
    resolve_capability,
)

REAL_REGISTRY = Path(sequencers.__file__).resolve().parent / "sequencers.toml"

_VALID_ROW = """
source = "https://vendor.example/docs"
verified_on = "2026-09-21"
"""


def _write_registry(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "sequencers.toml"
    p.write_text(body)
    return p


# --- shipped-registry integrity ----------------------------------------------


def test_saleshandy_stop_on_reply_is_granted():
    cap = resolve_capability("saleshandy", "stop_on_reply", registry_path=REAL_REGISTRY)
    assert cap.granted is True
    assert cap.supported is True
    assert cap.source.startswith("https://")
    # Re-dated 2026-09-22 when the live toggle-and-diff established setting code 3. The exact
    # date is pinned deliberately: a row whose readability claim changes without its date
    # moving is the silent re-dating this registry exists to catch.
    assert cap.verified_on == "2026-09-22"
    assert cap.readable_via_api is True


def test_saleshandy_reply_text_unsubscribe_is_refused():
    """The one row whose `supported` is the string "unknown" — the dated first-party
    observation contradicts vendor prose, so it must refuse, not pass on vendor say-so."""
    cap = resolve_capability("saleshandy", "reply_text_unsubscribe", registry_path=REAL_REGISTRY)
    assert cap.granted is False
    assert cap.supported == "unknown"


def test_unverified_provider_has_no_rows():
    """apollo/gmass carry a provider table and zero capability rows (rule 2) — any
    capability under them refuses exactly as 'unknown' does, with no row to cite."""
    cap = resolve_capability("apollo", "stop_on_reply", registry_path=REAL_REGISTRY)
    assert cap.granted is False
    assert cap.supported == "unknown"
    cap2 = resolve_capability("gmass", "reply_send", registry_path=REAL_REGISTRY)
    assert cap2.granted is False


def test_unknown_provider_refuses_without_raising():
    cap = resolve_capability("does-not-exist", "anything", registry_path=REAL_REGISTRY)
    assert cap.granted is False
    assert "unknown provider" in cap.reason


# --- the closed granting list --------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expect_grant"),
    [
        (True, True),
        (False, False),
        ("unknown", False),
        ("true", False),  # the STRING "true" — not the literal boolean
        (1, False),
        (0, False),
        (["true"], False),
        (None, False),
    ],
)
def test_grants_is_a_closed_list(value: object, expect_grant: bool) -> None:
    assert _grants(value) is expect_grant


def test_flipping_supported_to_unknown_moves_granted_to_false(tmp_path):
    """The negative control from the test plan: a row that WAS granted, isn't after the
    registry's own vendor fact changes — this is the whole point of the resolver."""
    reg = _write_registry(
        tmp_path,
        f"""
[providers.acme.capabilities.stop_on_reply]
supported = true
{_VALID_ROW}
""",
    )
    granted_before = resolve_capability("acme", "stop_on_reply", registry_path=reg)
    assert granted_before.granted is True

    reg2 = _write_registry(
        tmp_path,
        f"""
[providers.acme.capabilities.stop_on_reply]
supported = "unknown"
{_VALID_ROW}
""",
    )
    granted_after = resolve_capability("acme", "stop_on_reply", registry_path=reg2)
    assert granted_after.granted is False


# --- registry load failures (rule 2) ------------------------------------------


def test_missing_registry_raises(tmp_path):
    with pytest.raises(ValueError, match="sequencer registry not found"):
        resolve_capability("acme", "x", registry_path=tmp_path / "nope.toml")


def test_row_missing_supported_fails_the_whole_registry(tmp_path):
    reg = _write_registry(
        tmp_path,
        f"""
[providers.acme.capabilities.x]
{_VALID_ROW}
""",
    )
    with pytest.raises(ValueError, match="missing required 'supported'"):
        resolve_capability("acme", "x", registry_path=reg)


def test_row_missing_source_fails_the_whole_registry(tmp_path):
    reg = _write_registry(
        tmp_path,
        """
[providers.acme.capabilities.x]
supported = true
verified_on = "2026-09-21"
""",
    )
    with pytest.raises(ValueError, match="https:// URL"):
        resolve_capability("acme", "x", registry_path=reg)


def test_non_https_source_fails_the_whole_registry(tmp_path):
    reg = _write_registry(
        tmp_path,
        """
[providers.acme.capabilities.x]
supported = true
source = "http://vendor.example/docs"
verified_on = "2026-09-21"
""",
    )
    with pytest.raises(ValueError, match="https:// URL"):
        resolve_capability("acme", "x", registry_path=reg)


def test_row_missing_verified_on_fails_the_whole_registry(tmp_path):
    reg = _write_registry(
        tmp_path,
        """
[providers.acme.capabilities.x]
supported = true
source = "https://vendor.example/docs"
""",
    )
    with pytest.raises(ValueError, match="missing required 'verified_on'"):
        resolve_capability("acme", "x", registry_path=reg)


def test_unparseable_verified_on_fails_the_whole_registry(tmp_path):
    reg = _write_registry(
        tmp_path,
        """
[providers.acme.capabilities.x]
supported = true
source = "https://vendor.example/docs"
verified_on = "not-a-date"
""",
    )
    with pytest.raises(ValueError, match="not an ISO date"):
        resolve_capability("acme", "x", registry_path=reg)


def test_secret_shaped_value_fails_the_whole_registry(tmp_path):
    reg = _write_registry(
        tmp_path,
        """
[providers.acme.capabilities.x]
supported = true
source = "https://vendor.example/docs"
verified_on = "2026-09-21"
ui_path = "sk-ant-leaked-into-a-note-1234"
""",
    )
    with pytest.raises(ValueError, match="secret"):
        resolve_capability("acme", "x", registry_path=reg)


def test_one_bad_row_blocks_every_capability_not_just_its_own(tmp_path):
    """Rule 2 says the registry FAILS TO LOAD, not 'this row is dropped' — a bad row for
    provider B must refuse provider A's otherwise-valid capability too."""
    reg = _write_registry(
        tmp_path,
        f"""
[providers.acme.capabilities.good]
supported = true
{_VALID_ROW}

[providers.other.capabilities.bad]
supported = true
source = "not-a-url"
verified_on = "2026-09-21"
""",
    )
    with pytest.raises(ValueError, match="https:// URL"):
        resolve_capability("acme", "good", registry_path=reg)


# --- path confinement (rule 3) -------------------------------------------------


def test_registry_path_refused_under_profiles_root(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    bad_path = tmp_path / "profiles" / "acme" / "sequencers.toml"
    bad_path.parent.mkdir(parents=True)
    bad_path.write_text(f"[providers.acme.capabilities.x]\nsupported = true\n{_VALID_ROW}")
    with pytest.raises(ValueError, match="tenant-writable root"):
        resolve_capability("acme", "x", registry_path=bad_path)


def test_registry_path_refused_under_content_root(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path / "content"))
    bad_path = tmp_path / "content" / "acme" / "sequencers.toml"
    bad_path.parent.mkdir(parents=True)
    bad_path.write_text(f"[providers.acme.capabilities.x]\nsupported = true\n{_VALID_ROW}")
    with pytest.raises(ValueError, match="tenant-writable root"):
        resolve_capability("acme", "x", registry_path=bad_path)


def test_env_override_path_is_also_confined(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_PROFILES_ROOT", str(tmp_path / "profiles"))
    bad_path = tmp_path / "profiles" / "acme" / "sequencers.toml"
    bad_path.parent.mkdir(parents=True)
    bad_path.write_text(f"[providers.acme.capabilities.x]\nsupported = true\n{_VALID_ROW}")
    monkeypatch.setenv("GTM_SEQUENCERS_REGISTRY", str(bad_path))
    with pytest.raises(ValueError, match="tenant-writable root"):
        resolve_capability("acme", "x")


# --- unsafe segments ------------------------------------------------------------


@pytest.mark.parametrize("provider", ["../evil", "a/b", "..", "", "a\\b"])
def test_unsafe_provider_segment_rejected(provider):
    with pytest.raises(ValueError):
        resolve_capability(provider, "x", registry_path=REAL_REGISTRY)


@pytest.mark.parametrize("name", ["../evil", "a/b", ".."])
def test_unsafe_capability_segment_rejected(name):
    with pytest.raises(ValueError):
        resolve_capability("saleshandy", name, registry_path=REAL_REGISTRY)


# --- tenant overrides: tighten-only (rule 4) ------------------------------------


def test_override_tightens_a_granted_capability():
    base = {
        "stop_on_reply": resolve_capability(
            "saleshandy", "stop_on_reply", registry_path=REAL_REGISTRY
        )
    }
    assert base["stop_on_reply"].granted is True
    merged = apply_tenant_override(base, {"stop_on_reply": False})
    assert merged["stop_on_reply"].granted is False


def test_override_cannot_widen_a_refused_capability():
    base = {
        "reply_text_unsubscribe": resolve_capability(
            "saleshandy", "reply_text_unsubscribe", registry_path=REAL_REGISTRY
        )
    }
    assert base["reply_text_unsubscribe"].granted is False
    with pytest.raises(SequencerOverrideError) as exc_info:
        apply_tenant_override(base, {"reply_text_unsubscribe": True})
    assert exc_info.value.rule == "override_widen"


@pytest.mark.parametrize("value", ["unknown", 1, "false", None, [False]])
def test_override_value_must_be_the_literal_boolean_false(value):
    base = {
        "stop_on_reply": resolve_capability(
            "saleshandy", "stop_on_reply", registry_path=REAL_REGISTRY
        )
    }
    with pytest.raises(SequencerOverrideError) as exc_info:
        apply_tenant_override(base, {"stop_on_reply": value})
    assert exc_info.value.rule == "override_widen"


def test_override_unknown_capability_name_raises():
    base = {
        "stop_on_reply": resolve_capability(
            "saleshandy", "stop_on_reply", registry_path=REAL_REGISTRY
        )
    }
    with pytest.raises(SequencerOverrideError) as exc_info:
        apply_tenant_override(base, {"nonexistent_capability": False})
    assert exc_info.value.rule == "unknown_capability_override"


def test_untouched_capabilities_pass_through_unchanged():
    base = {
        "stop_on_reply": resolve_capability(
            "saleshandy", "stop_on_reply", registry_path=REAL_REGISTRY
        ),
        "webhooks": resolve_capability("saleshandy", "webhooks", registry_path=REAL_REGISTRY),
    }
    merged = apply_tenant_override(base, {"stop_on_reply": False})
    assert merged["webhooks"] == base["webhooks"]


def test_resolve_capability_applies_overrides_inline():
    cap = resolve_capability(
        "saleshandy",
        "stop_on_reply",
        overrides={"stop_on_reply": False},
        registry_path=REAL_REGISTRY,
    )
    assert cap.granted is False


# --- render_summary --------------------------------------------------------------


def test_render_summary_text_and_html_agree_on_content():
    caps = [
        resolve_capability("saleshandy", "stop_on_reply", registry_path=REAL_REGISTRY),
        resolve_capability("saleshandy", "reply_text_unsubscribe", registry_path=REAL_REGISTRY),
    ]
    text = render_summary(caps, fmt="text")
    html = render_summary(caps, fmt="html")
    assert "stop_on_reply" in text and "stop_on_reply" in html
    assert "GRANTED" in text and "GRANTED" in html
    assert "REFUSED" in text and "REFUSED" in html


def test_render_summary_html_escapes_untrusted_shaped_content():
    cap = Capability(
        provider="acme",
        name="<script>alert(1)</script>",
        supported=True,
        readable_via_api=None,
        settable_via_api=None,
        source="https://vendor.example",
        verified_on="2026-09-21",
        ui_path=None,
        granted=True,
        reason="granted",
    )
    html = render_summary([cap], fmt="html")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_render_summary_rejects_unknown_fmt():
    with pytest.raises(ValueError, match="unknown render fmt"):
        render_summary([], fmt="xml")


# --- CLI ---------------------------------------------------------------------


def test_cli_prints_all_capabilities_for_a_provider(capsys):
    rc = sequencers.main(["saleshandy", "--registry", str(REAL_REGISTRY)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "saleshandy/stop_on_reply: GRANTED" in out
    assert "saleshandy/reply_text_unsubscribe: REFUSED" in out


def test_cli_json_round_trips(capsys):
    rc = sequencers.main(
        ["saleshandy", "stop_on_reply", "--json", "--registry", str(REAL_REGISTRY)]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["provider"] == "saleshandy"
    assert payload[0]["granted"] is True


def test_cli_unverified_provider_with_no_rows_exits_2(capsys):
    rc = sequencers.main(["apollo", "--registry", str(REAL_REGISTRY)])
    assert rc == 2
    assert "no verified capability rows" in capsys.readouterr().err


def test_cli_unsafe_provider_segment_exits_2(capsys):
    rc = sequencers.main(["../evil", "--registry", str(REAL_REGISTRY)])
    assert rc == 2


def test_cli_bad_registry_exits_2(tmp_path, capsys):
    rc = sequencers.main(["acme", "--registry", str(tmp_path / "nope.toml")])
    assert rc == 2
    assert "not found" in capsys.readouterr().err


# --- gaps found by the mutation sweep (2026-09-21) ---------------------------------------


def test_a_resolved_capability_is_frozen():
    """A caller must not be able to grant itself a capability after resolution. The sweep
    found `@dataclass(frozen=True)` could be flipped with every test still passing."""
    cap = resolve_capability("saleshandy", "stop_on_reply", registry_path=REAL_REGISTRY)
    with pytest.raises(Exception):  # FrozenInstanceError
        cap.granted = True  # type: ignore[misc]
    with pytest.raises(Exception):
        cap.supported = True  # type: ignore[misc]
