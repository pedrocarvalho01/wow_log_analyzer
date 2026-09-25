"""OAuth client-credentials auth against the Warcraft Logs API v2."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

TOKEN_CACHE_PATH = Path(".token.json")
TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"
EXPIRY_SAFETY_MARGIN_SECONDS = 60


class AuthError(RuntimeError):
    pass


def _load_cached_token() -> dict | None:
    if not TOKEN_CACHE_PATH.exists():
        return None
    try:
        data = json.loads(TOKEN_CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if data.get("expires_at", 0) - EXPIRY_SAFETY_MARGIN_SECONDS > time.time():
        return data
    return None


def _fetch_new_token(client_id: str, client_secret: str) -> dict:
    response = requests.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
        timeout=30,
    )
    if response.status_code != 200:
        raise AuthError(
            f"WCL token request failed ({response.status_code}): {response.text}"
        )
    payload = response.json()
    access_token = payload.get("access_token")
    expires_in = payload.get("expires_in", 0)
    if not access_token:
        raise AuthError(f"WCL token response missing access_token: {payload}")
    return {
        "access_token": access_token,
        "expires_at": time.time() + expires_in,
    }


def get_token(force_refresh: bool = False) -> str:
    """Return a valid bearer token, using the on-disk cache when possible."""
    client_id = os.environ.get("WCL_CLIENT_ID")
    client_secret = os.environ.get("WCL_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise AuthError(
            "WCL_CLIENT_ID / WCL_CLIENT_SECRET are not set. Copy .env.example to "
            ".env and fill in credentials from https://www.warcraftlogs.com/api/clients"
        )

    if not force_refresh:
        cached = _load_cached_token()
        if cached:
            return cached["access_token"]

    token_data = _fetch_new_token(client_id, client_secret)
    TOKEN_CACHE_PATH.write_text(json.dumps(token_data), encoding="utf-8")
    return token_data["access_token"]
