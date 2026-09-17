"""Pure-logic tests for server/auth.py: the session-token mechanism
(signature/expiry/tamper handling) and verify_microsoft_id_token's
claim/issuer checks - JWKS fetching and signature verification
themselves (jwt.PyJWKClient/jwt.decode) are mocked, since those are
network calls to Microsoft, not this module's own logic."""

import time
from unittest.mock import MagicMock

import pytest

from server.auth import (
    InvalidMicrosoftToken,
    create_session_token,
    parse_allowed_emails,
    verify_microsoft_id_token,
    verify_session_token,
)

SECRET = "test-secret"
CLIENT_ID = "test-client-id"


def test_create_then_verify_round_trips_the_email() -> None:
    token = create_session_token("timo.lehtonen@gmail.com", SECRET)
    assert verify_session_token(token, SECRET) == "timo.lehtonen@gmail.com"


def test_verify_rejects_a_tampered_signature() -> None:
    token = create_session_token("timo.lehtonen@gmail.com", SECRET)
    payload, _sig = token.split(".", 1)
    tampered = f"{payload}.not-the-real-signature"
    assert verify_session_token(tampered, SECRET) is None


def test_verify_rejects_the_wrong_secret() -> None:
    token = create_session_token("timo.lehtonen@gmail.com", SECRET)
    assert verify_session_token(token, "a-different-secret") is None


def test_verify_rejects_malformed_tokens() -> None:
    assert verify_session_token("not-even-two-parts", SECRET) is None
    assert verify_session_token("", SECRET) is None


def test_verify_rejects_an_expired_token(monkeypatch: pytest.MonkeyPatch) -> None:
    token = create_session_token("timo.lehtonen@gmail.com", SECRET)
    real_time = time.time  # captured before patching - the lambda below replaces time.time itself, so it must not call the (by-then-patched) module attribute recursively
    monkeypatch.setattr(time, "time", lambda: real_time() + 31 * 24 * 3600)  # 31 days later
    assert verify_session_token(token, SECRET) is None


def test_parse_allowed_emails_lowercases_and_strips() -> None:
    assert parse_allowed_emails(" Timo.Lehtonen@Gmail.com , other@example.com ") == {
        "timo.lehtonen@gmail.com",
        "other@example.com",
    }


def test_parse_allowed_emails_handles_empty_string() -> None:
    assert parse_allowed_emails("") == set()


# --- verify_microsoft_id_token -------------------------------------------
#
# jwt.PyJWKClient.get_signing_key_from_jwt and jwt.decode are mocked -
# they're network/crypto calls to Microsoft, not logic this module
# owns. What IS this module's own logic, and worth testing directly:
# the manual issuer-shape check (verify_iss is deliberately off in the
# real call, since this app is multi-tenant - see the module
# docstring) and wrapping every failure as InvalidMicrosoftToken.


def _mock_decode(monkeypatch: pytest.MonkeyPatch, claims: dict) -> None:
    monkeypatch.setattr("server.auth._jwks_client.get_signing_key_from_jwt", lambda token: MagicMock(key="fake-key"))
    monkeypatch.setattr("server.auth.jwt.decode", lambda *a, **kw: claims)


def test_verify_microsoft_id_token_accepts_a_valid_org_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_decode(monkeypatch, {
        "email": "timo.lehtonen@gmail.com",
        "iss": "https://login.microsoftonline.com/000a162a-b82a-40ea-b3b3-221ac455b21a/v2.0",
    })
    claims = verify_microsoft_id_token("fake-token", CLIENT_ID)
    assert claims["email"] == "timo.lehtonen@gmail.com"


def test_verify_microsoft_id_token_accepts_the_personal_account_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    # The fixed tenant ID Microsoft uses for personal (non-org)
    # Microsoft accounts - this app's multi-tenant registration
    # (AzureADandPersonalMicrosoftAccount) must accept it too.
    _mock_decode(monkeypatch, {
        "email": "timo.lehtonen@gmail.com",
        "iss": "https://login.microsoftonline.com/9188040d-6c67-4c5b-b112-36a304b66dad/v2.0",
    })
    claims = verify_microsoft_id_token("fake-token", CLIENT_ID)
    assert claims["email"] == "timo.lehtonen@gmail.com"


def test_verify_microsoft_id_token_rejects_an_unexpected_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_decode(monkeypatch, {"email": "attacker@example.com", "iss": "https://not-microsoft.example.com/v2.0"})
    with pytest.raises(InvalidMicrosoftToken):
        verify_microsoft_id_token("fake-token", CLIENT_ID)


def test_verify_microsoft_id_token_wraps_a_decode_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_invalid(*args, **kwargs):
        raise ValueError("signature verification failed")

    monkeypatch.setattr("server.auth._jwks_client.get_signing_key_from_jwt", lambda token: MagicMock(key="fake-key"))
    monkeypatch.setattr("server.auth.jwt.decode", raise_invalid)
    with pytest.raises(InvalidMicrosoftToken):
        verify_microsoft_id_token("fake-token", CLIENT_ID)
