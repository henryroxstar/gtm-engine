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

from backend.schemas import OnboardIngestRequest, OnboardProductExtractRequest


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
