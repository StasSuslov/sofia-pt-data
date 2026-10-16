"""One HTTP request for the alerts feed."""

import requests


def fetch(url: str, user_agent: str, timeout: int) -> bytes:
    """Fetch the feed once.

    Args:
        url: The alerts feed URL.
        user_agent: The city profile's User-Agent.
        timeout: Request timeout in seconds.

    Returns:
        The response body.
    """
    response = requests.get(
        url,
        timeout=timeout,
        headers={'User-Agent': user_agent},
    )
    response.raise_for_status()
    return response.content
