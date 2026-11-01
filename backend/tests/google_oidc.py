"""Google sign-in's keys for tests: RSA keys, their JWKS (served by conftest's `google_oidc` at GOOGLE_SIGNIN_JWKS_URL)
and an ID-token minter with chosen claims. The token endpoint's OIDC answers are the fake Google's (fake_google.py)."""

import json
import time
from functools import cache
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

CLIENT_ID = "test-signin-client.apps.googleusercontent.com"
CLIENT_SECRET = "test-signin-secret"
ISSUER = "https://accounts.google.com"


@cache
def rsa_key(kid: str) -> rsa.RSAPrivateKey:
    """One 2048-bit key per kid for the whole run (generating one takes a while)."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class GoogleOidc:
    def __init__(self, client_id: str = CLIENT_ID) -> None:
        self.client_id = client_id
        self.published = ["key-1"]  # the kids in the JWKS
        self.kid = "key-1"  # the one tokens are signed with
        self.max_age = 3600  # the JWKS answer's Cache-Control max-age
        self.jwks_requests = 0

    def rotate(self, kid: str) -> None:
        """Signs with a new key, published beside the old ones."""
        self.kid = kid
        self.published.append(kid)

    def jwks(self) -> dict[str, Any]:
        keys = []
        for kid in self.published:
            jwk = json.loads(RSAAlgorithm.to_jwk(rsa_key(kid).public_key()))
            keys.append({**jwk, "kid": kid, "alg": "RS256", "use": "sig"})
        return {"keys": keys}

    def claims(self, **claims: Any) -> dict[str, Any]:
        """Google's claims for a verified Gmail account, with `claims` replacing them (None: left out)."""
        now = int(time.time())
        values: dict[str, Any] = {
            "iss": ISSUER,
            "aud": self.client_id,
            "azp": self.client_id,
            "sub": "100000000000000000001",
            "email": "user@gmail.com",
            "email_verified": True,
            "iat": now,
            "exp": now + 3600,
            **claims,
        }
        return {name: value for name, value in values.items() if value is not None}

    def id_token(
        self, *, kid: str | None = None, algorithm: str = "RS256", key: Any = None, **claims: Any
    ) -> str:
        """A signed ID token: by default RS256 with the current key and its kid. `key` signs with another key (an
        HS256 secret, a key Google never published); `kid` sets the header's."""
        signing = key if key is not None else rsa_key(self.kid)
        return jwt.encode(self.claims(**claims), signing, algorithm=algorithm, headers={"kid": kid or self.kid})

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.jwks_requests += 1
        return httpx.Response(
            200, json=self.jwks(), headers={"Cache-Control": f"public, max-age={self.max_age}"}, request=request
        )
