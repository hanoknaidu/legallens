"""Password hashing.

**scrypt, from the standard library.** argon2id would be the first choice on a
greenfield service, but it is a C extension and a new dependency, and `hashlib.scrypt`
is memory-hard, OpenSSL-backed, and already present in the image — which keeps the
shipped artifact at the 221MB DOCKER.md documents and keeps `uv.lock` untouched. The
cost parameters are stored inside each hash, so raising them later does not invalidate
hashes written under the old ones.

Two things here are security-relevant rather than incidental:

* `verify` is constant-time (`hmac.compare_digest`). A `==` on digests leaks, through
  timing, how many leading bytes were right.
* `DUMMY_HASH` exists so a sign-in attempt for an address with no account costs the
  same as one for an address with an account. Without it, response time answers
  "is this person a client of this firm?" — which for a legal product is itself
  privileged (BRD §8.2).
"""

from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import os
import secrets

#: OWASP's scrypt guidance is n >= 2**16, r = 8, p = 1. At 2**16 one hash costs about
#: 64MB and ~90ms, which is the right order for an endpoint that is rate limited and
#: called once per sign-in.
DEFAULT_N = 2**16
DEFAULT_R = 8
DEFAULT_P = 1

#: OpenSSL refuses a scrypt call whose working set exceeds its own limit, and the
#: default is below what n=2**16 needs — so it is raised explicitly rather than left
#: to fail at the first real sign-in. Covers n up to 2**17.
MAXMEM = 256 * 1024 * 1024

_SALT_BYTES = 16
_KEY_BYTES = 32
_ALGORITHM = "scrypt"


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def hash_password(
    password: str, *, n: int = DEFAULT_N, r: int = DEFAULT_R, p: int = DEFAULT_P
) -> str:
    """`scrypt$n$r$p$salt$key`, all of it safe to store."""
    salt = os.urandom(_SALT_BYTES)
    key = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=_KEY_BYTES, maxmem=MAXMEM
    )
    return f"{_ALGORITHM}${n}${r}${p}${_b64(salt)}${_b64(key)}"


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time verify. A malformed stored hash is a failure, never an exception.

    A hash this cannot parse means the column holds something it should not, and the
    safe reading of that is "this password does not match" — not a 500 that tells the
    caller the account exists and the record is broken.
    """
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$")
        if algorithm != _ALGORITHM:
            return False
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_unb64(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(_unb64(expected)),
            maxmem=MAXMEM,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, _unb64(expected))


@functools.lru_cache(maxsize=4)
def dummy_hash(n: int = DEFAULT_N, r: int = DEFAULT_R, p: int = DEFAULT_P) -> str:
    """A hash of a random password, for equalising the timing of a missing account.

    Built at the same cost as a real one, because the point is for the two paths to
    take the same time; cached, because rebuilding it per attempt would make the
    missing-account path slower than the real one and leak in the other direction.
    Nobody knows the input, so nothing verifies against it.
    """
    return hash_password(secrets.token_urlsafe(32), n=n, r=r, p=p)


def new_session_token() -> str:
    """The cookie value. 32 bytes from the OS CSPRNG, URL-safe."""
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    """What the `sessions` table stores, so a leaked table yields no live session."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
