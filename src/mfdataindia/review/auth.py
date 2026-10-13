"""Offline pinned Ed25519 delegated assertions; absent trust means no DB calls.

Cloud IAM/ingress remain mandatory deployment controls. This assertion proves
the caller's delegated actor/scopes; an arbitrary actor/role header proves nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr


class Claims(BaseModel):
    model_config = ConfigDict(extra="forbid")
    iss: StrictStr
    aud: StrictStr
    sub: StrictStr = Field(min_length=1, max_length=200)
    caller: StrictStr = Field(min_length=1, max_length=200)
    scope: list[StrictStr] = Field(min_length=1, max_length=2)
    iat: StrictInt
    nbf: StrictInt
    exp: StrictInt
    jti: StrictStr = Field(min_length=8, max_length=128)


@dataclass(frozen=True)
class Principal:
    caller: str
    actor: str
    scopes: frozenset[str]


class AssertionVerifier:
    """One pinned signer/config file; no live JWKS, URL or algorithm discovery."""

    def __init__(self, *, public_key: str, issuer: str, audience: str, caller: str,
                 allowed_scopes: frozenset[str]):
        key = serialization.load_pem_public_key(public_key.encode())
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("require an Ed25519 public verification key")
        if not issuer or not audience or not caller or not allowed_scopes or not allowed_scopes <= {"review:read", "review:admin"}:
            raise ValueError("invalid trust configuration")
        self.key, self.issuer, self.audience, self.caller = key, issuer, audience, caller
        self.allowed_scopes = allowed_scopes

    @classmethod
    def from_file(cls, path: str):
        import json
        document = json.loads(Path(path).read_text())
        if set(document) != {"public_key", "issuer", "audience", "caller", "allowed_scopes"}:
            raise ValueError("invalid trust configuration keys")
        return cls(**{**document, "allowed_scopes": frozenset(document["allowed_scopes"])})

    def verify(self, token: str) -> Principal:
        if len(token) > 8192:
            raise ValueError("oversized assertion")
        header = jwt.get_unverified_header(token)
        if set(header) - {"alg", "typ"} or header.get("alg") != "EdDSA" or header.get("typ") != "JWT":
            raise ValueError("unsupported assertion header")
        document = jwt.decode(token, self.key, algorithms=["EdDSA"], issuer=self.issuer,
                              audience=self.audience, options={"strict_aud": True,
                              "require": ["iss", "aud", "sub", "caller", "scope", "iat", "nbf", "exp", "jti"]})
        claims = Claims.model_validate(document)
        scopes = frozenset(claims.scope)
        if claims.caller != self.caller or not scopes <= self.allowed_scopes or len(scopes) != len(claims.scope):
            raise ValueError("unauthorized caller/scopes")
        if not 0 < claims.exp - claims.iat <= 300 or not claims.iat <= claims.nbf < claims.exp:
            raise ValueError("invalid assertion lifetime")
        return Principal(claims.caller, claims.sub, scopes)
