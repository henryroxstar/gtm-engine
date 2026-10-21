"""Unit tests for secret-paste-guard.sh hook script."""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IS_CARVE = not (ROOT / "oss").is_dir()
HOOK_PATH = (
    ROOT / ".claude" / "hooks" / "secret-paste-guard.sh"
    if IS_CARVE
    else ROOT / "oss" / "overlays" / ".claude" / "hooks" / "secret-paste-guard.sh"
)


def _run_hook(stdin_data: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(HOOK_PATH)],
        input=stdin_data,
        text=True,
        capture_output=True,
    )


def test_clean_prompt_allowed():
    payload = json.dumps({"prompt": "Explain what gtm-engine does"})
    res = _run_hook(payload)
    assert res.returncode == 0


def test_malformed_json_fails_closed():
    res = _run_hook("{broken json")
    assert res.returncode == 1


def test_null_byte_payload_blocked():
    # \u0000 in json string
    payload = '{"prompt": "hello \\u0000 sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234"}'
    res = _run_hook(payload)
    assert res.returncode == 2
    assert "looks like an API key or password" in res.stderr


def test_raw_null_byte_payload_blocked():
    # Raw null byte in stream
    raw_payload = b'{"prompt": "hello \x00 sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234"}'
    res = subprocess.run(
        [str(HOOK_PATH)],
        input=raw_payload,
        capture_output=True,
    )
    assert res.returncode == 2
    assert "looks like an API key or password" in res.stderr.decode("utf-8")


def test_json_key_value_credential_blocked():
    payload = json.dumps({"prompt": '{"api_key": "my_secret_token_123"}'})
    res = _run_hook(payload)
    assert res.returncode == 2
    assert "looks like an API key or password" in res.stderr


def test_quoted_passphrase_with_space_blocked():
    payload = json.dumps({"prompt": 'password = "my secret password"'})
    res = _run_hook(payload)
    assert res.returncode == 2
    assert "looks like an API key or password" in res.stderr


def test_bare_jwt_blocked():
    # Fictional JWT shape: header eyJ..., payload eyJ..., signature
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.t-IDcSemACt8x4iTMCda8Yhe3iZaWbvV5XKSTbuAn0M"
    payload = json.dumps({"prompt": f"Here is the token: {jwt}"})
    res = _run_hook(payload)
    assert res.returncode == 2
    assert "looks like an API key or password" in res.stderr


def test_doppler_tokens_blocked():
    token = "dp.st.test_secret_token_123456789012345678901234567890"
    payload = json.dumps({"prompt": f"my doppler token is {token}"})
    res = _run_hook(payload)
    assert res.returncode == 2
    assert "looks like an API key or password" in res.stderr


def test_doppler_ct_token_case_insensitive():
    token = "DP.CT.TEST_SECRET_TOKEN_123456789012345678901234567890"
    payload = json.dumps({"prompt": f"token: {token}"})
    res = _run_hook(payload)
    assert res.returncode == 2
