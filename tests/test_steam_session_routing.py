from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _default_to_no_local_accelerator(monkeypatch):
    from steam import session as steam_session

    monkeypatch.setattr(steam_session, "_steam_local_accelerator_active", lambda: False)


class _FakeSession:
    def __init__(self):
        self.trust_env = None
        self.proxies = {}
        self.verify = True
        self.cookies = {}
        self.headers = {}


def test_market_session_inherits_system_route_without_project_proxy(monkeypatch):
    from steam import session as steam_session

    fake = _FakeSession()
    monkeypatch.setattr(steam_session.requests, "Session", lambda: fake)
    monkeypatch.setattr(
        "utils.proxy_manager.get_proxy_manager",
        lambda: SimpleNamespace(get_steam_proxies=lambda: None),
    )

    result = steam_session.create_market_session(
        "sessionid=test; steamLoginSecure=token",
        "76561198000000000",
    )

    assert result is fake
    assert fake.trust_env is True
    assert fake.proxies == {}


def test_market_session_bypasses_environment_for_local_accelerator(monkeypatch):
    from steam import session as steam_session

    fake = _FakeSession()
    monkeypatch.setattr(steam_session.requests, "Session", lambda: fake)
    monkeypatch.setattr(
        "utils.proxy_manager.get_proxy_manager",
        lambda: SimpleNamespace(get_steam_proxies=lambda: None),
    )
    monkeypatch.setattr(steam_session, "_steam_local_accelerator_active", lambda: True)

    result = steam_session.create_market_session(
        "sessionid=test; steamLoginSecure=token",
        "76561198000000000",
    )

    assert result is fake
    assert fake.trust_env is False
    assert fake.proxies == {}


def test_market_session_uses_project_proxy_without_environment_fallback(monkeypatch):
    from steam import session as steam_session

    configured = {
        "http": "http://127.0.0.1:7890",
        "https": "http://127.0.0.1:7890",
    }
    fake = _FakeSession()
    monkeypatch.setattr(steam_session.requests, "Session", lambda: fake)
    monkeypatch.setattr(
        "utils.proxy_manager.get_proxy_manager",
        lambda: SimpleNamespace(get_steam_proxies=lambda: configured),
    )

    steam_session.create_market_session(
        "sessionid=test; steamLoginSecure=token",
        "76561198000000000",
    )

    assert fake.trust_env is False
    assert fake.proxies == configured

def test_steam_confirmer_inherits_system_route_without_project_proxy(monkeypatch):
    from app import steam_confirm

    fake = _FakeSession()
    monkeypatch.setattr(steam_confirm.requests, "Session", lambda: fake)
    monkeypatch.setattr(
        "utils.proxy_manager.get_proxy_manager",
        lambda: SimpleNamespace(get_steam_proxies=lambda: None),
    )

    confirmer = steam_confirm.SteamConfirmer(
        "c2VjcmV0",
        "android:test",
        "76561198000000000",
        "sessionid=test; steamLoginSecure=token",
    )

    assert confirmer.session is fake
    assert fake.trust_env is True

def test_history_request_inherits_system_route_without_project_proxy(monkeypatch):
    from steam import client as steam_client

    class HistoryResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"success": True, "prices": [["Aug 03 2026", 1.23, "2"]]}

    class HistorySession(_FakeSession):
        def get(self, url, **kwargs):
            self.url = url
            self.kwargs = kwargs
            return HistoryResponse()

    fake = HistorySession()
    monkeypatch.setattr(steam_client.requests, "Session", lambda: fake)
    monkeypatch.setattr(
        "utils.proxy_manager.get_proxy_manager",
        lambda: SimpleNamespace(get_steam_proxies=lambda: None),
    )

    result, error = steam_client.fetch_history(
        "Dual Berettas | Cobalt Quartz (Factory New)",
        return_error=True,
    )

    assert error is None
    assert result == [["Aug 03 2026", 1.23, "2"]]
    assert fake.trust_env is True
    assert "/market/pricehistory/" in fake.url
    assert fake.kwargs["headers"]["Accept"] == "application/json, text/javascript, */*; q=0.01"
    assert fake.kwargs["headers"]["X-Requested-With"] == "XMLHttpRequest"
    assert fake.kwargs["headers"]["Referer"].endswith(
        "/market/listings/730/Dual%20Berettas%20%7C%20Cobalt%20Quartz%20%28Factory%20New%29"
    )
    assert fake.kwargs["allow_redirects"] is False
