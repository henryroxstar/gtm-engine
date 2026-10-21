"""MM7/MM8: the self-serve sequence the portal drives is executable here.

The consumer lives in another repo, so the claim "our half matches what the portal sends" is
otherwise only prose. tests/fixtures/self_serve_billing.sample.json is the committed
sample both sides agree on; this test pins it to the live request/response models, so a
renamed or retyped field on our side turns this red before the portal finds out in staging.

Model validation alone cannot discriminate a renamed OPTIONAL field (pydantic ignores
unknown keys and defaults the missing one), so every sample body is also required to use
only field names the model declares.
"""

from __future__ import annotations

import importlib
import json
import uuid
from pathlib import Path

import pytest

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "self_serve_billing.sample.json"
SAMPLE = json.loads(FIXTURE.read_text())
STEPS = {s["name"]: s for s in SAMPLE["steps"]}


def _model(ref: str):
    module, _, name = ref.partition(":")
    return getattr(importlib.import_module(module), name)


def _bodies():
    for step in SAMPLE["steps"]:
        for side in ("request", "response"):
            ref = step.get(f"{side}_model")
            if ref:
                yield pytest.param(ref, step[side], id=f"{step['name']}-{side}")


@pytest.mark.parametrize(("ref", "body"), list(_bodies()))
def test_sample_body_matches_live_model(ref, body):
    model = _model(ref)
    model.model_validate(body)
    unknown = set(body) - set(model.model_fields)
    assert not unknown, (
        f"{ref} does not declare {sorted(unknown)} — the sample and the API disagree"
    )


def test_sequence_order_is_the_contract():
    # Sign-in creates the workspace; RevenueCat must already carry its id when payment lands.
    order = [s["name"] for s in SAMPLE["steps"]]
    assert order.index("sign_in") < order.index("payment_top_up")
    assert order.index("onboard_promote") < order.index("mint_key")
    assert order.index("entitlement_sync") < order.index("mint_key")


def test_one_workspace_id_threads_through_every_step():
    # The identity claim: the id sign-in mints is the id payment credits and the edge sees.
    ws = SAMPLE["workspace_id"]
    uuid.UUID(ws)
    assert STEPS["payment_top_up"]["path"] == f"/v1/wallet/{ws}/top-ups"
    assert STEPS["entitlement_sync"]["path"].endswith("/" + ws)
    assert STEPS["edge_verify"]["response"]["workspace_id"] == ws


def test_top_up_amount_is_the_balance_the_edge_sees():
    amount = STEPS["payment_top_up"]["request"]["amount_usd"]
    assert STEPS["edge_verify"]["response"]["balance_usd"] == amount


def test_proposed_steps_are_not_yet_routes():
    # A proposed step has no live model to check against. When its route lands, this goes red
    # so the step gains request_model/response_model and joins the check above.
    routers = (Path(__file__).resolve().parents[2] / "backend" / "routers").glob("*.py")
    source = "\n".join(r.read_text() for r in routers)
    for step in SAMPLE["steps"]:
        if step.get("status") == "proposed":
            assert "request_model" not in step
            prefix = "/" + step["path"].split("/")[2]
            assert f'prefix="{prefix}"' not in source, (
                f"{step['name']}: {prefix} now exists — add its models to the sample"
            )


def test_the_payment_step_is_a_live_modelled_route_not_a_proposal():
    """MM7 shipped: the step carries its models (so it joins the live-model check above) and is no longer
    marked proposed. A regression to 'proposed' would silently drop it out of that check."""
    step = STEPS["payment_top_up"]
    assert step.get("status") != "proposed"
    assert step["request_model"] == "backend.schemas:WalletTopUpRequest"
    assert step["response_model"] == "backend.schemas:WalletTopUpResponse"
