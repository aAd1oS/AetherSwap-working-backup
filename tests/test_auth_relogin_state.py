import sys
import types
import threading
import requests


def test_manual_buff_cookie_requires_session(monkeypatch):
    from app.routes import auth

    saved = []
    monkeypatch.setattr(auth, "update_buff_creds", lambda cookie: saved.append(cookie))

    result = auth.api_auth_manual_cookie("buff", auth.ManualCookieBody(cookies="csrf_token=abc"))

    assert result["ok"] is False
    assert "session" in result["error"]
    assert saved == []


def test_manual_steam_cookie_saves_locally_without_profile_fetch(monkeypatch):
    from app.routes import auth

    saved = []
    account_updates = []
    monkeypatch.setattr(
        auth,
        "update_steam_creds",
        lambda cookie, session_id, steam_id=None: saved.append((cookie, session_id, steam_id)),
    )
    monkeypatch.setattr(
        auth,
        "get_current_account",
        lambda: {"id": "account-1", "steam_id": ""},
    )
    monkeypatch.setattr(
        auth,
        "fetch_steam_profile_via_api",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("profile fetch must stay deferred")),
    )
    monkeypatch.setattr(
        auth,
        "update_account",
        lambda account_id, **kwargs: account_updates.append((account_id, kwargs)),
    )
    result = auth.api_auth_manual_cookie(
        "steam",
        auth.ManualCookieBody(
            cookies="sessionid=local-session; steamLoginSecure=76561198000000000%7C%7Clocal-token"
        ),
    )

    assert result["ok"] is True
    assert result["verification_deferred"] is True
    assert result["steam_id"] == "76561198000000000"
    assert saved == [
        (
            "sessionid=local-session; steamLoginSecure=76561198000000000%7C%7Clocal-token",
            "local-session",
            "76561198000000000",
        )
    ]
    assert account_updates == [
        ("account-1", {"steam_id": "76561198000000000"})
    ]


def test_steam_browser_cookie_selection_keeps_refresh_and_prefers_community_login():
    from app.routes import auth

    selected = auth._select_steam_browser_cookies([
        {
            "name": "steamLoginSecure",
            "value": "store-token",
            "domain": ".steampowered.com",
        },
        {
            "name": "steamRefresh_steam",
            "value": "refresh-token",
            "domain": "login.steampowered.com",
        },
        {
            "name": "steamLoginSecure",
            "value": "community-token",
            "domain": "steamcommunity.com",
        },
        {
            "name": "sessionid",
            "value": "community-session",
            "domain": "steamcommunity.com",
        },
        {"name": "unrelated", "value": "ignored", "domain": "example.com"},
    ])
    values = {cookie["name"]: cookie["value"] for cookie in selected}

    assert values["steamLoginSecure"] == "community-token"
    assert values["steamRefresh_steam"] == "refresh-token"
    assert values["sessionid"] == "community-session"
    assert "unrelated" not in values


def test_steam_auth_cookie_scope_allows_jwt_cookie_replacement():
    from app.services import steam_auth

    session = requests.Session()
    steam_auth._load_steam_auth_cookies(
        session,
        {
            "steamLoginSecure": "old-token",
            "sessionid": "session-value",
            "steamRefresh_steam": "refresh-value",
        },
    )
    session.cookies.set(
        "steamLoginSecure",
        "new-token",
        domain=".steamcommunity.com",
        path="/",
    )

    community_request = session.prepare_request(
        requests.Request("GET", "https://steamcommunity.com/my/profile")
    )
    login_request = session.prepare_request(
        requests.Request("GET", "https://login.steampowered.com/jwt/refresh")
    )

    assert community_request.headers["Cookie"].count("steamLoginSecure=") == 1
    assert "steamLoginSecure=new-token" in community_request.headers["Cookie"]
    assert "steamRefresh_steam=refresh-value" in login_request.headers["Cookie"]
    assert "steamLoginSecure=" not in login_request.headers["Cookie"]


def test_relogin_finish_surfaces_worker_error(monkeypatch):
    from app.routes import auth

    done = threading.Event()
    done.set()
    wake = threading.Event()

    monkeypatch.setattr(auth, "_relogin_context", object())
    monkeypatch.setattr(auth, "_relogin_error", "missing login cookie")
    monkeypatch.setattr(auth, "_relogin_done", done)
    monkeypatch.setattr(auth, "_relogin_wake", wake)

    result = auth._relogin_finish(True)

    assert result == {"ok": False, "error": "missing login cookie"}
    assert wake.is_set()


def test_buff_auto_relogin_success_clears_auth_and_verification(monkeypatch, tmp_path):
    from app.services import buff_auth

    calls = []

    class FakePage:
        def goto(self, *args, **kwargs):
            return None

        def wait_for_timeout(self, *args, **kwargs):
            return None

    class FakeContext:
        pages = [FakePage()]

        def cookies(self):
            return [
                {"name": "session", "value": "ok"},
                {"name": "csrf_token", "value": "csrf"},
            ]

        def close(self):
            return None

    class FakeChromium:
        def launch_persistent_context(self, *args, **kwargs):
            return FakeContext()

    class FakePlaywright:
        def __enter__(self):
            return types.SimpleNamespace(chromium=FakeChromium())

        def __exit__(self, exc_type, exc, tb):
            return False

    playwright_pkg = types.ModuleType("playwright")
    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: FakePlaywright()
    monkeypatch.setitem(sys.modules, "playwright", playwright_pkg)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    monkeypatch.setattr(buff_auth, "get_buff_credentials", lambda: {"cookies": "session=old"})
    monkeypatch.setattr(buff_auth, "get_current_id", lambda: "account-a")
    monkeypatch.setattr(buff_auth, "get_buff_profile_dir", lambda *_args: tmp_path / "buff-profile")
    monkeypatch.setattr(buff_auth, "update_buff_creds", lambda cookie: calls.append(("update", cookie)))
    monkeypatch.setattr(buff_auth, "set_buff_auth_expired", lambda value: calls.append(("auth", value)))
    monkeypatch.setattr(
        buff_auth,
        "set_buff_verification_required",
        lambda value, reason="": calls.append(("verify", value, reason)),
    )

    result = buff_auth._try_buff_auto_relogin_impl()

    assert result[0] is True
    assert ("auth", False) in calls
    assert ("verify", False, "") in calls


def test_relogin_context_retries_with_temp_profile(monkeypatch, tmp_path):
    from app.routes import auth

    calls = []

    class FakeChromium:
        def launch_persistent_context(self, profile_dir, **kwargs):
            calls.append(profile_dir)
            if len(calls) == 1:
                raise RuntimeError("BrowserType.launch_persistent_context: Target page, context or browser has been closed")
            return object()

    temp_profile = tmp_path / "temp_profile"
    monkeypatch.setattr(auth.tempfile, "mkdtemp", lambda prefix, dir: str(temp_profile))

    context, temp_dir = auth._launch_relogin_context(
        types.SimpleNamespace(chromium=FakeChromium()),
        tmp_path / "playwright_buff",
        "buff",
    )

    assert context is not None
    assert temp_dir == temp_profile
    assert len(calls) == 2


def test_browser_launch_error_is_user_friendly():
    from app.routes import auth

    raw = (
        "BrowserType.launch_persistent_context: Target page, context or browser has been closed\n"
        "Browser logs:\n<launching> very long chromium command"
    )

    message = auth._friendly_browser_launch_error(RuntimeError(raw), "buff", retried=True)

    assert "Buff" in message
    assert "完整错误见调试日志" in message
    assert "Browser logs" not in message


def test_steam_cookie_verification_uses_community_as_source_of_truth(monkeypatch):
    from app.services import steam_auth
    from steam import session as steam_session
    import utils.proxy_manager as proxy_manager

    monkeypatch.setattr(steam_session, "_steam_local_accelerator_active", lambda: False)
    sessions = []

    class FakeResponse:
        def __init__(self, status_code=200, url="", text="", headers=None):
            self.status_code = status_code
            self.url = url
            self.text = text
            self.headers = headers or {}

    class FakeSession:
        def __init__(self, community_result):
            self.community_result = community_result
            self.trust_env = None
            self.verify = True
            self.proxies = {}
            self.cookies = {}
            self.headers = {}
            sessions.append(self)

        def get(self, url, **kwargs):
            assert "steamcommunity.com" in url
            if self.community_result == "exception":
                raise RuntimeError("community unavailable")
            if self.community_result == "login":
                return FakeResponse(200, "https://steamcommunity.com/login/home/")
            if self.community_result == "429":
                return FakeResponse(429, "https://steamcommunity.com/my/profile")
            return FakeResponse(200, "https://steamcommunity.com/profiles/76561198000000000")

    monkeypatch.setattr(
        proxy_manager,
        "get_proxy_manager",
        lambda: types.SimpleNamespace(
            get_steam_proxies=lambda: {"http": None, "https": None, "all": None}
        ),
    )
    for community_result, expected_status, expected_valid in (
        ("login", "invalid", False),
        ("429", "rate_limited", False),
        ("exception", "unavailable", False),
        ("ok", "valid", True),
    ):
        monkeypatch.setattr(
            steam_auth._req,
            "Session",
            lambda result=community_result: FakeSession(result),
        )
        status, reason = steam_auth._check_steam_cookies("steamLoginSecure=token")
        assert status == expected_status
        assert reason
        assert steam_auth._verify_steam_cookies_valid("steamLoginSecure=token") is expected_valid
        assert sessions[-1].trust_env is True


def test_steam_cookie_verification_accepts_profile_redirect_without_following(monkeypatch):
    from app.services import steam_auth

    calls = []

    class FakeResponse:
        status_code = 302
        url = "https://steamcommunity.com/my/profile"
        text = ""
        headers = {"Location": "/profiles/76561198000000000/"}

    class FakeSession:
        verify = True
        trust_env = None
        proxies = {}
        cookies = {}
        headers = {}

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

    monkeypatch.setattr(steam_auth, "_configure_steam_session", lambda session: None)
    monkeypatch.setattr(steam_auth._req, "Session", FakeSession)

    status, reason = steam_auth._check_steam_cookies(
        "steamLoginSecure=token",
        "76561198000000000",
    )

    assert status == "valid"
    assert "账号主页" in reason
    assert len(calls) == 1
    assert calls[0][1]["allow_redirects"] is False


def test_steam_cookie_verification_rejects_login_and_unsafe_redirects(monkeypatch):
    from app.services import steam_auth

    class FakeResponse:
        status_code = 302
        url = "https://steamcommunity.com/my/profile"
        text = ""

        def __init__(self, location):
            self.headers = {"Location": location}

    class FakeSession:
        verify = True
        trust_env = None
        proxies = {}
        cookies = {}
        headers = {}

        def __init__(self, location):
            self.location = location

        def get(self, url, **kwargs):
            return FakeResponse(self.location)

    monkeypatch.setattr(steam_auth, "_configure_steam_session", lambda session: None)

    for location, expected in (
        ("/login/home/", "invalid"),
        ("https://store.steampowered.com/login/", "invalid"),
        ("https://example.com/profiles/76561198000000000/", "unavailable"),
        ("/profiles/76561198000000001/", "invalid"),
    ):
        monkeypatch.setattr(steam_auth._req, "Session", lambda target=location: FakeSession(target))
        status, reason = steam_auth._check_steam_cookies(
            "steamLoginSecure=token",
            "76561198000000000",
        )
        assert status == expected
        assert reason


def test_steam_cookie_verification_accepts_same_host_http_profile_redirect(monkeypatch):
    from app.services import steam_auth

    class FakeResponse:
        status_code = 302
        url = "https://steamcommunity.com/my/profile"
        text = ""
        headers = {"Location": "http://steamcommunity.com/profiles/76561198000000000/"}

    class FakeSession:
        verify = True
        trust_env = None
        proxies = {}
        cookies = {}
        headers = {}

        def get(self, url, **kwargs):
            assert kwargs["allow_redirects"] is False
            return FakeResponse()

    monkeypatch.setattr(steam_auth, "_configure_steam_session", lambda session: None)
    monkeypatch.setattr(steam_auth._req, "Session", FakeSession)

    status, reason = steam_auth._check_steam_cookies(
        "steamLoginSecure=token",
        "76561198000000000",
    )

    assert status == "valid"
    assert "账号主页" in reason


def test_steam_cookie_verification_allows_one_bounded_jwt_refresh(monkeypatch):
    from app.services import steam_auth

    calls = []

    class FakeResponse:
        status_code = 302
        text = ""

        def __init__(self, url, location):
            self.url = url
            self.headers = {"Location": location}

    class FakeSession:
        verify = True
        trust_env = None
        proxies = {}
        cookies = {}
        headers = {}

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            if len(calls) == 1:
                return FakeResponse(
                    "https://steamcommunity.com/my/profile",
                    "https://login.steampowered.com/jwt/refresh?redir=hidden",
                )
            if len(calls) == 2:
                return FakeResponse(
                    "https://login.steampowered.com/jwt/refresh?redir=hidden",
                    "https://steamcommunity.com/my/profile",
                )
            return FakeResponse(
                "https://steamcommunity.com/my/profile",
                "https://steamcommunity.com/profiles/76561198000000000/",
            )

    monkeypatch.setattr(steam_auth, "_configure_steam_session", lambda session: None)
    monkeypatch.setattr(steam_auth._req, "Session", FakeSession)

    status, reason = steam_auth._check_steam_cookies(
        "steamLoginSecure=token",
        "76561198000000000",
    )

    assert status == "valid"
    assert "刷新并验证成功" in reason
    assert len(calls) == 3
    assert all(kwargs["allow_redirects"] is False for _, kwargs in calls)


def test_steam_cookie_verification_stops_repeated_jwt_refresh(monkeypatch):
    from app.services import steam_auth

    calls = []

    class FakeResponse:
        status_code = 302
        text = ""

        def __init__(self, url):
            self.url = url
            self.headers = {"Location": "https://login.steampowered.com/jwt/refresh?redir=hidden"}

    class FakeSession:
        verify = True
        trust_env = None
        proxies = {}
        cookies = {}
        headers = {}

        def get(self, url, **kwargs):
            calls.append(url)
            if "/market/" in url:
                response = FakeResponse(url)
                response.status_code = 200
                response.headers = {}
                response.text = 'var g_steamID = "76561198000000000";'
                return response
            return FakeResponse(url)

    monkeypatch.setattr(steam_auth, "_configure_steam_session", lambda session: None)
    monkeypatch.setattr(steam_auth._req, "Session", FakeSession)

    status, reason = steam_auth._check_steam_cookies(
        "steamLoginSecure=token",
        "76561198000000000",
    )

    assert status == "valid"
    assert "Market 登录态验证通过" in reason
    assert len(calls) == 3


def test_steam_cookie_verification_keeps_loop_unavailable_when_market_is_ambiguous(monkeypatch):
    from app.services import steam_auth

    calls = []

    class FakeResponse:
        text = ""

        def __init__(self, url, status_code=302, location=""):
            self.url = url
            self.status_code = status_code
            self.headers = {"Location": location} if location else {}

    class FakeSession:
        verify = True
        trust_env = None
        proxies = {}
        cookies = {}
        headers = {}

        def get(self, url, **kwargs):
            calls.append(url)
            if "/market/" in url:
                return FakeResponse(url, status_code=200)
            return FakeResponse(
                url,
                location="https://login.steampowered.com/jwt/refresh?redir=hidden",
            )

    monkeypatch.setattr(steam_auth, "_configure_steam_session", lambda session: None)
    monkeypatch.setattr(steam_auth._req, "Session", FakeSession)

    status, reason = steam_auth._check_steam_cookies(
        "steamLoginSecure=token",
        "76561198000000000",
    )

    assert status == "unavailable"
    assert "刷新出现循环" in reason
    assert "未返回明确登录状态" in reason
    assert len(calls) == 3


def test_steam_cookie_verification_uses_configured_project_proxy(monkeypatch):
    from app.services import steam_auth
    import utils.proxy_manager as proxy_manager

    configured = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}

    class FakeResponse:
        status_code = 200

        def __init__(self, url, payload=None):
            self.url = url
            self._payload = payload or {}

        def json(self):
            return self._payload

    class FakeSession:
        def __init__(self):
            self.trust_env = None
            self.verify = True
            self.proxies = {}
            self.cookies = {}
            self.headers = {}

        def get(self, url, **kwargs):
            if "store.steampowered.com" in url:
                return FakeResponse(url, {"logged_in": True})
            return FakeResponse("https://steamcommunity.com/profiles/76561198000000000")

    session = FakeSession()
    monkeypatch.setattr(
        proxy_manager,
        "get_proxy_manager",
        lambda: types.SimpleNamespace(get_steam_proxies=lambda: configured),
    )
    monkeypatch.setattr(steam_auth._req, "Session", lambda: session)

    assert steam_auth._verify_steam_cookies_valid("steamLoginSecure=token") is True
    assert session.trust_env is False
    assert session.proxies == configured


def test_verify_steam_with_cookie_defers_to_business_request_without_http_login(monkeypatch):
    from app.services import steam_auth
    from app import account_scope

    monkeypatch.setattr(
        steam_auth,
        "get_account",
        lambda account_id: {
            "id": account_id,
            "steam_id": "76561198000000000",
            "username": "user",
            "password": "password",
        },
    )
    monkeypatch.setattr(steam_auth, "set_current", lambda account_id: True)
    monkeypatch.setattr(
        steam_auth,
        "get_steam_credentials",
        lambda: {
            "cookies": "steamLoginSecure=76561198000000000%7C%7Ctoken",
            "steam_id": "76561198000000000",
        },
    )
    monkeypatch.setattr(
        steam_auth,
        "_check_steam_cookies",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("dedicated HTTP verification must not run")),
    )
    monkeypatch.setattr(account_scope, "get_account_runtime_status", lambda account_id: {"steam_session": {"status": "pending"}})
    monkeypatch.setattr(
        steam_auth,
        "_do_steampy_login",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("pending evidence must not trigger login")),
    )

    result = steam_auth.verify_steam_auto_login("account-1")

    assert result["ok"] is True
    assert result["status"] == "verification_deferred"
    assert "真实业务请求确认" in result["message"]


def test_background_keepalive_defers_without_http_or_password_login(monkeypatch):
    from app.services import steam_auth
    from app import account_scope

    monkeypatch.setattr(
        steam_auth,
        "get_current_account",
        lambda: {
            "id": "account-1",
            "steam_id": "76561198000000000",
            "username": "user",
            "password": "password",
        },
    )
    monkeypatch.setattr(steam_auth, "set_current", lambda account_id: True)
    monkeypatch.setattr(
        steam_auth,
        "get_steam_credentials",
        lambda: {
            "cookies": "steamLoginSecure=76561198000000000%7C%7Ctoken",
            "steam_id": "76561198000000000",
        },
    )
    monkeypatch.setattr(
        steam_auth,
        "_check_steam_cookies",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("dedicated HTTP verification must not run")),
    )
    monkeypatch.setattr(account_scope, "get_account_runtime_status", lambda account_id: {"steam_session": {"status": "pending"}})
    monkeypatch.setattr(
        steam_auth,
        "_do_steampy_login",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("keepalive must not trigger login")),
    )

    result = steam_auth._try_steam_auto_relogin_impl()

    assert result[0] is False
    assert result[1] == "verification_deferred"
    assert "真实业务请求确认" in result[2]


def test_explicit_auth_expiry_can_force_login_without_dedicated_http_check(monkeypatch):
    from app.services import steam_auth

    calls = []
    monkeypatch.setattr(
        steam_auth,
        "get_current_account",
        lambda: {
            "id": "account-1",
            "steam_id": "76561198000000000",
            "username": "user",
            "password": "password",
        },
    )
    monkeypatch.setattr(steam_auth, "set_current", lambda account_id: True)
    monkeypatch.setattr(
        steam_auth,
        "get_steam_credentials",
        lambda: {
            "cookies": "steamLoginSecure=76561198000000000%7C%7Cold-token",
            "steam_id": "76561198000000000",
        },
    )
    monkeypatch.setattr(
        steam_auth,
        "_check_steam_cookies",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("force login must bypass redirect verification")),
    )
    monkeypatch.setattr(steam_auth, "load_app_config_validated", lambda: {})
    monkeypatch.setattr(
        steam_auth,
        "_do_steampy_login",
        lambda *args, **kwargs: (
            calls.append("login")
            or (True, "", {"sessionid": "new-session", "steamLoginSecure": "76561198000000000%7C%7Cnew-token"})
        ),
    )
    monkeypatch.setattr(steam_auth, "update_steam_creds", lambda *args, **kwargs: calls.append("saved"))
    monkeypatch.setattr(steam_auth, "fetch_steam_profile_via_api", lambda *args, **kwargs: ("User", "avatar"))
    monkeypatch.setattr(steam_auth, "update_account", lambda *args, **kwargs: None)

    result = steam_auth._try_steam_auto_relogin_impl(force_login=True)

    assert result[0] is True
    assert result[1] == "auto_ok"
    assert calls == ["login", "saved"]


def test_steam_auto_relogin_does_not_reuse_cookie_from_different_account(monkeypatch):
    from app.services import steam_auth

    calls = []
    monkeypatch.setattr(
        steam_auth,
        "get_current_account",
        lambda: {"id": "acc-new", "username": "new-user", "password": "pw", "steam_id": "222"},
    )
    monkeypatch.setattr(steam_auth, "set_current", lambda account_id: calls.append(("set_current", account_id)))
    monkeypatch.setattr(
        steam_auth,
        "get_steam_credentials",
        lambda: {
            "cookies": "sessionid=old; steamLoginSecure=111%7C%7Cold-token",
            "steam_id": "111",
        },
    )
    monkeypatch.setattr(
        steam_auth,
        "_verify_steam_cookies_valid",
        lambda cookies: (_ for _ in ()).throw(AssertionError("mismatched cookie must not be reused")),
    )
    monkeypatch.setattr(
        steam_auth,
        "_do_steampy_login",
        lambda username, password, guard: (
            True,
            "",
            {"sessionid": "new-session", "steamLoginSecure": "222%7C%7Cnew-token"},
        ),
    )
    monkeypatch.setattr(
        steam_auth,
        "update_steam_creds",
        lambda cookie, session_id, steam_id=None: calls.append(("update_creds", cookie, session_id, steam_id)),
    )
    monkeypatch.setattr(steam_auth, "fetch_steam_profile_via_api", lambda steam_id, cookies: ("New User", "avatar"))
    monkeypatch.setattr(
        steam_auth,
        "update_account",
        lambda account_id, **kwargs: calls.append(("update_account", account_id, kwargs)),
    )
    monkeypatch.setattr(steam_auth, "load_app_config_validated", lambda: {})

    result = steam_auth._try_steam_auto_relogin_impl()

    assert result[0] is True
    assert any(call[0] == "update_creds" and "222%7C%7Cnew-token" in call[1] for call in calls)
