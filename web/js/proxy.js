
let _proxyList = [];
function proxyUrl(entry) {
    if (entry.username && entry.password) {
        return `${entry.host}:${entry.port} (${entry.username})`;
    }
    return `${entry.host}:${entry.port}`;
}
async function loadProxyConfig() {
    try {
        const d = await fetchJson(API + "/proxy/config");
        const cfg = d.proxy_pool || {};
        _proxyList = (cfg.proxies || []).map(p => ({ ...p }));
        const strategy = cfg.strategy ?? 1;
        selectStrategy(strategy);
        const testUrlEl = el("proxy-test-url");
        if (testUrlEl) testUrlEl.value = cfg.test_url || "https://ipv4.webshare.io/";
        const timeoutEl = el("proxy-timeout");
        if (timeoutEl) timeoutEl.value = cfg.timeout_seconds ?? 10;
        const wsKeyEl = el("proxy-webshare-apikey");
        if (wsKeyEl) wsKeyEl.value = cfg.webshare_api_key || "";
        renderProxyList();
    } catch (e) {
        console.error("加载代理配置失败", e);
    }
}
function renderProxyList(testResults) {
    const tbody = el("proxy-list-tbody");
    if (!tbody) return;
    const resultMap = {};
    if (testResults) {
        testResults.forEach(r => { resultMap[`${r.host}:${r.port}`] = r; });
    }
    if (_proxyList.length === 0) {
        tbody.innerHTML = `
      <tr>
        <td colspan="6" style="text-align:center;opacity:.5;padding:24px 0;">
          暂无代理，请添加
        </td>
      </tr>`;
        return;
    }
    tbody.innerHTML = _proxyList.map((p, i) => {
        const key = `${p.host}:${p.port}`;
        const r = resultMap[key];
        let statusHtml = `<span class="proxy-badge proxy-badge--idle">未测试</span>`;
        if (r) {
            if (r.status === "ok") {
                statusHtml = `<span class="proxy-badge proxy-badge--ok">✓ 正常</span>`;
            } else {
                statusHtml = `<span class="proxy-badge proxy-badge--fail" title="${r.error || ""}">✗ 失败</span>`;
            }
        }
        const latency = r && r.status === "ok"
            ? `<span class="proxy-latency">${r.latency_ms} ms</span>`
            : `<span style="opacity:.35">—</span>`;
        const detectedIp = r && r.ip_detected
            ? `<code class="proxy-detected-ip">${r.ip_detected}</code>`
            : `<span style="opacity:.35">—</span>`;
        return `<tr data-idx="${i}">
      <td><span class="proxy-index">#${i + 1}</span></td>
      <td><code>${p.host}:${p.port}</code></td>
      <td><span style="opacity:.6">${p.username || "—"}</span></td>
      <td>${statusHtml}</td>
      <td>${latency}</td>
      <td>${detectedIp}</td>
      <td>
        <button class="btn btn-sm btn-danger-outline" onclick="removeProxy(${i})">删除</button>
      </td>
    </tr>`;
    }).join("");
}
function addSingleProxy() {
    const host = (el("proxy-add-host")?.value || "").trim();
    const port = parseInt(el("proxy-add-port")?.value || "0", 10);
    const user = (el("proxy-add-user")?.value || "").trim();
    const pass = (el("proxy-add-pass")?.value || "").trim();
    if (!host || !port) {
        toast("请填写主机和端口", "主机和端口为必填项");
        return;
    }
    _proxyList.push({ host, port, username: user, password: pass });
    renderProxyList();
    ["proxy-add-host", "proxy-add-port", "proxy-add-user", "proxy-add-pass"].forEach(id => {
        const e = el(id);
        if (e) e.value = "";
    });
    toast("已添加", `${host}:${port}`);
}
function parseBulkProxyImport() {
    const raw = (el("proxy-bulk-input")?.value || "").trim();
    if (!raw) { toast("请输入代理列表"); return; }
    const lines = raw.split(/[\n\r]+/).map(l => l.trim()).filter(Boolean);
    let added = 0;
    const errors = [];
    lines.forEach((line, i) => {
        const parts = line.split(":");
        if (parts.length < 2) {
            errors.push(`行${i + 1}: 格式错误`);
            return;
        }
        const host = parts[0].trim();
        const port = parseInt(parts[1], 10);
        const username = parts[2]?.trim() || "";
        const password = parts[3]?.trim() || "";
        if (!host || isNaN(port)) {
            errors.push(`行${i + 1}: IP 或端口无效`);
            return;
        }
        const dup = _proxyList.some(p => p.host === host && p.port === port);
        if (dup) return;
        _proxyList.push({ host, port, username, password });
        added++;
    });
    renderProxyList();
    const el_bulk = el("proxy-bulk-input");
    if (el_bulk) el_bulk.value = "";
    if (errors.length > 0) {
        toast(`已导入 ${added} 条，${errors.length} 条失败`, errors.slice(0, 3).join("; "));
    } else {
        toast(`已导入 ${added} 条代理`);
    }
}
function removeProxy(idx) {
    _proxyList.splice(idx, 1);
    renderProxyList();
}
async function testAllProxies() {
    if (_proxyList.length === 0) {
        toast("代理列表为空", "请先添加代理 IP");
        return;
    }
    const btn = el("btn-proxy-test");
    if (btn) {
        btn.disabled = true;
        btn.textContent = "测试中...";
    }
    await _doSaveProxyConfig();
    try {
        const d = await fetchJson(API + "/proxy/test", { method: "POST" });
        renderProxyList(d.results || []);
        const ok = (d.results || []).filter(r => r.status === "ok").length;
        const fail = (d.results || []).length - ok;
        toast(`测试完成：${ok} 成功 / ${fail} 失败`);
    } catch (e) {
        toast("测试失败", e.message || "请检查后端日志");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = "测试连通性";
        }
    }
}

async function checkNetworkServices() {
    const btn = el("btn-service-check");
    const target = el("service-check-results");
    if (btn) {
        btn.disabled = true;
        btn.textContent = "检查中...";
    }
    if (target) target.innerHTML = `<div style="opacity:.65">正在检查四项服务...</div>`;
    try {
        const data = await fetchJson(API + "/network/services/check", { method: "POST" });
        const services = data.services || [];
        if (target) {
            target.innerHTML = services.map(service => {
                const badgeClass = service.ok ? "proxy-badge--ok" : "proxy-badge--fail";
                const badgeText = service.rate_limited ? "受限" : (service.ok ? "正常" : "失败");
                const detail = service.ok
                    ? `HTTP ${service.http_status} · ${service.latency_ms} ms`
                    : (service.error || "连接失败");
                return `<div style="border:1px solid var(--border);border-radius:8px;padding:12px">
                  <div style="display:flex;justify-content:space-between;gap:8px;align-items:center">
                    <strong>${escapeHtml(service.label || service.id)}</strong>
                    <span class="proxy-badge ${badgeClass}">${badgeText}</span>
                  </div>
                  <div style="margin-top:8px;font-size:12px;opacity:.72">${escapeHtml(service.route || "")}</div>
                  <div style="margin-top:4px;font-size:12px;word-break:break-word">${escapeHtml(detail)}</div>
                </div>`;
            }).join("") || `<div style="opacity:.65">后端未返回检查结果</div>`;
        }
        toast(data.ok ? "四项服务均可用" : "部分服务不可用", data.ok ? "网络路径符合配置" : "请查看各服务的错误详情");
    } catch (e) {
        if (target) target.innerHTML = `<div style="color:var(--danger)">${escapeHtml(e.message || "自检失败")}</div>`;
        toast("服务自检失败", e.message || "请检查后端日志");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = "检查四项服务";
        }
    }
}
async function _doSaveProxyConfig() {
    const strategy = Number(
        document.querySelector(".proxy-strategy-card.active")?.dataset?.strategy ?? 1
    );
    const enabled = strategy !== 3;
    const testUrl = el("proxy-test-url")?.value?.trim() || "https://ipv4.webshare.io/";
    const timeout = parseInt(el("proxy-timeout")?.value || "10", 10);
    const webshareApiKey = el("proxy-webshare-apikey")?.value?.trim() || "";
    await fetchJson(API + "/proxy/config", {
        method: "POST",
        body: JSON.stringify({
            proxy_pool: {
                enabled,
                strategy,
                test_url: testUrl,
                timeout_seconds: timeout,
                webshare_api_key: webshareApiKey,
                proxies: _proxyList,
            }
        })
    });
}
async function saveProxyConfig() {
    try {
        await _doSaveProxyConfig();
        toast("代理池配置已保存");
    } catch (e) {
        toast("保存失败", e.message || "请检查后端日志");
    }
}
function selectStrategy(strategyId) {
    document.querySelectorAll(".proxy-strategy-card").forEach(card => {
        card.classList.toggle("active", Number(card.dataset.strategy) === strategyId);
    });
}
async function clearAllProxies() {
    if (!(await appConfirm(`确定要清空所有 ${_proxyList.length} 个代理吗？此操作不可撤销。`, { title: "清空代理", danger: true, confirmText: "清空" }))) return;
    const btn = el("btn-proxy-clear");
    if (btn) { btn.disabled = true; btn.textContent = "清除中..."; }
    try {
        const d = await fetchJson(API + "/proxy/clear", { method: "POST" });
        if (d.ok) {
            _proxyList = [];
            renderProxyList();
            toast("已清空代理列表");
        } else {
            toast("清除失败", d.message || "");
        }
    } catch (e) {
        toast("清除失败", e.message || "");
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = "🗑 清除全部"; }
    }
}
async function fetchWebshareProxies() {
    const apiKey = (el("proxy-webshare-apikey")?.value || "").trim();
    if (!apiKey) {
        toast("请先填写 Webshare API Key", "在测试参数区域输入后再点击获取");
        return;
    }
    const btn = el("btn-proxy-webshare");
    if (btn) { btn.disabled = true; btn.textContent = "获取中..."; }
    try {
        await _doSaveProxyConfig();
    } catch (e) {  }
    try {
        const d = await fetchJson(API + "/proxy/webshare", { method: "POST" });
        if (d.ok) {
            toast(`✅ 获取成功`, d.message || `已导入 ${d.count} 个代理`);
            await loadProxyConfig();  
        } else {
            toast("获取失败", d.message || "请检查 API Key 或账户状态");
        }
    } catch (e) {
        toast("获取失败", e.message || "");
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = "☁ 获取订阅"; }
    }
}
