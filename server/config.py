"""Fail-fast env-var config for the snapshot.timolehtonen.tech gallery
(spec 095/096). Plain os.environ.get, raising AT IMPORT TIME when a
required value is missing rather than silently degrading - a missing
secret surfacing as an opaque 500 on the very first sign-in attempt is
much worse to debug on a cold-starting container than a clear startup
failure in the platform's own logs."""

from __future__ import annotations

import os


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


# spec 096: Microsoft Entra ID, not Google - see server/auth.py's
# module docstring for why.
MICROSOFT_CLIENT_ID = _require("SHOT_MICROSOFT_CLIENT_ID")
MICROSOFT_CLIENT_SECRET = _require("SHOT_MICROSOFT_CLIENT_SECRET")
ALLOWED_EMAILS = _require("SHOT_ALLOWED_EMAILS")
SESSION_SECRET = _require("SHOT_SESSION_SECRET")
# Spec 147: a plain shared secret the Android app sends as a Bearer token to
# POST /api/android/upload - not a session cookie, since the phone never does
# the Microsoft sign-in flow. Compared with secrets.compare_digest, not `==`
# (see server/main.py) - constant-time, the same reasoning PyJWT's own
# signature check already gets for free but a plain string compare wouldn't.
ANDROID_UPLOAD_TOKEN = _require("SHOT_ANDROID_UPLOAD_TOKEN")
