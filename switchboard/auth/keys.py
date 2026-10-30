"""API key generation and hashing.

Keys are 32 random bytes, so a plain SHA-256 is sufficient (there is nothing to brute
force) and keeps per-request verification well under a millisecond. Argon2 is the right
tool for low-entropy passwords, not for random tokens on the request hot path.
"""

import hashlib
import hmac
import secrets

KEY_PREFIX = "sb_"
LOOKUP_PREFIX_LEN = 11  # "sb_" + 8 chars, indexed for lookup before the hash comparison


def generate_api_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def lookup_prefix(secret: str) -> str:
    return secret[:LOOKUP_PREFIX_LEN]


def hash_key(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def verify_key(secret: str, key_hash: str) -> bool:
    return hmac.compare_digest(hash_key(secret), key_hash)


def looks_like_key(value: str) -> bool:
    return value.startswith(KEY_PREFIX) and len(value) > LOOKUP_PREFIX_LEN
