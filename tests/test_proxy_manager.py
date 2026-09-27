import sys
import threading
from collections import Counter
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _构造代理管理器(entries: list):
    """用假数据直接构造 ProxyManager，跳过读配置文件"""
    from utils.proxy_manager import ProxyManager
    with patch.object(ProxyManager, "_reload", return_value=None):
        mgr = ProxyManager.__new__(ProxyManager)
        mgr._lock = threading.Lock()
        mgr._warming_up = False
        mgr._proxies = entries
        mgr._cached_strategy = 2  # 完全走代理
        mgr._cached_enabled = True
        mgr._cached_steam_route_mode = "project_proxy"
        mgr._proxy_configs = [p["config"] for p in entries]
        mgr._proxy_weights = [max(0, p["score"]) for p in entries]
        mgr._last_disabled_proxy_log_key = None
        mgr._last_disabled_proxy_log_at = 0.0
    return mgr


def test_代理_score为0的永远不被选():
    entries = [
        {"config": {"host": "好的", "port": 80},  "score": 1000},
        {"config": {"host": "坏的",  "port": 80},  "score": 0},
    ]
    mgr = _构造代理管理器(entries)
    结果 = [mgr.get_next_proxy_dict() for _ in range(200)]
    hosts = [d["http"].split("//")[1].split(":")[0] for d in 结果 if d]
    assert "坏的" not in hosts, f"score=0的代理不应被选中: {Counter(hosts)}"
    assert "好的" in hosts


def test_代理_空池返回None():
    mgr = _构造代理管理器([])
    assert mgr.get_next_proxy_dict() is None


def test_代理_全挂了返回None():
    # 所有代理score都是0
    entries = [
        {"config": {"host": "死的1", "port": 80}, "score": 0},
        {"config": {"host": "死的2", "port": 80}, "score": 0},
    ]
    mgr = _构造代理管理器(entries)
    assert mgr.get_next_proxy_dict() is None


def test_代理_高分代理被选得明显更多():
    # score=9000 vs score=100，跑500次看分布
    entries = [
        {"config": {"host": "快代理", "port": 80}, "score": 9000},
        {"config": {"host": "慢代理", "port": 80}, "score": 100},
    ]
    mgr = _构造代理管理器(entries)
    结果 = [mgr.get_next_proxy_dict() for _ in range(500)]
    hosts = [d["http"].split("//")[1].split(":")[0] for d in 结果 if d]
    counts = Counter(hosts)
    # 快代理至少应该比慢代理多5倍
    assert counts["快代理"] > counts["慢代理"] * 5, f"高分代理没被优先选: {counts}"


def test_代理配置重载后无需预热即可使用(monkeypatch):
    from utils import proxy_manager

    monkeypatch.setattr(
        proxy_manager,
        "_load_proxy_pool_cfg",
        lambda: {
            "enabled": True,
            "strategy": 2,
            "proxies": [{"host": "127.0.0.1", "port": 7890}],
        },
    )
    mgr = proxy_manager.ProxyManager()

    selected = mgr.get_proxies_for_request()
    assert selected == {
        "http": "http://127.0.0.1:7890/",
        "https": "http://127.0.0.1:7890/",
    }


def test_代理池关闭时不预热():
    entries = [
        {"config": {"host": "不会测速", "port": 80}, "score": 0},
    ]
    mgr = _构造代理管理器(entries)
    mgr._cached_enabled = False
    mgr._cached_strategy = 3
    with patch("utils.proxy_manager.test_one_proxy") as mock_test:
        mgr.warmup()
    mock_test.assert_not_called()
    assert mgr._warming_up is False


def test_steam_route_uses_pool_even_when_strategy_is_local_first():
    from utils.proxy_manager import ProxyManager

    mgr = ProxyManager.__new__(ProxyManager)
    mgr._lock = threading.Lock()
    mgr._proxies = [{"config": {"host": "127.0.0.1", "port": 7890}, "score": 1}]
    mgr._proxy_configs = [mgr._proxies[0]["config"]]
    mgr._proxy_weights = [1]
    mgr._cached_strategy = 1
    mgr._cached_enabled = True
    mgr._last_disabled_proxy_log_key = None
    mgr._last_disabled_proxy_log_at = 0.0

    assert mgr.get_proxies_for_request() is None
    assert mgr.get_steam_proxies() == {
        "http": "http://127.0.0.1:7890/",
        "https": "http://127.0.0.1:7890/",
    }


def test_steam_route_does_not_fall_back_to_direct_when_enabled_pool_is_empty():
    import pytest
    from utils.proxy_manager import ProxyManager

    mgr = ProxyManager.__new__(ProxyManager)
    mgr._lock = threading.Lock()
    mgr._proxies = []
    mgr._proxy_configs = []
    mgr._proxy_weights = []
    mgr._cached_strategy = 2
    mgr._cached_enabled = True

    with pytest.raises(RuntimeError, match="no usable proxy"):
        mgr.get_steam_proxies()


def test_steam_route_auto_prefers_ready_local_accelerator(monkeypatch):
    from utils.proxy_manager import ProxyManager

    mgr = _构造代理管理器([
        {"config": {"host": "127.0.0.1", "port": 7890}, "score": 1},
    ])
    mgr._cached_steam_route_mode = "auto"
    monkeypatch.setattr(
        "steam.session.get_local_steam_accelerator_status",
        lambda: {"active": True, "reason": "ready", "addresses": ["127.0.0.1"]},
    )

    route = mgr.get_steam_route()

    assert route["name"] == "local_accelerator"
    assert route["proxies"] == {"http": "", "https": "", "all": ""}


def test_steam_route_auto_falls_back_to_project_proxy(monkeypatch):
    mgr = _构造代理管理器([
        {"config": {"host": "127.0.0.1", "port": 7890}, "score": 1},
    ])
    mgr._cached_steam_route_mode = "auto"
    monkeypatch.setattr(
        "steam.session.get_local_steam_accelerator_status",
        lambda: {"active": False, "reason": "dns_not_loopback", "addresses": []},
    )

    route = mgr.get_steam_route()

    assert route["name"] == "project_proxy"
    assert route["proxies"]["https"] == "http://127.0.0.1:7890/"
