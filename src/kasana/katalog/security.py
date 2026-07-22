"""Local credential primitives for profile PINs."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets


def hash_profile_pin(pin: str) -> str:
    """Return a salted scrypt verifier without retaining the PIN itself."""

    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(pin.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$16384$8$1$" + base64.b64encode(salt + digest).decode("ascii")


def verify_profile_pin(stored_hash: str, pin: str | None) -> bool:
    """Verify a versioned scrypt PIN verifier in constant time."""

    if pin is None:
        return False
    try:
        algorithm, raw_n, raw_r, raw_p, encoded = stored_hash.split("$", maxsplit=4)
        if algorithm != "scrypt":
            return False
        n, r, p = int(raw_n), int(raw_r), int(raw_p)
        combined = base64.b64decode(encoded.encode("ascii"), validate=True)
        salt, expected = combined[:16], combined[16:]
        actual = hashlib.scrypt(pin.encode("utf-8"), salt=salt, n=n, r=r, p=p)
    except ValueError, TypeError:
        return False
    return hmac.compare_digest(actual, expected)
