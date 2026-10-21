"""B2 — the network onboarding API must not accept a server-local file path.

`source_type:"file"` let any authenticated tenant make the server read an arbitrary
local file (extension-allowlisted only) and read it back via the diff endpoint. The
fix removes "file" from the backend request models, so FastAPI rejects it at body
validation (422) before any handler runs. The CLI/VPS path (agent.onboard.ingest)
keeps "file" — that is the operator on-box, not a remote client — and is unaffected.

No pytest-asyncio: these are pure model-validation checks (the enforcement point).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.schemas import (
    OnboardIngestRequest,
    OnboardProductExtractRequest,
    OnboardPromoteRequest,
)


@pytest.mark.parametrize("model", [OnboardIngestRequest, OnboardProductExtractRequest])
def test_file_source_type_rejected(model):
    """`file` is not a valid source_type on the network API — a Literal violation."""
    with pytest.raises(ValidationError):
        model(source_type="file", source="/etc/passwd")
    # a path-shaped source under a "file" type must fail on the type, not sneak
    # through as text.
    with pytest.raises(ValidationError):
        model(source_type="file", source="../../profiles/acme/knowledge/company.md")


@pytest.mark.parametrize("model", [OnboardIngestRequest, OnboardProductExtractRequest])
def test_url_and_text_still_accepted(model):
    """Positive control: the two safe source types the API does support."""
    assert model(source_type="url", source="https://example.com/about").source_type == "url"
    assert model(source_type="text", source="We build X for Y.").source_type == "text"


# ON-03 — five request fields the handlers never read (backend/routers/onboard.py
# ingest_endpoint / promote_endpoint use only source/source_type and
# confirmed_company_name, respectively). Dropping them from the models so the API
# stops promising something it silently discards. Pydantic's default
# extra="ignore" means a client that still sends them keeps getting a normal 2xx —
# asserted below so the removal is not a client-visible break.


def test_dead_ingest_fields_removed():
    """company_confirmation/additional_notes were accepted but never read."""
    assert "company_confirmation" not in OnboardIngestRequest.model_fields
    assert "additional_notes" not in OnboardIngestRequest.model_fields


def test_dead_promote_fields_removed():
    """telegram_chat_id/monthly_tool_budget_usd/per_run_cap_usd/additional_notes
    were accepted but promote_endpoint only ever reads confirmed_company_name."""
    for field in (
        "telegram_chat_id",
        "monthly_tool_budget_usd",
        "per_run_cap_usd",
        "additional_notes",
    ):
        assert field not in OnboardPromoteRequest.model_fields


def test_old_client_sending_dropped_fields_still_validates():
    """A client still sending the removed fields is not broken by this change —
    pydantic's default extra="ignore" drops them silently."""
    req = OnboardIngestRequest(
        source_type="text",
        source="We build X for Y.",
        company_confirmation="Acme",
        additional_notes="please hurry",
    )
    assert req.source == "We build X for Y."

    promote = OnboardPromoteRequest(
        confirmed_company_name="Acme",
        telegram_chat_id=123,
        monthly_tool_budget_usd=50.0,
        per_run_cap_usd=10.0,
        additional_notes="please hurry",
    )
    assert promote.confirmed_company_name == "Acme"
