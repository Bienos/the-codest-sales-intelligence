"""Per-user secrets (OAuth refresh tokens, personal API keys) sealed at rest with AES-256-GCM.

The key comes from RI_VAULT_KEY (urlsafe base64 of 32 random bytes). Without it nothing can be stored and the connections page says so.
Every sealed value is bound to its owner (`aad` = "<user id>:<provider>"), so a value copied to another row or user cannot be opened.
Format: 1 version byte, 12 byte nonce, ciphertext+tag. Never log a plaintext or a sealed value."""
from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = b"\x01"


class VaultError(Exception):
    """The vault is not configured, or a sealed value cannot be opened."""


def _key(key_b64: str | None) -> bytes:
    if not key_b64:
        raise VaultError("RI_VAULT_KEY is not set: personal connections cannot store credentials")
    try:
        raw = base64.urlsafe_b64decode(key_b64 + "=" * (-len(key_b64) % 4))
    except Exception as exc:  # noqa: BLE001
        raise VaultError("RI_VAULT_KEY is not valid base64") from exc
    if len(raw) != 32:
        raise VaultError("RI_VAULT_KEY must be 32 bytes (urlsafe base64)")
    return raw


def available(key_b64: str | None) -> bool:
    try:
        _key(key_b64)
        return True
    except VaultError:
        return False


def seal(key_b64: str | None, plaintext: str, aad: str) -> bytes:
    nonce = os.urandom(12)
    return VERSION + nonce + AESGCM(_key(key_b64)).encrypt(nonce, plaintext.encode("utf-8"), aad.encode("utf-8"))


def unseal(key_b64: str | None, blob: bytes | memoryview, aad: str) -> str:
    raw = bytes(blob)
    if len(raw) < 1 + 12 + 16 or raw[:1] != VERSION:
        raise VaultError("sealed value has an unknown format")
    try:
        return AESGCM(_key(key_b64)).decrypt(raw[1:13], raw[13:], aad.encode("utf-8")).decode("utf-8")
    except InvalidTag as exc:
        raise VaultError("sealed value cannot be opened (wrong key or another owner)") from exc


def new_key() -> str:
    """A fresh key for RI_VAULT_KEY (printed once by an administrator; never stored by the app)."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
