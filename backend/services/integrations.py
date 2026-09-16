"""Database service layer for BYOK encrypted credentials."""

import json
from typing import Any

from ..database import workspace_scope
from ..vault import decrypt, encrypt, get_kek


async def upsert_credential(
    pool: Any, workspace_id: str, provider: str, account_ref: str, payload: dict
) -> None:
    """Encrypt and store a credential payload for a workspace."""
    kek = get_kek()
    if not kek:
        raise ValueError("VAULT_KEK is not configured on the backend")

    plaintext = json.dumps(payload)
    enc = encrypt(plaintext, kek)

    async with workspace_scope(pool, workspace_id) as conn:
        await conn.execute(
            """
            INSERT INTO encrypted_credentials
                (workspace_id, provider, account_ref, encrypted_data, wrapped_dek, iv, tag, key_version)
            VALUES
                ($1::uuid, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (workspace_id, provider, account_ref)
            DO UPDATE SET
                encrypted_data = EXCLUDED.encrypted_data,
                wrapped_dek = EXCLUDED.wrapped_dek,
                iv = EXCLUDED.iv,
                tag = EXCLUDED.tag,
                key_version = EXCLUDED.key_version,
                rotated_at = now()
            """,
            workspace_id,
            provider,
            account_ref,
            enc["encrypted_data"],
            enc["wrapped_dek"],
            enc["iv"],
            enc["tag"],
            enc["key_version"],
        )


async def get_workspace_credentials(pool: Any, workspace_id: str) -> dict[str, str]:
    """Fetch and decrypt all integration credentials for a workspace."""
    kek = get_kek()
    if not kek:
        return {}  # Degrade gracefully if the vault is unconfigured

    async with workspace_scope(pool, workspace_id) as conn:
        rows = await conn.fetch(
            "SELECT * FROM encrypted_credentials WHERE workspace_id = $1::uuid", workspace_id
        )

    creds = {}
    for row in rows:
        try:
            pt = decrypt(dict(row), kek)
            data = json.loads(pt)
            if "api_key" in data:
                creds[row["provider"]] = data["api_key"]
        except Exception as e:
            # Skip corrupted or un-decryptable keys (e.g. if VAULT_KEK rotated without re-encryption)
            import logging

            logging.getLogger(__name__).warning(
                f"Failed to decrypt credential {row['provider']} for workspace {workspace_id}: {e}"
            )

    return creds
