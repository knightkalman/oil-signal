#!/usr/bin/env python3
"""Reference client for 3rd-party agents consuming the encrypted Oil Signal API."""

from __future__ import annotations

import argparse
import json
import os
from typing import Callable
from urllib.request import Request, urlopen

from build_api import ENDPOINT_URL, decrypt_envelope


DEFAULT_ENVELOPE_URL = f"{ENDPOINT_URL.rstrip('/')}/index.json"
USER_AGENT = "oil-signal-agent-client/1.0"
HTTP_TIMEOUT_SECONDS = 20


def normalize_envelope_url(endpoint: str) -> str:
    cleaned = endpoint.strip()
    if cleaned.endswith(".json"):
        return cleaned
    return f"{cleaned.rstrip('/')}/index.json"


def fetch_oil_signal_api(
    api_key: str,
    endpoint: str = DEFAULT_ENVELOPE_URL,
    *,
    opener: Callable = urlopen,
) -> dict:
    """Fetch and decrypt the Oil Signal API payload using OIL_SIGNAL_API_KEY."""
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("A non-empty OIL_SIGNAL_API_KEY is required")

    url = normalize_envelope_url(endpoint)
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "Cache-Control": "no-cache",
            "User-Agent": USER_AGENT,
        },
    )
    with opener(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        envelope = json.loads(response.read().decode("utf-8"))
    return decrypt_envelope(envelope, api_key.strip())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch and decrypt the Oil Signal Agent API."
    )
    parser.add_argument(
        "--endpoint",
        default=DEFAULT_ENVELOPE_URL,
        help=f"API endpoint URL (default: {DEFAULT_ENVELOPE_URL}).",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key (defaults to OIL_SIGNAL_API_KEY environment variable).",
    )
    args = parser.parse_args()
    api_key = (args.api_key or os.environ.get("OIL_SIGNAL_API_KEY") or "").strip()
    if not api_key:
        parser.error("Provide --api-key or set OIL_SIGNAL_API_KEY")

    payload = fetch_oil_signal_api(api_key, args.endpoint)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

