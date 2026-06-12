"""Google Sign-In identity for the API Lambda.

The browser obtains a Google ID token (a JWT, ~1h life) via Google Identity
Services and sends it as `Authorization: Bearer <token>`. We verify it locally
with google-auth (Google's signing certs are fetched once and cached), so there
is no per-request round trip to Google after warm-up.

Anonymous is first-class: a missing/expired/invalid token makes
`identity_from_event` return None and the caller falls back to the existing
anonymous flow. Auth is also disabled wholesale when GOOGLE_CLIENT_ID is unset,
so the stack runs unchanged before the OAuth client exists.

The HTTP API has no route-level authorizer (the deploy uses a single $default
route into the in-Lambda router), which is exactly why per-call verification
here is the right fit — public routes stay open, /me* gate on the result.
"""

import os

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

_VALID_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
_request = None  # cached transport (caches Google's certs across invocations)


def _transport():
    global _request
    if _request is None:
        _request = google_requests.Request()
    return _request


def verify_google_token(token: str) -> dict | None:
    """Return {sub,email,name,picture} for a valid Google ID token, else None.

    verify_oauth2_token validates the signature, expiry, and audience (against
    GOOGLE_CLIENT_ID); we additionally pin the issuer. Any failure -> None
    (anonymous), never an exception to the caller."""
    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    if not client_id or not token:
        return None
    try:
        claims = google_id_token.verify_oauth2_token(token, _transport(), client_id)
    except Exception:  # noqa: BLE001 — any verification failure => anonymous
        return None
    if claims.get("iss") not in _VALID_ISSUERS or not claims.get("sub"):
        return None
    return {
        "sub": claims["sub"],
        "email": claims.get("email", ""),
        "name": claims.get("name", ""),
        "picture": claims.get("picture", ""),
    }


def _bearer(event) -> str | None:
    """Pull the bearer token out of the (case-insensitive) Authorization header.
    HTTP API payload v2 lowercases header keys, but we normalize defensively."""
    headers = {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}
    raw = headers.get("authorization")
    if not raw:
        return None
    parts = raw.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


def identity_from_event(event) -> dict | None:
    """The signed-in user for this request, or None (anonymous)."""
    token = _bearer(event)
    return verify_google_token(token) if token else None
