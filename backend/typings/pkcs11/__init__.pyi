"""Type stubs for ``pkcs11`` (optional HSM backend)."""

from __future__ import annotations

from enum import Enum
from typing import Any

class KeyType(Enum):
    RSA = 0
    EC = 3
    GENERIC_SECRET = 0x10
    ED25519 = 0x40

class Mechanism(Enum):
    SHA256_RSA_PKCS = 0x40
    ECDSA = 0x1040
    EDDSA = 0x1050

class Attribute(Enum):
    CLASS = 0
    VALUE = 0x11

class ObjectClass(Enum):
    PRIVATE_KEY = 2
    PUBLIC_KEY = 3

class Token:
    def open(self, user_pin: str | None = ..., rw: bool = ...) -> Session: ...

class Session:
    def get_objects(self, template: dict[Any, Any] | None = ..., **kwargs: Any) -> list[Any]: ...
    def get_key(
        self,
        key_type: KeyType | None = ...,
        label: str | None = ...,
        **kwargs: Any,
    ) -> Any: ...
    def sign(self, key: Any, data: bytes, mechanism: Any = ..., **kwargs: Any) -> bytes: ...
    def close(self) -> None: ...
    def __enter__(self) -> Session: ...
    def __exit__(self, *exc: object) -> None: ...

class Lib:
    def get_token(self, token_slot: int | None = ..., **kwargs: Any) -> Token: ...

def lib(library_path: str) -> Lib: ...
