import sys
import asyncio
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_static_route_serves_files_inside_web_root(tmp_path, monkeypatch):
    from app.routes import static

    web_dir = tmp_path / "web"
    web_dir.mkdir()
    asset = web_dir / "app.js"
    asset.write_text("console.log('ok')", encoding="utf-8")
    monkeypatch.setattr(static, "WEB_DIR", web_dir)

    response = static.static_or_index("app.js")

    assert Path(response.path) == asset


def test_static_route_blocks_path_traversal(tmp_path, monkeypatch):
    from app.routes import static

    web_dir = tmp_path / "web"
    web_dir.mkdir()
    index = web_dir / "index.html"
    index.write_text("<html></html>", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    monkeypatch.setattr(static, "WEB_DIR", web_dir)

    response = static.static_or_index("../secret.txt")

    assert Path(response.path) == index
    assert Path(response.path) != secret


async def _asgi_get(app, path):
    messages = []
    request_sent = False

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 28472),
        },
        receive,
        send,
    )
    status = next(message["status"] for message in messages if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return status, body


def test_local_read_only_api_smoke():
    from app import api

    responses = {}
    for path in (
        "/api/runtime",
        "/api/status",
        "/api/accounts",
        "/api/purchases",
        "/api/orders",
        "/api/stats",
        "/api/strategies",
    ):
        status, body = asyncio.run(_asgi_get(api.app, path))
        assert status == 200, path
        responses[path] = json.loads(body)

    accounts = responses["/api/accounts"]["accounts"]
    assert all("password" not in account for account in accounts)


def test_auto_listing_price_cap_is_present_in_settings_ui():
    root = Path(__file__).resolve().parent.parent
    html = (root / "web" / "index.html").read_text(encoding="utf-8")
    js = (root / "web" / "js" / "settings.js").read_text(encoding="utf-8")

    assert 'id="cfg-max-auto-listing-price-cny"' in html
    assert 'p.max_auto_listing_price_cny ?? 50' in js
    assert 'max_auto_listing_price_cny: readNumberInput("cfg-max-auto-listing-price-cny")' in js


def test_staged_listing_and_strategy_log_switches_are_bound_in_settings_ui():
    root = Path(__file__).resolve().parent.parent
    html = (root / "web" / "index.html").read_text(encoding="utf-8")
    js = (root / "web" / "js" / "settings.js").read_text(encoding="utf-8")

    for field_id, config_key in (
        ("cfg-stale-listing-staged-mode-enabled", "stale_listing_staged_mode_enabled"),
        ("cfg-strategy-module-logs-enabled", "strategy_module_logs_enabled"),
    ):
        assert f'id="{field_id}"' in html
        assert f'p.{config_key}' in js
        assert f'{config_key}: !!el("{field_id}")?.checked' in js
