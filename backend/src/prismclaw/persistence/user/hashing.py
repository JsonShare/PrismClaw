from __future__ import annotations

"""Scheme-tagged password hashing for the DB-backed web user store.

Storage format: ``<scheme>:<payload>``

- ``scrypt:<salt_b64>:<N>:<r>:<p>:<dk_b64>``  (stdlib ``hashlib.scrypt``, default)
- ``bcrypt:<bcrypt_hash>``                    (``bcrypt`` package, optional)

The default scheme is ``scrypt`` so the local/cloud user store needs **no new
dependency** beyond what the project already pulls in (``sqlalchemy[asyncio]``).
The scheme tag makes the format forward-compatible: operators can switch to
``bcrypt``/``argon2`` later by bumping ``WebUserStoreSettings.password_scheme``
and re-hashing on next password change.
"""

import base64
import hashlib
import hmac
import os

_SCRYPT_N = 16384  # 2^14 — memory-hard cost factor
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 64
_SCRYPT_MAXMEM = 32 * 1024 * 1024  # 32 MiB cap, matches N/r/p above
_SALT_LEN = 16


def hash_password(password: str, *, scheme: str = "scrypt") -> str:
    pw = password.encode("utf-8")
    if scheme == "scrypt":
        salt = os.urandom(_SALT_LEN)
        dk = hashlib.scrypt(
            pw,
            salt=salt,
            n=_SCRYPT_N,
            r=_SCRYPT_R,
            p=_SCRYPT_P,
            dklen=_SCRYPT_DKLEN,
            maxmem=_SCRYPT_MAXMEM,
        )
        return "scrypt:{}:{}:{}:{}:{}".format(
            base64.b64encode(salt).decode("ascii"),
            _SCRYPT_N,
            _SCRYPT_R,
            _SCRYPT_P,
            base64.b64encode(dk).decode("ascii"),
        )
    if scheme == "bcrypt":
        import bcrypt

        return "bcrypt:" + bcrypt.hashpw(pw, bcrypt.gensalt()).decode("ascii")
    raise ValueError(f"unknown password scheme: {scheme!r}")


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or ":" not in stored:
        return False
    scheme, _, payload = stored.partition(":")
    pw = password.encode("utf-8")
    try:
        if scheme == "scrypt":
            salt_b64, n_s, r_s, p_s, dk_b64 = payload.split(":")
            salt = base64.b64decode(salt_b64)
            expected = base64.b64decode(dk_b64)
            actual = hashlib.scrypt(
                pw,
                salt=salt,
                n=int(n_s),
                r=int(r_s),
                p=int(p_s),
                dklen=len(expected),
                maxmem=_SCRYPT_MAXMEM,
            )
            return hmac.compare_digest(expected, actual)
        if scheme == "bcrypt":
            import bcrypt

            return bcrypt.checkpw(pw, payload.encode("ascii"))
    except Exception:
        # Any malformed stored hash / decode error → treat as mismatch.
        return False
    return False


def parse_scheme(stored: str | None) -> str | None:
    if not stored or ":" not in stored:
        return None
    return stored.partition(":")[0]


def needs_upgrade(stored: str | None, preferred: str = "scrypt") -> bool:
    """True when ``stored`` uses a different scheme than ``preferred``."""
    if not stored:
        return False
    return parse_scheme(stored) != preferred
