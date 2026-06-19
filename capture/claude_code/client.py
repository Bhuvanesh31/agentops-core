"""HTTP client for submitting normalized events to the ingestion API."""

import time
from collections.abc import Callable

import httpx


def fetch_repositories(http: httpx.Client, api_url: str) -> list[dict]:
    """GET /repositories and return the JSON list."""
    response = http.get(f"{api_url.rstrip('/')}/repositories", timeout=30)
    response.raise_for_status()
    return response.json()


def post_event(
    http: httpx.Client,
    api_url: str,
    event: dict,
    retries: int = 3,
    backoff: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str, int]:
    """POST one event. Retries transport errors and 5xx; 4xx is terminal.

    Returns (status, http_status) with status in {"created","duplicate","error"}.
    Raises RuntimeError if retries are exhausted on a retryable failure.
    """
    url = f"{api_url.rstrip('/')}/runs/events"
    last_detail = ""
    for attempt in range(retries):
        try:
            response = http.post(url, json=event, timeout=30)
        except httpx.HTTPError as exc:
            last_detail = str(exc)
            sleep(backoff * (2**attempt))
            continue
        if response.status_code in (200, 201):
            return response.json().get("status", "error"), response.status_code
        if response.status_code >= 500:
            last_detail = f"server {response.status_code}"
            sleep(backoff * (2**attempt))
            continue
        return "error", response.status_code  # 4xx: non-retryable
    raise RuntimeError(f"post_event failed after {retries} attempts: {last_detail}")
