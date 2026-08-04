"""HTTP helpers that never inherit OS or process proxy environment variables."""

from __future__ import annotations

from typing import Any

import requests


def direct_request(method: str, url: str, **kwargs: Any) -> requests.Response:
    kwargs.pop("proxies", None)
    session = requests.Session()
    session.trust_env = False
    try:
        return session.request(method, url, proxies={}, **kwargs)
    finally:
        session.close()


def direct_get(url: str, **kwargs: Any) -> requests.Response:
    return direct_request("GET", url, **kwargs)
