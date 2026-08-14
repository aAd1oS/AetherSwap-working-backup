from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def test_auth_flow_probe_is_bounded_and_redacts_sensitive_values():
    source = (ROOT / "diagnostics" / "steam_auth_flow_probe.py").read_text(encoding="utf-8")

    assert "for step in range(1, 5)" in source
    assert "allow_redirects=False" in source
    assert "safe_target" in source
    assert "response.text" not in source
    assert "raw_cookies" in source
    assert "print(raw_cookies)" not in source
    assert "raw_set_cookie_inventory" in source
    assert "request_cookie_inventory" in source
    assert "Expected browser profile exists" in source


def test_auth_flow_probe_redacts_query_values_and_cookie_values():
    from diagnostics.steam_auth_flow_probe import cookie_header_names, safe_target

    target = safe_target(
        "https://login.steampowered.com/jwt/refresh?redir=secret-url&auth=secret-token"
    )

    assert target == "https://login.steampowered.com/jwt/refresh?keys=auth,redir"
    assert "secret" not in target
    assert cookie_header_names("steamLoginSecure=secret; sessionid=also-secret") == (
        "steamLoginSecure, sessionid"
    )


def test_auth_flow_probe_reads_raw_set_cookie_metadata_without_values():
    from diagnostics.steam_auth_flow_probe import raw_set_cookie_inventory

    class Headers:
        @staticmethod
        def getlist(name):
            assert name == "Set-Cookie"
            return [
                "steamLoginSecure=secret-token; Domain=steamcommunity.com; Secure; HttpOnly; SameSite=None",
                "steamDidLoginRefresh=1; Path=/; Secure",
            ]

    response = SimpleNamespace(raw=SimpleNamespace(headers=Headers()), headers={})
    inventory = raw_set_cookie_inventory(response)

    assert "steamLoginSecure@steamcommunity.com" in inventory
    assert "steamDidLoginRefresh@<host-only>" in inventory
    assert "Secure" in inventory
    assert "HttpOnly" in inventory
    assert "secret-token" not in inventory
