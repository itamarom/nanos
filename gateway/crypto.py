"""
In-memory master key management and credential encryption.

The master key is held ONLY in process memory — never written to disk,
env vars, or the database.  On process restart the key is lost and
the dashboard must prompt the user to re-enter it.
"""
from __future__ import annotations

import base64
import hashlib
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

# ── In-memory master key (None = locked) ─────────────────────────
_master_key: str | None = None
_fernet: Fernet | None = None


def _derive_fernet_key(passphrase: str) -> bytes:
    """Derive a 32-byte Fernet key from a passphrase via SHA-256."""
    digest = hashlib.sha256(passphrase.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def set_master_key(passphrase: str) -> None:
    """Store the master key in process memory."""
    global _master_key, _fernet
    _master_key = passphrase
    _fernet = Fernet(_derive_fernet_key(passphrase))


def clear_master_key() -> None:
    """Clear the master key from memory (lock the system)."""
    global _master_key, _fernet
    _master_key = None
    _fernet = None


def is_unlocked() -> bool:
    """Return True if the master key is currently set."""
    return _fernet is not None


def encrypt(plaintext: str) -> str:
    """Encrypt a plaintext string.  Returns a base64 Fernet token."""
    if _fernet is None:
        raise RuntimeError("System is locked — master key not set")
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt a Fernet token back to plaintext."""
    if _fernet is None:
        raise RuntimeError("System is locked — master key not set")
    try:
        return _fernet.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise ValueError("Decryption failed — wrong master key or corrupted data")


def encrypt_with_passphrase(plaintext: str, passphrase: str) -> str:
    """Encrypt using a provided passphrase (for CLI use without unlock)."""
    key = _derive_fernet_key(passphrase)
    return Fernet(key).encrypt(plaintext.encode()).decode()


def decrypt_with_passphrase(ciphertext: str, passphrase: str) -> str:
    """Decrypt using a provided passphrase (for import without matching master key)."""
    key = _derive_fernet_key(passphrase)
    try:
        return Fernet(key).decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise ValueError("Decryption failed — wrong password")


def decrypt_json(stored: str) -> dict[str, Any]:
    """Decrypt a stored credential string and parse as JSON.

    Always decrypts first.  Falls back to plaintext JSON only as a
    migration aid, logging a warning so unencrypted rows are visible.
    """
    import json
    import logging

    _log = logging.getLogger(__name__)

    # Try decryption first — this is the expected path
    try:
        plaintext = decrypt(stored)
        decrypted: dict[str, Any] = json.loads(plaintext)
        return decrypted
    except (RuntimeError, ValueError):
        pass  # system locked or wrong key — try plaintext fallback

    # Plaintext fallback for legacy unencrypted rows
    try:
        result = json.loads(stored)
        if isinstance(result, dict):
            _log.warning(
                "Loaded UNENCRYPTED credential — re-save to encrypt. "
                "Run: cli.py encrypt-credentials"
            )
            return result
    except (json.JSONDecodeError, TypeError):
        pass

    raise ValueError("Cannot decrypt credential — system locked or data corrupted")
