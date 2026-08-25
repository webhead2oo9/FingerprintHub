"""Application-level field encryption helpers.

Encrypted values are stored as text envelopes:
    enc:v1:<key_id>:<nonce_b64>:<ciphertext_b64>
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from secrets import token_bytes
from typing import Any, Dict, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


KEYS_ENV = "FINGERPRINTHUB_FIELD_ENCRYPTION_KEYS"
ACTIVE_KEY_ENV = "FINGERPRINTHUB_FIELD_ENCRYPTION_ACTIVE_KEY_ID"
ENVELOPE_VERSION = "v1"
ENVELOPE_PREFIX = f"enc:{ENVELOPE_VERSION}:"
NONCE_BYTES = 12
KEY_BYTES = 32

_KEY_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class FieldEncryptionError(ValueError):
    """Base error for field encryption failures."""


class FieldEncryptionConfigError(FieldEncryptionError):
    """Raised when encryption key configuration is missing or invalid."""


class FieldDecryptionError(FieldEncryptionError):
    """Raised when an encrypted envelope cannot be decrypted."""


@dataclass(frozen=True)
class FieldEncryptionKeyring:
    keys: Dict[str, bytes]
    active_key_id: str


def _decode_base64_key(raw_value: str, *, key_id: str) -> bytes:
    try:
        decoded = base64.b64decode(raw_value.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise FieldEncryptionConfigError(
            f"{KEYS_ENV} entry {key_id!r} is not valid base64"
        ) from exc
    if len(decoded) != KEY_BYTES:
        raise FieldEncryptionConfigError(
            f"{KEYS_ENV} entry {key_id!r} must decode to {KEY_BYTES} bytes"
        )
    return decoded


def parse_keyring(raw_keys: Optional[str], active_key_id: Optional[str]) -> FieldEncryptionKeyring:
    """Parse env-shaped key material into a keyring."""
    if not raw_keys or not raw_keys.strip():
        raise FieldEncryptionConfigError(f"{KEYS_ENV} is required")
    if not active_key_id or not active_key_id.strip():
        raise FieldEncryptionConfigError(f"{ACTIVE_KEY_ENV} is required")

    keys: Dict[str, bytes] = {}
    key_material_ids: Dict[bytes, str] = {}
    for raw_entry in raw_keys.split(","):
        entry = raw_entry.strip()
        if not entry:
            raise FieldEncryptionConfigError(f"{KEYS_ENV} contains an empty key entry")
        if ":" not in entry:
            raise FieldEncryptionConfigError(
                f"{KEYS_ENV} entries must use key_id:base64_key format"
            )
        key_id, raw_key = entry.split(":", 1)
        key_id = key_id.strip()
        raw_key = raw_key.strip()
        if not _KEY_ID_RE.fullmatch(key_id):
            raise FieldEncryptionConfigError(
                f"{KEYS_ENV} key id {key_id!r} must contain only letters, numbers, _, ., or -"
            )
        if key_id in keys:
            raise FieldEncryptionConfigError(f"{KEYS_ENV} contains duplicate key id {key_id!r}")
        decoded_key = _decode_base64_key(raw_key, key_id=key_id)
        existing_key_id = key_material_ids.get(decoded_key)
        if existing_key_id is not None:
            raise FieldEncryptionConfigError(
                f"{KEYS_ENV} contains duplicate key material for key ids "
                f"{existing_key_id!r} and {key_id!r}"
            )
        keys[key_id] = decoded_key
        key_material_ids[decoded_key] = key_id

    active = active_key_id.strip()
    if active not in keys:
        raise FieldEncryptionConfigError(
            f"{ACTIVE_KEY_ENV} {active!r} is not present in {KEYS_ENV}"
        )
    return FieldEncryptionKeyring(keys=keys, active_key_id=active)


@lru_cache(maxsize=1)
def load_keyring_from_env() -> FieldEncryptionKeyring:
    return parse_keyring(os.getenv(KEYS_ENV), os.getenv(ACTIVE_KEY_ENV))


def clear_keyring_cache() -> None:
    load_keyring_from_env.cache_clear()


def generate_key_entry(key_id: str) -> str:
    if not _KEY_ID_RE.fullmatch(key_id):
        raise FieldEncryptionConfigError(
            "key id must contain only letters, numbers, _, ., or -"
        )
    return f"{key_id}:{base64.b64encode(token_bytes(KEY_BYTES)).decode('ascii')}"


def is_encrypted_value(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(ENVELOPE_PREFIX)


def encrypt_text(
    plaintext: Optional[str],
    *,
    keyring: Optional[FieldEncryptionKeyring] = None,
    key_id: Optional[str] = None,
) -> Optional[str]:
    if plaintext is None:
        return None
    ring = keyring or load_keyring_from_env()
    resolved_key_id = key_id or ring.active_key_id
    key = ring.keys.get(resolved_key_id)
    if key is None:
        raise FieldEncryptionConfigError(f"encryption key id {resolved_key_id!r} is not configured")
    nonce = token_bytes(NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return ":".join(
        (
            "enc",
            ENVELOPE_VERSION,
            resolved_key_id,
            base64.b64encode(nonce).decode("ascii"),
            base64.b64encode(ciphertext).decode("ascii"),
        )
    )


def decrypt_text(envelope: str, *, keyring: Optional[FieldEncryptionKeyring] = None) -> str:
    if not is_encrypted_value(envelope):
        raise FieldDecryptionError("value is not an encrypted field envelope")
    parts = envelope.split(":", 4)
    if len(parts) != 5 or parts[0] != "enc" or parts[1] != ENVELOPE_VERSION:
        raise FieldDecryptionError("encrypted field envelope is malformed")
    _, _version, key_id, nonce_b64, ciphertext_b64 = parts
    ring = keyring or load_keyring_from_env()
    key = ring.keys.get(key_id)
    if key is None:
        raise FieldDecryptionError(f"encrypted field references unknown key id {key_id!r}")
    try:
        nonce = base64.b64decode(nonce_b64.encode("ascii"), validate=True)
        ciphertext = base64.b64decode(ciphertext_b64.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise FieldDecryptionError("encrypted field envelope contains invalid base64") from exc
    if len(nonce) != NONCE_BYTES:
        raise FieldDecryptionError("encrypted field envelope has invalid nonce length")
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
    except InvalidTag as exc:
        raise FieldDecryptionError("encrypted field authentication failed") from exc
    return plaintext.decode("utf-8")


def decrypt_text_field(
    value: Any,
    *,
    keyring: Optional[FieldEncryptionKeyring] = None,
) -> Optional[str]:
    if value is None:
        return None
    if not is_encrypted_value(value):
        raise FieldDecryptionError("encrypted text field is not encrypted")
    return decrypt_text(value, keyring=keyring)


def encrypt_json(value: Any) -> Optional[str]:
    if value is None:
        return None
    return encrypt_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def decrypt_json_field(
    value: Any,
    *,
    keyring: Optional[FieldEncryptionKeyring] = None,
) -> Any:
    if value is None:
        return None
    if not is_encrypted_value(value):
        raise FieldDecryptionError("encrypted JSON field is not encrypted")
    decrypted = decrypt_text(value, keyring=keyring)
    try:
        return json.loads(decrypted)
    except (TypeError, ValueError) as exc:
        raise FieldDecryptionError("encrypted JSON field contains invalid JSON") from exc


def rekey_envelope(envelope: str, *, target_key_id: Optional[str] = None) -> str:
    plaintext = decrypt_text(envelope)
    return encrypt_text(plaintext, key_id=target_key_id)
