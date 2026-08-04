from utils import direct_http


def test_direct_request_ignores_proxy_environment(monkeypatch):
    captured = {}

    class _Response:
        pass

    class _Session:
        def __init__(self):
            self.trust_env = True

        def request(self, method, url, **kwargs):
            captured.update(method=method, url=url, trust_env=self.trust_env, **kwargs)
            return _Response()

        def close(self):
            captured["closed"] = True

    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9999")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    monkeypatch.setattr(direct_http.requests, "Session", _Session)

    direct_http.direct_get("https://buff.163.com/", timeout=3)

    assert captured["trust_env"] is False
    assert captured["proxies"] == {}
    assert captured["closed"] is True
