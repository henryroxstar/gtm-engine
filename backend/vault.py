"""Envelope encryption utility for BYOK credentials.

Implements AES-256-GCM envelope encryption for the `encrypted_credentials` table (V003).
The Data Encryption Key (DEK) is randomly generated per row, encrypts the plaintext,
and is then wrapped (encrypted) by the environment-provided Key Encryption Key (KEK).
"""

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def get_kek() -> bytes | None:
    """Return the 32-byte Key Encryption Key from the environment, if configured."""
    kek_hex = os.getenv("VAULT_KEK", "").strip()
    if not kek_hex:
        return None
    try:
        kek = bytes.fromhex(kek_hex)
        if len(kek) != 32:
            return None
        return kek
    except ValueError:
        return None


def encrypt(plaintext: str, kek: bytes) -> dict:
    """Encrypt a plaintext string using AES-256-GCM envelope encryption."""
    # 1. Generate a random DEK and encrypt the plaintext
    dek = AESGCM.generate_key(bit_length=256)
    aesgcm_dek = AESGCM(dek)
    iv = os.urandom(12)

    # AESGCM.encrypt appends the 16-byte authentication tag to the ciphertext
    ct_and_tag = aesgcm_dek.encrypt(iv, plaintext.encode("utf-8"), None)
    encrypted_data = ct_and_tag[:-16]
    tag = ct_and_tag[-16:]

    # 2. Wrap the DEK using the KEK
    aesgcm_kek = AESGCM(kek)
    kek_iv = os.urandom(12)
    # We prepend the kek_iv to the wrapped DEK for storage in a single BYTEA column
    wrapped_dek = kek_iv + aesgcm_kek.encrypt(kek_iv, dek, None)

    return {
        "encrypted_data": encrypted_data,
        "wrapped_dek": wrapped_dek,
        "iv": iv,
        "tag": tag,
        "key_version": "v1",
    }


def decrypt(row: dict, kek: bytes) -> str:
    """Decrypt a database row returning the plaintext string."""
    wrapped_dek_bytes = bytes(row["wrapped_dek"])

    # Extract KEK IV and the wrapped ciphertext
    kek_iv = wrapped_dek_bytes[:12]
    wrapped_ciphertext = wrapped_dek_bytes[12:]

    # Unwrap the DEK
    aesgcm_kek = AESGCM(kek)
    dek = aesgcm_kek.decrypt(kek_iv, wrapped_ciphertext, None)

    # Reassemble ciphertext + tag and decrypt the plaintext
    aesgcm_dek = AESGCM(dek)
    ct_and_tag = bytes(row["encrypted_data"]) + bytes(row["tag"])

    plaintext_bytes = aesgcm_dek.decrypt(bytes(row["iv"]), ct_and_tag, None)
    return plaintext_bytes.decode("utf-8")
