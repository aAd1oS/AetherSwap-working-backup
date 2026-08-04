from utils import service_check


class _Response:
    status_code = 200


class _ProxyManager:
    def get_steam_proxies(self):
        return {"http": "http://127.0.0.1:7890/", "https": "http://127.0.0.1:7890/"}


class _Throttle:
    def __init__(self):
        self.calls = []

    def wait(self, domain, seconds):
        self.calls.append((domain, seconds))


def test_steam_service_check_uses_steam_proxy_pool(monkeypatch):
    captured = {}
    throttle = _Throttle()
    monkeypatch.setattr(service_check, "get_proxy_manager", lambda: _ProxyManager())
    monkeypatch.setattr(service_check, "get_throttle", lambda: throttle)
    monkeypatch.setattr(
        service_check.requests,
        "get",
        lambda url, **kwargs: captured.update(url=url, **kwargs) or _Response(),
    )

    result = service_check._check_one(service_check.SERVICE_CHECKS[0], timeout=3)

    assert result["ok"] is True
    assert captured["proxies"]["https"] == "http://127.0.0.1:7890/"
    assert "AetherSwap-Service-Check" not in captured["headers"]["User-Agent"]
    assert throttle.calls == [("steamcommunity.com", 2.0)]


def test_buff_service_check_is_direct_and_obeys_global_throttle(monkeypatch):
    captured = {}
    throttle = _Throttle()
    monkeypatch.setattr(service_check, "get_throttle", lambda: throttle)
    monkeypatch.setattr(
        service_check,
        "direct_get",
        lambda url, **kwargs: captured.update(url=url, **kwargs) or _Response(),
    )

    result = service_check._check_one(service_check.SERVICE_CHECKS[2], timeout=3)

    assert result["ok"] is True
    assert result["route"] == "直连"
    assert "proxies" not in captured
    assert throttle.calls == [("buff.163.com", 3.0)]


def test_service_check_exposes_all_four_services(monkeypatch):
    monkeypatch.setattr(service_check, "_check_one", lambda spec, timeout: {"id": spec[0], "ok": True})

    result = service_check.check_services(timeout=1)

    assert result["ok"] is True
    assert [item["id"] for item in result["services"]] == [
        "steam_community",
        "steam_store",
        "buff_direct",
        "steamdt",
    ]


def test_steam_service_check_marks_429_as_rate_limited(monkeypatch):
    class RateLimitedResponse:
        status_code = 429

    monkeypatch.setattr(service_check, "get_proxy_manager", lambda: _ProxyManager())
    monkeypatch.setattr(service_check, "get_throttle", lambda: _Throttle())
    monkeypatch.setattr(service_check.requests, "get", lambda *args, **kwargs: RateLimitedResponse())

    result = service_check._check_one(service_check.SERVICE_CHECKS[0], timeout=3)

    assert result["ok"] is False
    assert result["rate_limited"] is True
    assert result["error"] == "HTTP 429"