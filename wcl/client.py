"""GraphQL POST helper for the Warcraft Logs API v2, with retries and
rate-limit backoff."""
from __future__ import annotations

import time

import requests

from wcl.auth import get_token

API_URL = "https://www.warcraftlogs.com/api/v2/client"
MAX_RETRIES = 5
BASE_BACKOFF_SECONDS = 2.0


class GraphQLError(RuntimeError):
    def __init__(self, message: str, errors: list | None = None):
        super().__init__(message)
        self.errors = errors or []


def graphql(query: str, variables: dict | None = None) -> dict:
    """POST a GraphQL query, returning the `data` payload.

    Retries on 429 (rate limit) and transient 5xx errors with exponential
    backoff. Refreshes the bearer token once on a 401.
    """
    variables = variables or {}
    token = get_token()
    refreshed_once = False

    for attempt in range(1, MAX_RETRIES + 1):
        response = requests.post(
            API_URL,
            json={"query": query, "variables": variables},
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
        )

        if response.status_code == 401 and not refreshed_once:
            token = get_token(force_refresh=True)
            refreshed_once = True
            continue

        if response.status_code == 429:
            retry_after = float(response.headers.get("Retry-After", BASE_BACKOFF_SECONDS * attempt))
            time.sleep(retry_after)
            continue

        if response.status_code >= 500:
            time.sleep(BASE_BACKOFF_SECONDS * attempt)
            continue

        if response.status_code != 200:
            raise GraphQLError(
                f"WCL API request failed ({response.status_code}): {response.text}"
            )

        payload = response.json()
        if "errors" in payload and payload["errors"]:
            raise GraphQLError(
                f"WCL API returned GraphQL errors: {payload['errors']}",
                errors=payload["errors"],
            )

        rate_limit = payload.get("data", {}).get("rateLimitData")
        if rate_limit and rate_limit.get("pointsSpentThisHour", 0) >= rate_limit.get(
            "limitPerHour", float("inf")
        ) * 0.95:
            # Getting close to the hourly points budget; slow down proactively.
            time.sleep(1.0)

        return payload["data"]

    raise GraphQLError(f"WCL API request failed after {MAX_RETRIES} retries")
