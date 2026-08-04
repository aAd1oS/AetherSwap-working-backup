from diagnostics.steam_history_once import _classify


def test_classifies_rate_limit():
    assert "429 confirmed" in _classify(429, "Too Many Requests")


def test_classifies_login_redirect():
    result = _classify(302, "", "https://steamcommunity.com/login/home/")
    assert "Cookie is not accepted" in result


def test_classifies_valid_history_json():
    body = '{"success": true, "prices": [["Aug 04 2026", 1.23, "2"]]}'
    assert _classify(200, body).startswith("SUCCESS")