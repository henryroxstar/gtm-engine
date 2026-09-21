"""``python -m backend.push_selftest`` — a credential-only check for FCM push (A4d).

Mints an OAuth2 access token from ``PUSH_FCM_SERVICE_ACCOUNT_JSON`` and sends a
``validate_only`` FCM v1 message. That exercises the whole credential path — the
service-account JSON parses, the RS256 JWT it signs is accepted by Google's token
endpoint, and FCM v1 itself is reachable — without notifying any real device: the
target token need not exist. Prints exactly one line, "FCM credentials OK" or the
failure, and exits 1 on failure so a deploy script can gate on it.

Deferred from real use until the operator finishes A4 (the Firebase iOS/Android bundle
IDs aren't finalized — no client app is built yet, so there is nothing to register the
service account against; see PENDING.md FL11). This module ships as working code now
so A4d is a one-command check the moment credentials land, not a follow-up to write.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from . import push

#: FCM v1 rejects a malformed/nonexistent token AFTER accepting the Bearer credential —
#: seeing this (rather than 401 UNAUTHENTICATED) is exactly what proves auth succeeded.
_VALIDATE_ONLY_TOKEN = "push-selftest-validate-only"  # nosec B105 — a dummy FCM device token, not a credential


async def _probe(transport: push.Transport | None) -> str:
    account = push._service_account()
    if account is None:
        return "no usable PUSH_FCM_SERVICE_ACCOUNT_JSON"

    access_token = await push._access_token(account, transport=transport)
    if access_token is None:
        return "OAuth2 token exchange failed"

    result = await push._post_retrying(
        transport or push._httpx_transport,
        f"https://fcm.googleapis.com/v1/projects/{account['project_id']}/messages:send",
        "the validate_only send",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json_body={
            "message": {
                "token": _VALIDATE_ONLY_TOKEN,
                "notification": {"title": "push-selftest", "body": "push-selftest"},
            },
            "validate_only": True,
        },
    )
    if result is None:
        return "FCM validate_only request failed (network error)"
    status_code, body = result
    if status_code == push._HTTP_OK:
        return "OK"
    if status_code == push._HTTP_CLIENT_ERROR and "INVALID_ARGUMENT" in str(body):
        # The dummy token was rejected, but AFTER the Bearer credential was accepted —
        # the thing this self-test exists to prove.
        return "OK"
    return f"FCM validate_only rejected (status {status_code})"


async def _check(transport: push.Transport | None = None) -> str:
    """ "OK" or a short, non-secret failure description. Never raises."""
    try:
        return await _probe(transport)
    except Exception as exc:  # noqa: BLE001 — the operator gets one line, never a traceback
        return f"unexpected {type(exc).__name__}"


def main(transport: push.Transport | None = None) -> int:
    # The result line is the whole contract. push's own logging would add its messages
    # and a transport traceback around it, and the line already names the failure.
    push_log = logging.getLogger(push.__name__)
    previous = push_log.disabled
    push_log.disabled = True
    try:
        result = asyncio.run(_check(transport))
    finally:
        push_log.disabled = previous
    if result == "OK":
        print("FCM credentials OK")
        return 0
    print(f"FCM credentials FAILED: {result}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
