from backend.schemas import RunResponse


def test_run_response_accepts_fallback_mode():
    data = {
        "run_id": "r123",
        "status": "ok",
        "profile_name": "acme",
        "fallback_mode": "checklist",
    }
    r = RunResponse(**data)
    assert r.fallback_mode == "checklist"
