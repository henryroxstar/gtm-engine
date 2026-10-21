"""Restore-check helper (F9-3): prove a stored credential row still decrypts with the KEK.

Reads ONE ``encrypted_credentials`` row as JSON on stdin (hex-encoded bytea columns), takes the key
from ``VAULT_KEK`` in the environment, and exits 0 only if the AES-GCM unwrap + decrypt succeeds.
Never prints the plaintext or the key. Used by ``systemd/restore-check.sh`` with the key held in the
operator's separate secret store, so a backup whose key was lost fails loudly.
"""

from __future__ import annotations

import json
import sys

from backend import vault

_FIELDS = ("encrypted_data", "wrapped_dek", "iv", "tag")


def main() -> int:
    kek = vault.get_kek()
    if kek is None:
        print("vault_selfcheck: VAULT_KEK missing or not 32-byte hex", file=sys.stderr)
        return 2
    try:
        row = json.loads(sys.stdin.read())
        raw = {f: bytes.fromhex(row[f]) for f in _FIELDS}
        vault.decrypt(raw, kek)
    except Exception as exc:  # noqa: BLE001 — any failure means "does not decrypt"; type only, no values
        print(f"vault_selfcheck: FAILED ({type(exc).__name__})", file=sys.stderr)
        return 1
    print("vault_selfcheck: decrypt ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
