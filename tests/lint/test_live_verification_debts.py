"""The live-verification debts of the sequencer capability contract, pinned in code.

§R13: a permanent ban lives in code, not prose. The same applies to a permanent DEBT. The
test plan's live tier cannot run in CI — it needs a real provider account, and two of its
checks send mail to a real person and write to a third party's suppression list. So the
risk is not that somebody runs them wrong; it is that somebody stops remembering they are
owed, reads a green suite, and treats the change as finished.

Each test below asserts that one debt is still RECORDED, and names what to change when it
is paid. They are not asserting the debt is good — they are asserting it is visible. When
an operator completes a live check, the corresponding test fails and tells them which file
now has to tell the truth.

This file is the reason `scripts/sc_live_verify.sh` can say "none of them can be quietly
forgotten" without that being a promise nobody keeps.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
REGISTRY = REPO / "gtm_core" / "sequencers.toml"


def _capability(name: str) -> dict:
    with REGISTRY.open("rb") as fh:
        return tomllib.load(fh)["providers"]["saleshandy"]["capabilities"][name]


def test_sc2_stop_on_reply_code_and_registry_agree():
    """SC2 DISCHARGED 2026-09-22 — finish-on-reply is setting code 3, established by a live
    toggle-and-diff on a never-sent sequence (exactly one code moved; restore byte-identical).

    The debt test that used to live here asserted the row was still unread. It is replaced
    rather than deleted, because the failure it guarded against is still live: the registry's
    `readable_via_api` and `SETTING_CODES` must move TOGETHER. A row claiming the setting is
    readable while no code is registered blocks on rung 5 for a reason nobody can find; a code
    registered against an "unknown" row reads a value the registry says it may not trust.
    """
    from gtm_core.capability_preflight import AUTOSET_ALLOWLIST, SETTING_CODES

    cap = _capability("stop_on_reply")
    assert cap["readable_via_api"] is True
    assert SETTING_CODES.get("stop_on_reply") == 3
    assert cap["verified_on"] >= "2026-09-22", "a readable row must carry the date it was read"

    # The one thing the live read must NOT unlock. Code 3 decides whether a follow-up goes out
    # after someone replies; auto-flipping that is a human's call, whatever the env var says.
    assert 3 not in AUTOSET_ALLOWLIST, (
        "setting code 3 governs whether follow-ups continue after a reply — it is never a "
        "config-not-send key and must never become auto-settable"
    )


def test_sc3_text_unsubscribe_is_still_an_open_contradiction():
    """SC3. Vendor docs say a "Stop"/"Remove" reply opts a lead out; a dated first-party
    observation in this account says it did not. `unknown` refuses, which is correct while
    it is unresolved — assuming the provider suppresses when it may not is the one error
    here with a legal deadline attached.

    WHEN SC3 IS DONE: set `supported` to the observed truth with a fresh `verified_on`,
    update the note in `gtm_core/optout_watch.py`, and delete this test.
    """
    cap = _capability("reply_text_unsubscribe")
    assert cap["supported"] == "unknown"
    assert cap["verified_on"] == "2026-09-21", (
        "the row was re-dated without this test being retired — if a live send settled "
        "the contradiction, record the result and remove this debt"
    )


def test_sc4_ooo_has_no_documented_read_path():
    """SC4. No API or CLI read path is documented for the account-level OOO setting, so the
    only honest check is a per-run operator attestation (`--attest ooo_auto_pause`).

    WHEN a read path is found: set `readable_via_api = true`, register its code, and delete
    this test — an attestation must never be accepted for something that can be read.
    """
    cap = _capability("ooo_auto_pause")
    assert cap["readable_via_api"] is False


def test_sc9_the_dnc_add_endpoint_is_still_unverified():
    """SC9. The add endpoint's path, verb and body come from the vendor's CLI reference and
    have never run against a live account. The marker is what says so in the one place a
    reader of that function will look.

    WHEN the live add passes: remove the `# VERIFY:` marker in `_add_items`, and delete
    this test. Until then `GTM_DNC_ADD_ENABLED` stays closed by default.
    """
    source = (REPO / "agent" / "dnc_dispatch.py").read_text(encoding="utf-8")
    assert "# VERIFY:" in source, (
        "the VERIFY marker was removed from agent/dnc_dispatch.py — if the live DNC add "
        "passed, also retire this test; if it did not, put the marker back"
    )


def test_the_dnc_kill_switch_is_closed_by_default():
    """The switch that makes the unverified endpoint safe to ship. Nothing in the repo may
    default it on before SC9's live add has passed."""
    from agent import dnc_dispatch

    assert dnc_dispatch.enabled() is False or __import__("os").getenv("GTM_DNC_ADD_ENABLED"), (
        "GTM_DNC_ADD_ENABLED reads as enabled with nothing set — the default must be off"
    )
    # `docker-compose.yml` and `.env.example` both ship in the public cut (the compose file
    # as its overlay), so both are checked there too — that is the point: the overlay gaining
    # the switch late is exactly the drift this catches. `deploy/` is private ops config and
    # is absent from a public checkout, so it is checked only where it exists rather than
    # marking the whole test private-tree, which would leave the overlay unguarded.
    required = ["docker-compose.yml", ".env.example"]
    optional = ["deploy/docker-compose.yml"]
    for path in required + optional:
        f = REPO / path
        if path in optional and not f.is_file():
            continue
        text = f.read_text(encoding="utf-8")
        assert "GTM_DNC_ADD_ENABLED" in text, f"{path} does not carry the switch at all"
        assert "GTM_DNC_ADD_ENABLED: true" not in text, f"{path} defaults the switch ON"
        assert "GTM_DNC_ADD_ENABLED=true" not in text, f"{path} defaults the switch ON"


def test_the_autoset_allowlist_is_still_empty_now_that_a_code_is_classified():
    """Auto-set may only ever write a config-not-send key. A code is now classified
    (`stop_on_reply` = 3, live-read 2026-09-22) and it is NOT one of those: it decides whether
    a follow-up goes out after somebody replies. So the allowlist stays empty, and this test
    now means something it could not mean while both collections were empty."""
    from gtm_core.capability_preflight import AUTOSET_ALLOWLIST, SETTING_CODES

    assert AUTOSET_ALLOWLIST == frozenset()
    assert SETTING_CODES, "a live read has happened; this test is no longer vacuous"
    assert 3 not in AUTOSET_ALLOWLIST


@pytest.mark.private_tree
def test_the_operator_runner_exists_and_refuses_to_send():
    """The read-only half of the live tier is scripted; the two irreversible checks are
    deliberately NOT, and the script must keep saying so.

    private_tree: the carve ships only the bootstrap/stack/seed scripts, so this operator
    runner does not exist there and the assertion has no premise to test.
    """
    script = REPO / "scripts" / "sc_live_verify.sh"
    assert script.is_file()
    text = script.read_text(encoding="utf-8")
    assert "SALESHANDY_API_KEY" in text, "the runner must refuse without a key"
    for verb in ("add_dnc_items", "add_leads_to_sequence", "update_sequence_status"):
        assert verb not in text, f"the read-only runner calls a write verb: {verb}"
