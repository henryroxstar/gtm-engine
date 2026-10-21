import os

import pytest

from backend.vault import decrypt, encrypt, get_kek


def test_vault_roundtrip():
    kek = os.urandom(32)
    plaintext = '{"api_key": "sk-12345"}'

    enc = encrypt(plaintext, kek)
    assert "encrypted_data" in enc
    assert "wrapped_dek" in enc

    # Decrypt
    dec = decrypt(enc, kek)
    assert dec == plaintext


def test_vault_tampering():
    kek = os.urandom(32)
    plaintext = "supersecret"
    enc = encrypt(plaintext, kek)

    # Tamper with encrypted_data
    enc["encrypted_data"] = b"X" + enc["encrypted_data"][1:]

    with pytest.raises(Exception):
        decrypt(enc, kek)


def test_vault_get_kek(monkeypatch):
    monkeypatch.setenv(
        "VAULT_KEK", "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    )
    kek = get_kek()
    assert kek is not None
    assert len(kek) == 32
