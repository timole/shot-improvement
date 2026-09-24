"""Microsoft Entra ID sign-in verification and a signed session cookie
for the snapshot.timolehtonen.tech gallery (spec 095/096) - identity
provider is Microsoft, not Google, by explicit request: no GCP
dependency anywhere in this service, including for sign-in.

A standard server-side OAuth 2.0 authorization-code flow (see
server/main.py's /api/login/start and /api/login/callback), not a
client-side SDK - the browser is redirected to Microsoft, then back
here with a `code`, which this server exchanges for an ID token and
verifies against Microsoft's own JWKS (PyJWT). The App Registration
(created via `az ad app create --sign-in-audience
AzureADandPersonalMicrosoftAccount`) accepts both organizational and
personal Microsoft accounts, each under their own tenant issuer -
deliberately not pinned to one specific tenant ID here; the real
access boundary is the email allowlist check applied after this
returns (server/main.py), same as the Google flow this replaces.

The session cookie mechanism itself is unchanged from that Google-based
version: a small hand-rolled HMAC-signed token, not JWT and not a real
session-store dependency - proven adequate at this gallery's scale
(a single owner)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import jwt

AUTHORIZE_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
JWKS_URL = "https://login.microsoftonline.com/common/discovery/v2.0/keys"
# Multi-tenant (AzureADandPersonalMicrosoftAccount): the issuer varies
# per account's actual tenant (a GUID for org accounts, the fixed
# "9188040d-6c67-4c5b-b112-36a304b66dad" tenant for personal Microsoft
# accounts) - checked as a prefix/suffix shape rather than one pinned
# value.
ISSUER_PREFIX = "https://login.microsoftonline.com/"
ISSUER_SUFFIX = "/v2.0"

SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600  # 30 days

_jwks_client = jwt.PyJWKClient(JWKS_URL)


class InvalidMicrosoftToken(Exception):
    pass


def verify_microsoft_id_token(token: str, client_id: str) -> dict:
    """Verifies signature and audience, and sanity-checks the issuer
    shape (see module docstring - not pinned to one tenant). Raises
    InvalidMicrosoftToken on any failure instead of letting the
    underlying library's various exception types leak out."""
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token, signing_key.key, algorithms=["RS256"],
            audience=client_id,
            options={"verify_iss": False},  # checked manually below - multi-tenant, no single expected value
        )
    except Exception as exc:
        raise InvalidMicrosoftToken(str(exc)) from exc
    issuer = str(claims.get("iss", ""))
    if not (issuer.startswith(ISSUER_PREFIX) and issuer.endswith(ISSUER_SUFFIX)):
        raise InvalidMicrosoftToken(f"unexpected issuer: {issuer!r}")
    return claims


def _sign(payload: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def create_session_token(email: str, secret: str) -> str:
    payload = json.dumps({"email": email, "exp": int(time.time()) + SESSION_MAX_AGE_SECONDS})
    encoded_payload = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{encoded_payload}.{_sign(encoded_payload, secret)}"


def verify_session_token(token: str, secret: str) -> str | None:
    """Returns the session's email if the token's signature is valid and
    it hasn't expired, else None - never raises, so callers can treat
    any failure uniformly as "not signed in"."""
    try:
        encoded_payload, signature = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(signature, _sign(encoded_payload, secret)):
        return None
    try:
        padded = encoded_payload + "=" * (-len(encoded_payload) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        email, exp = payload["email"], payload["exp"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(email, str) or time.time() > exp:
        return None
    return email


def parse_allowed_emails(raw: str) -> set[str]:
    return {e.strip().lower() for e in raw.split(",") if e.strip()}
