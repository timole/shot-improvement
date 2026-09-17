"""server/config.py raises at IMPORT time if its required env vars are
missing (spec 095 - fail fast, deliberately) - these must be set
before anything imports server.main anywhere in the test session, so
this sets them here, at conftest module-load time, before pytest
collects/imports any test module in this directory."""

import os

os.environ.setdefault("SHOT_MICROSOFT_CLIENT_ID", "test-client-id")
os.environ.setdefault("SHOT_MICROSOFT_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("SHOT_ALLOWED_EMAILS", "timo.lehtonen@gmail.com")
os.environ.setdefault("SHOT_SESSION_SECRET", "test-session-secret")
